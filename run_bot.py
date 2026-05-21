import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from main import bot, dp
from handlers import main_router
from core.database import setup_database
from utils.price_updater import update_all_prices
from utils.logger import setup_logging
from utils.queue_worker import TaskQueue


logger = logging.getLogger(__name__)


async def start_bot():
    # Настраиваем логирование
    log_file = Path("logs/bot.log")
    setup_logging(level="INFO", log_file=log_file)

    logger.info("=" * 60)
    logger.info("Запуск бота мониторинга цен Ozon")
    logger.info("=" * 60)

    # Инициализируем базу данных
    logger.info("Инициализация базы данных...")
    await setup_database()
    logger.info("База данных готова")

    # Инициализируем очередь задач
    logger.info("Инициализация очереди задач...")
    task_queue = TaskQueue(bot)
    await task_queue.start()
    logger.info("Очередь задач запущена")

    # Сохраняем ссылку на очередь в bot для доступа из хендлеров
    bot.task_queue = task_queue

    # Настраиваем планировщик задач
    scheduler = AsyncIOScheduler()

    # Обновление цен с 0 до 2 часов (запуск в 00:00)
    scheduler.add_job(
        lambda: asyncio.create_task(update_all_prices(task_queue)),
        trigger=CronTrigger(hour=0, minute=0),
        id="update_prices_night",
        name="Обновление цен (ночь)",
        replace_existing=True
    )

    # Обновление цен с 12 до 14 часов (запуск в 12:00)
    scheduler.add_job(
        lambda: asyncio.create_task(update_all_prices(task_queue)),
        trigger=CronTrigger(hour=12, minute=0),
        id="update_prices_day",
        name="Обновление цен (день)",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Планировщик задач запущен")
    logger.info("Обновление цен: 00:00 и 12:00 ежедневно")

    # Удаляем вебхуки и пропускаем старые сообщения
    logger.info("Удаление вебхуков...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Добавляем роутеры
    logger.info("Регистрация обработчиков...")
    dp.include_router(main_router)

    # Запускаем опрос
    logger.info("Запуск polling...")
    logger.info("Бот готов к работе!")

    try:
        await dp.start_polling(bot)
    finally:
        # Останавливаем очередь при завершении
        logger.info("Остановка очереди задач...")
        await task_queue.stop()
        logger.info("Очередь задач остановлена")


if __name__ == "__main__":
    asyncio.run(start_bot())
