import asyncio
import os
import logging
import base64
import io
import threading
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import aiohttp
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Токены не найдены!")

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

user_histories = {}

# Используем стабильные модели
TEXT_MODEL = "nvidia/gpt-oss-120b:free"
VISION_MODEL = "google/gemma-3-27b-it:free"

# --- Клавиатура ---
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="chat"),
            InlineKeyboardButton(text="📸 Распознать фото", callback_data="photo")
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help")
        ]
    ])
    return keyboard

# --- Текстовый чат ---
async def get_ai_response(user_id: int, user_message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный AI-ассистент. Отвечай на русском языке кратко и по делу."}
        ]
    
    user_histories[user_id].append({"role": "user", "content": user_message})
    
    if len(user_histories[user_id]) > 20:
        user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-19:]
    
    async with aiohttp.ClientSession() as session:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": TEXT_MODEL,
            "messages": user_histories[user_id],
            "max_tokens": 1000,
            "temperature": 0.7
        }
        
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    ai_response = result["choices"][0]["message"]["content"]
                    user_histories[user_id].append({"role": "assistant", "content": ai_response})
                    return ai_response
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка API: {response.status} - {error_text}")
                    return f"❌ Ошибка API: {response.status}"
                    
        except asyncio.TimeoutError:
            return "⏰ Превышено время ожидания. Попробуйте ещё раз."
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return f"❌ Ошибка: {e}"

# --- Распознавание фото ---
async def analyze_photo(image_bytes: bytes) -> str:
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt = "Извлеки и распознай весь текст с этого фото. Напиши только распознанный текст, без лишних комментариев."
    
    async with aiohttp.ClientSession() as session:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                        }
                    ]
                }
            ],
            "max_tokens": 1000
        }
        
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"]
                else:
                    logger.error(f"Vision ошибка: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Vision ошибка: {e}")
            return None

# --- Обработчики ---

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 **Привет! Я бот с искусственным интеллектом!**\n\n"
        "Вот что я умею:\n"
        "• 💬 **Общаться** — просто напиши мне сообщение\n"
        "• 📸 **Распознавать текст с фото** — отправь картинку\n\n"
        "👇 **Используй кнопки ниже**",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("clear"))
async def clear_history_command(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        user_histories[user_id] = [user_histories[user_id][0]]
    await message.answer("🧹 История диалога очищена!")

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if callback.data == "chat":
        await callback.message.answer("💬 Просто напиши мне любое сообщение, и я отвечу!")
    elif callback.data == "photo":
        await callback.message.answer("📸 Отправь мне фото с текстом, и я распознаю его!")
    elif callback.data == "clear":
        if user_id in user_histories:
            user_histories[user_id] = [user_histories[user_id][0]]
        await callback.message.answer("🧹 История диалога очищена!")
    elif callback.data == "help":
        await callback.message.answer(
            "📖 **Помощь**\n\n"
            "• **Чат** — просто напиши текст\n"
            "• **Распознать фото** — отправь картинку\n"
            "• **Очистить историю** — бот забудет предыдущие сообщения\n\n"
            "🔹 **Примеры запросов:**\n"
            "- `Что такое ИИ?`\n"
            "- Отправь фото чека\n"
            "- `Расскажи шутку`"
        )
    await callback.answer()

@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: types.Message):
    await bot.send_chat_action(message.from_user.id, "typing")
    
    status_msg = await message.answer("📷 Получил фото! Распознаю текст...")
    
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    photo_bytes = io.BytesIO()
    await bot.download_file(file.file_path, destination=photo_bytes)
    photo_bytes.seek(0)
    
    result = await analyze_photo(photo_bytes.getvalue())
    
    await status_msg.delete()
    
    if result:
        if len(result) > 4000:
            result = result[:4000] + "\n\n...(текст обрезан)"
        await message.answer(f"📝 **Распознанный текст:**\n\n{result}", parse_mode="Markdown")
    else:
        await message.answer("❌ Не удалось распознать текст. Попробуйте фото с более чётким текстом.")

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text
    
    await bot.send_chat_action(user_id, "typing")
    response = await get_ai_response(user_id, user_text)
    await message.answer(response)

async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
