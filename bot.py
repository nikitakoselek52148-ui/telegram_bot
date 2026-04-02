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

# Модели
TEXT_MODEL = "mistralai/mistral-small-3.2-24b-instruct:free"
VISION_MODEL = "google/gemma-3-27b-it:free"

# --- Клавиатура ---
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="chat"),
            InlineKeyboardButton(text="📸 Распознать фото", callback_data="photo")
        ],
        [
            InlineKeyboardButton(text="🎨 Сгенерировать картинку", callback_data="generate"),
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear")
        ],
        [
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

# --- Генерация картинки через бесплатный API (Hugging Face) ---
async def generate_image(prompt: str):
    """Генерирует картинку через бесплатный Hugging Face API"""
    
    # Используем бесплатную модель на Hugging Face
    api_url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-dev"
    
    headers = {
        "Authorization": f"Bearer {os.getenv('HF_TOKEN', 'hf_fake')}"
    }
    
    # Пробуем без токена сначала (публичные модели)
    async with aiohttp.ClientSession() as session:
        try:
            payload = {
                "inputs": prompt,
                "parameters": {
                    "width": 512,
                    "height": 512,
                    "num_inference_steps": 4
                }
            }
            
            async with session.post(
                api_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    logger.error(f"HF ошибка: {response.status}")
                    # Пробуем альтернативный сервис
                    return await generate_image_alt(prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации: {e}")
            return await generate_image_alt(prompt)

# Альтернативный бесплатный генератор (без ключа)
async def generate_image_alt(prompt: str):
    """Резервный генератор через pollinations (другой эндпоинт)"""
    encoded_prompt = prompt.replace(" ", "%20")
    url = f"https://pollinations.ai/p/{encoded_prompt}?width=512&height=512&seed=42&nologo=true"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    return None
    except Exception as e:
        logger.error(f"Alt ошибка: {e}")
        return None

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
        "• 📸 **Распознавать текст с фото** — отправь картинку\n"
        "• 🎨 **Генерировать картинки** — нажми кнопку или напиши 'нарисуй ...'\n\n"
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
    elif callback.data == "generate":
        await callback.message.answer(
            "🎨 **Что нарисовать?**\n\n"
            "Напиши в одном сообщении:\n"
            "`нарисуй кота в космосе`\n\n"
            "Доступные стили: реализм, аниме, фэнтези."
        )
    elif callback.data == "clear":
        if user_id in user_histories:
            user_histories[user_id] = [user_histories[user_id][0]]
        await callback.message.answer("🧹 История диалога очищена!")
    elif callback.data == "help":
        await callback.message.answer(
            "📖 **Помощь**\n\n"
            "• **Чат** — просто напиши текст\n"
            "• **Распознать фото** — отправь картинку\n"
            "• **Сгенерировать картинку** — напиши 'нарисуй ...'\n"
            "• **Очистить историю** — бот забудет предыдущие сообщения\n\n"
            "🔹 **Примеры запросов:**\n"
            "- `нарисуй закат на море`\n"
            "- `Что такое ИИ?`\n"
            "- `нарисуй кота в шляпе`"
        )
    await callback.answer()

# Генерация картинки
@dp.message(lambda msg: msg.text and msg.text.lower().startswith("нарисуй"))
async def handle_generate(message: types.Message):
    prompt = message.text.replace("нарисуй", "").strip()
    if not prompt:
        await message.answer("🎨 Напиши, что нарисовать после слова 'нарисуй'.\nНапример: `нарисуй закат на море`")
        return
    
    status_msg = await message.answer(f"🎨 Генерирую: *{prompt}*...\n\n⏳ Обычно 15-30 секунд.", parse_mode="Markdown")
    
    image_data = await generate_image(prompt)
    
    await status_msg.delete()
    
    if image_data:
        from aiogram.types import BufferedInputFile
        photo_file = BufferedInputFile(image_data, filename="image.png")
        await message.answer_photo(photo=photo_file, caption=f"🖼 *{prompt}*", parse_mode="Markdown")
    else:
        await message.answer("❌ Не удалось сгенерировать картинку. Попробуйте другой запрос.\n\nСовет: напишите запрос подробнее, например 'нарисуй рыжего кота в космическом скафандре'")

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
    logger.info("Бот запущен с Mistral и генерацией картинок!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
