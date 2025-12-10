from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from functools import wraps, lru_cache
import psycopg
from psycopg import pool
from psycopg.rows import dict_row
import base64
import logging
import random
from threading import Thread
import io

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

logging.getLogger('psycopg').setLevel(logging.WARNING)

BOT_TOKEN = os.getenv('BOT_TOKEN', 'your_bot_token_here')
DATABASE_URL = os.getenv('DATABASE_URL')
PHOTO_DIR = 'photos'

if not os.path.exists(PHOTO_DIR):
    os.makedirs(PHOTO_DIR, exist_ok=True)

# ✅ CONNECTION POOL - одна из самых важных оптимизаций!
connection_pool = None

def init_connection_pool():
    global connection_pool
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")
    connection_pool = psycopg.pool.SimpleConnectionPool(
        min_size=2,
        max_size=10,
        conninfo=DATABASE_URL,
        row_factory=dict_row
    )

def get_db_connection():
    global connection_pool
    if connection_pool is None:
        init_connection_pool()
    return connection_pool.getconn()

def return_db_connection(conn):
    global connection_pool
    if connection_pool and conn:
        connection_pool.putconn(conn)

# ✅ КЭШИРОВАНИЕ тегов (они почти никогда не меняются)
_tags_cache = None
_tags_cache_time = 0

def get_tags_cached(force_refresh=False):
    global _tags_cache, _tags_cache_time
    current_time = time.time()
    
    # Обновляем кэш каждые 1 час или если принудительно попросили
    if force_refresh or _tags_cache is None or (current_time - _tags_cache_time) > 3600:
        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT id, name, emoji FROM tags ORDER BY name')
                    _tags_cache = cur.fetchall()
                    _tags_cache_time = current_time
            finally:
                return_db_connection(conn)
        except:
            _tags_cache = []
    
    return _tags_cache or []

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
        try:
            conn.rollback()
        except:
            pass
        print(f"Database error: {e}")
        raise e
    finally:
        return_db_connection(conn)

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
        try:
            conn.rollback()
        except:
            pass
        return False
    finally:
        return_db_connection(conn)

