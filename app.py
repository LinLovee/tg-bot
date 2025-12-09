from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from functools import wraps
import psycopg
from psycopg.rows import dict_row
import base64

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Конфиг
BOT_TOKEN = os.getenv('BOT_TOKEN', 'your_bot_token_here')
DATABASE_URL = os.getenv('DATABASE_URL')
PHOTO_DIR = '/tmp/photos'  # Render эфемерная папка, локально для теста

if not os.path.exists(PHOTO_DIR):
    os.makedirs(PHOTO_DIR, exist_ok=True)

# ======================== DATABASE ========================

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def execute_query(query, params=(), fetch_one=False, fetch_all=False, commit=False):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            pg_query = query.replace('?', '%s')
            cur.execute(pg_query, params)
            
            result = None
            if fetch_one:
                result = cur.fetchone()
            elif fetch_all:
                result = cur.fetchall()
            
            if commit:
                conn.commit()
                
            return result
    except Exception as e:
        conn.rollback()
        print(f"Database error: {e}")
        raise e
    finally:
        conn.close()

def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Пользователи (enhanced with photo, premium status, like limits)
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    name TEXT NOT NULL,
                    age INTEGER,
                    city TEXT,
                    bio TEXT,
                    interests TEXT,
                    username TEXT,
                    photo_url TEXT,
                    is_premium BOOLEAN DEFAULT FALSE,
                    daily_likes_used INTEGER DEFAULT 0,
                    last_like_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            
            # Чаты (with last_message_at for expiring matches)
            c.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    id SERIAL PRIMARY KEY,
                    user1_id BIGINT,
                    user2_id BIGINT,
                    last_message_at TIMESTAMP,
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
            
            # Predefined tags
            c.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    emoji TEXT
                )
            ''')
            
            # Вставка тегов, если ещё нет
            c.execute('SELECT COUNT(*) as cnt FROM tags')
            if c.fetchone()['cnt'] == 0:
                tags_data = [
                    ('Sport', '⚽'),
                    ('Crypto', '🢰'),
                    ('Travel', '✈️'),
                    ('Music', '🎵'),
                    ('Gaming', '🎮'),
                    ('Dogs', '🐕'),
                    ('Cats', '🐱'),
                    ('Fitness', '💪'),
                    ('Art', '🎨'),
                    ('Books', '📚'),
                    ('Food', '🍕'),
                    ('Fashion', '👗')
                ]
                for name, emoji in tags_data:
                    c.execute('INSERT INTO tags (name, emoji) VALUES (?, ?)', (name, emoji))
            
            # User-tags mapping
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_tags (
                    user_id BIGINT,
                    tag_id INTEGER,
                    PRIMARY KEY (user_id, tag_id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(tag_id) REFERENCES tags(id)
                )
            ''')
            
            conn.commit()
            print("Database initialized successfully with new features!")
    except Exception as e:
        print(f"Init DB error: {e}")
    finally:
        conn.close()

# Инициализация при старте
try:
    if DATABASE_URL:
        init_db()
    else:
        print("WARNING: DATABASE_URL not set")
except Exception as e:
    print(f"Startup error: {e}")

# ======================== UTILITY FUNCTIONS ========================

def reset_daily_likes(user_id):
    """Reset daily like counter if 24h passed"""
    user = execute_query('SELECT last_like_reset FROM users WHERE id = ?', (user_id,), fetch_one=True)
    if user and user['last_like_reset']:
        time_since_reset = (datetime.now(user['last_like_reset'].tzinfo or None) - user['last_like_reset']).total_seconds()
        if time_since_reset > 86400:  # 24 hours
            execute_query('UPDATE users SET daily_likes_used = 0, last_like_reset = CURRENT_TIMESTAMP WHERE id = ?', (user_id,), commit=True)
            return True
    return False

def delete_expired_chats():
    """Delete chats with no messages for 24 hours"""
    try:
        # Find and delete expired chats
        execute_query('''
            DELETE FROM messages WHERE chat_id IN (
                SELECT id FROM chats WHERE last_message_at IS NULL 
                AND created_at < NOW() - INTERVAL '24 hours'
            )
        ''', commit=True)
        
        execute_query('''
            DELETE FROM chats WHERE last_message_at IS NULL 
            AND created_at < NOW() - INTERVAL '24 hours'
        ''', commit=True)
    except Exception as e:
        print(f"Error deleting expired chats: {e}")

