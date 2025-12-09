from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import sqlite3
import os
import json
import hashlib
import hmac
import time
from datetime import datetime
from functools import wraps

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Конфиг
BOT_TOKEN = os.getenv('BOT_TOKEN', 'your_bot_token_here')
DB_PATH = 'dating.db'

# ======================== DATABASE ========================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
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
    
    # Таблица лайков
    c.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_user, to_user),
            FOREIGN KEY(from_user) REFERENCES users(id),
            FOREIGN KEY(to_user) REFERENCES users(id)
        )
    ''')
    
    # Таблица чатов
    c.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user1_id, user2_id),
            FOREIGN KEY(user1_id) REFERENCES users(id),
            FOREIGN KEY(user2_id) REFERENCES users(id)
        )
    ''')
    
    # Таблица сообщений
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            from_user INTEGER,
            text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chats(id),
            FOREIGN KEY(from_user) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Инициализируем БД сразу при старте приложения
try:
    init_db()
    print("Database initialized successfully")
except Exception as e:
    print(f"Error initializing database: {e}")

# ======================== STATIC ROUTES ========================

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_file(filename)

# ======================== API ROUTES ========================

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return jsonify({
            'id': user['id'],
            'name': user['name'],
            'age': user['age'],
            'city': user['city'],
            'bio': user['bio'],
            'interests': user['interests'],
            'username': user['username']
        })
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user', methods=['POST'])
def create_user():
    data = request.json
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT OR REPLACE INTO users 
            (id, name, age, city, bio, interests, username, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            data['id'],
            data['name'],
            data.get('age'),
            data.get('city'),
            data.get('bio'),
            data.get('interests'),
            data.get('username')
        ))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/profiles/<int:user_id>', methods=['GET'])
def get_profiles(user_id):
    """Получить профили для поиска (исключая пользователя и уже лайкнутые)"""
    conn = get_db()
    c = conn.cursor()
    
    # Получаем ID пользователей, которых уже лайкнул текущий юзер
    c.execute('''
        SELECT to_user FROM likes WHERE from_user = ?
    ''', (user_id,))
    liked_ids = [row['to_user'] for row in c.fetchall()]
    liked_ids.append(user_id)
    
    # Получаем профили, но исключаем уже лайкнутые
    placeholders = ','.join('?' * len(liked_ids))
    c.execute(f'''
        SELECT id, name, age, city, bio, interests 
        FROM users 
        WHERE id NOT IN ({placeholders})
        LIMIT 50
    ''', liked_ids)
    
    profiles = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(profiles)

@app.route('/api/like', methods=['POST'])
def like_profile():
    """Лайк профилю"""
    data = request.json
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT OR IGNORE INTO likes (from_user, to_user)
            VALUES (?, ?)
        ''', (data['from_user'], data['to_user']))
        conn.commit()
        
        # Проверяем, есть ли взаимный лайк (совпадение)
        c.execute('''
            SELECT * FROM likes 
            WHERE from_user = ? AND to_user = ?
        ''', (data['to_user'], data['from_user']))
        
        mutual_like = c.fetchone()
        
        if mutual_like:
            # Создаём чат если его ещё нет
            c.execute('''
                INSERT OR IGNORE INTO chats (user1_id, user2_id)
                VALUES (?, ?)
            ''', (min(data['from_user'], data['to_user']), 
                  max(data['from_user'], data['to_user'])))
            conn.commit()
        
        conn.close()
        return jsonify({'match': bool(mutual_like)})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/matches/<int:user_id>', methods=['GET'])
def get_matches(user_id):
    """Получить совпадения (взаимные лайки)"""
    conn = get_db()
    c = conn.cursor()
    
    # Получаем чаты пользователя
    c.execute('''
        SELECT user1_id, user2_id FROM chats 
        WHERE user1_id = ? OR user2_id = ?
    ''', (user_id, user_id))
    
    chats = c.fetchall()
    matches = []
    
    for chat in chats:
        match_id = chat['user2_id'] if chat['user1_id'] == user_id else chat['user1_id']
        c.execute('SELECT id, name, city FROM users WHERE id = ?', (match_id,))
        user = c.fetchone()
        if user:
            matches.append({
                'id': user['id'],
                'name': user['name'],
                'city': user['city']
            })
    
    conn.close()
    return jsonify(matches)

@app.route('/api/messages/<int:chat_id>', methods=['GET'])
def get_messages(chat_id):
    """Получить сообщения чата"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''
        SELECT m.id, m.from_user, m.text, m.created_at, u.name
        FROM messages m
        JOIN users u ON m.from_user = u.id
        WHERE m.chat_id = ?
        ORDER BY m.created_at DESC
        LIMIT 50
    ''', (chat_id,))
    
    messages = [{
        'id': row['id'],
        'from_user': row['from_user'],
        'name': row['name'],
        'text': row['text'],
        'created_at': row['created_at']
    } for row in c.fetchall()]
    
    conn.close()
    return jsonify(messages[::-1])

@app.route('/api/messages', methods=['POST'])
def send_message():
    """Отправить сообщение"""
    data = request.json
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT INTO messages (chat_id, from_user, text)
            VALUES (?, ?, ?)
        ''', (data['chat_id'], data['from_user'], data['text']))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# ======================== ERROR HANDLERS ========================

@app.errorhandler(404)
def not_found(e):
    # Для SPA - вернуть index.html для всех неизвестных маршрутов
    if not request.path.startswith('/api/'):
        return send_file('index.html')
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

# ======================== MAIN ========================

if __name__ == '__main__':
    # init_db() уже вызывается выше
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
