import asyncio
import os
import logging
import sqlite3
import threading
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [1912287053]

if not BOT_TOKEN:
    raise ValueError("Токен не найден!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Веб-сервер для Render ---
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is running!", 200

def run_web_server():
    web_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

# --- Бот ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- База данных SQLite ---
DB_PATH = "shop.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            phone TEXT,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            registered_at TEXT
        )
    ''')
    
    # Таблица товаров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            photo TEXT
        )
    ''')
    
    # Таблица корзин
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    
    # Таблица заказов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            items TEXT NOT NULL
        )
    ''')
    
    # Таблица избранного
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    
    # Таблица новостей (с типом: news или product)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            publish_at TEXT,
            photo TEXT,
            news_type TEXT DEFAULT 'news',
            product_id INTEGER,
            is_published INTEGER DEFAULT 0
        )
    ''')
    
    # Добавляем тестовые товары
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        test_products = [
            ("Кроссовки Nike Air", 8900, "Спортивные кроссовки, размер 40-45", None),
            ("Футболка Adidas", 2500, "Хлопковая футболка, размеры S-XXL", None),
            ("Кепка New Era", 1800, "Бейсболка, регулируемая", None),
            ("Рюкзак Puma", 4200, "Вместительный рюкзак для города", None),
        ]
        cursor.executemany(
            "INSERT INTO products (name, price, description, photo) VALUES (?, ?, ?, ?)",
            test_products
        )
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Функции новостей ---
def get_published_news(limit: int = 20):
    """Получить опубликованные новости (включая товары-новости)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute('''
        SELECT id, title, content, created_at, photo, news_type, product_id
        FROM news 
        WHERE is_published = 1 AND (publish_at IS NULL OR publish_at <= ?)
        ORDER BY id DESC LIMIT ?
    ''', (now, limit))
    news = cursor.fetchall()
    conn.close()
    result = []
    for n in news:
        item = {
            'id': n[0], 
            'title': n[1], 
            'content': n[2], 
            'created_at': n[3], 
            'photo': n[4],
            'news_type': n[5],
            'product_id': n[6]
        }
        # Если это товар-новость, добавляем информацию о товаре
        if n[5] == 'product' and n[6]:
            product = get_product(n[6])
            if product:
                item['product'] = product
        result.append(item)
    return result

def get_all_news(limit: int = 30):
    """Получить все новости (для админа)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, title, content, created_at, publish_at, photo, news_type, product_id, is_published 
        FROM news ORDER BY id DESC LIMIT ?
    ''', (limit,))
    news = cursor.fetchall()
    conn.close()
    return [{
        'id': n[0], 'title': n[1], 'content': n[2], 'created_at': n[3], 
        'publish_at': n[4], 'photo': n[5], 'news_type': n[6], 
        'product_id': n[7], 'is_published': n[8]
    } for n in news]

def add_news(title: str, content: str, publish_in_hours: int = None, photo: str = None, news_type: str = 'news', product_id: int = None):
    """Добавить новость (обычную или товар) с отложенной публикацией"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    publish_at = None
    if publish_in_hours:
        publish_at = (datetime.now() + timedelta(hours=publish_in_hours)).strftime("%d.%m.%Y %H:%M")
    cursor.execute(
        "INSERT INTO news (title, content, created_at, publish_at, photo, news_type, product_id, is_published) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (title, content, created_at, publish_at, photo, news_type, product_id)
    )
    conn.commit()
    news_id = cursor.lastrowid
    conn.close()
    return news_id

def publish_news(news_id: int):
    """Опубликовать новость"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE news SET is_published = 1 WHERE id = ?", (news_id,))
    conn.commit()
    conn.close()

def delete_news(news_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM news WHERE id = ?", (news_id,))
    conn.commit()
    conn.close()

def publish_scheduled_news():
    """Публикует запланированные новости (вызывать периодически)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute('''
        UPDATE news SET is_published = 1 
        WHERE is_published = 0 AND publish_at IS NOT NULL AND publish_at <= ?
    ''', (now,))
    conn.commit()
    conn.close()

# --- Функции пользователей ---
def register_user(user_id: int, phone: str = None, first_name: str = None, last_name: str = None, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()
    if existing:
        if phone:
            cursor.execute("UPDATE users SET phone = ? WHERE user_id = ?", (phone, user_id))
    else:
        cursor.execute('''
            INSERT INTO users (user_id, phone, first_name, last_name, username, registered_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, phone, first_name, last_name, username, datetime.now().strftime("%d.%m.%Y %H:%M")))
    conn.commit()
    conn.close()

