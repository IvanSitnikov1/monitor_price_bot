import asyncio

from main import bot, dp
from handlers import main_router
from core.database import setup_database


async def start_bot():
    # Инициализируем базу данных
    await setup_database()
    # Удаляем вебхуки и пропускаем старые сообщения
    await bot.delete_webhook(drop_pending_updates=True)
    # Добавляем роутеры
    dp.include_router(main_router)
    # Запускаем опрос
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start_bot())
