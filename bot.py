import asyncio
import os
import logging
import sqlite3
import threading
import json
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [1912287053]  # Ваш Telegram ID

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
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- База данных SQLite ---
DB_PATH = "shop.db"

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица товаров (добавлено поле photo)
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
    
    # Таблица заказов (добавлен статус)
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
    
    # Таблица избранного (wishlist)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    
    # Таблица отзывов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    
    # Таблица промокодов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            discount INTEGER NOT NULL,
            expires_at TEXT,
            uses_left INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    # Добавляем тестовые товары, если таблица пуста
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
    
    # Добавляем тестовый промокод
    cursor.execute("SELECT COUNT(*) FROM promocodes")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO promocodes (code, discount, expires_at, uses_left, is_active) VALUES (?, ?, ?, ?, ?)",
            ("WELCOME10", 10, None, 100, 1)
        )
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Функции работы с БД ---

def get_products(sort: str = None, search: str = None):
    """Получить все товары с сортировкой и поиском"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = "SELECT id, name, price, description, photo FROM products WHERE 1=1"
    params = []
    
    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")
    
    if sort == "price_asc":
        query += " ORDER BY price ASC"
    elif sort == "price_desc":
        query += " ORDER BY price DESC"
    elif sort == "name_asc":
        query += " ORDER BY name ASC"
    else:
        query += " ORDER BY id ASC"
    
    cursor.execute(query, params)
    products = cursor.fetchall()
    conn.close()
    return products

def get_product(product_id: int):
    """Получить товар по ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, description, photo FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def update_product_photo(product_id: int, photo_file_id: str):
    """Обновить фото товара"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET photo = ? WHERE id = ?", (photo_file_id, product_id))
    conn.commit()
    conn.close()

def add_product(name: str, price: int, description: str):
    """Добавить товар"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO products (name, price, description) VALUES (?, ?, ?)",
        (name, price, description)
    )
    conn.commit()
    product_id = cursor.lastrowid
    conn.close()
    return product_id

