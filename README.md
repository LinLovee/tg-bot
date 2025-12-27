# 💕 Dating Bot - Telegram WebApp

**Современный dating app для Telegram с iOS 18 glassmorphism дизайном**

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![Telegram](https://img.shields.io/badge/Telegram-WebApp-0088cc)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ Особенности

- 🎨 **iOS 18 Glassmorphism дизайн** - современный и красивый интерфейс
- 💬 **Реал-тайм совпадения** - взаимные лайки создают чаты
- 🔐 **Безопасная авторизация** - через Telegram ID
- 📱 **Полная мобильная оптимизация** - работает на всех устройствах
- 🚀 **Развёрнуто на Render** - 24/7 без перебоев
- 💾 **SQLite БД** - с поддержкой лайков и сообщений
- 🌍 **WebApp интеграция** - встроено прямо в Telegram

## 🏗️ Архитектура

```
┌─────────────────────────────┐
│   Telegram Bot (@BotFather) │
└──────────────┬──────────────┘
               │
        ┌──────▼──────┐
        │  WebApp URL │
        │  (Render)   │
        └──────┬──────┘
               │
        ┌──────▼────────────┐
        │   Flask Backend    │
        │  - User Profiles   │
        │  - Likes System    │
        │  - Matches Logic   │
        └──────┬────────────┘
               │
        ┌──────▼────────┐
        │  SQLite DB     │
        │  - users       │
        │  - likes       │
        │  - chats       │
        │  - messages    │
        └────────────────┘
```

## 🚀 Быстрый старт

### Локально

```bash
# 1. Клонируй репозиторий
git clone https://github.com/yourusername/dating-bot.git
cd dating-bot

# 2. Установи зависимости
pip install -r requirements.txt

# 3. Запусти
python app.py

# 4. Открой http://localhost:5000
```

### На Render (рекомендуется)

Читай **[DEPLOY.md](DEPLOY.md)** для подробной инструкции

## 📱 API Endpoints

| Метод | Endpoint | Описание |
|-------|----------|---------|
| GET | `/api/user/<id>` | Получить профиль |
| POST | `/api/user` | Создать профиль |
| GET | `/api/profiles/<id>` | Получить профили для поиска |
| POST | `/api/like` | Лайкнуть профиль |
| GET | `/api/matches/<id>` | Получить совпадения |
| GET | `/api/messages/<chat_id>` | Получить сообщения |
| POST | `/api/messages` | Отправить сообщение |

## 🎨 Кастомизация

### Изменить цвета

В `index.html`:
```css
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
```

Популярные палитры:
- **Ночь**: `#667eea` → `#764ba2` (текущая)
- **Закат**: `#ff6a88` → `#ff9a9e`
- **Лес**: `#134e5e` → `#71b280`
- **Фиолет**: `#667eea` → `#667eea`

### Добавить поля профиля

1. **Обнови БД** в `app.py`:
```python
c.execute('''
    ALTER TABLE users ADD COLUMN new_field TEXT
''')
```

2. **Добавь инпут** в `index.html`:
```html
<div class="form-group">
    <label for="user-new-field">Новое поле</label>
    <input type="text" id="user-new-field" placeholder="...">
</div>
```

3. **Обнови API** в `app.py`:
```python
@app.route('/api/user', methods=['POST'])
def create_user():
    # ... добавь new_field
```

## 📊 Структура проекта

```
tg-bot/
├── app.py                 # Flask бэкенд (API)
├── app_full.py            # Flask бэкенд + статика для Render
├── index.html             # Фронтенд WebApp (iOS 18 Glassmorphism)
├── requirements.txt       # Python зависимости
├── build.sh               # Скрипт сборки для Render
├── runtime.txt            # Версия Python
├── .env.example           # Пример переменных окружения
├── DEPLOY.md              # Инструкция по деплою
└── README.md              # Этот файл
```

## 🔐 Безопасность

- ✅ Валидация `initData` от Telegram
- ✅ Проверка hash от Telegram (HMAC-SHA256)
- ✅ Проверка возраста данных (max 1 час)
- ✅ CORS включён для безопасности
- ✅ BOT_TOKEN хранится в переменных окружения
- ❗ **Всегда валидируй данные на сервере, не доверяй клиентской информации**

## 📈 Масштабирование

### Для большего числа пользователей:

1. **Перейди на PostgreSQL** (вместо SQLite)
   ```bash
   pip install psycopg2-binary
   ```

2. **Добавь кэширование** (Redis)
   ```bash
   pip install redis
   ```

3. **Оптимизируй запросы** (индексы, пагинация)

4. **Добавь WebSocket** для реал-тайма

## 🐛 Решение проблем

### Telegram WebApp не грузится
```
❌ Проверь:
- URL в @BotFather (должен быть HTTPS)
- Логи на Render
- Консоль браузера (F12)
```

### БД не сохраняет данные
```
❌ Render удаляет файлы при рестарте
✅ Решение: используй PostgreSQL или реплику БД
```

### Error 502 Bad Gateway
```
❌ Проверь:
- pip freeze (все ли зависимости установлены)
- Start Command (gunicorn app:app)
- PORT переменную окружения
```

## 🎯 Функции для добавления

- [ ] Фото профилей (S3/Cloudinary)
- [ ] WebSocket для чатов
- [ ] Премиум подписка
- [ ] Telegram Stars платежи
- [ ] Свайп анимации
- [ ] Push-уведомления
- [ ] История лайков
- [ ] Блокировка пользователей
- [ ] Репорт спама
- [ ] Поиск по интересам

## 📞 Контакты

**Вопросы?** Напиши в Telegram: [@yourusername](https://t.me/yourusername)

## 📄 Лицензия

Код защищен авторским правом

Коммерческое использование запрещено

Форки должны быть удалены

---

**Создано с ❤️ для Telegram**

**Присоединяйся к сообществу молодежи, ищущей своих вторых половинок!**
