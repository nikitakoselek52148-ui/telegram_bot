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
logger.info("Веб-сервер запущен")

# --- Бот ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Обработчик фото с подробным логированием
@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: types.Message):
    await message.answer("📷 Получил фото, начинаю обработку...")
    logger.info(f"Получено фото от пользователя {message.from_user.id}")
    
    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        logger.info(f"ID файла: {file.file_id}, размер: {file.file_size}")
        
        photo_bytes = io.BytesIO()
        await bot.download_file(file.file_path, destination=photo_bytes)
        photo_bytes.seek(0)
        
        # Конвертируем в base64
        image_base64 = base64.b64encode(photo_bytes.getvalue()).decode('utf-8')
        logger.info(f"Base64 длина: {len(image_base64)} символов")
        
        # Запрос к OpenRouter
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "google/gemini-2.0-flash-exp:free",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Распознай и напиши весь текст, который видишь на этом изображении. Если текста нет, напиши 'Текст не найден'."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 1000
            }
            
            logger.info("Отправляю запрос в OpenRouter...")
            
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            ) as resp:
                result = await resp.json()
                logger.info(f"Статус ответа: {resp.status}")
                logger.info(f"Ответ API: {result}")
                
                if resp.status != 200:
                    error_msg = result.get("error", {}).get("message", "Неизвестная ошибка")
                    await message.answer(f"❌ Ошибка API ({resp.status}): {error_msg}")
                else:
                    text = result["choices"][0]["message"]["content"]
                    await message.answer(f"📝 **Распознанный текст:**\n\n{text}")
                    
    except Exception as e:
        logger.error(f"Ошибка в обработчике: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "📸 Отправь мне фото с текстом, и я его распознаю!\n\n"
        "Попробуй отправить фото с чётким, крупным текстом."
    )

@dp.message()
async def handle_text(message: types.Message):
    await message.answer("Отправь мне фото с текстом, а не просто текст.")

async def main():
    logger.info("Бот запущен с улучшенным логированием!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
