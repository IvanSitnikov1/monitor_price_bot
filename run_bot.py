import asyncio
import random
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from main import bot, dp
from handlers import (
    common_router,
    products_router,
    admin_router,
    callbacks_router,
    auth_ozon_router
)
from core.database import setup_database
from utils.price_updater import update_all_prices
from loggers import logger
from utils.queue_worker import TaskQueue


async def schedule_price_update_with_delay(task_queue):
    """
    Запускает обновление цен со случайной задержкой в пределах 2-часового окна.

    Args:
        task_queue: Экземпляр TaskQueue для добавления задач
    """
    # Генерируем случайную задержку в пределах 10 минут
    delay_seconds = random.randint(0, 10 * 60)
    minutes = (delay_seconds % 3600) // 60
    seconds = delay_seconds % 60

    logger.info(f"Следующее обновление цен через {minutes}м {seconds}с")

    # Ждем случайное время
    await asyncio.sleep(delay_seconds)

    # Запускаем обновление цен
    await update_all_prices(task_queue)


async def start_bot():
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

    for t in range(0, 24, 1):
        scheduler.add_job(
            schedule_price_update_with_delay,
            trigger=CronTrigger(hour=t, minute=0),
            args=[task_queue],
            id=f"update_prices_{t}",
            name=f"Обновление цен {t}",
            replace_existing=True
        )

    scheduler.start()
    logger.info("Планировщик задач запущен")

    # Удаляем вебхуки и пропускаем старые сообщения
    logger.info("Удаление вебхуков...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Добавляем роутеры
    logger.info("Регистрация обработчиков...")
    dp.include_router(admin_router)
    dp.include_router(products_router)
    dp.include_router(auth_ozon_router)
    dp.include_router(callbacks_router)
    dp.include_router(common_router)  # Последний, т.к. содержит echo-обработчик

    # Запускаем опрос
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
