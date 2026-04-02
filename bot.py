import asyncio
import os
import logging
import json
import threading
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "1912287053").split(",") if id.strip()]

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

# --- Данные магазина (в памяти, для продакшена замените на БД) ---

products = {
    1: {"id": 1, "name": "Кроссовки Nike Air", "price": 8900, "desc": "Спортивные кроссовки, размер 40-45", "photo": None},
    2: {"id": 2, "name": "Футболка Adidas", "price": 2500, "desc": "Хлопковая футболка, размеры S-XXL", "photo": None},
    3: {"id": 3, "name": "Кепка New Era", "price": 1800, "desc": "Бейсболка, регулируемая", "photo": None},
    4: {"id": 4, "name": "Рюкзак Puma", "price": 4200, "desc": "Вместительный рюкзак для города", "photo": None},
}

carts = {}  # {user_id: {product_id: quantity}}
orders = {}  # {user_id: [order1, order2, ...]}

# Множество пользователей, ожидающих ввода нового товара
awaiting_product = set()

# --- Клавиатуры ---

def main_menu_keyboard(is_admin: bool = False):
    """Главное меню"""
    if is_admin:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"),
                InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")
            ],
            [
                InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"),
                InlineKeyboardButton(text="👤 Профиль", callback_data="profile")
            ],
            [
                InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_panel")
            ]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Каталог", callback_data="catalog"),
                InlineKeyboardButton(text="🛒 Корзина", callback_data="cart")
            ],
            [
                InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"),
                InlineKeyboardButton(text="👤 Профиль", callback_data="profile")
            ]
        ])
    return keyboard

def admin_panel_keyboard():
    """Панель администратора"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")
        ],
        [
            InlineKeyboardButton(text="📋 Управление товарами", callback_data="admin_products"),
            InlineKeyboardButton(text="📦 Все заказы", callback_data="admin_orders")
        ],
        [
            InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")
        ]
    ])
    return keyboard

def product_card_keyboard(product_id: int, in_cart: bool = False):
    """Клавиатура для карточки товара"""
    buttons = []
    
    if in_cart:
        buttons.append([InlineKeyboardButton(text="❌ Удалить из корзины", callback_data=f"remove_from_cart_{product_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add_to_cart_{product_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад к каталогу", callback_data="catalog")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cart_keyboard():
    """Клавиатура для корзины"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    return keyboard