def get_user_phone(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT phone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def is_user_registered(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- Функции товаров ---
def get_products():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, description, photo FROM products")
    products = cursor.fetchall()
    conn.close()
    return [{'id': p[0], 'name': p[1], 'price': p[2], 'description': p[3], 'photo': p[4]} for p in products]

def get_product(product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, description, photo FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    if product:
        return {'id': product[0], 'name': product[1], 'price': product[2], 'description': product[3], 'photo': product[4]}
    return None

def add_product(name, price, description, photo=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO products (name, price, description, photo) VALUES (?, ?, ?, ?)",
        (name, price, description, photo)
    )
    conn.commit()
    product_id = cursor.lastrowid
    conn.close()
    return product_id

def delete_product(product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    cursor.execute("DELETE FROM carts WHERE product_id = ?", (product_id,))
    cursor.execute("DELETE FROM wishlist WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()

def get_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.product_id, p.name, p.price, c.quantity, p.photo
        FROM carts c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = ?
    ''', (user_id,))
    cart = cursor.fetchall()
    conn.close()
    return [{'product_id': c[0], 'name': c[1], 'price': c[2], 'quantity': c[3], 'photo': c[4]} for c in cart]

def add_to_cart(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO carts (user_id, product_id, quantity) 
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, product_id) 
        DO UPDATE SET quantity = quantity + 1
    ''', (user_id, product_id))
    conn.commit()
    conn.close()

def update_cart_quantity(user_id, product_id, quantity):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if quantity <= 0:
        cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    else:
        cursor.execute("UPDATE carts SET quantity = ? WHERE user_id = ? AND product_id = ?", (quantity, user_id, product_id))
    conn.commit()
    conn.close()

def remove_from_cart(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def clear_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def create_order(user_id, cart_items, total):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    items_json = json.dumps(cart_items)
    order_date = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute('''
        INSERT INTO orders (user_id, order_date, total, status, items)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, order_date, total, "pending", items_json))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, order_date, total, status FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (user_id,)
    )
    orders = cursor.fetchall()
    conn.close()
    return [{'id': o[0], 'order_date': o[1], 'total': o[2], 'status': o[3]} for o in orders]

def get_all_orders():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, order_date, total, status FROM orders ORDER BY id DESC LIMIT 20"
    )
    orders = cursor.fetchall()
    conn.close()
    return [{'id': o[0], 'user_id': o[1], 'order_date': o[2], 'total': o[3], 'status': o[4]} for o in orders]

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders")
    users = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM orders")
    orders_count = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(total) FROM orders")
    revenue = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM products")
    products_count = cursor.fetchone()[0] or 0
    conn.close()
    return users, orders_count, revenue, products_count

def add_to_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO wishlist (user_id, product_id) VALUES (?, ?)", (user_id, product_id))
    conn.commit()
    conn.close()

def remove_from_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM wishlist WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def get_wishlist(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.id, p.name, p.price, p.description, p.photo
        FROM wishlist w
        JOIN products p ON w.product_id = p.id
        WHERE w.user_id = ?
    ''', (user_id,))
    wishlist = cursor.fetchall()
    conn.close()
    return [{'id': w[0], 'name': w[1], 'price': w[2], 'description': w[3], 'photo': w[4]} for w in wishlist]

def is_in_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM wishlist WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- Клавиатуры ---
def get_phone_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

def remove_keyboard():
    return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)

def main_menu_keyboard(is_admin=False):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"), InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"), InlineKeyboardButton(text="❤️ Избранное", callback_data="wishlist")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="📰 Новости", callback_data="news")]
    ])
    if is_admin:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")])
    return keyboard

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")],
        [InlineKeyboardButton(text="📋 Товары", callback_data="admin_products"), InlineKeyboardButton(text="📦 Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton(text="🔄 Статусы заказов", callback_data="admin_update_status"), InlineKeyboardButton(text="📰 Управление новостями", callback_data="admin_news")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

def admin_news_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Обычная новость", callback_data="admin_add_news"), InlineKeyboardButton(text="🛍 Товар-новость", callback_data="admin_add_product_news")],
        [InlineKeyboardButton(text="📋 Список новостей", callback_data="admin_list_news")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ])

def news_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="news")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def product_news_keyboard(product_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}")],
        [InlineKeyboardButton(text="🔙 К новостям", callback_data="news"), InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def cart_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def order_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_order"), InlineKeyboardButton(text="❌ Нет", callback_data="cart")]
    ])

