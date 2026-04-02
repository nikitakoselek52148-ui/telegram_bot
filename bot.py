import asyncio
import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("Токены не найдены! Проверьте файл .env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_histories = {}

async def get_ai_response(user_id: int, user_message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный AI-ассистент. Отвечай на русском языке."}
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
            "model": "openrouter/free",
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
                    return f"❌ Ошибка API: {response.status}. Попробуйте позже."
                    
        except asyncio.TimeoutError:
            return "⏰ Превышено время ожидания ответа. Попробуйте ещё раз."
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return f"❌ Произошла ошибка: {e}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 Привет! Я бот с доступом к нейросетям через OpenRouter.\n\n"
        "Просто напиши мне любое сообщение, и я отвечу!\n\n"
        "/clear — очистить историю диалога"
    )

@dp.message(Command("clear"))
async def clear_history(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        user_histories[user_id] = [user_histories[user_id][0]]
    await message.answer("🧹 История диалога очищена!")

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