def delete_product(product_id: int):
    """Удалить товар"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    cursor.execute("DELETE FROM carts WHERE product_id = ?", (product_id,))
    cursor.execute("DELETE FROM wishlist WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()

def get_cart(user_id: int):
    """Получить корзину пользователя"""
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
    return cart

def add_to_cart(user_id: int, product_id: int, quantity: int = 1):
    """Добавить товар в корзину"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO carts (user_id, product_id, quantity) 
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, product_id) 
        DO UPDATE SET quantity = quantity + ?
    ''', (user_id, product_id, quantity, quantity))
    conn.commit()
    conn.close()

def update_cart_quantity(user_id: int, product_id: int, quantity: int):
    """Изменить количество товара в корзине"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if quantity <= 0:
        cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    else:
        cursor.execute("UPDATE carts SET quantity = ? WHERE user_id = ? AND product_id = ?", (quantity, user_id, product_id))
    conn.commit()
    conn.close()

def remove_from_cart(user_id: int, product_id: int):
    """Удалить товар из корзины"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def clear_cart(user_id: int):
    """Очистить корзину"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def create_order(user_id: int, cart_items, total: int, discount: int = 0):
    """Создать заказ"""
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

def update_order_status(order_id: int, status: str):
    """Обновить статус заказа"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_user_orders(user_id: int):
    """Получить заказы пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, order_date, total, status FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (user_id,)
    )
    orders = cursor.fetchall()
    conn.close()
    return orders

def get_all_orders():
    """Получить все заказы (для админа)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, order_date, total, status FROM orders ORDER BY id DESC LIMIT 20"
    )
    orders = cursor.fetchall()
    conn.close()
    return orders

def get_stats():
    """Получить статистику"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders")
    users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM orders")
    orders_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(total) FROM orders")
    revenue = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM products")
    products_count = cursor.fetchone()[0]
    
    # Средний рейтинг товаров
    cursor.execute("SELECT AVG(rating) FROM reviews")
    avg_rating = cursor.fetchone()[0] or 0
    
    conn.close()
    return users, orders_count, revenue, products_count, round(avg_rating, 1)

# --- Избранное ---
def add_to_wishlist(user_id: int, product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO wishlist (user_id, product_id) VALUES (?, ?)", (user_id, product_id))
    conn.commit()
    conn.close()

def remove_from_wishlist(user_id: int, product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM wishlist WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def get_wishlist(user_id: int):
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
    return wishlist

def is_in_wishlist(user_id: int, product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM wishlist WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- Отзывы ---
def add_review(product_id: int, user_id: int, rating: int, comment: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reviews (product_id, user_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
        (product_id, user_id, rating, comment, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.commit()
    conn.close()

def get_product_reviews(product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT rating, comment, created_at FROM reviews WHERE product_id = ? ORDER BY id DESC LIMIT 10",
        (product_id,)
    )
    reviews = cursor.fetchall()
    conn.close()
    return reviews

def get_product_rating(product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE product_id = ?", (product_id,))
    avg, count = cursor.fetchone()
    conn.close()
    return round(avg or 0, 1), count or 0

# --- Промокоды ---
def validate_promocode(code: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT discount, expires_at, uses_left, is_active FROM promocodes WHERE code = ?",
        (code.upper(),)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return None, None
    
    discount, expires_at, uses_left, is_active = result
    
    if not is_active:
        return None, None
    
    if uses_left <= 0:
        return None, None
    
    if expires_at:
        expires_date = datetime.strptime(expires_at, "%Y-%m-%d")
        if expires_date < datetime.now():
            return None, None
    
    return discount, code.upper()

def use_promocode(code: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", (code.upper(),))
    conn.commit()
    conn.close()

# --- Клавиатуры ---

def main_menu_keyboard(is_admin: bool = False):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")
        ],
        [
            InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"),
            InlineKeyboardButton(text="❤️ Избранное", callback_data="wishlist")
        ],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
            InlineKeyboardButton(text="🔍 Поиск", callback_data="search_menu")
        ]
    ])
    if is_admin:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")
        ])
    return keyboard

def catalog_sort_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 По умолчанию", callback_data="sort_default"),
            InlineKeyboardButton(text="💰 По цене ↑", callback_data="sort_price_asc")
        ],
        [
            InlineKeyboardButton(text="💰 По цене ↓", callback_data="sort_price_desc"),
            InlineKeyboardButton(text="🔤 По названию", callback_data="sort_name_asc")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]
    ])

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")
        ],
        [
            InlineKeyboardButton(text="📋 Товары", callback_data="admin_products"),
            InlineKeyboardButton(text="📦 Заказы", callback_data="admin_orders")
        ],
        [
            InlineKeyboardButton(text="🎫 Промокоды", callback_data="admin_promocodes"),
            InlineKeyboardButton(text="🔄 Статусы заказов", callback_data="admin_update_status")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]
    ])

def product_card_keyboard(product_id: int, user_id: int, in_cart: bool = False):
    buttons = []
    
    # Кнопки количества в корзине
    if in_cart:
        buttons.append([
            InlineKeyboardButton(text="➖", callback_data=f"cart_decr_{product_id}"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"remove_from_cart_{product_id}"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_incr_{product_id}")
        ])
    else:
        buttons.append([InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}")])
    
    # Избранное
    if is_in_wishlist(user_id, product_id):
        buttons.append([InlineKeyboardButton(text="❤️ В избранном", callback_data=f"remove_wishlist_{product_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🤍 В избранное", callback_data=f"add_wishlist_{product_id}")])
    
    # Отзывы
    buttons.append([InlineKeyboardButton(text="⭐ Отзывы", callback_data=f"show_reviews_{product_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="catalog")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cart_item_keyboard(product_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖", callback_data=f"cart_decr_{product_id}"),
            InlineKeyboardButton(text="❌", callback_data=f"remove_from_cart_{product_id}"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_incr_{product_id}")
        ]
    ])

def cart_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🎫 Промокод", callback_data="enter_promocode")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def order_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cart")]
    ])

def rating_keyboard(product_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ 1", callback_data=f"rate_{product_id}_1"),
            InlineKeyboardButton(text="⭐⭐ 2", callback_data=f"rate_{product_id}_2"),
            InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data=f"rate_{product_id}_3"),
            InlineKeyboardButton(text="⭐⭐⭐⭐ 4", callback_data=f"rate_{product_id}_4"),
            InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data=f"rate_{product_id}_5")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"product_{product_id}")]
    ])

def update_status_keyboard(order_id: int, current_status: str):
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
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_orders")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Обработчики ---

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    text = "🛍 **Добро пожаловать в магазин!**\n\nЗдесь вы можете:\n• 📋 Посмотреть каталог\n• 🛒 Добавить товары в корзину\n• ✅ Оформить заказ\n• ❤️ Добавить товары в избранное\n• ⭐ Оставить отзывы"
    if is_admin:
        text += "\n\n🔐 **Вы вошли как администратор**"
    
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    is_admin = user_id in ADMIN_IDS
    
    await callback.answer()
    
    # --- Навигация ---
    if data == "back_to_main":
        await callback.message.edit_text("🛍 **Главное меню**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
    
    elif data == "admin_panel" and is_admin:
        await callback.message.edit_text("🔐 **Панель администратора**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    
    # --- Каталог с сортировкой ---
    elif data == "catalog":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Сортировка", callback_data="sort_menu")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
        ])
        products = get_products()
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n", keyboard)
    
    elif data == "sort_menu":
        await callback.message.edit_text("📊 **Выберите сортировку:**", reply_markup=catalog_sort_keyboard(), parse_mode="Markdown")
    
    elif data == "sort_default":
        products = get_products(sort=None)
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n(По умолчанию)\n\n", main_menu_keyboard(is_admin))
    
    elif data == "sort_price_asc":
        products = get_products(sort="price_asc")
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n(Сначала дешёвые)\n\n", main_menu_keyboard(is_admin))
    
    elif data == "sort_price_desc":
        products = get_products(sort="price_desc")
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n(Сначала дорогие)\n\n", main_menu_keyboard(is_admin))
    
    elif data == "sort_name_asc":
        products = get_products(sort="name_asc")
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n(По названию)\n\n", main_menu_keyboard(is_admin))
    
    # --- Поиск ---
    elif data == "search_menu":
        await callback.message.edit_text("🔍 **Поиск товаров**\n\nВведите название товара:", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
        dp.waiting_for_search = getattr(dp, "waiting_for_search", set())
        dp.waiting_for_search.add(user_id)
    
    # --- Карточка товара ---
    elif data.startswith("product_"):
        product_id = int(data.split("_")[1])
        product = get_product(product_id)
        if not product:
            await callback.message.answer("❌ Товар не найден")
            return
        
        cart = get_cart(user_id)
        in_cart = any(c[0] == product_id for c in cart)
        avg_rating, reviews_count = get_product_rating(product_id)
        
        text = f"**{product[1]}**\n\n💰 Цена: {product[2]} ₽\n📝 {product[3]}\n⭐ Рейтинг: {avg_rating} ({reviews_count} отзывов)"
        
        if product[4]:
            await bot.send_photo(user_id, product[4], caption=text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
            await callback.message.delete()
        else:
            await callback.message.edit_text(text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
    
    # --- Корзина с количеством ---
    elif data.startswith("add_to_cart_"):
        product_id = int(data.split("_")[3])
        add_to_cart(user_id, product_id)
        await callback.message.answer("✅ Товар добавлен в корзину!")
    
    elif data.startswith("cart_incr_"):
        product_id = int(data.split("_")[2])
        add_to_cart(user_id, product_id)
        await show_cart(callback, user_id)
    
    elif data.startswith("cart_decr_"):
        product_id = int(data.split("_")[2])
        cart = get_cart(user_id)
        for item in cart:
            if item[0] == product_id:
                new_qty = item[3] - 1
                update_cart_quantity(user_id, product_id, new_qty)
                break
        await show_cart(callback, user_id)
    
    elif data.startswith("remove_from_cart_"):
        product_id = int(data.split("_")[3])
        remove_from_cart(user_id, product_id)
        await callback.message.answer("❌ Товар удалён из корзины!")
        await show_cart(callback, user_id)
    
    elif data == "cart":
        await show_cart(callback, user_id)
    
    elif data == "clear_cart":
        clear_cart(user_id)
        await callback.message.edit_text("🛒 **Корзина очищена**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
    
    # --- Избранное ---
    elif data.startswith("add_wishlist_"):
        product_id = int(data.split("_")[2])
        add_to_wishlist(user_id, product_id)
        await callback.message.answer("❤️ Товар добавлен в избранное!")
        product = get_product(product_id)
        cart = get_cart(user_id)
        in_cart = any(c[0] == product_id for c in cart)
        text = f"**{product[1]}**\n\n💰 Цена: {product[2]} ₽\n📝 {product[3]}"
        await callback.message.edit_text(text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
    
    elif data.startswith("remove_wishlist_"):
        product_id = int(data.split("_")[2])
        remove_from_wishlist(user_id, product_id)
        await callback.message.answer("🤍 Товар удалён из избранного!")
        product = get_product(product_id)
        cart = get_cart(user_id)
        in_cart = any(c[0] == product_id for c in cart)
        text = f"**{product[1]}**\n\n💰 Цена: {product[2]} ₽\n📝 {product[3]}"
        await callback.message.edit_text(text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
    
    elif data == "wishlist":
        wishlist = get_wishlist(user_id)
        if not wishlist:
            await callback.message.edit_text("❤️ **Избранное пусто**\n\nДобавляйте товары в избранное через карточку товара.", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
            return
        
        text = "❤️ **Ваше избранное**\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for item in wishlist:
            text += f"• {item[1]} - {item[2]} ₽\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"📦 {item[1]}", callback_data=f"product_{item[0]}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    # --- Отзывы ---
    elif data.startswith("show_reviews_"):
        product_id = int(data.split("_")[2])
        product = get_product(product_id)
        reviews = get_product_reviews(product_id)
        avg_rating, reviews_count = get_product_rating(product_id)
        
        text = f"⭐ **Отзывы о {product[1]}**\n\nСредний рейтинг: {avg_rating} ({reviews_count} отзывов)\n\n"
        
        if reviews:
            for review in reviews[:5]:
                stars = "⭐" * review[0]
                text += f"{stars}\n📝 {review[1]}\n📅 {review[2]}\n\n"
        else:
            text += "Пока нет отзывов. Будьте первым!\n"
        
        text += "\nХотите оставить отзыв? Оцените товар:"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 1", callback_data=f"rate_{product_id}_1"),
                InlineKeyboardButton(text="⭐⭐ 2", callback_data=f"rate_{product_id}_2"),
                InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data=f"rate_{product_id}_3"),
                InlineKeyboardButton(text="⭐⭐⭐⭐ 4", callback_data=f"rate_{product_id}_4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data=f"rate_{product_id}_5")
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"product_{product_id}")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif data.startswith("rate_"):
        parts = data.split("_")
        product_id = int(parts[1])
        rating = int(parts[2])
        
        await callback.message.edit_text(f"⭐ Вы выбрали оценку {rating}\n\nНапишите ваш отзыв (текст):", parse_mode="Markdown")
        dp.waiting_for_review = getattr(dp, "waiting_for_review", {})
        dp.waiting_for_review[user_id] = {"product_id": product_id, "rating": rating}
    
    # --- Оформление заказа ---
    elif data == "checkout":
        cart = get_cart(user_id)
        if not cart:
            await callback.message.answer("🛒 Корзина пуста")
            return
        
        total = 0
        items_text = ""
        for item in cart:
            subtotal = item[2] * item[3]
            total += subtotal
            items_text += f"• {item[1]} x{item[3]} = {subtotal} ₽\n"
        
        text = f"📦 **Подтверждение заказа**\n\n{items_text}\n**Итого: {total} ₽**"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Применить промокод", callback_data="enter_promocode")],
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cart")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        dp.pending_order = getattr(dp, "pending_order", {})
        dp.pending_order[user_id] = {"total": total}
    
    elif data == "enter_promocode":
        await callback.message.edit_text("🎫 **Введите промокод:**", reply_markup=cart_keyboard(), parse_mode="Markdown")
        dp.waiting_for_promocode = getattr(dp, "waiting_for_promocode", set())
        dp.waiting_for_promocode.add(user_id)
    
    elif data == "confirm_order":
        cart = get_cart(user_id)
        if not cart:
            await callback.message.answer("❌ Корзина пуста")
            return
        
        total = 0
        items = []
        for item in cart:
            subtotal = item[2] * item[3]
            total += subtotal
            items.append({
                "product_id": item[0],
                "name": item[1],
                "price": item[2],
                "quantity": item[3],
                "subtotal": subtotal
            })
        
        order_id = create_order(user_id, items, total)
        clear_cart(user_id)
        
        text = f"✅ **Заказ #{order_id} оформлен!**\n\n💰 Сумма: {total} ₽\nСтатус можно отслеживать в «Мои заказы»"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"🆕 **Новый заказ #{order_id}!**\nПользователь: {callback.from_user.id}\nСумма: {total} ₽")
            except:
                pass
    
    # --- Заказы пользователя ---
    elif data == "my_orders":
        orders = get_user_orders(user_id)
        if not orders:
            await callback.message.edit_text("📦 **У вас пока нет заказов**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
            return
        
        text = "📦 **Ваши заказы**\n\n"
        keyboard = InlineKeyboardMarkup(inline