def update_status_keyboard(order_id, current_status):
    statuses = [("🟡 Ожидает", "pending"), ("🟢 В пути", "shipping"), ("✅ Доставлен", "delivered"), ("❌ Отменён", "cancelled")]
    buttons = []
    for name, code in statuses:
        if code != current_status:
            buttons.append([InlineKeyboardButton(text=name, callback_data=f"set_status_{order_id}_{code}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_orders")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Карусель товаров ---
async def send_product_carousel(message: types.Message, products, start_index=0):
    if not products:
        await message.answer("📭 Каталог пуст")
        return
    
    total = len(products)
    current = start_index % total
    product = products[current]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️", callback_data=f"carousel_prev_{current}"),
            InlineKeyboardButton(text=f"{current+1}/{total}", callback_data="carousel_info"),
            InlineKeyboardButton(text="▶️", callback_data=f"carousel_next_{current}")
        ],
        [
            InlineKeyboardButton(text="🛒 В корзину", callback_data=f"carousel_add_{product['id']}_{current}"),
            InlineKeyboardButton(text="❤️ В избранное", callback_data=f"carousel_wishlist_{product['id']}_{current}")
        ],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main")]
    ])
    
    text = f"<b>{product['name']}</b>\n\n💰 Цена: {product['price']} ₽\n📝 {product['description']}"
    
    if product['photo']:
        await message.answer_photo(photo=product['photo'], caption=text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)

# --- Обработчики ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    if not is_user_registered(user_id):
        register_user(
            user_id=user_id,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username
        )
        
        await message.answer(
            "🛍 <b>Добро пожаловать в магазин!</b>\n\n"
            "Для оформления заказов нам нужен ваш номер телефона.\n"
            "Нажмите на кнопку ниже, чтобы поделиться номером.",
            reply_markup=get_phone_keyboard(),
            parse_mode="HTML"
        )
        return
    
    phone = get_user_phone(user_id)
    text = "🛍 <b>Добро пожаловать в магазин!</b>\n\n"
    if phone:
        text += f"📱 Ваш номер: {phone}\n\n"
    text += "• 📋 Каталог\n• 🛒 Корзина\n• 📦 Мои заказы\n• ❤️ Избранное\n• 👤 Профиль\n• 📰 Новости"
    
    if is_admin:
        text += "\n\n🔐 <b>Вы вошли как администратор</b>"
    
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")