# ✅ АСИНХРОННОЕ сохранение фотографий
def save_photo_async(user_id, photo_base64):
    def _save():
        try:
            if ',' in photo_base64:
                photo_base64_clean = photo_base64.split(',')[1]
            else:
                photo_base64_clean = photo_base64
            
            photo_bytes = base64.b64decode(photo_base64_clean)
            photo_path = os.path.join(PHOTO_DIR, f'{user_id}_profile.jpg')
            
            with open(photo_path, 'wb') as f:
                f.write(photo_bytes)
        except Exception as e:
            print(f"Photo save error: {e}")
    
    thread = Thread(target=_save, daemon=True)
    thread.start()

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
                try:
                    conn.rollback()
                except:
                    pass
            
            safe_execute('ALTER TABLE users ADD COLUMN photo_url TEXT')
            safe_execute('ALTER TABLE users ADD COLUMN is_premium BOOLEAN DEFAULT FALSE')
            safe_execute('ALTER TABLE users ADD COLUMN daily_likes_used INTEGER DEFAULT 0')
            safe_execute('ALTER TABLE users ADD COLUMN last_like_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
            
            # ✅ СОЗДАНИЕ ИНДЕКСОВ для быстрых запросов
            try:
                c.execute('CREATE INDEX IF NOT EXISTS idx_users_city ON users(city)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_users_age ON users(age)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_likes_from_user ON likes(from_user)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_likes_to_user ON likes(to_user)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_chats_users ON chats(user1_id, user2_id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_user_tags_user_id ON user_tags(user_id)')
                conn.commit()
                print("✅ Indexes created")
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                print(f"Index creation note: {e}")
            
            # Migration: Drop old likes table constraints and recreate with CASCADE
            try:
                c.execute('ALTER TABLE likes DROP CONSTRAINT IF EXISTS likes_from_user_fkey')
                c.execute('ALTER TABLE likes DROP CONSTRAINT IF EXISTS likes_to_user_fkey')
                c.execute('ALTER TABLE likes ADD CONSTRAINT likes_from_user_fkey FOREIGN KEY(from_user) REFERENCES users(id) ON DELETE CASCADE')
                c.execute('ALTER TABLE likes ADD CONSTRAINT likes_to_user_fkey FOREIGN KEY(to_user) REFERENCES users(id) ON DELETE CASCADE')
                conn.commit()
                print("✅ Updated likes table constraints")
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                print(f"Likes migration note: {e}")
            
            # Migration: Drop old chats table constraints and recreate with CASCADE
            try:
                c.execute('ALTER TABLE chats DROP CONSTRAINT IF EXISTS chats_user1_id_fkey')
                c.execute('ALTER TABLE chats DROP CONSTRAINT IF EXISTS chats_user2_id_fkey')
                c.execute('ALTER TABLE chats ADD CONSTRAINT chats_user1_id_fkey FOREIGN KEY(user1_id) REFERENCES users(id) ON DELETE CASCADE')
                c.execute('ALTER TABLE chats ADD CONSTRAINT chats_user2_id_fkey FOREIGN KEY(user2_id) REFERENCES users(id) ON DELETE CASCADE')
                conn.commit()
                print("✅ Updated chats table constraints")
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                print(f"Chats migration note: {e}")
            
            # Migration: Fix messages table to add CASCADE for from_user and chat_id
            try:
                c.execute('ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_from_user_fkey')
                c.execute('ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_chat_id_fkey')
                c.execute('ALTER TABLE messages ADD CONSTRAINT messages_from_user_fkey FOREIGN KEY(from_user) REFERENCES users(id) ON DELETE CASCADE')
                c.execute('ALTER TABLE messages ADD CONSTRAINT messages_chat_id_fkey FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE')
                conn.commit()
                print("✅ Updated messages table constraints")
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                print(f"Messages migration note: {e}")
            
            # Migration: Fix user_tags table to add CASCADE for user_id and tag_id
            try:
                c.execute('ALTER TABLE user_tags DROP CONSTRAINT IF EXISTS user_tags_user_id_fkey')
                c.execute('ALTER TABLE user_tags DROP CONSTRAINT IF EXISTS user_tags_tag_id_fkey')
                c.execute('ALTER TABLE user_tags ADD CONSTRAINT user_tags_user_id_fkey FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE')
                c.execute('ALTER TABLE user_tags ADD CONSTRAINT user_tags_tag_id_fkey FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE')
                conn.commit()
                print("✅ Updated user_tags table constraints")
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                print(f"User_tags migration note: {e}")
            
            try:
                c.execute('''
                    CREATE TABLE IF NOT EXISTS chats (
                        id SERIAL PRIMARY KEY,
                        user1_id BIGINT,
                        user2_id BIGINT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user1_id, user2_id),
                        FOREIGN KEY(user1_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(user2_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
            
            try:
                c.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        from_user BIGINT,
                        text TEXT,
                        chat_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(from_user) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
                    )
                ''')
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
            
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
                try:
                    conn.rollback()
                except:
                    pass
            
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
                try:
                    conn.rollback()
                except:
                    pass
            
            try:
                c.execute('''
                    CREATE TABLE IF NOT EXISTS user_tags (
                        user_id BIGINT,
                        tag_id INTEGER,
                        PRIMARY KEY (user_id, tag_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
                    )
                ''')
                conn.commit()
            except:
                try:
                    conn.rollback()
                except:
                    pass
            
            print("✅ Database is ready!")
    except Exception as e:
        print(f"Init DB error: {e}")
    finally:
        return_db_connection(conn)

try:
    if DATABASE_URL:
        init_db()
        get_tags_cached()  # Предзагружаем теги в памяти
except Exception as e:
    print(f"Init error: {e}")

def reset_daily_likes(user_id):
    try:
        user = execute_query('SELECT last_like_reset FROM users WHERE id = ?', (user_id,), fetch_one=True)
        if user and user['last_like_reset']:
            time_since_reset = (datetime.now(user['last_like_reset'].tzinfo or None) - user['last_like_reset']).total_seconds()
            if time_since_reset > 86400:
                execute_query('UPDATE users SET daily_likes_used = 0, last_like_reset = CURRENT_TIMESTAMP WHERE id = ?', (user_id,), commit=True)
    except:
        pass

@app.route('/api/tags', methods=['GET'])
def get_tags():
    # ✅ Используем кэшированные теги
    tags = get_tags_cached()
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

@app.route('/api/user/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    try:
        # CASCADE will automatically delete chats, messages, likes, user_tags
        execute_query('DELETE FROM users WHERE id = ?', (user_id,), commit=True)
        
        # Delete photo file асинхронно
        def delete_photo():
            photo_path = os.path.join(PHOTO_DIR, f'{user_id}_profile.jpg')
            try:
                if os.path.exists(photo_path):
                    os.remove(photo_path)
            except:
                pass
        
        thread = Thread(target=delete_photo, daemon=True)
        thread.start()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete user error: {e}")
        return jsonify({'error': str(e)}), 400

@app.route('/api/profiles/<int:user_id>', methods=['GET'])
def get_profiles(user_id):
    try:
        age_min = int(request.args.get('age_min', 18))
        age_max = int(request.args.get('age_max', 99))
        city = request.args.get('city', '')
        
        reset_daily_likes(user_id)
        
        # ✅ ОДИНОЧНЫЙ запрос вместо N+1
        interacted = execute_query('SELECT to_user FROM likes WHERE from_user = ?', (user_id,), fetch_all=True)
        interacted_ids = [row['to_user'] for row in (interacted or [])] + [user_id]
        
        where_clause = f'WHERE users.id NOT IN ({" , ".join(["%s"] * len(interacted_ids))})'
        where_clause += f' AND users.age >= %s AND users.age <= %s'
        
        if city:
            where_clause += ' AND users.city ILIKE %s'
            params = tuple(interacted_ids) + (age_min, age_max, f'%{city}%')
        else:
            params = tuple(interacted_ids) + (age_min, age_max)
        
        # ✅ Получаем профили с тегами в одном JOIN запросе
        query = f'''
            SELECT DISTINCT 
                users.id, users.name, users.age, users.city, users.bio, users.photo_url
            FROM users
            LEFT JOIN user_tags ON users.id = user_tags.user_id
            {where_clause}
            ORDER BY RANDOM() 
            LIMIT 50
        '''
        profiles = execute_query(query, params, fetch_all=True)
        
        # ✅ Получаем все теги для этих профилей ОДНИМ запросом
        if profiles:
            profile_ids = [p['id'] for p in profiles]
            tags_query = f'''
                SELECT user_id, id, name, emoji FROM user_tags ut
                JOIN tags t ON ut.tag_id = t.id
                WHERE ut.user_id IN ({", ".join(["%s"] * len(profile_ids))})
            '''
            all_tags = execute_query(tags_query, tuple(profile_ids), fetch_all=True)
            
            # Создаём маппинг тегов по user_id
            tags_by_user = {}
            for tag in (all_tags or []):
                user_id_tag = tag['user_id']
                if user_id_tag not in tags_by_user:
                    tags_by_user[user_id_tag] = []
                tags_by_user[user_id_tag].append({'id': tag['id'], 'name': tag['name'], 'emoji': tag['emoji']})
            
            # Присваиваем теги профилям
            for profile in profiles:
                profile['tags'] = tags_by_user.get(profile['id'], [])
        
        return jsonify(profiles or [])
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])

