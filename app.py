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
import logging
import random

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

logging.getLogger('psycopg').setLevel(logging.WARNING)

BOT_TOKEN = os.getenv('BOT_TOKEN', 'your_bot_token_here')
DATABASE_URL = os.getenv('DATABASE_URL')
PHOTO_DIR = '/tmp/photos'

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
            
            if params:
                cur.execute(pg_query, params)
            else:
                cur.execute(pg_query)
            
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

def safe_execute(query, params=()):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            pg_query = query.replace('?', '%s')
            if params:
                cur.execute(pg_query, params)
            else:
                cur.execute(pg_query)
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        return False
    finally:
        conn.close()

def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            try:
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
                conn.commit()
            except Exception as e:
                conn.rollback()
            
            safe_execute('ALTER TABLE users ADD COLUMN photo_url TEXT')
            safe_execute('ALTER TABLE users ADD COLUMN is_premium BOOLEAN DEFAULT FALSE')
            safe_execute('ALTER TABLE users ADD COLUMN daily_likes_used INTEGER DEFAULT 0')
            safe_execute('ALTER TABLE users ADD COLUMN last_like_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
            
            try:
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
                conn.commit()
            except Exception as e:
                conn.rollback()
            
            try:
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
                conn.commit()
            except Exception as e:
                conn.rollback()
            
            try:
                c.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        from_user BIGINT,
                        to_user BIGINT,
                        text TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(from_user) REFERENCES users(id),
                        FOREIGN KEY(to_user) REFERENCES users(id)
                    )
                ''')
                conn.commit()
            except Exception as e:
                conn.rollback()
            
            try:
                c.execute('''
                    CREATE TABLE IF NOT EXISTS tags (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        emoji TEXT
                    )
                ''')
                conn.commit()
            except Exception as e:
                conn.rollback()
            
            try:
                c.execute('SELECT COUNT(*) as cnt FROM tags')
                result = c.fetchone()
                if result and result['cnt'] == 0:
                    tags_data = [
                        ('Sport', '⚽'),
                        ('Crypto', '🧑‍💻'),
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
                        try:
                            c.execute('INSERT INTO tags (name, emoji) VALUES (%s, %s)', (name, emoji))
                        except:
                            pass
                    conn.commit()
            except:
                conn.rollback()
            
            try:
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
            except:
                conn.rollback()
            
            print("✅ База данных скремга!")
    except Exception as e:
        print(f"Init DB error: {e}")
    finally:
        conn.close()

try:
    if DATABASE_URL:
        init_db()
except:
    pass

# ======================== UTILITY FUNCTIONS ========================

def reset_daily_likes(user_id):
    try:
        user = execute_query('SELECT last_like_reset FROM users WHERE id = ?', (user_id,), fetch_one=True)
        if user and user['last_like_reset']:
            time_since_reset = (datetime.now(user['last_like_reset'].tzinfo or None) - user['last_like_reset']).total_seconds()
            if time_since_reset > 86400:
                execute_query('UPDATE users SET daily_likes_used = 0, last_like_reset = CURRENT_TIMESTAMP WHERE id = ?', (user_id,), commit=True)
    except:
        pass

# ======================== API ROUTES ========================

@app.route('/api/tags', methods=['GET'])
def get_tags():
    tags = execute_query('SELECT id, name, emoji FROM tags ORDER BY name', fetch_all=True)
    return jsonify(tags or [])

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    user = execute_query('SELECT id, name, age, city, bio, photo_url FROM users WHERE id = ?', (user_id,), fetch_one=True)
    if user:
        tags = execute_query('''
            SELECT t.id, t.name, t.emoji FROM user_tags ut
            JOIN tags t ON ut.tag_id = t.id
            WHERE ut.user_id = ?
        ''', (user_id,), fetch_all=True)
        user['tags'] = tags or []
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
        
        if data.get('tag_ids'):
            execute_query('DELETE FROM user_tags WHERE user_id = ?', (data['id'],), commit=True)
            for tag_id in data['tag_ids']:
                execute_query('INSERT INTO user_tags (user_id, tag_id) VALUES (?, ?)', (data['id'], tag_id), commit=True)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/profiles/<int:user_id>', methods=['GET'])
def get_profiles(user_id):
    try:
        age_min = int(request.args.get('age_min', 18))
        age_max = int(request.args.get('age_max', 99))
        city = request.args.get('city', '')
        
        reset_daily_likes(user_id)
        
        interacted = execute_query('SELECT to_user FROM likes WHERE from_user = ?', (user_id,), fetch_all=True)
        interacted_ids = [row['to_user'] for row in (interacted or [])] + [user_id]
        
        # FIX: Use proper table alias 'u' and correct SQL syntax
        where_clause = f'WHERE users.id NOT IN ({" , ".join(["%s"] * len(interacted_ids))})'
        where_clause += f' AND users.age >= %s AND users.age <= %s'
        
        if city:
            where_clause += ' AND users.city ILIKE %s'
            params = tuple(interacted_ids) + (age_min, age_max, f'%{city}%')
        else:
            params = tuple(interacted_ids) + (age_min, age_max)
        
        query = f'SELECT id, name, age, city, bio, photo_url FROM users {where_clause} ORDER BY RANDOM() LIMIT 50'
        profiles = execute_query(query, params, fetch_all=True)
        
        for profile in (profiles or []):
            tags = execute_query('''
                SELECT t.id, t.name, t.emoji FROM user_tags ut
                JOIN tags t ON ut.tag_id = t.id
                WHERE ut.user_id = ?
            ''', (profile['id'],), fetch_all=True)
            profile['tags'] = tags or []
        
        return jsonify(profiles or [])
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])

@app.route('/api/like', methods=['POST'])
def like_profile():
    data = request.json
    
    reset_daily_likes(data['from_user'])
    user = execute_query('SELECT daily_likes_used FROM users WHERE id = ?', (data['from_user'],), fetch_one=True)
    
    if user and user['daily_likes_used'] >= 20:
        return jsonify({'error': 'Daily like limit reached', 'limit_reached': True}), 429
    
    try:
        if data.get('dislike'):
            # Delete the like/dislike
            execute_query('DELETE FROM likes WHERE from_user = ? AND to_user = ?', (data['from_user'], data['to_user']), commit=True)
        else:
            execute_query('''
                INSERT INTO likes (from_user, to_user) VALUES (?, ?) ON CONFLICT DO NOTHING
            ''', (data['from_user'], data['to_user']), commit=True)
            
            execute_query('UPDATE users SET daily_likes_used = daily_likes_used + 1 WHERE id = ?', (data['from_user'],), commit=True)
        
        # FIX: Changed to_user -> correct column names
        mutual = execute_query('SELECT * FROM likes WHERE from_user = ? AND to_user = ?', (data['to_user'], data['from_user']), fetch_one=True)
        
        if mutual and not data.get('dislike'):
            u1, u2 = sorted([data['from_user'], data['to_user']])
            execute_query('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?) ON CONFLICT DO NOTHING', (u1, u2), commit=True)
            return jsonify({'match': True})
        
        return jsonify({'match': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/likes/<int:user_id>', methods=['GET'])
def get_likes(user_id):
    """Get users who liked current user"""
    try:
        likes = execute_query('''
            SELECT u.id, u.name, u.age, u.city, u.photo_url
            FROM likes l
            JOIN users u ON l.from_user = u.id
            WHERE l.to_user = ?
        ''', (user_id,), fetch_all=True)
        
        return jsonify(likes or [])
    except Exception as e:
        return jsonify([])

@app.route('/api/chats/<int:user_id>', methods=['GET'])
def get_chats(user_id):
    """Get all chats for user"""
    try:
        # FIX: Fixed the entire query to properly handle chat partner selection
        chats = execute_query('''
            SELECT 
                CASE WHEN user1_id = ? THEN user2_id ELSE user1_id END as user_id,
                u.name as user_name,
                u.photo_url as user_photo,
                (SELECT text FROM messages WHERE (from_user = ? AND to_user = CASE WHEN chats.user1_id = ? THEN chats.user2_id ELSE chats.user1_id END) 
                 OR (from_user = CASE WHEN chats.user1_id = ? THEN chats.user2_id ELSE chats.user1_id END AND to_user = ?) 
                 ORDER BY created_at DESC LIMIT 1) as last_message,
                chats.created_at
            FROM chats
            JOIN users u ON (CASE WHEN user1_id = ? THEN user2_id ELSE user1_id END) = u.id
            WHERE user1_id = ? OR user2_id = ?
            ORDER BY chats.created_at DESC
        ''', (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id), fetch_all=True)
        
        return jsonify(chats or [])
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])

@app.route('/api/messages/<int:user_id_1>/<int:user_id_2>', methods=['GET'])
def get_messages(user_id_1, user_id_2):
    """Get messages between two users - FIX: Changed from to_user to proper column"""
    try:
        messages = execute_query('''
            SELECT from_user, text, created_at
            FROM messages
            WHERE (from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?)
            ORDER BY created_at ASC
        ''', (user_id_1, user_id_2, user_id_2, user_id_1), fetch_all=True)
        
        return jsonify(messages or [])
    except Exception as e:
        print(f"Error in get_messages: {e}")
        return jsonify([])

@app.route('/api/message', methods=['POST'])
def send_message():
    data = request.json
    try:
        execute_query('''
            INSERT INTO messages (from_user, to_user, text) VALUES (?, ?, ?)
        ''', (data['from_user'], data['to_user'], data['text']), commit=True)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/upload-photo', methods=['POST'])
def upload_photo():
    data = request.json
    try:
        user_id = data['user_id']
        photo_base64 = data['photo_data']
        
        photo_filename = f'{user_id}_profile.jpg'
        photo_path = os.path.join(PHOTO_DIR, photo_filename)
        
        if ',' in photo_base64:
            photo_base64 = photo_base64.split(',')[1]
        
        photo_bytes = base64.b64decode(photo_base64)
        with open(photo_path, 'wb') as f:
            f.write(photo_bytes)
        
        photo_url = f'/api/photo/{user_id}'
        execute_query('UPDATE users SET photo_url = ? WHERE id = ?', (photo_url, user_id), commit=True)
        
        return jsonify({'success': True, 'photo_url': photo_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/photo/<int:user_id>', methods=['GET'])
def get_photo(user_id):
    photo_path = os.path.join(PHOTO_DIR, f'{user_id}_profile.jpg')
    if os.path.exists(photo_path):
        return send_file(photo_path, mimetype='image/jpeg')
    return '', 404

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_file(filename)

@app.errorhandler(404)
def not_found(e):
    if not request.path.startswith('/api/'):
        return send_file('index.html')
    return jsonify({'error': 'Not found'}), 404

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