@dp.message(lambda msg: msg.contact is not None)
async def handle_contact(message: types.Message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    
    register_user(
        user_id=user_id,
        phone=phone,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        username=message.from_user.username
    )
    
    is_admin = user_id in ADMIN_IDS
    
    await message.answer(
        f"✅ <b>Номер телефона сохранён!</b>\n\n📱 Ваш номер: {phone}\n\nТеперь вы можете оформлять заказы.",
        reply_markup=remove_keyboard(),
        parse_mode="HTML"
    )
    
    text = "🛍 <b>Добро пожаловать в магазин!</b>\n\n"
    text += "• 📋 Каталог\n• 🛒 Корзина\n• 📦 Мои заказы\n• ❤️ Избранное\n• 👤 Профиль\n• 📰 Новости"
    
    if is_admin:
        text += "\n\n🔐 <b>Вы вошли как администратор</b>"
    
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")

@dp.message(Command("catalog"))
async def catalog_command(message: types.Message):
    products = get_products()
    if not products:
        await message.answer("📭 Каталог пуст")
        return
    await send_product_carousel(message, products, 0)

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    is_admin = user_id in ADMIN_IDS
    await callback.answer()
    
    # --- Новости ---
    if data == "news":
        news_list = get_published_news()
        if not news_list:
            await callback.message.edit_text("📰 <b>Новостей пока нет</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
            return
        
        # Отправляем первую новость
        await send_news_message(callback.message, news_list, 0)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data.startswith("news_"):
        parts = data.split("_")
        news_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else None
        
        if action == "prev":
            current = int(parts[3]) if len(parts) > 3 else 0
            news_list = get_published_news()
            new_index = (current - 1) % len(news_list)
            await send_news_message(callback.message, news_list, new_index)
            try:
                await callback.message.delete()
            except:
                pass
        elif action == "next":
            current = int(parts[3]) if len(parts) > 3 else 0
            news_list = get_published_news()
            new_index = (current + 1) % len(news_list)
            await send_news_message(callback.message, news_list, new_index)
            try:
                await callback.message.delete()
            except:
                pass
        return
    
    # --- Карусель ---
    if data == "catalog":
        products = get_products()
        if not products:
            await callback.message.edit_text("📭 Каталог пуст")
            return
        await send_product_carousel(callback.message, products, 0)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data.startswith("carousel_prev_"):
        current = int(data.split("_")[2])
        products = get_products()
        new_index = (current - 1) % len(products) if len(products) > 0 else 0
        await send_product_carousel(callback.message, products, new_index)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data.startswith("carousel_next_"):
        current = int(data.split("_")[2])
        products = get_products()
        new_index = (current + 1) % len(products) if len(products) > 0 else 0
        await send_product_carousel(callback.message, products, new_index)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data.startswith("carousel_add_"):
        parts = data.split("_")
        product_id = int(parts[2])
        current = int(parts[3])
        add_to_cart(user_id, product_id)
        products = get_products()
        await callback.message.answer("✅ Товар добавлен в корзину!")
        await send_product_carousel(callback.message, products, current)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data.startswith("carousel_wishlist_"):
        parts = data.split("_")
        product_id = int(parts[2])
        current = int(parts[3])
        if is_in_wishlist(user_id, product_id):
            remove_from_wishlist(user_id, product_id)
            await callback.message.answer("🤍 Удалено из избранного")
        else:
            add_to_wishlist(user_id, product_id)
            await callback.message.answer("❤️ Добавлено в избранное")
        products = get_products()
        await send_product_carousel(callback.message, products, current)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    # --- Добавление в корзину из новостей ---
    if data.startswith("add_to_cart_"):
        product_id = int(data.split("_")[3])
        add_to_cart(user_id, product_id)
        await callback.message.answer("✅ Товар добавлен в корзину!")
        return
    
    # --- Навигация ---
    if data == "back_to_main":
        phone = get_user_phone(user_id)
        text = "🛍 <b>Главное меню</b>\n\n"
        if phone:
            text += f"📱 Ваш номер: {phone}\n"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
    
    elif data == "admin_panel" and is_admin:
        await callback.message.edit_text("🔐 <b>Панель администратора</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    
    elif data == "admin_news" and is_admin:
        await callback.message.edit_text("📰 <b>Управление новостями</b>", reply_markup=admin_news_keyboard(), parse_mode="HTML")
    
    # --- Добавление обычной новости ---
    elif data == "admin_add_news" and is_admin:
        await callback.message.edit_text(
            "📝 <b>Добавление обычной новости</b>\n\n"
            "Отправьте данные в формате:\n"
            "<code>Заголовок | Текст</code>\n\n"
            "Пример:\n"
            "<code>Акция! | Скидка 20% на всю обувь до конца недели!</code>\n\n"
            "📸 Чтобы добавить фото, отправьте фото после текста.\n"
            "⏰ Чтобы отложить публикацию, добавьте | часы (например: | 2)\n\n"
            "<code>Акция! | Скидка 20%! | 3</code> — опубликуется через 3 часа",
            parse_mode="HTML"
        )
        dp.awaiting_news = getattr(dp, "awaiting_news", {})
        dp.awaiting_news[user_id] = {"type": "news"}
    
    # --- Добавление товара-новости ---
    elif data == "admin_add_product_news" and is_admin:
        products = get_products()
        if not products:
            await callback.message.edit_text("❌ Сначала добавьте товары в каталог!", reply_markup=admin_news_keyboard(), parse_mode="HTML")
            return
        
        text = "🛍 <b>Выберите товар для новости</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for p in products:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"{p['name']} - {p['price']} ₽", callback_data=f"select_product_for_news_{p['id']}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_news")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("select_product_for_news_") and is_admin:
        product_id = int(data.split("_")[4])
        dp.selected_product_for_news = getattr(dp, "selected_product_for_news", {})
        dp.selected_product_for_news[user_id] = product_id
        await callback.message.edit_text(
            "📝 <b>Добавление товара-новости</b>\n\n"
            "Отправьте текст новости:\n"
            "Пример:\n"
            "<code>🔥 Новое поступление! Успейте купить!</code>\n\n"
            "📸 Чтобы добавить фото, отправьте фото после текста.\n"
            "⏰ Чтобы отложить публикацию, добавьте | часы\n\n"
            "<code>Новинка! | 2</code> — опубликуется через 2 часа",
            parse_mode="HTML"
        )
        dp.awaiting_news = getattr(dp, "awaiting_news", {})
        dp.awaiting_news[user_id] = {"type": "product_news", "product_id": product_id}
    
    # --- Список новостей для админа ---
    elif data == "admin_list_news" and is_admin:
        news_list = get_all_news()
        if not news_list:
            await callback.message.edit_text("📰 <b>Нет новостей</b>", reply_markup=admin_news_keyboard(), parse_mode="HTML")
            return
        
        text = "📰 <b>Все новости</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for n in news_list:
            status = "✅" if n['is_published'] else "⏳"
            type_emoji = "🛍" if n['news_type'] == 'product' else "📰"
            text += f"{status} {type_emoji} {n['title'][:30]}...\n"
            text += f"📅 {n['created_at']}"
            if n['publish_at'] and not n['is_published']:
                text += f" (до {n['publish_at']})"
            text += "\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"❌ Удалить #{n['id']}", callback_data=f"admin_delete_news_{n['id']}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_news")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("admin_delete_news_") and is_admin:
        news_id = int(data.split("_")[3])
        delete_news(news_id)
        await callback.message.answer(f"✅ Новость #{news_id} удалена!")
    
    # --- Корзина ---
    elif data == "cart":
        cart = get_cart(user_id)
        if not cart:
            await callback.message.edit_text("🛒 <b>Корзина пуста</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
            return
        total = 0
        text = "🛒 <b>Ваша корзина</b>\n\n"
        for item in cart:
            subtotal = item['price'] * item['quantity']
            total += subtotal
            text += f"• {item['name']} x{item['quantity']} = {subtotal} ₽\n"
        text += f"\n<b>Итого: {total} ₽</b>"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for item in cart:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"➖", callback_data=f"cart_decr_{item['product_id']}"),
                InlineKeyboardButton(text=f"{item['name']}", callback_data="pass"),
                InlineKeyboardButton(text=f"➕", callback_data=f"cart_incr_{item['product_id']}")
            ])
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"❌ Удалить", callback_data=f"remove_from_cart_{item['product_id']}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("cart_incr_"):
        product_id = int(data.split("_")[2])
        add_to_cart(user_id, product_id)
        await update_cart_message(callback, user_id, is_admin)
    
    elif data.startswith("cart_decr_"):
        product_id = int(data.split("_")[2])
        cart = get_cart(user_id)
        for item in cart:
            if item['product_id'] == product_id:
                new_qty = item['quantity'] - 1
                update_cart_quantity(user_id, product_id, new_qty)
                break
        await update_cart_message(callback, user_id, is_admin)
    
    elif data.startswith("remove_from_cart_"):
        product_id = int(data.split("_")[3])
        remove_from_cart(user_id, product_id)
        await update_cart_message(callback, user_id, is_admin)
    
    elif data == "clear_cart":
        clear_cart(user_id)
        await callback.message.edit_text("🛒 <b>Корзина очищена</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
    
    elif data == "checkout":
        phone = get_user_phone(user_id)
        if not phone:
            await callback.message.answer(
                "❌ <b>Для оформления заказа нужен номер телефона!</b>\n\n"
                "Пожалуйста, отправьте команду /start и поделитесь номером.",
                parse_mode="HTML"
            )
            return
        
        cart = get_cart(user_id)
        if not cart:
            await callback.message.answer("🛒 Корзина пуста")
            return
        
        total = 0
        items_text = ""
        for item in cart:
            subtotal = item['price'] * item['quantity']
            total += subtotal
            items_text += f"• {item['name']} x{item['quantity']} = {subtotal} ₽\n"
        
        text = f"📦 <b>Подтверждение заказа</b>\n\n{items_text}\n<b>Итого: {total} ₽</b>\n\n📱 Номер для связи: {phone}"
        await callback.message.edit_text(text, reply_markup=order_confirmation_keyboard(), parse_mode="HTML")
    
    elif data == "confirm_order":
        cart = get_cart(user_id)
        if not cart:
            await callback.message.answer("❌ Корзина пуста")
            return
        
        total = 0
        items = []
        for item in cart:
            subtotal = item['price'] * item['quantity']
            total += subtotal
            items.append({"product_id": item['product_id'], "name": item['name'], "price": item['price'], "quantity": item['quantity'], "subtotal": subtotal})
        
        order_id = create_order(user_id, items, total)
        clear_cart(user_id)
        
        text = f"✅ <b>Заказ #{order_id} оформлен!</b>\n\n💰 Сумма: {total} ₽\nСтатус можно отслеживать в «Мои заказы»"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"🆕 <b>Новый заказ #{order_id}!</b>\nПользователь: {callback.from_user.id}\nСумма: {total} ₽", parse_mode="HTML")
            except:
                pass
    
    # --- Заказы пользователя ---
    elif data == "my_orders":
        orders = get_user_orders(user_id)
        if not orders:
            await callback.message.edit_text("📦 <b>У вас пока нет заказов</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
            return
        text = "📦 <b>Ваши заказы</b>\n\n"
        for order in orders:
            status_emoji = "🟡" if order['status'] == "pending" else ("🟢" if order['status'] == "shipping" else ("✅" if order['status'] == "delivered" else "❌"))
            text += f"{status_emoji} <b>Заказ #{order['id']}</b>\n📅 {order['order_date']}\n💰 {order['total']} ₽\n\n"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
    
    # --- Профиль ---
    elif data == "profile":
        orders = get_user_orders(user_id)
        total_spent = sum(o['total'] for o in orders)
        phone = get_user_phone(user_id) or "Не указан"
        text = f"👤 <b>Ваш профиль</b>\n\n🆔 ID: {user_id}\n📱 Телефон: {phone}\n📦 Заказов: {len(orders)}\n💰 Потрачено: {total_spent} ₽\n🛒 Товаров в корзине: {len(get_cart(user_id))}\n❤️ В избранном: {len(get_wishlist(user_id))}"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
    
    # --- Избранное ---
    elif data == "wishlist":
        wishlist = get_wishlist(user_id)
        if not wishlist:
            await callback.message.edit_text("❤️ <b>Избранное пусто</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
            return
        text = "❤️ <b>Ваше избранное</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for item in wishlist:
            text += f"• {item['name']} - {item['price']} ₽\n"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=f"📦 {item['name']}", callback_data=f"product_{item['id']}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    # --- Админ функции ---
    elif data == "admin_stats" and is_admin:
        users, orders_count, revenue, products_count = get_stats()
        text = f"📊 <b>Статистика</b>\n\n👥 Пользователей: {users}\n📦 Заказов: {orders_count}\n💰 Выручка: {revenue} ₽\n🛒 Товаров: {products_count}"
        await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    
    elif data == "admin_products" and is_admin:
        products = get_products()
        if not products:
            await callback.message.edit_text("📭 <b>Нет товаров</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
            return
        text = "📋 <b>Управление товарами</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for p in products:
            text += f"• {p['name']} - {p['price']} ₽\n"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Удалить {p['name']}", callback_data=f"admin_delete_product_{p['id']}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("admin_delete_product_") and is_admin:
        product_id = int(data.split("_")[3])
        product = get_product(product_id)
        if product:
            delete_product(product_id)
            await callback.message.answer(f"✅ Товар «{product['name']}» удалён")
        else:
            await callback.message.answer("❌ Товар не найден")
    
    elif data == "admin_orders" and is_admin:
        all_orders = get_all_orders()
        if not all_orders:
            await callback.message.edit_text("📦 <b>Нет заказов</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
            return
        text = "📦 <b>Все заказы</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for order in all_orders:
            status_emoji = "🟡" if order['status'] == "pending" else ("🟢" if order['status'] == "shipping" else ("✅" if order['status'] == "delivered" else "❌"))
            text += f"{status_emoji} <b>Заказ #{order['id']}</b>\n👤 Пользователь: {order['user_id']}\n📅 {order['order_date']}\n💰 {order['total']} ₽\n\n"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=f"🔄 Заказ #{order['id']}", callback_data=f"update_status_{order['id']}")])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("update_status_") and is_admin:
        order_id = int(data.split("_")[2])
        current_status = None
        for order in get_all_orders():
            if order['id'] == order_id:
                current_status = order['status']
                break
        if current_status:
            await callback.message.edit_text(f"🔄 <b>Заказ #{order_id}</b>\n\nТекущий статус: {current_status}\n\nВыберите новый статус:", reply_markup=update_status_keyboard(order_id, current_status), parse_mode="HTML")
    
    elif data.startswith("set_status_") and is_admin:
        parts = data.split("_")
        order_id = int(parts[2])
        new_status = parts[3]
        update_order_status(order_id, new_status)
        status_names = {"pending": "🟡 Ожидает", "shipping": "🟢 В пути", "delivered": "✅ Доставлен", "cancelled": "❌ Отменён"}
        await callback.message.answer(f"✅ Статус заказа #{order_id} изменён на {status_names.get(new_status, new_status)}")
        order_info = None
        for order in get_all_orders():
            if order['id'] == order_id:
                order_info = order
                break
        if order_info:
            try:
                await bot.send_message(order_info['user_id'], f"🔄 Статус вашего заказа #{order_id} изменён на {status_names.get(new_status, new_status)}")
            except:
                pass
    
    elif data == "admin_add_product" and is_admin:
        await callback.message.edit_text(
            "📝 <b>Добавление товара</b>\n\nОтправьте данные в формате:\n<code>Название | Цена | Описание</code>\n\nПример:\n<code>Кроссовки Nike | 8900 | Спортивные кроссовки</code>",
            parse_mode="HTML"
        )
        dp.awaiting_product = getattr(dp, "awaiting_product", set())
        dp.awaiting_product.add(user_id)
    
    elif data == "admin_update_status" and is_admin:
        await callback.message.edit_text(
            "🔄 <b>Изменение статуса заказа</b>\n\nВведите номер заказа:",
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML"
        )
        dp.waiting_for_order_id = getattr(dp, "waiting_for_order_id", set())
        dp.waiting_for_order_id.add(user_id)

async def update_cart_message(callback: CallbackQuery, user_id: int, is_admin: bool):
    cart = get_cart(user_id)
    if not cart:
        await callback.message.edit_text("🛒 <b>Корзина пуста</b>", reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
        return
    total = 0
    text = "🛒 <b>Ваша корзина</b>\n\n"
    for item in cart:
        subtotal = item['price'] * item['quantity']
        total += subtotal
        text += f"• {item['name']} x{item['quantity']} = {subtotal} ₽\n"
    text += f"\n<b>Итого: {total} ₽</b>"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for item in cart:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"➖", callback_data=f"cart_decr_{item['product_id']}"),
            InlineKeyboardButton(text=f"{item['name']}", callback_data="pass"),
            InlineKeyboardButton(text=f"➕", callback_data=f"cart_incr_{item['product_id']}")
        ])
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"❌ Удалить", callback_data=f"remove_from_cart_{item['product_id']}")
        ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

