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
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- База данных SQLite ---
DB_PATH = "shop.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            photo TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wishlist (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    
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

# --- Функции работы с БД ---

def get_products(sort: str = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = "SELECT id, name, price, description, photo FROM products"
    
    if sort == "price_asc":
        query += " ORDER BY price ASC"
    elif sort == "price_desc":
        query += " ORDER BY price DESC"
    elif sort == "name_asc":
        query += " ORDER BY name ASC"
    else:
        query += " ORDER BY id ASC"
    
    cursor.execute(query)
    products = cursor.fetchall()
    conn.close()
    return products

def get_product(product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, description, photo FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def add_product(name: str, price: int, description: str):
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    cursor.execute("DELETE FROM carts WHERE product_id = ?", (product_id,))
    cursor.execute("DELETE FROM wishlist WHERE product_id = ?", (product_id,))
    conn.commit()
    conn.close()

def get_cart(user_id: int):
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

def add_to_cart(user_id: int, product_id: int):
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

def update_cart_quantity(user_id: int, product_id: int, quantity: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if quantity <= 0:
        cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    else:
        cursor.execute("UPDATE carts SET quantity = ? WHERE user_id = ? AND product_id = ?", (quantity, user_id, product_id))
    conn.commit()
    conn.close()

def remove_from_cart(user_id: int, product_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def clear_cart(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def create_order(user_id: int, cart_items, total: int):
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_user_orders(user_id: int):
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, order_date, total, status FROM orders ORDER BY id DESC LIMIT 20"
    )
    orders = cursor.fetchall()
    conn.close()
    return orders

def get_stats():
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
    
    conn.close()
    return users, orders_count, revenue, products_count

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
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile")
        ]
    ])
    if is_admin:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")
        ])
    return keyboard

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
            InlineKeyboardButton(text="🔄 Статусы заказов", callback_data="admin_update_status"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]
    ])

def product_card_keyboard(product_id: int, user_id: int, in_cart: bool = False):
    buttons = []
    
    if in_cart:
        buttons.append([
            InlineKeyboardButton(text="➖", callback_data=f"cart_decr_{product_id}"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"remove_from_cart_{product_id}"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_incr_{product_id}")
        ])
    else:
        buttons.append([InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}")])
    
    if is_in_wishlist(user_id, product_id):
        buttons.append([InlineKeyboardButton(text="❤️ В избранном", callback_data=f"remove_wishlist_{product_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🤍 В избранное", callback_data=f"add_wishlist_{product_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="catalog")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cart_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def order_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cart")]
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

# --- Вспомогательные функции ---

async def show_product_list(callback: CallbackQuery, products, title, back_keyboard=None):
    if not products:
        await callback.message.edit_text("📭 **Каталог пуст**", reply_markup=back_keyboard or main_menu_keyboard(callback.from_user.id in ADMIN_IDS), parse_mode="Markdown")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for p in products:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"{p[1]} - {p[2]} ₽", callback_data=f"product_{p[0]}")
        ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    
    await callback.message.edit_text(title, reply_markup=keyboard, parse_mode="Markdown")

async def show_cart(callback: CallbackQuery, user_id: int):
    cart = get_cart(user_id)
    is_admin = user_id in ADMIN_IDS
    
    if not cart:
        await callback.message.edit_text("🛒 **Корзина пуста**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
        return
    
    total = 0
    text = "🛒 **Ваша корзина**\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for item in cart:
        subtotal = item[2] * item[3]
        total += subtotal
        text += f"• {item[1]} x{item[3]} = {subtotal} ₽\n"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"➖ {item[1]} ➕", callback_data=f"cart_item_{item[0]}")
        ])
    
    text += f"\n**Итого: {total} ₽**"
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