def get_ai_icebreaker(user1_id, user2_id):
    """Generate AI icebreaker based on common interests"""
    # Get user1 tags
    user1_tags = execute_query('''
        SELECT t.name FROM user_tags ut
        JOIN tags t ON ut.tag_id = t.id
        WHERE ut.user_id = ?
    ''', (user1_id,), fetch_all=True)
    
    user1_tag_names = [t['name'] for t in user1_tags]
    
    # Get user2 tags
    user2_tags = execute_query('''
        SELECT t.name, t.emoji FROM user_tags ut
        JOIN tags t ON ut.tag_id = t.id
        WHERE ut.user_id = ?
    ''', (user2_id,), fetch_all=True)
    
    # Find common tags
    common_tags = [t for t in user2_tags if t['name'] in user1_tag_names]
    
    if common_tags:
        tag = common_tags[0]
        icebreakers = {
            'Sport': f'Wow, you both love sports! {tag["emoji"]} What\'s your favorite team?',
            'Crypto': f'Crypto enthusiasts! {tag["emoji"]} What\'s your favorite coin?',
            'Travel': f'You both love traveling! {tag["emoji"]} What was your last trip?',
            'Music': f'Music lovers! {tag["emoji"]} What\'s your favorite artist?',
            'Dogs': f'You both have dogs! {tag["emoji"]} Tell about yours!',
            'Fitness': f'Fitness buddies! {tag["emoji"]} What\'s your workout routine?',
            'Food': f'Foodies! {tag["emoji"]} What\'s your favorite cuisine?',
        }
        return icebreakers.get(tag['name'], f'You both love {tag["name"]}! {tag["emoji"]}')
    
    return 'Say hi and break the ice!'

def check_premium_status(user_id):
    """Check if user has Telegram Premium (basic check)"""
    # In production, integrate with Telegram Bot API
    # For now, returning placeholder
    return False

# ======================== VALIDATION ========================

def validate_init_data(init_data):
    """Валидирует initData от Telegram WebApp"""
    try:
        data = {}
        for item in init_data.split('&'):
            if '=' in item:
                k, v = item.split('=', 1)
                data[k] = v
        
        hash_val = data.pop('hash', '')
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        
        secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, hash_val):
            return None
        
        auth_date = int(data.get('auth_date', 0))
        if time.time() - auth_date > 3600:
            return None
        
        user = json.loads(data.get('user', '{}'))
        return user
    except Exception as e:
        print(f'Validation error: {e}')
        return None

# ======================== API ROUTES ========================

@app.route('/api/tags', methods=['GET'])
def get_tags():
    """Get all available tags"""
    tags = execute_query('SELECT id, name, emoji FROM tags ORDER BY name', fetch_all=True)
    return jsonify(tags)

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    user = execute_query('SELECT id, name, age, city, bio, photo_url, is_premium FROM users WHERE id = ?', (user_id,), fetch_one=True)
    if user:
        # Get user tags
        tags = execute_query('''
            SELECT t.id, t.name, t.emoji FROM user_tags ut
            JOIN tags t ON ut.tag_id = t.id
            WHERE ut.user_id = ?
        ''', (user_id,), fetch_all=True)
        user['tags'] = tags
        return jsonify(user)
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user', methods=['POST'])
def create_user():
    data = request.json
    try:
        execute_query('''
            INSERT INTO users (id, name, age, city, bio, photo_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            age = EXCLUDED.age,
            city = EXCLUDED.city,
            bio = EXCLUDED.bio,
            photo_url = EXCLUDED.photo_url,
            updated_at = CURRENT_TIMESTAMP
        ''', (
            data['id'], data['name'], data.get('age'), data.get('city'),
            data.get('bio'), data.get('photo_url')
        ), commit=True)
        
        # Update tags if provided
        if data.get('tag_ids'):
            # Delete old tags
            execute_query('DELETE FROM user_tags WHERE user_id = ?', (data['id'],), commit=True)
            # Add new tags
            for tag_id in data['tag_ids']:
                execute_query('INSERT INTO user_tags (user_id, tag_id) VALUES (?, ?)', (data['id'], tag_id), commit=True)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/profiles/<int:user_id>', methods=['GET'])
def get_profiles(user_id):
    """Get profiles for search"""
    delete_expired_chats()
    reset_daily_likes(user_id)
    
    # Get liked IDs
    likes = execute_query('SELECT to_user FROM likes WHERE from_user = ?', (user_id,), fetch_all=True)
    liked_ids = [row['to_user'] for row in likes]
    liked_ids.append(user_id)
    
    # Get profiles with tags
    query = 'SELECT id, name, age, city, bio, photo_url FROM users WHERE id != ALL(%s) LIMIT 50'
    profiles = execute_query(query, (liked_ids,), fetch_all=True)
    
    # Add tags for each profile
    for profile in profiles:
        tags = execute_query('''
            SELECT t.id, t.name, t.emoji FROM user_tags ut
            JOIN tags t ON ut.tag_id = t.id
            WHERE ut.user_id = ?
        ''', (profile['id'],), fetch_all=True)
        profile['tags'] = tags
    
    return jsonify(profiles)