async def send_news_message(message: types.Message, news_list, index):
    """Отправляет одну новость из списка"""
    if not news_list or index >= len(news_list):
        await message.answer("📰 Новостей пока нет")
        return
    
    news = news_list[index]
    total = len(news_list)
    
    # Формируем текст
    text = f"📰 <b>{news['title']}</b>\n\n{news['content']}\n\n📅 {news['created_at']}"
    
    # Клавиатура для навигации
    nav_buttons = []
    if total > 1:
        nav_buttons = [
            InlineKeyboardButton(text="◀️", callback_data=f"news_{news['id']}_prev_{index}"),
            InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="news_info"),
            InlineKeyboardButton(text="▶️", callback_data=f"news_{news['id']}_next_{index}")
        ]
    
    # Если это товар-новость, добавляем кнопку "В корзину"
    if news.get('news_type') == 'product' and news.get('product'):
        product = news['product']
        keyboard_buttons = [nav_buttons] if nav_buttons else []
        keyboard_buttons.append([InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product['id']}")])
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    else:
        keyboard_buttons = [nav_buttons] if nav_buttons else []
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    if news.get('photo'):
        await message.answer_photo(photo=news['photo'], caption=text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)

@dp.message()
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    # Добавление товара
    if hasattr(dp, "awaiting_product") and user_id in dp.awaiting_product:
        dp.awaiting_product.remove(user_id)
        try:
            parts = message.text.split("|")
            if len(parts) >= 3:
                name = parts[0].strip()
                price = int(parts[1].strip())
                desc = parts[2].strip()
                product_id = add_product(name, price, desc)
                await message.answer(f"✅ Товар «{name}» добавлен! ID: {product_id}")
            else:
                await message.answer("❌ Неверный формат. Используйте: <code>Название | Цена | Описание</code>", parse_mode="HTML")
        except ValueError:
            await message.answer("❌ Цена должна быть числом")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
        return
    
    # Добавление новости
    if hasattr(dp, "awaiting_news") and user_id in dp.awaiting_news:
        news_data = dp.awaiting_news[user_id]
        text = message.text.strip()
        
        # Парсим часы отложенной публикации
        publish_in_hours = None
        if " | " in text:
            parts = text.split(" | ")
            text = parts[0].strip()
            if len(parts) > 1 and parts[1].isdigit():
                publish_in_hours = int(parts[1])
        
        if news_data["type"] == "news":
            # Обычная новость: ожидаем "Заголовок | Текст"
            if " | " in text:
                parts = text.split(" | ", 1)
                title = parts[0].strip()
                content = parts[1].strip()
                add_news(title=title, content=content, publish_in_hours=publish_in_hours)
                await message.answer(f"✅ Новость «{title}» добавлена!{' Опубликуется через ' + str(publish_in_hours) + ' ч.' if publish_in_hours else ''}")
            else:
                await message.answer("❌ Неверный формат. Используйте: <code>Заголовок | Текст</code>", parse_mode="HTML")
        else:
            # Товар-новость
            product_id = news_data["product_id"]
            product = get_product(product_id)
            if product:
                title = f"🛍 {product['name']}"
                content = text
                add_news(title=title, content=content, publish_in_hours=publish_in_hours, news_type='product', product_id=product_id)
                await message.answer(f"✅ Товар-новость «{title}» добавлена!{' Опубликуется через ' + str(publish_in_hours) + ' ч.' if publish_in_hours else ''}")
            else:
                await message.answer("❌ Товар не найден")
        
        # Предлагаем добавить фото
        await message.answer("📸 Хотите добавить фото к новости? Отправьте фото сейчас или нажмите /skip")
        dp.waiting_for_news_photo = getattr(dp, "waiting_for_news_photo", {})
        dp.waiting_for_news_photo[user_id] = {"news_id": None, "type": news_data["type"]}
        return
    
    # Добавление фото к новости
    if hasattr(dp, "waiting_for_news_photo") and user_id in dp.waiting_for_news_photo:
        if message.text == "/skip":
            del dp.waiting_for_news_photo[user_id]
            await message.answer("✅ Фото не добавлено", reply_markup=admin_panel_keyboard() if is_admin else main_menu_keyboard(is_admin))
            return
        
        if message.photo:
            photo = message.photo[-1]
            # Обновляем последнюю добавленную новость с фото
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE news SET photo = ? WHERE id = (SELECT MAX(id) FROM news)", (photo.file_id,))
            conn.commit()
            conn.close()
            del dp.waiting_for_news_photo[user_id]
            await message.answer("✅ Фото добавлено к новости!", reply_markup=admin_panel_keyboard() if is_admin else main_menu_keyboard(is_admin))
        return
    
    # Изменение статуса заказа
    if hasattr(dp, "waiting_for_order_id") and user_id in dp.waiting_for_order_id:
        dp.waiting_for_order_id.remove(user_id)
        try:
            order_id = int(message.text.strip())
            current_status = None
            for order in get_all_orders():
                if order['id'] == order_id:
                    current_status = order['status']
                    break
            if current_status:
                await message.answer(f"🔄 <b>Заказ #{order_id}</b>\n\nТекущий статус: {current_status}\n\nВыберите новый статус:", reply_markup=update_status_keyboard(order_id, current_status), parse_mode="HTML")
            else:
                await message.answer(f"❌ Заказ #{order_id} не найден")
        except ValueError:
            await message.answer("❌ Введите номер заказа (цифрами)")
        return

# --- Настройка меню команд ---
async def set_commands():
    commands = [
        types.BotCommand(command="start", description="🛍 Главное меню"),
        types.BotCommand(command="catalog", description="📋 Каталог товаров"),
    ]
    await bot.set_my_commands(commands)
    logger.info("✅ Меню команд установлено")

# --- Фоновая задача для публикации отложенных новостей ---
async def scheduler():
    """Проверяет каждую минуту, не пора ли опубликовать новости"""
    while True:
        try:
            publish_scheduled_news()
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
        await asyncio.sleep(60)  # Проверяем каждую минуту

async def main():
    init_db()
    await set_commands()
    # Запускаем фоновую задачу
    asyncio.create_task(scheduler())
    logger.info("🛍 БОТ-МАГАЗИН ЗАПУЩЕН с отложенными новостями")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
