import asyncio
import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
from flask import Flask
import threading

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

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

# Простой обработчик команды /start
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 **Привет! Я бот Рифлейн!**\n\n"
        "Я простой бот, который работает и отвечает на сообщения.\n\n"
        "Просто напиши мне что-нибудь!",
        parse_mode="Markdown"
    )

# Обработчик текстовых сообщений
@dp.message()
async def echo_message(message: types.Message):
    await message.answer(f"Ты написал: {message.text}")

async def main():
    logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
