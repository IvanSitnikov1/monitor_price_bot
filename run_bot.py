import asyncio
import logging
import random
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


async def schedule_price_update_with_delay(task_queue, time_window: str):
    """
    Запускает обновление цен со случайной задержкой в пределах 2-часового окна.

    Args:
        task_queue: Экземпляр TaskQueue для добавления задач
        time_window: Название временного окна для логирования ("ночь" или "день")
    """
    # Генерируем случайную задержку от 0 до 120 минут (2 часа)
    delay_seconds = random.randint(0, 120 * 60)

    hours = delay_seconds // 3600
    minutes = (delay_seconds % 3600) // 60
    seconds = delay_seconds % 60

    logger.info(f"Следующее обновление цен ({time_window}) через {hours}ч {minutes}м {seconds}с")

    # Ждем случайное время
    await asyncio.sleep(delay_seconds)

    # Запускаем обновление цен
    await update_all_prices(task_queue)


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

    # Обновление цен с 0 до 2 часов (запуск в 00:00 + случайная задержка)
    scheduler.add_job(
        schedule_price_update_with_delay,
        trigger=CronTrigger(hour=0, minute=0),
        args=[task_queue, "ночь"],
        id="update_prices_night",
        name="Обновление цен (ночь)",
        replace_existing=True
    )

    # Обновление цен с 12 до 14 часов (запуск в 12:00 + случайная задержка)
    scheduler.add_job(
        schedule_price_update_with_delay,
        trigger=CronTrigger(hour=12, minute=0),
        args=[task_queue, "день"],
        id="update_prices_day",
        name="Обновление цен (день)",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Планировщик задач запущен")
    logger.info("Обновление цен: 00:00-02:00 и 12:00-14:00 ежедневно (случайное время)")

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
