import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from main import bot, dp
from handlers import main_router
from core.database import setup_database
from utils.price_updater import update_all_prices


logger = logging.getLogger(__name__)


async def start_bot():
    # Инициализируем базу данных
    await setup_database()

    # Настраиваем планировщик задач
    scheduler = AsyncIOScheduler()

    # Обновление цен с 0 до 2 часов (запуск в 00:00)
    scheduler.add_job(
        update_all_prices,
        trigger=CronTrigger(hour=0, minute=0),
        id="update_prices_night",
        name="Обновление цен (ночь)",
        replace_existing=True
    )

    # Обновление цен с 12 до 14 часов (запуск в 12:00)
    scheduler.add_job(
        update_all_prices,
        trigger=CronTrigger(hour=12, minute=0),
        id="update_prices_day",
        name="Обновление цен (день)",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Планировщик задач запущен")
    logger.info("Обновление цен: 00:00 и 12:00 ежедневно")

    # Удаляем вебхуки и пропускаем старые сообщения
    await bot.delete_webhook(drop_pending_updates=True)
    # Добавляем роутеры
    dp.include_router(main_router)
    # Запускаем опрос
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start_bot())
