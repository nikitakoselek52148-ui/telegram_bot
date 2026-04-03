import asyncio
import os
import logging
import sqlite3
import threading
import json
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [1912287053]
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL")

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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            phone TEXT,
            phone_verified INTEGER DEFAULT 0,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            registered_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
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
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Функция загрузки товаров из Google Sheets ---
async def load_products_from_google_sheets():
    """Загружает товары из Google Sheets и обновляет БД"""
    if not GOOGLE_SHEETS_URL:
        logger.warning("GOOGLE_SHEETS_URL не задан, используем локальные товары")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GOOGLE_SHEETS_URL, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"Ошибка загрузки Google Sheets: {response.status}")
                    return False
                
                csv_text = await response.text()
                lines = csv_text.strip().split('\n')
                
                if len(lines) < 2:
                    logger.warning("Google Sheets пуст")
                    return False
                
                # Парсим CSV (простой способ)
                products = []
                headers = [h.strip().lower() for h in lines[0].split(',')]
                
                # Находим индексы колонок
                id_idx = headers.index('id') if 'id' in headers else 0
                name_idx = headers.index('name') if 'name' in headers else 1
                price_idx = headers.index('price') if 'price' in headers else 2
                desc_idx = headers.index('description') if 'description' in headers else 3
                photo_idx = headers.index('photo') if 'photo' in headers else 4
                
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) < 4:
                        continue
                    
                    try:
                        product_id = int(parts[id_idx]) if parts[id_idx].isdigit() else None
                        name = parts[name_idx] if len(parts) > name_idx else ""
                        price = int(parts[price_idx]) if len(parts) > price_idx and parts[price_idx].isdigit() else 0
                        description = parts[desc_idx] if len(parts) > desc_idx else ""
                        photo = parts[photo_idx] if len(parts) > photo_idx and parts[photo_idx] else None
                        
                        if product_id and name and price > 0:
                            products.append({
                                'id': product_id,
                                'name': name,
                                'price': price,
                                'description': description,
                                'photo': photo
                            })
                    except Exception as e:
                        logger.error(f"Ошибка парсинга строки: {e}")
                        continue
                
                if products:
                    # Обновляем БД
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    
                    # Очищаем старые товары
                    cursor.execute("DELETE FROM products")
                    
                    # Добавляем новые
                    for p in products:
                        cursor.execute('''
                            INSERT INTO products (id, name, price, description, photo)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (p['id'], p['name'], p['price'], p['description'], p['photo']))
                    
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Загружено {len(products)} товаров из Google Sheets")
                    return True
                else:
                    logger.warning("Нет валидных товаров в Google Sheets")
                    return False
                    
    except Exception as e:
        logger.error(f"Ошибка загрузки Google Sheets: {e}")
        return False

# --- Функции работы с пользователями ---
def register_user(user_id: int, first_name: str = None, last_name: str = None, username: str = None, phone: str = None, verified: bool = False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        if phone:
            cursor.execute("UPDATE users SET phone = ?, phone_verified = ? WHERE user_id = ?", (phone, 1 if verified else 0, user_id))
    else:
        cursor.execute('''
            INSERT INTO users (user_id, phone, phone_verified, first_name, last_name, username, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, phone, 1 if verified else 0, first_name, last_name, username, datetime.now().strftime("%d.%m.%Y %H:%M")))
    
    conn.commit()
    conn.close()

def get_user_phone(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT phone, phone_verified FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (None, 0)

def is_user_registered(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def is_phone_verified(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT phone_verified FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] == 1 if result else False

# --- Функции работы с БД (товары из Google Sheets) ---
def get_products():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, description, photo FROM products ORDER BY id")
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

# --- Клавиатура для ввода номера ---
def get_phone_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

def remove_keyboard():
    return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)

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
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"carousel_prev_{current}"),
            InlineKeyboardButton(text=f"{current+1}/{total}", callback_data="carousel_info"),
            InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"carousel_next_{current}")
        ],
        [
            InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"carousel_add_{product['id']}_{current}"),
            InlineKeyboardButton(text="❤️ В избранное", callback_data=f"carousel_wishlist_{product['id']}_{current}")
        ],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    
    text = f"<b>{product['name']}</b>\n\n💰 Цена: {product['price']} ₽\n📝 {product['description']}"
    
    if product['photo']:
        await message.answer_photo(photo=product['photo'], caption=text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)