def order_confirmation_keyboard():
    """Клавиатура подтверждения заказа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cart")
        ]
    ])
    return keyboard

# --- Основные функции ---

async def show_catalog(callback: CallbackQuery):
    """Показывает список товаров"""
    if not products:
        await callback.message.edit_text("📭 **Каталог пуст**", reply_markup=main_menu_keyboard(callback.from_user.id in ADMIN_IDS), parse_mode="Markdown")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for product in products.values():
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"{product['name']} - {product['price']} ₽", callback_data=f"product_{product['id']}")
        ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    
    await callback.message.edit_text("📋 **Каталог товаров**\n\nВыберите товар:", reply_markup=keyboard, parse_mode="Markdown")

async def show_product(callback: CallbackQuery, product_id: int, user_id: int, in_cart: bool = None):
    """Показывает карточку товара"""
    product = products.get(product_id)
    if not product:
        await callback.message.answer("❌ Товар не найден")
        return
    
    if in_cart is None:
        in_cart = user_id in carts and product_id in carts[user_id]
    
    text = f"**{product['name']}**\n\n"
    text += f"💰 Цена: {product['price']} ₽\n"
    text += f"📝 {product['desc']}\n"
    if in_cart:
        quantity = carts[user_id].get(product_id, 0)
        text += f"\n🛒 В корзине: {quantity} шт."
    
    await callback.message.edit_text(text, reply_markup=product_card_keyboard(product_id, in_cart), parse_mode="Markdown")

async def show_cart(callback: CallbackQuery, user_id: int):
    """Показывает содержимое корзины"""
    cart = carts.get(user_id, {})
    
    if not cart:
        await callback.message.edit_text("🛒 **Корзина пуста**\n\nДобавьте товары через каталог.", reply_markup=main_menu_keyboard(user_id in ADMIN_IDS), parse_mode="Markdown")
        return
    
    total = 0
    text = "🛒 **Ваша корзина**\n\n"
    
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product:
            subtotal = product['price'] * quantity
            total += subtotal
            text += f"• {product['name']} x{quantity} = {subtotal} ₽\n"
    
    text += f"\n**Итого: {total} ₽**"
    
    await callback.message.edit_text(text, reply_markup=cart_keyboard(), parse_mode="Markdown")

async def checkout(callback: CallbackQuery, user_id: int):
    """Оформление заказа"""
    cart = carts.get(user_id, {})
    
    if not cart:
        await callback.message.answer("🛒 Корзина пуста. Добавьте товары через каталог.", show_alert=True)
        await show_cart(callback, user_id)
        return
    
    total = 0
    items_text = ""
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product:
            subtotal = product['price'] * quantity
            total += subtotal
            items_text += f"• {product['name']} x{quantity} = {subtotal} ₽\n"
    
    text = f"📦 **Подтверждение заказа**\n\n"
    text += items_text
    text += f"\n**Итого: {total} ₽**\n\n"
    text += "Подтвердите заказ для оформления."
    
    await callback.message.edit_text(text, reply_markup=order_confirmation_keyboard(), parse_mode="Markdown")

async def confirm_order(callback: CallbackQuery, user_id: int):
    """Подтверждение и сохранение заказа"""
    cart = carts.get(user_id, {})
    
    if not cart:
        await callback.message.answer("❌ Корзина пуста")
        return
    
    total = 0
    items = []
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product:
            subtotal = product['price'] * quantity
            total += subtotal
            items.append({
                "product_id": product_id,
                "name": product['name'],
                "price": product['price'],
                "quantity": quantity,
                "subtotal": subtotal
            })
    
    order_num = len(orders.get(user_id, [])) + 1
    order = {
        "id": order_num,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "items": items,
        "total": total,
        "status": "🟡 Ожидает обработки"
    }
    
    if user_id not in orders:
        orders[user_id] = []
    orders[user_id].append(order)
    
    # Очищаем корзину
    carts[user_id] = {}
    
    text = f"✅ **Заказ #{order_num} оформлен!**\n\n"
    text += f"📅 Дата: {order['date']}\n"
    text += f"💰 Сумма: {total} ₽\n\n"
    text += "Статус заказа можно отслеживать в разделе «Мои заказы»."
    
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(user_id in ADMIN_IDS), parse_mode="Markdown")
    
    # Уведомление администраторам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"🆕 **Новый заказ!**\n\nПользователь: {callback.from_user.username or callback.from_user.id}\nСумма: {total} ₽\nНомер заказа: #{order_num}")
        except:
            pass

async def show_orders(callback: CallbackQuery, user_id: int):
    """Показывает историю заказов"""
    user_orders = orders.get(user_id, [])
    
    if not user_orders:
        await callback.message.edit_text("📦 **У вас пока нет заказов**", reply_markup=main_menu_keyboard(user_id in ADMIN_IDS), parse_mode="Markdown")
        return
    
    text = "📦 **Ваши заказы**\n\n"
    for order in user_orders[-5:]:  # Последние 5 заказов
        text += f"**Заказ #{order['id']}**\n"
        text += f"📅 {order['date']}\n"
        text += f"💰 {order['total']} ₽\n"
        text += f"Статус: {order['status']}\n"
        text += "─" * 20 + "\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def show_profile(callback: CallbackQuery, user_id: int):
    """Показывает профиль пользователя"""
    total_spent = 0
    total_orders = len(orders.get(user_id, []))
    
    for order in orders.get(user_id, []):
        total_spent += order['total']
    
    text = f"👤 **Ваш профиль**\n\n"
    text += f"🆔 ID: {user_id}\n"
    text += f"📦 Заказов: {total_orders}\n"
    text += f"💰 Потрачено: {total_spent} ₽\n"
    text += f"🛒 Товаров в корзине: {len(carts.get(user_id, {}))}\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

# --- Админ-функции ---

async def show_admin_stats(callback: CallbackQuery):
    """Статистика для администратора"""
    total_users = len(set(list(carts.keys()) + list(orders.keys())))
    total_orders = sum(len(ords) for ords in orders.values())
    total_revenue = sum(order['total'] for ords in orders.values() for order in ords)
    
    text = f"📊 **Статистика магазина**\n\n"
    text += f"👥 Всего пользователей: {total_users}\n"
    text += f"📦 Всего заказов: {total_orders}\n"
    text += f"💰 Выручка: {total_revenue} ₽\n"
    text += f"🛒 Товаров в каталоге: {len(products)}\n"
    text += f"🛍 Активных корзин: {len([c for c in carts.values() if c])}\n"
    
    await callback.message.edit_text(text, reply_markup=admin_panel_keyboard(), parse_mode="Markdown")

async def show_admin_products(callback: CallbackQuery):
    """Список товаров для администратора (с возможностью удаления)"""
    if not products:
        await callback.message.edit_text("📭 **Каталог пуст**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
        return
    
    text = "📋 **Управление товарами**\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for product in products.values():
        text += f"• **{product['name']}** - {product['price']} ₽\n"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"❌ Удалить {product['name']}", callback_data=f"admin_delete_product_{product['id']}")
        ])
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def show_admin_orders(callback: CallbackQuery):
    """Все заказы для администратора"""
    all_orders = []
    for user_id, user_orders in orders.items():
        for order in user_orders:
            all_orders.append((user_id, order))
    
    if not all_orders:
        await callback.message.edit_text("📦 **Нет заказов**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
        return
    
    text = "📦 **Все заказы**\n\n"
    for user_id, order in all_orders[-10:]:  # Последние 10 заказов
        text += f"👤 Пользователь: {user_id}\n"
        text += f"📦 Заказ #{order['id']}\n"
        text += f"📅 {order['date']}\n"
        text += f"💰 {order['total']} ₽\n"
        text += f"Статус: {order['status']}\n"
        text += "─" * 20 + "\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def add_new_product(message: types.Message):
    """Добавление нового товара админом"""
    try:
        parts = message.text.split("|")
        if len(parts) >= 3:
            name = parts[0].strip()
            price = int(parts[1].strip())
            desc = parts[2].strip()
            
            new_id = max(products.keys()) + 1 if products else 1
            products[new_id] = {
                "id": new_id,
                "name": name,
                "price": price,
                "desc": desc,
                "photo": None
            }
            
            await message.answer(f"✅ Товар «{name}» добавлен! ID: {new_id}\n\nЦена: {price} ₽\nОписание: {desc}")
            
            # Уведомление всем админам
            for admin_id in ADMIN_IDS:
                if admin_id != message.from_user.id:
                    try:
                        await bot.send_message(admin_id, f"🆕 Новый товар добавлен пользователем @{message.from_user.username or message.from_user.id}\n\n{name} - {price} ₽")
                    except:
                        pass
        else:
            await message.answer("❌ Неверный формат. Используйте:\n`Название | Цена | Описание`\n\nПример:\n`Кроссовки Nike | 8900 | Спортивные кроссовки`", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Цена должна быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    welcome_text = (
        "🛍 **Добро пожаловать в магазин!**\n\n"
        "Здесь вы можете:\n"
        "• 📋 Посмотреть каталог товаров\n"
        "• 🛒 Добавить товары в корзину\n"
        "• ✅ Оформить заказ\n\n"
        "Используйте кнопки ниже для навигации."
    )
    
    if is_admin:
        welcome_text += "\n\n🔐 **Вы вошли как администратор**"
    
    await message.answer(welcome_text, reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")

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
        await show_catalog(callback)
    
    elif data.startswith("product_"):
        product_id = int(data.split("_")[1])
        await show_product(callback, product_id, user_id)
    
    # --- Корзина ---
    elif data.startswith("add_to_cart_"):
        product_id = int(data.split("_")[3])
        if user_id not in carts:
            carts[user_id] = {}
        carts[user_id][product_id] = carts[user_id].get(product_id, 0) + 1
        await callback.message.answer(f"✅ Товар добавлен в корзину!")
        await show_product(callback, product_id, user_id, True)
    
    elif data.startswith("remove_from_cart_"):
        product_id = int(data.split("_")[3])
        if user_id in carts and product_id in carts[user_id]:
            del carts[user_id][product_id]
            await callback.message.answer(f"❌ Товар удалён из корзины")
            await show_product(callback, product_id, user_id, False)
        else:
            await show_product(callback, product_id, user_id, False)
    
    elif data == "cart":
        await show_cart(callback, user_id)
    
    elif data == "clear_cart":
        if user_id in carts:
            carts[user_id] = {}
        await callback.message.edit_text("🛒 **Корзина очищена**", reply_markup=main_menu_keyboard(is_admin), parse_mode="Markdown")
    
    elif data == "checkout":
        await checkout(callback, user_id)
    
    elif data == "confirm_order":
        await confirm_order(callback, user_id)
    
    # --- Профиль и заказы ---
    elif data == "my_orders":
        await show_orders(callback, user_id)
    
    elif data == "profile":
        await show_profile(callback, user_id)
    
    # --- Админ функции ---
    elif data == "admin_stats" and is_admin:
        await show_admin_stats(callback)
    
    elif data == "admin_products" and is_admin:
        await show_admin_products(callback)
    
    elif data == "admin_orders" and is_admin:
        await show_admin_orders(callback)
    
    elif data == "admin_add_product" and is_admin:
        awaiting_product.add(user_id)
        await callback.message.edit_text(
            "📝 **Добавление товара**\n\n"
            "Отправьте данные в формате:\n"
            "`Название | Цена | Описание`\n\n"
            "**Пример:**\n"
            "`Кроссовки Nike | 8900 | Спортивные кроссовки, размер 40-45`\n\n"
            "Отправьте сообщение с данными товара:",
            reply_markup=admin_panel_keyboard(),
            parse_mode="Markdown"
        )
    
    elif data.startswith("admin_delete_product_") and is_admin:
        product_id = int(data.split("_")[3])
        if product_id in products:
            product_name = products[product_id]['name']
            del products[product_id]
            await callback.message.answer(f"✅ Товар «{product_name}» удалён")
            await show_admin_products(callback)

# --- Обработка текстовых сообщений (добавление товара) ---
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    
    # Добавление нового товара
    if user_id in awaiting_product:
        awaiting_product.remove(user_id)
        await add_new_product(message)
        return
    
    # Обычное сообщение от пользователя
    is_admin = user_id in ADMIN_IDS
    await message.answer("🛍 Используйте кнопки для навигации по магазину.", reply_markup=main_menu_keyboard(is_admin))

# --- Запуск ---
async def main():
    logger.info("=" * 50)
    logger.info("🛍 БОТ-МАГАЗИН ЗАПУЩЕН")
    logger.info(f"🤖 Бот: @{(await bot.get_me()).username}")
    logger.info(f"👑 Администраторы: {ADMIN_IDS}")
    logger.info(f"📦 Товаров в каталоге: {len(products)}")
    logger.info("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
