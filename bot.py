# 🚀 Улучшенная версия Telegram-бота магазина

## 📋 **Основные улучшения**

```python
import asyncio
import os
import logging
import sqlite3
import threading
import json
import csv
import io
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile
from aiogram.exceptions import TelegramBadRequest
from flask import Flask, send_file
import aiosqlite
from contextlib import asynccontextmanager
import hashlib
import re

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "1912287053").split(",")))

if not BOT_TOKEN:
    raise ValueError("Токен не найден!")

# Настройка логирования для Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# --- Веб-сервер для Render с оптимизацией ---
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is running!", 200

@web_app.route('/export/orders.csv')
def export_orders_csv():
    """Экспорт заказов в CSV"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, order_date, total, status, 
                   json_extract(items, '$[0].name') as first_item
            FROM orders 
            ORDER BY id DESC
        """)
        orders = cursor.fetchall()
        conn.close()
        
        # Создаем CSV в памяти
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'User ID', 'Date', 'Total', 'Status', 'First Item'])
        writer.writerows(orders)
        
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'orders_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        logger.error(f"Export error: {e}")
        return "Error generating export", 500

@web_app.route('/export/products.csv')
def export_products_csv():
    """Экспорт товаров в CSV"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, description FROM products ORDER BY id")
        products = cursor.fetchall()
        conn.close()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Name', 'Price', 'Description'])
        writer.writerows(products)
        
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'products_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        logger.error(f"Export error: {e}")
        return "Error generating export", 500

def run_web_server():
    """Запуск веб-сервера с оптимизацией для Render"""
    port = int(os.environ.get('PORT', 10000))
    web_app.run(
        host='0.0.0.0',
        port=port,
        threaded=True,
        debug=False  # Отключаем debug для продакшена
    )

# Запуск веб-сервера в отдельном потоке
if os.environ.get('RENDER', 'false').lower() == 'true':
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info(f"Web server started on port {os.environ.get('PORT', 10000)}")

# --- Бот ---
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# --- База данных SQLite с оптимизацией ---
DB_PATH = "shop.db"

def init_db():
    """Инициализация базы данных с оптимизациями"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Создание таблиц с индексами для производительности
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price >= 0),
            description TEXT,
            photo_id TEXT,
            photo_path TEXT,
            category TEXT DEFAULT 'other',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1 CHECK(quantity > 0),
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            total INTEGER NOT NULL CHECK(total >= 0),
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'shipping', 'delivered', 'cancelled')),
            items TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            file_path TEXT,
            is_main BOOLEAN DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    ''')
    
    # Создание индексов для ускорения запросов
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_carts_user_id ON carts(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wishlist_user_id ON wishlist(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)')
    
    # Проверка существования тестовых данных
    cursor.execute("SELECT COUNT(*) FROM products WHERE is_active = 1")
    if cursor.fetchone()[0] == 0:
        test_products = [
            ("Кроссовки Nike Air", 8900, "Спортивные кроссовки для бега", "footwear"),
            ("Футболка Adidas", 2500, "Хлопковая футболка с логотипом", "clothing"),
            ("Кепка New Era", 1800, "Бейсболка с регулируемым ремешком", "accessories"),
            ("Рюкзак Puma", 4200, "Вместительный рюкзак для спорта", "accessories"),
            ("Шорты спортивные", 3200, "Легкие шорты для тренировок", "clothing"),
            ("Бутылка для воды", 800, "Спортивная бутылка 750мл", "accessories"),
        ]
        cursor.executemany(
            "INSERT INTO products (name, price, description, category) VALUES (?, ?, ?, ?)",
            test_products
        )
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована с оптимизациями")

# --- Асинхронные функции для работы с БД ---
@asynccontextmanager
async def get_db_connection():
    """Асинхронный контекстный менеджер для подключения к БД"""
    conn = await aiosqlite.connect(DB_PATH)
    try:
        yield conn
    finally:
        await conn.close()

async def execute_query(query, params=()):
    """Выполнение SQL запроса с обработкой ошибок"""
    async with get_db_connection() as conn:
        try:
            await conn.execute(query, params)
            await conn.commit()
        except Exception as e:
            logger.error(f"Query error: {e}")
            raise

async def fetch_one(query, params=()):
    """Получение одной записи"""
    async with get_db_connection() as conn:
        cursor = await conn.execute(query, params)
        result = await cursor.fetchone()
        await cursor.close()
        return result

async def fetch_all(query, params=()):
    """Получение всех записей"""
    async with get_db_connection() as conn:
        cursor = await conn.execute(query, params)
        result = await cursor.fetchall()
        await cursor.close()
        return result

# --- Функции для работы с товарами ---
def get_products(category=None, page=1, limit=10):
    """Получение товаров с пагинацией и фильтрацией"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    offset = (page - 1) * limit
    query = "SELECT id, name, price, description, category FROM products WHERE is_active = 1"
    params = []
    
    if category and category != 'all':
        query += " AND category = ?"
        params.append(category)
    
    query += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    products = cursor.fetchall()
    
    # Получаем общее количество для пагинации
    count_query = "SELECT COUNT(*) FROM products WHERE is_active = 1"
    if category and category != 'all':
        count_query += " AND category = ?"
        cursor.execute(count_query, (category,))
    else:
        cursor.execute(count_query)
    
    total = cursor.fetchone()[0]
    conn.close()
    
    return {
        'products': products,
        'total': total,
        'page': page,
        'pages': (total + limit - 1) // limit
    }

def get_product(product_id):
    """Получение товара с фото"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.price, p.description, p.category, 
               pi.file_id, pi.file_path
        FROM products p
        LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.is_main = 1
        WHERE p.id = ? AND p.is_active = 1
    """, (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def add_product(name, price, description, category='other'):
    """Добавление товара с валидацией"""
    # Валидация данных
    if not name or len(name.strip()) < 2:
        raise ValueError("Название товара слишком короткое")
    if price < 0:
        raise ValueError("Цена не может быть отрицательной")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO products (name, price, description, category) VALUES (?, ?, ?, ?)",
        (name.strip(), price, description.strip(), category)
    )
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

def save_product_photo(product_id, file_id, file_path=None):
    """Сохранение фото товара"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверяем, есть ли уже главное фото
    cursor.execute("SELECT id FROM product_images WHERE product_id = ? AND is_main = 1", (product_id,))
    existing = cursor.fetchone()
    
    if existing:
        # Обновляем существующее фото
        cursor.execute(
            "UPDATE product_images SET file_id = ?, file_path = ? WHERE id = ?",
            (file_id, file_path, existing[0])
        )
    else:
        # Добавляем новое фото
        cursor.execute(
            "INSERT INTO product_images (product_id, file_id, file_path, is_main) VALUES (?, ?, ?, 1)",
            (product_id, file_id, file_path)
        )
    
    conn.commit()
    conn.close()

def delete_product(product_id):
    """Мягкое удаление товара"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()

# --- Функции для работы с корзиной ---
def get_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.product_id, p.name, p.price, c.quantity 
        FROM carts c 
        JOIN products p ON c.product_id = p.id 
        WHERE c.user_id = ? AND p.is_active = 1
        ORDER BY c.added_at DESC
    """, (user_id,))
    cart = cursor.fetchall()
    conn.close()
    return [{'product_id': c[0], 'name': c[1], 'price': c[2], 'quantity': c[3]} for c in cart]

def add_to_cart(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO carts (user_id, product_id, quantity) 
        VALUES (?, ?, 1) 
        ON CONFLICT(user_id, product_id) 
        DO UPDATE SET quantity = quantity + 1, added_at = CURRENT_TIMESTAMP
    """, (user_id, product_id))
    conn.commit()
    conn.close()

def update_cart_quantity(user_id, product_id, quantity):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if quantity <= 0:
        cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    else:
        cursor.execute(
            "UPDATE carts SET quantity = ? WHERE user_id = ? AND product_id = ?",
            (quantity, user_id, product_id)
        )
    conn.commit()
    conn.close()

def clear_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- Функции для работы с заказами ---
def create_order(user_id, cart_items, total):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    items_json = json.dumps(cart_items, ensure_ascii=False)
    order_date = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute(
        "INSERT INTO orders (user_id, order_date, total, items) VALUES (?, ?, ?, ?)",
        (user_id, order_date, total, items_json)
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, order_id)
    )
    conn.commit()
    conn.close()

def get_user_orders(user_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, order_date, total, status 
        FROM orders 
        WHERE user_id = ? 
        ORDER BY id DESC 
        LIMIT ?
    """, (user_id, limit))
    orders = cursor.fetchall()
    conn.close()
    return [{'id': o[0], 'order_date': o[1], 'total': o[2], 'status': o[3]} for o in orders]

def get_all_orders(limit=20):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, user_id, order_date, total, status 
        FROM orders 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    orders = cursor.fetchall()
    conn.close()
    return [{'id': o[0], 'user_id': o[1], 'order_date': o[2], 'total': o[3], 'status': o[4]} for o in orders]

def get_stats(days=30):
    """Получение статистики за последние N дней"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id) 
        FROM orders 
        WHERE date(substr(order_date, 7, 4) || '-' || 
                   substr(order_date, 4, 2) || '-' || 
                   substr(order_date, 1, 2)) >= ?
    """, (date_from,))
    users = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM orders")
    orders_count = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT SUM(total) FROM orders")
    revenue = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM products WHERE is_active = 1")
    products_count = cursor.fetchone()[0] or 0
    
    # Статистика по дням
    cursor.execute("""
        SELECT date(substr(order_date, 7, 4) || '-' || 
                    substr(order_date, 4, 2) || '-' || 
                    substr(order_date, 1, 2)) as day,
               COUNT(*) as count,
               SUM(total) as revenue
        FROM orders
        WHERE date(substr(order_date, 7, 4) || '-' || 
                   substr(order_date, 4, 2) || '-' || 
                   substr(order_date, 1, 2)) >= ?
        GROUP BY day
        ORDER BY day DESC
        LIMIT 7
    """, (date_from,))
    daily_stats = cursor.fetchall()
    
    conn.close()
    return {
        'users': users,
        'orders_count': orders_count,
        'revenue': revenue,
        'products_count': products_count,
        'daily_stats': daily_stats
    }

# --- Функции для избранного ---
def add_to_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO wishlist (user_id, product_id) VALUES (?, ?)",
        (user_id, product_id)
    )
    conn.commit()
    conn.close()

def remove_from_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM wishlist WHERE user_id = ? AND product_id = ?",
        (user_id, product_id)
    )
    conn.commit()
    conn.close()

def get_wishlist(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.price 
        FROM wishlist w 
        JOIN products p ON w.product_id = p.id 
        WHERE w.user_id = ? AND p.is_active = 1
        ORDER BY w.added_at DESC
    """, (user_id,))
    wishlist = cursor.fetchall()
    conn.close()
    return [{'id': w[0], 'name': w[1], 'price': w[2]} for w in wishlist]

def is_in_wishlist(user_id, product_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM wishlist WHERE user_id = ? AND product_id = ?",
        (user_id, product_id)
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- Клавиатуры ---
def main_menu_keyboard(is_admin=False):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Каталог", callback_data="catalog")],
        [InlineKeyboardButton(text="🛒 Корзина", callback_data="cart"), 
         InlineKeyboardButton(text="📦 Заказы", callback_data="my_orders")],
        [InlineKeyboardButton(text="❤️ Избранное", callback_data="wishlist"),
         InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
    ])
    if is_admin:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")])
    return keyboard

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product"),
         InlineKeyboardButton(text="📋 Товары", callback_data="admin_products")],
        [InlineKeyboardButton(text="📦 Заказы", callback_data="admin_orders"),
         InlineKeyboardButton(text="🔄 Статусы", callback_data="admin_update_status")],
        [InlineKeyboardButton(text="📤 Экспорт", callback_data="admin_export")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

def catalog_keyboard(category='all', page=1, total_pages=1):
    """Клавиатура каталога с пагинацией и категориями"""
    categories = [
        ("👟 Обувь", "footwear"),
        ("👕 Одежда", "clothing"),
        ("🎒 Аксессуары", "accessories"),
        ("📦 Все товары", "all")
    ]
    
    keyboard = []
    
    # Кнопки категорий
    row = []
    for name, cat in categories:
        if cat == category:
            row.append(InlineKeyboardButton(text=f"✅ {name}", callback_data=f"cat_{cat}_1"))
        else:
            row.append(InlineKeyboardButton(text=name, callback_data=f"cat_{cat}_1"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Кнопки пагинации
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat_{category}_{page-1}"))
    pagination.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"cat_{category}_{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def product_card_keyboard(product_id, user_id, in_cart=False, in_wishlist=False):
    buttons = []
    
    # Кнопки управления количеством
    if in_cart:
        buttons.append([
            InlineKeyboardButton(text="➖", callback_data=f"cart_decr_{product_id}"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"remove_from_cart_{product_id}"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_incr_{product_id}")
        ])
    else:
        buttons.append([InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}")])
    
    # Кнопка избранного
    if in_wishlist:
        buttons.append([InlineKeyboardButton(text="❤️ В избранном", callback_data=f"remove_wishlist_{product_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🤍 В избранное", callback_data=f"add_wishlist_{product_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад в каталог", callback_data="catalog")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cart_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def order_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data="confirm_order")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cart")]
    ])

def update_status_keyboard(order_id, current_status):
    statuses = [
        ("🟡 Ожидает", "pending"),
        ("🟢 В пути", "shipping"),
        ("✅ Доставлен", "delivered"),
        ("❌ Отменён", "cancelled")
    ]
    
    buttons = []
    for name, code in statuses:
        if code != current_status:
            buttons.append([InlineKeyboardButton(text=name, callback_data=f"set_status_{order_id}_{code}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад к заказам", callback_data="admin_orders")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def export_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Экспорт заказов (CSV)", callback_data="export_orders")],
        [InlineKeyboardButton(text="🛒 Экспорт товаров (CSV)", callback_data="export_products")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ])

# --- Настройка меню команд ---
async def set_commands():
    commands = [
        types.BotCommand(command="start", description="🛍 Главное меню"),
        types.BotCommand(command="catalog", description="📋 Каталог товаров"),
        types.BotCommand(command="cart", description="🛒 Моя корзина"),
        types.BotCommand(command="orders", description="📦 Мои заказы"),
        types.BotCommand(command="wishlist", description="❤️ Избранное"),
        types.BotCommand(command="profile", description="👤 Мой профиль"),
        types.BotCommand(command="help", description="❓ Помощь"),
    ]
    await bot.set_my_commands(commands)
    logger.info("✅ Меню команд установлено")

# --- Обработчики команд ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    welcome_text = (
        "🛍 <b>Добро пожаловать в наш магазин!</b>\n\n"
        "Здесь вы можете:\n"
        "• 📋 Просматривать каталог товаров\n"
        "• 🛒 Добавлять товары в корзину\n"
        "• 📦 Оформлять заказы\n"
        "• ❤️ Сохранять товары в избранное\n"
        "• 👤 Смотреть свой профиль\n\n"
        "Используйте кнопки ниже для навигации:"
    )
    
    if is_admin:
        welcome_text += "\n\n🔐 <i>Вы вошли как администратор</i>"
    
    await message.answer(welcome_text, reply_markup=main_menu_keyboard(is_admin))

@dp.message(Command("catalog"))
async def catalog_command(message: types.Message):
    await show_catalog(message, 'all', 1)

async def show_catalog(message, category='all', page=1):
    """Показать каталог с пагинацией"""
    data = get_products(category, page)
    
    if not data['products']:
        await message.answer("📭 В этой категории пока нет товаров")
        return
    
    text = f"📋 <b>Каталог товаров</b>\n"
    if category != 'all':
        category_names = {
            'footwear': '👟 Обувь',
            'clothing': '👕 Одежда',
            'accessories': '🎒 Аксессуары'
        }
        text += f"Категория: {category_names.get(category, category)}\n\n"
    
    for product in data['products']:
        text += f"• <b>{product[1]}</b> - {product[2]} ₽\n"
    
    if data['pages'] > 1:
        text += f"\nСтраница {page} из {data['pages']}"
    
    await message.answer(text, reply_markup=catalog_keyboard(category, page, data['pages']))

@dp.message(Command("cart"))
async def cart_command(message: types.Message):
    await show_user_cart(message)

async def show_user_cart(message: types.Message = None, callback: CallbackQuery = None):
    """Показать корзину пользователя"""
    user_id = message.from_user.id if message else callback.from_user.id
    cart = get_cart(user_id)
    is_admin = user_id in ADMIN_IDS
    
    if not cart:
        text = "🛒 <b>Ваша корзина пуста</b>\n\nДобавьте товары из каталога!"
        if callback:
            await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin))
        else:
            await message.answer(text, reply_markup=main_menu_keyboard(is_admin))
        return
    
    total = 0
    text = "🛒 <b>Ваша корзина</b>\n\n"
    
    for item in cart:
        subtotal = item['price'] * item['quantity']
        total += subtotal
        text += f"• <b>{item['name']}</b>\n  x{item['quantity']} = {subtotal} ₽\n"
    
    text += f"\n<b>Итого: {total} ₽</b>"
    
    if callback:
        await callback.message.edit_text(text, reply_markup=cart_keyboard())
    else:
        await message.answer(text, reply_markup=cart_keyboard())

@dp.message(Command("orders"))
async def orders_command(message: types.Message):
    user_id = message.from_user.id
    orders = get_user_orders(user_id)
    
    if not orders:
        await message.answer("📦 <b>У вас пока нет заказов</b>")
        return
    
    text = "📦 <b>Ваши заказы</b>\n\n"
    
    for order in orders:
        status_emoji = {
            "pending": "🟡",
            "shipping": "🟢",
            "delivered": "✅",
            "cancelled": "❌"
        }.get(order['status'], "❓")
        
        text += (
            f"{status_emoji} <b>Заказ #{order['id']}</b>\n"
            f"📅 {order['order_date']}\n"
            f"💰 {order['total']} ₽\n"
            f"Статус: {order['status']}\n\n"
        )
    
    await message.answer(text)

@dp.message(Command("wishlist"))
async def wishlist_command(message: types.Message):
    user_id = message.from_user.id
    wishlist = get_wishlist(user_id)
    
    if not wishlist:
        await message.answer("❤️ <b>Избранное пусто</b>\n\nДобавьте товары в избранное из каталога!")
        return
    
    text = "❤️ <b>Ваше избранное</b>\n\n"
    
    for item in wishlist:
        text += f"• <b>{item['name']}</b> - {item['price']} ₽\n"
    
    await message.answer(text)

@dp.message(Command("profile"))
async def profile_command(message: types.Message):
    user_id = message.from_user.id
    orders = get_user_orders(user_id)
    total_spent = sum(o['total'] for o in orders)
    cart_count = len(get_cart(user_id))
    wishlist_count = len(get_wishlist(user_id))
    
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📦 Заказов: {len(orders)}\n"
        f"💰 Потрачено: {total_spent} ₽\n"
        f"🛒 Товаров в корзине: {cart_count}\n"
        f"❤️ В избранном: {wishlist_count}"
    )
    
    await message.answer(text)

@dp.message(Command("help"))
async def help_command(message: types.Message):
    text = (
        "❓ <b>Помощь</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start - Главное меню\n"
        "/catalog - Каталог товаров\n"
        "/cart - Моя корзина\n"
        "/orders - История заказов\n"
        "/wishlist - Избранное\n"
        "/profile - Мой профиль\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Выберите товары в каталоге\n"
        "2. Добавьте в корзину\n"
        "3. Перейдите в корзину и оформите заказ\n"
        "4. Следите за статусом заказа\n\n"
        "Для связи с поддержкой: @support"
    )
    
    await message.answer(text)

# --- Основной обработчик callback-запросов ---
@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    is_admin = user_id in ADMIN_IDS
    
    try:
        await callback.answer()
        
                if data == "back_to_main":
            await callback.message.edit_text(
                "🛍 <b>Главное меню</b>\n\nВыберите действие:",
                reply_markup=main_menu_keyboard(is_admin)
            )
        
        elif data == "admin_panel" and is_admin:
            await callback.message.edit_text(
                "🔐 <b>Панель администратора</b>\n\nВыберите действие:",
                reply_markup=admin_panel_keyboard()
            )
        
        elif data == "catalog":
            await show_catalog(callback.message, 'all', 1)
        
        elif data.startswith("cat_"):
            # Обработка категорий и пагинации
            parts = data.split("_")
            category = parts[1]
            page = int(parts[2])
            await show_catalog(callback.message, category, page)
        
        elif data.startswith("product_"):
            product_id = int(data.split("_")[1])
            product = get_product(product_id)
            
            if not product:
                await callback.message.answer("❌ <b>Товар не найден</b>")
                return
            
            # Проверяем наличие в корзине и избранном
            cart = get_cart(user_id)
            in_cart = any(c['product_id'] == product_id for c in cart)
            in_wishlist = is_in_wishlist(user_id, product_id)
            
            text = (
                f"<b>{product[1]}</b>\n\n"
                f"💰 <b>Цена:</b> {product[2]} ₽\n"
                f"📝 <b>Описание:</b> {product[3]}\n"
                f"🏷 <b>Категория:</b> {product[4]}"
            )
            
            # Если есть фото, отправляем его
            if product[5]:  # file_id
                try:
                    await bot.send_photo(
                        chat_id=callback.message.chat.id,
                        photo=product[5],
                        caption=text,
                        reply_markup=product_card_keyboard(product_id, user_id, in_cart, in_wishlist)
                    )
                    await callback.message.delete()
                except Exception as e:
                    logger.error(f"Error sending photo: {e}")
                    await callback.message.edit_text(
                        text,
                        reply_markup=product_card_keyboard(product_id, user_id, in_cart, in_wishlist)
                    )
            else:
                await callback.message.edit_text(
                    text,
                    reply_markup=product_card_keyboard(product_id, user_id, in_cart, in_wishlist)
                )
        
        elif data.startswith("add_to_cart_"):
            product_id = int(data.split("_")[3])
            add_to_cart(user_id, product_id)
            await callback.message.answer("✅ <b>Товар добавлен в корзину!</b>")
        
        elif data.startswith("cart_incr_"):
            product_id = int(data.split("_")[2])
            add_to_cart(user_id, product_id)
            await show_user_cart(callback=callback)
        
        elif data.startswith("cart_decr_"):
            product_id = int(data.split("_")[2])
            cart = get_cart(user_id)
            for item in cart:
                if item['product_id'] == product_id:
                    new_qty = item['quantity'] - 1
                    update_cart_quantity(user_id, product_id, new_qty)
                    break
            await show_user_cart(callback=callback)
        
        elif data.startswith("remove_from_cart_"):
            product_id = int(data.split("_")[3])
            remove_from_cart(user_id, product_id)
            await callback.message.answer("❌ <b>Товар удалён из корзины!</b>")
            await show_user_cart(callback=callback)
        
        elif data == "cart":
            await show_user_cart(callback=callback)
        
        elif data == "clear_cart":
            clear_cart(user_id)
            await callback.message.edit_text(
                "🗑 <b>Корзина очищена</b>",
                reply_markup=main_menu_keyboard(is_admin)
            )
        
        elif data == "checkout":
            cart = get_cart(user_id)
            if not cart:
                await callback.message.answer("🛒 <b>Корзина пуста</b>")
                return
            
            total = 0
            items_text = ""
            for item in cart:
                subtotal = item['price'] * item['quantity']
                total += subtotal
                items_text += f"• {item['name']} x{item['quantity']} = {subtotal} ₽\n"
            
            text = (
                f"📦 <b>Подтверждение заказа</b>\n\n"
                f"{items_text}\n"
                f"<b>Итого: {total} ₽</b>\n\n"
                f"Для подтверждения нажмите кнопку ниже:"
            )
            
            await callback.message.edit_text(text, reply_markup=order_confirmation_keyboard())
        
        elif data == "confirm_order":
            cart = get_cart(user_id)
            if not cart:
                await callback.message.answer("❌ <b>Корзина пуста</b>")
                return
            
            total = 0
            items = []
            for item in cart:
                subtotal = item['price'] * item['quantity']
                total += subtotal
                items.append({
                    "product_id": item['product_id'],
                    "name": item['name'],
                    "price": item['price'],
                    "quantity": item['quantity'],
                    "subtotal": subtotal
                })
            
            order_id = create_order(user_id, items, total)
            clear_cart(user_id)
            
            await callback.message.edit_text(
                f"✅ <b>Заказ #{order_id} оформлен!</b>\n\n"
                f"💰 <b>Сумма:</b> {total} ₽\n"
                f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Статус заказа можно отслеживать в разделе 'Мои заказы'",
                reply_markup=main_menu_keyboard(is_admin)
            )
            
            # Уведомление администраторов
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 <b>Новый заказ #{order_id}!</b>\n"
                        f"👤 Пользователь: {user_id}\n"
                        f"💰 Сумма: {total} ₽\n"
                        f"📦 Товаров: {len(items)}"
                    )
                except Exception as e:
                    logger.error(f"Error notifying admin {admin_id}: {e}")
        
        elif data == "my_orders":
            orders = get_user_orders(user_id)
            if not orders:
                await callback.message.edit_text(
                    "📦 <b>У вас пока нет заказов</b>",
                    reply_markup=main_menu_keyboard(is_admin)
                )
                return
            
            text = "📦 <b>Ваши заказы</b>\n\n"
            for order in orders:
                status_emoji = {
                    "pending": "🟡",
                    "shipping": "🟢",
                    "delivered": "✅",
                    "cancelled": "❌"
                }.get(order['status'], "❓")
                
                text += (
                    f"{status_emoji} <b>Заказ #{order['id']}</b>\n"
                    f"📅 {order['order_date']}\n"
                    f"💰 {order['total']} ₽\n"
                    f"Статус: {order['status']}\n\n"
                )
            
            await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin))
        
        elif data == "profile":
            orders = get_user_orders(user_id)
            total_spent = sum(o['total'] for o in orders)
            cart_count = len(get_cart(user_id))
            wishlist_count = len(get_wishlist(user_id))
            
            text = (
                f"👤 <b>Ваш профиль</b>\n\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"📦 Заказов: {len(orders)}\n"
                f"💰 Потрачено: {total_spent} ₽\n"
                f"🛒 Товаров в корзине: {cart_count}\n"
                f"❤️ В избранном: {wishlist_count}"
            )
            
            await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin))
        
        elif data.startswith("add_wishlist_"):
            product_id = int(data.split("_")[2])
            add_to_wishlist(user_id, product_id)
            await callback.message.answer("❤️ <b>Товар добавлен в избранное!</b>")
        
        elif data.startswith("remove_wishlist_"):
            product_id = int(data.split("_")[2])
            remove_from_wishlist(user_id, product_id)
            await callback.message.answer("🤍 <b>Товар удалён из избранного!</b>")
        
        elif data == "wishlist":
            wishlist = get_wishlist(user_id)
            if not wishlist:
                await callback.message.edit_text(
                    "❤️ <b>Избранное пусто</b>\n\nДобавьте товары в избранное из каталога!",
                    reply_markup=main_menu_keyboard(is_admin)
                )
                return
            
            text = "❤️ <b>Ваше избранное</b>\n\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            
            for item in wishlist:
                text += f"• <b>{item['name']}</b> - {item['price']} ₽\n"
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text=f"📦 {item['name']}", callback_data=f"product_{item['id']}")
                ])
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif data == "admin_stats" and is_admin:
            stats = get_stats(30)
            
            text = (
                f"📊 <b>Статистика магазина (за 30 дней)</b>\n\n"
                f"👥 <b>Пользователей:</b> {stats['users']}\n"
                f"📦 <b>Заказов:</b> {stats['orders_count']}\n"
                f"💰 <b>Выручка:</b> {stats['revenue']} ₽\n"
                f"🛒 <b>Товаров:</b> {stats['products_count']}\n\n"
                f"<b>Продажи за последние 7 дней:</b>\n"
            )
            
            if stats['daily_stats']:
                for day in stats['daily_stats']:
                    text += f"📅 {day[0]}: {day[1]} заказ(ов) на {day[2] or 0} ₽\n"
            else:
                text += "Нет данных за последние 7 дней"
            
            await callback.message.edit_text(text, reply_markup=admin_panel_keyboard())
        
        elif data == "admin_products" and is_admin:
            products = get_products()['products']
            if not products:
                await callback.message.edit_text(
                    "📭 <b>Нет активных товаров</b>",
                    reply_markup=admin_panel_keyboard()
                )
                return
            
            text = "📋 <b>Управление товарами</b>\n\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            
            for p in products:
                text += f"• <b>{p[1]}</b> - {p[2]} ₽ ({p[4]})\n"
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=f"❌ Удалить {p[1][:15]}...",
                        callback_data=f"admin_delete_product_{p[0]}"
                    )
                ])
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif data.startswith("admin_delete_product_") and is_admin:
            product_id = int(data.split("_")[3])
            product = get_product(product_id)
            
            if product:
                delete_product(product_id)
                await callback.message.answer(f"✅ <b>Товар «{product[1]}» удалён</b>")
            else:
                await callback.message.answer("❌ <b>Товар не найден</b>")
        
        elif data == "admin_orders" and is_admin:
            all_orders = get_all_orders()
            if not all_orders:
                await callback.message.edit_text(
                    "📦 <b>Нет заказов</b>",
                    reply_markup=admin_panel_keyboard()
                )
                return
            
            text = "📦 <b>Все заказы</b>\n\n"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            
            for order in all_orders:
                status_emoji = {
                    "pending": "🟡",
                    "shipping": "🟢",
                    "delivered": "✅",
                    "cancelled": "❌"
                }.get(order['status'], "❓")
                
                text += (
                    f"{status_emoji} <b>Заказ #{order['id']}</b>\n"
                    f"👤 Пользователь: {order['user_id']}\n"
                    f"📅 {order['order_date']}\n"
                    f"💰 {order['total']} ₽\n\n"
                )
                
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=f"🔄 Заказ #{order['id']}",
                        callback_data=f"update_status_{order['id']}"
                    )
                ])
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif data.startswith("update_status_") and is_admin:
            order_id = int(data.split("_")[2])
            current_status = None
            
            for order in get_all_orders():
                if order['id'] == order_id:
                    current_status = order['status']
                    break
            
            if current_status:
                await callback.message.edit_text(
                    f"🔄 <b>Заказ #{order_id}</b>\n\n"
                    f"Текущий статус: <b>{current_status}</b>\n\n"
                    f"Выберите новый статус:",
                    reply_markup=update_status_keyboard(order_id, current_status)
                )
        
        elif data.startswith("set_status_") and is_admin:
            parts = data.split("_")
            order_id = int(parts[2])
            new_status = parts[3]
            
            update_order_status(order_id, new_status)
            
            status_names = {
                "pending": "🟡 Ожидает",
                "shipping": "🟢 В пути",
                "delivered": "✅ Доставлен",
                "cancelled": "❌ Отменён"
            }
            
            await callback.message.answer(
                f"✅ <b>Статус заказа #{order_id} изменён на {status_names.get(new_status, new_status)}</b>"
            )
            
            # Уведомление пользователя
            order_info = None
            for order in get_all_orders():
                if order['id'] == order_id:
                    order_info = order
                    break
            
            if order_info:
                try:
                    await bot.send_message(
                        order_info['user_id'],
                        f"🔄 <b>Статус вашего заказа #{order_id} изменён</b>\n\n"
                        f"Новый статус: {status_names.get(new_status, new_status)}"
                    )
                except Exception as e:
                    logger.error(f"Error notifying user {order_info['user_id']}: {e}")
        
        elif data == "admin_add_product" and is_admin:
            await callback.message.edit_text(
                "📝 <b>Добавление товара</b>\n\n"
                "Отправьте данные в формате:\n"
                "<code>Название | Цена | Описание | Категория</code>\n\n"
                "<b>Пример:</b>\n"
                "<code>Кроссовки Nike | 8900 | Спортивные кроссовки | footwear</code>\n\n"
                "<b>Доступные категории:</b>\n"
                "• footwear - 👟 Обувь\n"
                "• clothing - 👕 Одежда\n"
                "• accessories - 🎒 Аксессуары\n"
                "• other - 📦 Другое\n\n"
                "Или отправьте /cancel для отмены"
            )
            dp.awaiting_product = getattr(dp, "awaiting_product", set())
            dp.awaiting_product.add(user_id)
        
        elif data == "admin_export" and is_admin:
            await callback.message.edit_text(
                "📤 <b>Экспорт данных</b>\n\n"
                "Выберите что экспортировать:",
                reply_markup=export_keyboard()
            )
        
        elif data == "export_orders" and is_admin:
            await callback.message.answer(
                "📦 <b>Экспорт заказов</b>\n\n"
                "Файл доступен по ссылке:\n"
                f"https://{os.environ.get('RENDER_SERVICE', 'your-app')}.onrender.com/export/orders.csv\n\n"
                "Или нажмите кнопку ниже:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="📥 Скачать CSV",
                        url=f"https://{os.environ.get('RENDER_SERVICE', 'your-app')}.onrender.com/export/orders.csv"
                    )
                ]])
            )
        
        elif data == "export_products" and is_admin:
            await callback.message.answer(
                "🛒 <b>Экспорт товаров</b>\n\n"
                "Файл доступен по ссылке:\n"
                f"https://{os.environ.get('RENDER_SERVICE', 'your-app')}.onrender.com/export/products.csv\n\n"
                "Или нажмите кнопку ниже:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="📥 Скачать CSV",
                        url=f"https://{os.environ.get('RENDER_SERVICE', 'your-app')}.onrender.com/export/products.csv"
                    )
                ]])
            )
        
        elif data == "admin_update_status" and is_admin:
            await callback.message.edit_text(
                "🔄 <b>Изменение статуса заказа</b>\n\n"
                "Введите номер заказа:",
                reply_markup=admin_panel_keyboard()
            )
            dp.waiting_for_order_id = getattr(dp, "waiting_for_order_id", set())
            dp.waiting_for_order_id.add(user_id)
        
        elif data == "noop":
            # Пустое действие (для кнопок пагинации)
            pass
    
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass  # Игнорируем эту ошибку
        else:
            logger.error(f"Telegram error: {e}")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback.message.answer("❌ <b>Произошла ошибка</b>\nПопробуйте еще раз.")

# --- Обработчик текстовых сообщений ---
[dp.message](workspace://dp.message)()
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    # Обработка отмены
    if message.text and message.text.lower() == "/cancel":
        if hasattr(dp, "awaiting_product") and user_id in dp.awaiting_product:
            dp.awaiting_product.remove(user_id)
        if hasattr(dp, "waiting_for_order_id") and user_id in dp.waiting_for_order_id:
            dp.waiting_for_order_id.remove(user_id)
        
        await message.answer(
            "❌ <b>Действие отменено</b>",
            reply_markup=main_menu_keyboard(is_admin)
        )
        return
    
    # Обработка добавления товара
    if hasattr(dp, "awaiting_product") and user_id in dp.awaiting_product:
        dp.awaiting_product.remove(user_id)
        
        try:
            parts = [p.strip() for p in message.text.split("|")]
            
            if len(parts) < 3:
                await message.answer(
                    "❌ <b>Неверный формат</b>\n\n"
                    "Используйте: <code>Название | Цена | Описание | Категория(опционально)</code>"
                )
                return
            
            name = parts[0]
            price = int(parts[1])
            desc = parts[2]
            category = parts[3] if len(parts) > 3 else 'other'
            
            # Валидация категории
            valid_categories = ['footwear', 'clothing', 'accessories', 'other']
            if category not in valid_categories:
                category = 'other'
            
            product_id = add_product(name, price, desc, category)
            
            await message.answer(
                f"✅ <b>Товар добавлен!</b>\n\n"
                f"<b>Название:</b> {name}\n"
                f"<b>Цена:</b> {price} ₽\n"
                f"<b>Категория:</b> {category}\n"
                f"<b>ID товара:</b> {product_id}\n\n"
                f"Теперь вы можете отправить фото товара (опционально)."
            )
            
            # Запоминаем ID товара для добавления фото
            dp.awaiting_photo = getattr(dp, "awaiting_photo", {})
            dp.awaiting_photo[user_id] = product_id
            
        except ValueError as e:
            await message.answer(f"❌ <b>Ошибка:</b> {str(e)}")
        except Exception as e:
            logger.error(f"Add product error: {e}")
            await message.answer("❌ <b>Произошла ошибка при добавлении товара</b>")
        
        await message.answer(
            "🛍 <b>Вернуться в меню:</b>",
            reply_markup=main_menu_keyboard(is_admin)
        )
        return
    
    # Обработка фото товара
    if hasattr(dp, "awaiting_photo") and user_id in dp.awaiting_photo:
        if message.photo:
            product_id = dp.awaiting_photo[user_id]
            file_id = message.photo[-1].file_id
            
            # Сохраняем фото
            save_product_photo(product_id, file_id)
            
            await message.answer(
                f"✅ <b>Фото добавлено к товару #{product_id}</b>"
            )
            del dp.awaiting_photo[user_id]
        else:
            await message.answer(
                "❌ <b>Пожалуйста, отправьте фото</b>\n"
                "Или отправьте /cancel для отмены"
            )
        return
    
    # Обработка ввода номера заказа для изменения статуса
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
                await message.answer(
                    f"🔄 <b>Заказ #{order_id}</b>\n\n"
                    f"Текущий статус: <b>{current_status}</b>\n\n"
                    f"Выберите новый статус:",
                    reply_markup=update_status_keyboard(order_id, current_status)
                )
            else:
                await message.answer(f"❌ <b>Заказ #{order_id} не найден</b>")
        except ValueError:
            await message.answer("❌ <b>Введите номер заказа (цифрами)</b>")
        return
    
    # Обработка обычных сообщений
    await message.answer(
        "🛍 <b>Используйте кнопки для навигации</b>\n\n"
        "Или введите команду /help для справки",
        reply_markup=main_menu_keyboard(is_admin)
    )

# --- Основная функция ---
async def main():
    # Инициализация базы данных
    init_db()
    
    # Установка команд бота
    await set_commands()
    
    logger.info("🛍 БОТ-МАГАЗИН ЗАПУЩЕН")
    logger.info(f"📋 Администраторы: {ADMIN_IDS}")
    logger.info("✅ Меню команд установлено")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