# --- Клавиатуры меню ---
def main_menu_keyboard(is_admin=False):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"), InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"), InlineKeyboardButton(text="❤️ Избранное", callback_data="wishlist")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])
    if is_admin:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")])
    return keyboard

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton(text="🔄 Обновить товары", callback_data="admin_sync")],
        [InlineKeyboardButton(text="📋 Товары", callback_data="admin_products"), InlineKeyboardButton(text="📦 Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton(text="🔄 Статусы заказов", callback_data="admin_update_status"), InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

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

def update_status_keyboard(order_id, current_status):
    statuses = [("🟡 Ожидает", "pending"), ("🟢 В пути", "shipping"), ("✅ Доставлен", "delivered"), ("❌ Отменён", "cancelled")]
    buttons = []
    for name, code in statuses:
        if code != current_status:
            buttons.append([InlineKeyboardButton(text=name, callback_data=f"set_status_{order_id}_{code}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_orders")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Обработчики ---

# Глобальная переменная для хранения токенов верификации (в памяти)
pending_verifications = {}

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    # Загружаем товары из Google Sheets при старте
    if GOOGLE_SHEETS_URL:
        await load_products_from_google_sheets()
    
    # Проверяем, есть ли параметр верификации
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("verify_"):
        token = args[1].replace("verify_", "")
        if token in pending_verifications:
            verif = pending_verifications[token]
            if verif["expires"] > datetime.now():
                phone = verif["phone"]
                register_user(user_id=user_id, phone=phone, verified=True)
                del pending_verifications[token]
                await message.answer(f"✅ <b>Номер телефона подтверждён!</b>\n\n📱 Ваш номер: {phone}", reply_markup=remove_keyboard())
            else:
                await message.answer("❌ Срок действия ссылки истёк.")
                del pending_verifications[token]
        else:
            await message.answer("❌ Неверный код подтверждения.")
        return
    
    # Обычный /start
    if not is_user_registered(user_id):
        register_user(user_id=user_id, first_name=message.from_user.first_name, last_name=message.from_user.last_name, username=message.from_user.username)
        await message.answer(
            "🛍 <b>Добро пожаловать в магазин!</b>\n\n"
            "Для вашей безопасности и связи с вами, мы должны подтвердить ваш номер телефона.\n\n"
            "Используйте команду /verify, чтобы начать процесс подтверждения.",
            parse_mode="HTML"
        )
        return
    
    phone, verified = get_user_phone(user_id)
    text = "🛍 <b>Добро пожаловать в магазин!</b>\n\n"
    
    if phone:
        if verified:
            text += f"✅ Номер подтверждён: {phone}\n\n"
        else:
            text += f"📱 Ваш номер: {phone} (не подтверждён)\n⚠️ Для оформления заказов необходимо подтвердить номер командой /verify\n\n"
    else:
        text += "📱 Для оформления заказов необходимо подтвердить номер командой /verify\n\n"
    
    text += "• 📋 Посмотреть каталог\n• 🛒 Добавить товары в корзину\n• ✅ Оформить заказ\n• ❤️ Добавить товары в избранное"
    
    if is_admin:
        text += "\n\n🔐 <b>Вы вошли как администратор</b>"
    
    await message.answer(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")

@dp.message(Command("verify"))
async def verify_command(message: types.Message):
    user_id = message.from_user.id
    phone, verified = get_user_phone(user_id)
    if verified:
        await message.answer(f"✅ Ваш номер {phone} уже подтверждён!")
        return
    
    await message.answer(
        "📱 <b>Подтверждение номера телефона</b>\n\n"
        "Пожалуйста, введите ваш номер телефона в формате:\n"
        "<code>+7XXXXXXXXXX</code> (10 цифр после +7)\n\n"
        "Например: <code>+79123456789</code>",
        parse_mode="HTML"
    )
    dp.waiting_for_phone = getattr(dp, "waiting_for_phone", set())
    dp.waiting_for_phone.add(user_id)

@dp.message(lambda msg: msg.text and msg.text.startswith("+") and hasattr(dp, "waiting_for_phone") and msg.from_user.id in dp.waiting_for_phone)
async def handle_phone_input(message: types.Message):
    user_id = message.from_user.id
    dp.waiting_for_phone.remove(user_id)
    
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 10:
        await message.answer("❌ Неверный формат. Используйте: <code>+79123456789</code>", parse_mode="HTML")
        return
    
    import uuid
    token = str(uuid.uuid4())[:8]
    bot_username = (await bot.get_me()).username
    
    pending_verifications[token] = {
        "phone": phone,
        "expires": datetime.now() + timedelta(minutes=5),
        "user_id": user_id
    }
    
    verification_link = f"https://t.me/{bot_username}?start=verify_{token}"
    
    await message.answer(
        f"📱 <b>Код подтверждения отправлен!</b>\n\n"
        f"👉 <a href='{verification_link}'>Нажмите сюда, чтобы подтвердить номер {phone}</a>\n\n"
        f"⚠️ Ссылка действительна 5 минут.",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

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
    
    # --- Админ: синхронизация с Google Sheets ---
    if data == "admin_sync" and is_admin:
        await callback.message.edit_text("🔄 <b>Синхронизация товаров с Google Sheets...</b>", parse_mode="HTML")
        success = await load_products_from_google_sheets()
        if success:
            await callback.message.answer("✅ <b>Товары успешно обновлены из Google Sheets!</b>", parse_mode="HTML")
        else:
            await callback.message.answer("❌ <b>Ошибка синхронизации.</b>\nПроверьте ссылку на Google Sheets.", parse_mode="HTML")
        await callback.message.edit_text("🔐 <b>Панель администратора</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
        return
    
    # --- Карусель ---
    if data.startswith("catalog"):
        products = get_products()
        if not products:
            await callback.message.edit_text("📭 Каталог пуст")
            return
        await send_product_carousel(callback.message, products, 0)
        if hasattr(callback.message, 'delete'):
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
            await callback.message.answer("🤍 Товар удалён из избранного!")
        else:
            add_to_wishlist(user_id, product_id)
            await callback.message.answer("❤️ Товар добавлен в избранное!")
        products = get_products()
        await send_product_carousel(callback.message, products, current)
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    # --- Навигация ---
    if data == "back_to_main":
        phone, verified = get_user_phone(user_id)
        text = "🛍 <b>Главное меню</b>"
        if not verified:
            text += "\n\n⚠️ <b>Номер телефона не подтверждён!</b>\nИспользуйте команду /verify"
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(is_admin), parse_mode="HTML")
    
    elif data == "admin_panel" and is_admin:
        await callback.message.edit_text("🔐 <b>Панель администратора</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    
    elif data == "catalog":
        products = get_products()
        if not products:
            await callback.message.edit_text("📭 Каталог пуст")
            return
        await send_product_carousel(callback.message, products, 0)
        try:
            await callback.message.delete()
        except:
            pass
    
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
                InlineKeyboardButton(text=f"➖ {item['name']}", callback_data=f"cart_decr_{item['product_id']}"),
                InlineKeyboardButton(text=f"❌", callback_data=f"remove_from_cart_{item['product_id']}"),
                InlineKeyboardButton(text=f"➕", callback_data=f"cart_incr_{item['product_id']}")
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
        _, verified = get_user_phone(user_id)
        if not verified:
            await callback.message.answer("❌ <b>Номер телефона не подтверждён!</b>\n\nИспользуйте команду /verify", parse_mode="HTML")
            return
        
        cart = get_cart(user_id)
        if not cart:
            await callback.message.answer("🛒 Корзина пуста")
            return
        
        phone, _ = get_user_phone(user_id)
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
        phone, verified = get_user_phone(user_id)
        phone_display = f"{phone} ✅" if verified else f"{phone or 'Не указан'} ❌ (не подтверждён)"
        text = f"👤 <b>Ваш профиль</b>\n\n🆔 ID: {user_id}\n📱 Телефон: {phone_display}\n📦 Заказов: {len(orders)}\n💰 Потрачено: {total_spent} ₽\n🛒 Товаров в корзине: {len(get_cart(user_id))}\n❤️ В избранном: {len(get_wishlist(user_id))}"
        if not verified:
            text += "\n\n⚠️ <b>Номер не подтверждён!</b> Используйте команду /verify"
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
        text = "📋 <b>Текущие товары (из Google Sheets)</b>\n\n"
        for p in products:
            text += f"• {p['name']} - {p['price']} ₽\n"
        text += "\n📝 <b>Для изменения товаров:</b>\nОтредактируйте Google Таблицу и нажмите «🔄 Обновить товары»"
        await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    
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
            InlineKeyboardButton(text=f"➖ {item['name']}", callback_data=f"cart_decr_{item['product_id']}"),
            InlineKeyboardButton(text=f"❌", callback_data=f"remove_from_cart_{item['product_id']}"),
            InlineKeyboardButton(text=f"➕", callback_data=f"cart_incr_{item['product_id']}")
        ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@dp.message()
async def handle_input(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
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
    
    await message.answer("🛍 Используйте кнопки для навигации.", reply_markup=main_menu_keyboard(is_admin))

# --- Настройка меню команд ---
async def set_commands():
    commands = [
        types.BotCommand(command="start", description="🛍 Главное меню"),
        types.BotCommand(command="catalog", description="📋 Каталог товаров"),
        types.BotCommand(command="verify", description="📱 Подтвердить номер телефона"),
    ]
    await bot.set_my_commands(commands)
    logger.info("✅ Меню команд установлено")

async def main():
    init_db()
    # Загружаем товары из Google Sheets при запуске
    if GOOGLE_SHEETS_URL:
        await load_products_from_google_sheets()
    else:
        logger.warning("GOOGLE_SHEETS_URL не задан, используем локальные товары")
    await set_commands()
    logger.info("🛍 БОТ-МАГАЗИН ЗАПУЩЕН с Google Sheets интеграцией!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