@app.route('/api/like', methods=['POST'])
def like_profile():
    data = request.json
    from_user = data.get('from_user')
    to_user = data.get('to_user')
    is_dislike = data.get('dislike', False)
    
    try:
        if is_dislike:
            execute_query('DELETE FROM likes WHERE from_user = ? AND to_user = ?', (from_user, to_user), commit=True)
            return jsonify({'match': False})
        
        # Check if already liked
        existing = execute_query('SELECT * FROM likes WHERE from_user = ? AND to_user = ?', (from_user, to_user), fetch_one=True)
        if not existing:
            execute_query('INSERT INTO likes (from_user, to_user) VALUES (?, ?)', (from_user, to_user), commit=True)
        
        # Check for mutual like
        mutual = execute_query('SELECT * FROM likes WHERE from_user = ? AND to_user = ?', (to_user, from_user), fetch_one=True)
        
        if mutual:
            # Create chat
            u1, u2 = sorted([from_user, to_user])
            execute_query('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?) ON CONFLICT DO NOTHING', (u1, u2), commit=True)
            return jsonify({'match': True})
        
        return jsonify({'match': False})
    except Exception as e:
        print(f"Like error: {e}")
        return jsonify({'error': str(e), 'match': False}), 400

@app.route('/api/likes/<int:user_id>', methods=['GET'])
def get_likes(user_id):
    try:
        likes = execute_query('''
            SELECT u.id, u.name, u.age, u.city, u.photo_url
            FROM likes l
            JOIN users u ON l.from_user = u.id
            WHERE l.to_user = ?
        ''', (user_id,), fetch_all=True)
        return jsonify(likes or [])
    except Exception as e:
        print(f"Get likes error: {e}")
        return jsonify([])