@app.route('/api/like', methods=['POST'])
def like_profile():
    """Like a profile with like limit check"""
    data = request.json
    
    # Check like limits (20 per day)
    reset_daily_likes(data['from_user'])
    user = execute_query('SELECT daily_likes_used FROM users WHERE id = ?', (data['from_user'],), fetch_one=True)
    
    if user and user['daily_likes_used'] >= 20:
        return jsonify({'error': 'Daily like limit reached (20 per day)', 'limit_reached': True}), 429
    
    try:
        execute_query('''
            INSERT INTO likes (from_user, to_user) VALUES (?, ?) ON CONFLICT DO NOTHING
        ''', (data['from_user'], data['to_user']), commit=True)
        
        # Increment like counter
        execute_query('UPDATE users SET daily_likes_used = daily_likes_used + 1 WHERE id = ?', (data['from_user'],), commit=True)
        
        # Check for mutual like
        mutual_like = execute_query('''
            SELECT * FROM likes WHERE from_user = ? AND to_user = ?
        ''', (data['to_user'], data['from_user']), fetch_one=True)
        
        if mutual_like:
            # Sort IDs for uniqueness
            u1, u2 = sorted([data['from_user'], data['to_user']])
            execute_query('''
                INSERT INTO chats (user1_id, user2_id, last_message_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING
            ''', (u1, u2), commit=True)
            
            # Get AI icebreaker
            icebreaker = get_ai_icebreaker(data['from_user'], data['to_user'])
            return jsonify({'match': True, 'icebreaker': icebreaker})
        
        return jsonify({'match': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/matches/<int:user_id>', methods=['GET'])
def get_matches(user_id):
    """Get matches with common tags highlighted"""
    delete_expired_chats()
    chats = execute_query('''
        SELECT user1_id, user2_id FROM chats WHERE user1_id = ? OR user2_id = ?
    ''', (user_id, user_id), fetch_all=True)
    
    matches = []
    for chat in chats:
        match_id = chat['user2_id'] if chat['user1_id'] == user_id else chat['user1_id']
        user = execute_query('SELECT id, name, city, photo_url FROM users WHERE id = ?', (match_id,), fetch_one=True)
        if user:
            # Get common tags
            user_tags = execute_query('''
                SELECT t.name FROM user_tags ut
                JOIN tags t ON ut.tag_id = t.id
                WHERE ut.user_id = ?
            ''', (user_id,), fetch_all=True)
            user_tag_names = [t['name'] for t in user_tags]
            
            match_tags = execute_query('''
                SELECT t.id, t.name, t.emoji FROM user_tags ut
                JOIN tags t ON ut.tag_id = t.id
                WHERE ut.user_id = ?
            ''', (match_id,), fetch_all=True)
            
            common_tags = [t for t in match_tags if t['name'] in user_tag_names]
            
            user['common_tags'] = common_tags
            matches.append(user)
    
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
        
        # Update last_message_at in chat
        execute_query('UPDATE chats SET last_message_at = CURRENT_TIMESTAMP WHERE id = ?', (data['chat_id'],), commit=True)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/upload-photo', methods=['POST'])
def upload_photo():
    """Upload photo as base64"""
    data = request.json
    try:
        user_id = data['user_id']
        photo_base64 = data['photo_data']
        
        # Save to /tmp or return URL
        # For production, use Cloudinary/AWS S3
        photo_filename = f'{user_id}_profile.jpg'
        photo_path = os.path.join(PHOTO_DIR, photo_filename)
        
        # Decode and save
        if ',' in photo_base64:
            photo_base64 = photo_base64.split(',')[1]
        
        photo_bytes = base64.b64decode(photo_base64)
        with open(photo_path, 'wb') as f:
            f.write(photo_bytes)
        
        # Return photo URL
        photo_url = f'/api/photo/{user_id}'
        
        # Update user photo_url
        execute_query('UPDATE users SET photo_url = ? WHERE id = ?', (photo_url, user_id), commit=True)
        
        return jsonify({'success': True, 'photo_url': photo_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/photo/<int:user_id>', methods=['GET'])
def get_photo(user_id):
    """Serve user photo"""
    photo_path = os.path.join(PHOTO_DIR, f'{user_id}_profile.jpg')
    if os.path.exists(photo_path):
        return send_file(photo_path, mimetype='image/jpeg')
    return '', 404

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
