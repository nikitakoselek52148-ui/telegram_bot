import asyncio
import os
import logging
import base64
import io
import threading
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Токены не найдены! Проверьте файл .env")

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
logger.info("Веб-сервер для Render запущен на порту 10000")

# --- Создаём бота и диспетчер ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище истории диалогов
user_histories = {}

# Модели
VISION_MODEL = "google/gemini-2.5-flash-lite-preview-03-25:free"
TEXT_MODEL = "openrouter/free"

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

async def analyze_photo_with_vision(image_bytes: bytes, user_question: str = None) -> str:
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    if user_question:
        prompt = f"Посмотри на это фото и ответь на вопрос: {user_question}"
    else:
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
            "max_tokens": 2000,
            "temperature": 0.3
        }
        
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=90)
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"]
                else:
                    error_text = await response.text()
                    logger.error(f"Vision API ошибка: {response.status} - {error_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Vision ошибка: {e}")
            return None

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 Привет! Я бот с доступом к нейросетям через OpenRouter.\n\n"
        "📝 **Что я умею:**\n"
        "• Отвечать на текстовые сообщения\n"
        "• 📸 Распознавать текст с фотографий\n"
        "• Отвечать на вопросы по фото\n\n"
        "**Как пользоваться:**\n"
        "• Просто напиши текст — я отвечу\n"
        "• Отправь фото — я распознаю текст\n"
        "• Отправь фото с вопросом в подписи — отвечу по фото\n\n"
        "/clear — очистить историю диалога"
    )

@dp.message(Command("clear"))
async def clear_history(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        user_histories[user_id] = [user_histories[user_id][0]]
    await message.answer("🧹 История диалога очищена!")

@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    
    await bot.send_chat_action(user_id, "typing")
    user_question = message.caption if message.caption else None
    
    status_msg = await message.answer("📷 Получил фото! Анализирую...")
    
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    photo_bytes = io.BytesIO()
    await bot.download_file(file.file_path, destination=photo_bytes)
    photo_bytes.seek(0)
    
    result = await analyze_photo_with_vision(photo_bytes.getvalue(), user_question)
    
    await status_msg.delete()
    
    if result:
        if len(result) > 4000:
            result = result[:4000] + "\n\n...(текст обрезан)"
        
        if user_question:
            await message.answer(f"📸 **Ответ по фото:**\n\n{result}")
        else:
            await message.answer(f"📝 **Распознанный текст:**\n\n{result}")
    else:
        await message.answer("❌ Не удалось распознать текст на фото. Попробуйте сделать фото чётче или используйте другое освещение.")

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text
    
    await bot.send_chat_action(user_id, "typing")
    response = await get_ai_response(user_id, user_text)
    await message.answer(response)

async def main():
    logger.info("Бот запущен с поддержкой фото и веб-сервером для Render!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