@app.route('/api/chats/<int:user_id>', methods=['GET'])
def get_chats(user_id):
    try:
        # ✅ Всё в одном запросе - очень важно!
        chats = execute_query('''
            SELECT 
                CASE WHEN user1_id = %s THEN user2_id ELSE user1_id END as user_id,
                u.name as user_name,
                u.photo_url as user_photo,
                c.id as chat_id,
                c.created_at,
                (SELECT text FROM messages WHERE chat_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message
            FROM chats c
            JOIN users u ON (CASE WHEN c.user1_id = %s THEN c.user2_id ELSE c.user1_id END) = u.id
            WHERE c.user1_id = %s OR c.user2_id = %s
            ORDER BY c.created_at DESC
        ''', (user_id, user_id, user_id, user_id), fetch_all=True)
        
        # Обрабатываем null сообщения
        for chat in (chats or []):
            if not chat['last_message']:
                chat['last_message'] = 'Начни разговор...'
        
        return jsonify(chats or [])
    except Exception as e:
        print(f"Get chats error: {e}")
        return jsonify([])

@app.route('/api/messages/<int:user_id_1>/<int:user_id_2>', methods=['GET'])
def get_messages(user_id_1, user_id_2):
    try:
        u1, u2 = sorted([user_id_1, user_id_2])
        chat = execute_query('SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?', (u1, u2), fetch_one=True)
        
        if not chat:
            return jsonify([])
        
        messages = execute_query('''
            SELECT from_user, text, created_at
            FROM messages
            WHERE chat_id = ?
            ORDER BY created_at ASC
        ''', (chat['id'],), fetch_all=True)
        return jsonify(messages or [])
    except Exception as e:
        print(f"Get messages error: {e}")
        return jsonify([])

@app.route('/api/message', methods=['POST'])
def send_message():
    data = request.json
    from_user = data.get('from_user')
    to_user = data.get('to_user')
    text = data.get('text')
    
    try:
        u1, u2 = sorted([from_user, to_user])
        chat = execute_query('SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?', (u1, u2), fetch_one=True)
        
        if not chat:
            execute_query('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)', (u1, u2), commit=True)
            chat = execute_query('SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?', (u1, u2), fetch_one=True)
        
        execute_query('''
            INSERT INTO messages (from_user, chat_id, text) VALUES (?, ?, ?)
        ''', (from_user, chat['id'], text), commit=True)
        return jsonify({'success': True})
    except Exception as e:
        print(f"Send message error: {e}")
        return jsonify({'error': str(e)}), 400

@app.route('/api/upload-photo', methods=['POST'])
def upload_photo():
    data = request.json
    try:
        user_id = data['user_id']
        photo_base64 = data['photo_data']
        
        # ✅ Обновляем БД сразу (файл сохранится асинхронно)
        photo_url = f'/api/photo/{user_id}'
        execute_query('UPDATE users SET photo_url = ? WHERE id = ?', (photo_url, user_id), commit=True)
        
        # ✅ Сохраняем файл в отдельном потоке
        save_photo_async(user_id, photo_base64)
        
        return jsonify({'success': True, 'photo_url': photo_url})
    except Exception as e:
        print(f"Photo upload error: {e}")
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

def close_connection_pool():
    global connection_pool
    if connection_pool:
        connection_pool.closeall()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