# --- Обработчики ---

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    text = "🛍 **Добро пожаловать в магазин!**\n\nЗдесь вы можете:\n• 📋 Посмотреть каталог\n• 🛒 Добавить товары в корзину\n• ✅ Оформить заказ\n• ❤️ Добавить товары в избранное"
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
    
    # --- Каталог ---
    elif data == "catalog":
        products = get_products()
        await show_product_list(callback, products, "📋 **Каталог товаров**\n\n", main_menu_keyboard(is_admin))
    
    elif data.startswith("product_"):
        product_id = int(data.split("_")[1])
        product = get_product(product_id)
        if not product:
            await callback.message.answer("❌ Товар не найден")
            return
        
        cart = get_cart(user_id)
        in_cart = any(c[0] == product_id for c in cart)
        
        text = f"**{product[1]}**\n\n💰 Цена: {product[2]} ₽\n📝 {product[3]}"
        
        if product[4]:
            await bot.send_photo(user_id, product[4], caption=text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
            await callback.message.delete()
        else:
            await callback.message.edit_text(text, reply_markup=product_card_keyboard(product_id, user_id, in_cart), parse_mode="Markdown")
    
    # --- Корзина ---
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
        await callback.message.edit_text(text, reply_markup=order_confirmation_keyboard(), parse_mode="Markdown")
    
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
        for order in orders:
            status_emoji = "🟡" if order[3] == "pending" else ("🟢" if order[3] == "shipping" else ("✅" if order[3] == "delivered" else "❌"))
            text += f"{status_emoji} **Заказ #{order[0]}**\n📅 {order[1]}\n💰 {order[2]} ₽\n\n"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
    
    # --- Профиль ---
    elif data == "profile":
        orders = get_user_orders(user_id)
        total_spent = sum(o[2] for o in orders)
        text = f"👤 **Ваш профиль**\n\n🆔 ID: {user_id}\n📦 Заказов: {len(orders)}\n💰 Потрачено: {total_spent} ₽\n🛒 Товаров в корзине: {len(get_cart(user_id))}\n❤️ В избранном: {len(get_wishlist(user_id))}"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
    
    # --- Избранное ---
    elif data.startswith("add_wishlist_"):
        product_id = int(data.split("_")[2])
        add_to_wishlist(user_id, product_id)
        await callback.message.answer("❤️ Товар добавлен в избранное!")
    
    elif data.startswith("remove_wishlist_"):
        product_id = int(data.split("_")[2])
        remove_from_wishlist(user_id, product_id)
        await callback.message.answer("🤍 Товар удалён из избранного!")
    
    elif data == "wishlist":
        wishlist = get_wishlist(user_id)
        if not wishlist:
            await callback.message.edit_text("❤️ **Избранное пусто**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
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
    
    # --- Админ функции ---
    elif data == "admin_stats" and is_admin:
        users, orders_count, revenue, products_count = get_stats()
        text = f"📊 **Статистика**\n\n👥 Пользователей: {users}\n📦 Заказов: {orders_count}\n💰 Выручка: {revenue} ₽\n🛒 Товаров: {products_count}"
        await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    
    elif data == "admin_products" and is_admin:
        products = get_products()
        if not products:
            await callback.message.edit_text("📭 **Нет товаров**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
            return
        
        text = "📋 **Управление товарами**\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for p in products:
            text += f"• {p[1]} - {p[2]} ₽\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"❌ Удалить {p[1]}", callback_data=f"admin_delete_product_{p[0]}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif data.startswith("admin_delete_product_") and is_admin:
        product_id = int(data.split("_")[3])
        product = get_product(product_id)
        if product:
            delete_product(product_id)
            await callback.message.answer(f"✅ Товар «{product[1]}» удалён")
        else:
            await callback.message.answer("❌ Товар не найден")
    
    elif data == "admin_orders" and is_admin:
        all_orders = get_all_orders()
        if not all_orders:
            await callback.message.edit_text("📦 **Нет заказов**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
            return
        
        text = "📦 **Все заказы**\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for order in all_orders:
            status_emoji = "🟡" if order[4] == "pending" else ("🟢" if order[4] == "shipping" else ("✅" if order[4] == "delivered" else "❌"))
            text += f"{status_emoji} **Заказ #{order[0]}**\n👤 Пользователь: {order[1]}\n📅 {order[2]}\n💰 {order[3]} ₽\n\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"🔄 Заказ #{order[0]}", callback_data=f"update_status_{order[0]}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif data.startswith("update_status_") and is_admin:
        order_id = int(data.split("_")[2])
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        current_status = cursor.fetchone()[0]
        conn.close()
        
        await callback.message.edit_text(f"🔄 **Заказ #{order_id}**\n\nТекущий статус: {current_status}\n\nВыберите новый статус:", reply_markup=update_status_keyboard(order_id, current_status), parse_mode="Markdown")
    
    elif data.startswith("set_status_") and is_admin:
        parts = data.split("_")
        order_id = int(parts[2])
        new_status = parts[3]
        
        update_order_status(order_id, new_status)
        
        status_names = {"pending": "🟡 Ожидает", "shipping": "🟢 В пути", "delivered": "✅ Доставлен", "cancelled": "❌ Отменён"}
        await callback.message.answer(f"✅ Статус заказа #{order_id} изменён на {status_names.get(new_status, new_status)}")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        user_id_order = cursor.fetchone()[0]
        conn.close()
        
        try:
            await bot.send_message(user_id_order, f"🔄 Статус вашего заказа #{order_id} изменён на {status_names.get(new_status, new_status)}")
        except:
            pass
    
    elif data == "admin_add_product" and is_admin:
        await callback.message.edit_text(
            "📝 **Добавление товара**\n\nОтправьте данные в формате:\n`Название | Цена | Описание`\n\nПример:\n`Кроссовки Nike | 8900 | Спортивные кроссовки`",
            reply_markup=admin_panel_keyboard(),
            parse_mode="Markdown"
        )
        dp.awaiting_product = getattr(dp, "awaiting_product", set())
        dp.awaiting_product.add(user_id)
    
    elif data == "admin_update_status" and is_admin:
        await callback.message.edit_text(
            "🔄 **Изменение статуса заказа**\n\nВведите номер заказа:",
            reply_markup=admin_panel_keyboard(),
            parse_mode="Markdown"
        )
        dp.waiting_for_order_id = getattr(dp, "waiting_for_order_id", set())
        dp.waiting_for_order_id.add(user_id)

# --- Обработка добавления товара ---
@dp.message()
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
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
                await message.answer("❌ Неверный формат. Используйте: `Название | Цена | Описание`", parse_mode="Markdown")
        except ValueError:
            await message.answer("❌ Цена должна быть числом")
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
        
        await message.answer("🛍 Вернуться в меню:", reply_markup=main_menu_keyboard(is_admin))
        return
    
    if hasattr(dp, "waiting_for_order_id") and user_id in dp.waiting_for_order_id:
        dp.waiting_for_order_id.remove(user_id)
        try:
            order_id = int(message.text.strip())
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                current_status = result[0]
                await message.answer(f"🔄 **Заказ #{order_id}**\n\nТекущий статус: {current_status}\n\nВыберите новый статус:", reply_markup=update_status_keyboard(order_id, current_status), parse_mode="Markdown")
            else:
                await message.answer(f"❌ Заказ #{order_id} не найден")
        except ValueError:
            await message.answer("❌ Введите номер заказа (цифрами)")
        return
    
    await message.answer("🛍 Используйте кнопки для навигации.", reply_markup=main_menu_keyboard(is_admin))

# --- Запуск ---
async def main():
    init_db()
    logger.info("=" * 40)
    logger.info("🛍 БОТ-МАГАЗИН ЗАПУЩЕН")
    logger.info(f"🤖 Бот: @{(await bot.get_me()).username}")
    logger.info(f"👑 Администратор: {ADMIN_IDS}")
    logger.info("=" * 40)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
