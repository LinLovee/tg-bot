from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import hashlib
import hmac
import time
from datetime import datetime
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Конфиг
BOT_TOKEN = os.getenv('BOT_TOKEN', 'your_bot_token_here')
DATABASE_URL = os.getenv('DATABASE_URL')

# ======================== DATABASE ========================

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def execute_query(query, params=(), fetch_one=False, fetch_all=False, commit=False):
    conn = get_db_connection()
    cur = conn.cursor()
    # Адаптация плейсхолдеров SQLite (?) под Postgres (%s)
    pg_query = query.replace('?', '%s')
    
    try:
        cur.execute(pg_query, params)
        if commit:
            conn.commit()
            result = True
        elif fetch_one:
            result = cur.fetchone()
        elif fetch_all:
            result = cur.fetchall()
        else:
            result = None
        cur.close()
        conn.close()
        return result
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        print(f"Database error: {e}")
        raise e

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Пользователи (ID - это Telegram ID, поэтому BIGINT)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT NOT NULL,
            age INTEGER,
            city TEXT,
            bio TEXT,
            interests TEXT,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Лайки
    c.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            from_user BIGINT,
            to_user BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_user, to_user),
            FOREIGN KEY(from_user) REFERENCES users(id),
            FOREIGN KEY(to_user) REFERENCES users(id)
        )
    ''')
    
    # Чаты
    c.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id SERIAL PRIMARY KEY,
            user1_id BIGINT,
            user2_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user1_id, user2_id),
            FOREIGN KEY(user1_id) REFERENCES users(id),
            FOREIGN KEY(user2_id) REFERENCES users(id)
        )
    ''')
    
    # Сообщения
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            chat_id INTEGER,
            from_user BIGINT,
            text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chats(id),
            FOREIGN KEY(from_user) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized successfully (PostgreSQL)")

# Инициализация при старте
try:
    if DATABASE_URL:
        init_db()
    else:
        print("WARNING: DATABASE_URL not set")
except Exception as e:
    print(f"Init DB error: {e}")

# ======================== VALIDATION ========================

def validate_init_data(init_data):
    """Валидирует initData от Telegram WebApp"""
    try:
        # Parse query string
        data = {}
        for item in init_data.split('&'):
            if '=' in item:
                k, v = item.split('=', 1)
                data[k] = v
        
        hash_val = data.pop('hash', '')
        
        # Создаём data_check_string
        data_check_string = '\n'.join(
            f'{k}={v}' for k, v in sorted(data.items())
        )
        
        # Вычисляем secret_key и hash
        secret_key = hmac.new(
            b'WebAppData',
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Проверяем hash
        if not hmac.compare_digest(calculated_hash, hash_val):
            return None
        
        # Проверяем время (максимум 1 час)
        auth_date = int(data.get('auth_date', 0))
        if time.time() - auth_date > 3600:
            return None
        
        # Парсим user JSON
        user = json.loads(data.get('user', '{}'))
        return user
    except Exception as e:
        print(f'Validation error: {e}')
        return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        init_data = request.headers.get('X-Init-Data')
        if not init_data:
            return jsonify({'error': 'No init data'}), 401
        
        user = validate_init_data(init_data)
        if not user:
            return jsonify({'error': 'Invalid init data'}), 401
        
        request.user_id = user.get('id')
        return f(*args, **kwargs)
    return decorated

# ======================== API ROUTES ========================

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    user = execute_query('SELECT * FROM users WHERE id = ?', (user_id,), fetch_one=True)
    if user:
        return jsonify(dict(user))
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user', methods=['POST'])
def create_user():
    data = request.json
    try:
        # Postgres UPSERT syntax (ON CONFLICT)
        execute_query('''
            INSERT INTO users (id, name, age, city, bio, interests, username, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            age = EXCLUDED.age,
            city = EXCLUDED.city,
            bio = EXCLUDED.bio,
            interests = EXCLUDED.interests,
            username = EXCLUDED.username,
            updated_at = CURRENT_TIMESTAMP
        ''', (
            data['id'], data['name'], data.get('age'), data.get('city'),
            data.get('bio'), data.get('interests'), data.get('username')
        ), commit=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/profiles/<int:user_id>', methods=['GET'])
def get_profiles(user_id):
    """Получить профили для поиска (исключая пользователя и уже лайкнутые)"""
    # Получаем ID лайкнутых
    likes = execute_query('SELECT to_user FROM likes WHERE from_user = ?', (user_id,), fetch_all=True)
    liked_ids = [row['to_user'] for row in likes]
    liked_ids.append(user_id)
    
    # Postgres ANY/ALL syntax for array
    # Используем list(tuple) для правильной передачи в %s
    query = 'SELECT id, name, age, city, bio, interests FROM users WHERE id != ALL(%s) LIMIT 50'
    profiles = execute_query(query, (liked_ids,), fetch_all=True)
    
    return jsonify([dict(row) for row in profiles])

@app.route('/api/like', methods=['POST'])
def like_profile():
    data = request.json
    try:
        execute_query('''
            INSERT INTO likes (from_user, to_user) VALUES (?, ?) ON CONFLICT DO NOTHING
        ''', (data['from_user'], data['to_user']), commit=True)
        
        mutual_like = execute_query('''
            SELECT * FROM likes WHERE from_user = ? AND to_user = ?
        ''', (data['to_user'], data['from_user']), fetch_one=True)
        
        if mutual_like:
            # Сортируем ID для уникальности чата
            u1, u2 = sorted([data['from_user'], data['to_user']])
            execute_query('''
                INSERT INTO chats (user1_id, user2_id) VALUES (?, ?) ON CONFLICT DO NOTHING
            ''', (u1, u2), commit=True)
        
        return jsonify({'match': bool(mutual_like)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/matches/<int:user_id>', methods=['GET'])
def get_matches(user_id):
    chats = execute_query('''
        SELECT user1_id, user2_id FROM chats WHERE user1_id = ? OR user2_id = ?
    ''', (user_id, user_id), fetch_all=True)
    
    matches = []
    for chat in chats:
        match_id = chat['user2_id'] if chat['user1_id'] == user_id else chat['user1_id']
        user = execute_query('SELECT id, name, city FROM users WHERE id = ?', (match_id,), fetch_one=True)
        if user:
            matches.append(dict(user))
            
    return jsonify(matches)

@app.route('/api/messages/<int:chat_id>', methods=['GET'])
def get_messages(chat_id):
    messages = execute_query('''
        SELECT m.id, m.from_user, m.text, m.created_at, u.name
        FROM messages m
        JOIN users u ON m.from_user = u.id
        WHERE m.chat_id = ?
        ORDER BY m.created_at DESC
        LIMIT 50
    ''', (chat_id,), fetch_all=True)
    
    # Конвертируем datetime в строку для JSON
    result = []
    for msg in messages:
        m_dict = dict(msg)
        if isinstance(m_dict['created_at'], datetime):
            m_dict['created_at'] = m_dict['created_at'].isoformat()
        result.append(m_dict)
        
    return jsonify(result[::-1])

@app.route('/api/messages', methods=['POST'])
def send_message():
    data = request.json
    try:
        execute_query('''
            INSERT INTO messages (chat_id, from_user, text) VALUES (?, ?, ?)
        ''', (data['chat_id'], data['from_user'], data['text']), commit=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# ======================== ERROR HANDLERS ========================

@app.errorhandler(404)
def not_found(e):
    if not request.path.startswith('/api/'):
        return send_file('index.html')
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

# ======================== MAIN ========================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
