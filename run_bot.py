import asyncio
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from main import bot, dp
from handlers import (
    common_router,
    products_router,
    admin_router,
    callbacks_router,
    settings_router,
    auth_router
)
from core.database import setup_database
from config import config
from utils.price_updater import update_all_prices
from loggers import logger
from utils.process_lock import ProcessLock, ProcessLockError
from utils.queue_worker import TaskQueue
from utils.reply_keyboard_sync import sync_reply_keyboards


async def schedule_price_update_with_delay(task_queue):
    """
    Запускает обновление цен со случайной задержкой в пределах 2-часового окна.

    Args:
        task_queue: Экземпляр TaskQueue для добавления задач
    """
    # Генерируем случайную задержку в пределах 120 минут
    delay_seconds = random.randint(0, 120 * 60)
    minutes = (delay_seconds % 3600) // 60
    seconds = delay_seconds % 60

    logger.info(f"Следующее обновление цен через {minutes}м {seconds}с")

    # Ждем случайное время
    await asyncio.sleep(delay_seconds)

    # Запускаем обновление цен
    await update_all_prices(task_queue)


class BotStartupError(RuntimeError):
    """Ошибка безопасного запуска рабочего экземпляра бота."""


async def _run_bot():
    """Запускает сервис и освобождает очередь при ошибке любого этапа запуска."""
    task_queue = TaskQueue(bot)
    scheduler = AsyncIOScheduler()
    try:
        await setup_database()
        await task_queue.start()
        bot.task_queue = task_queue

        for hour in (7, 15, 22):
            scheduler.add_job(
                schedule_price_update_with_delay,
                trigger=CronTrigger(hour=hour, minute=0),
                args=[task_queue],
                id=f'update_prices_{hour}',
                name=f'Обновление цен {hour}',
                replace_existing=True,
            )
        scheduler.start()
        await bot.delete_webhook(drop_pending_updates=True)

        await sync_reply_keyboards(bot)

        dp.include_router(admin_router)
        dp.include_router(products_router)
        dp.include_router(auth_router)
        dp.include_router(callbacks_router)
        dp.include_router(settings_router)
        dp.include_router(common_router)
        logger.info('Бот готов к работе')
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        task_queue.accepting = False
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
                # AsyncIOScheduler завершает задания на следующем шаге цикла событий.
                await asyncio.sleep(0)
        finally:
            try:
                await task_queue.stop()
            finally:
                await bot.session.close()


async def start_bot():
    """Запускает единственный рабочий экземпляр бота вне режима диагностики."""
    if config.OZON_DIAGNOSTICS_ENABLED:
        raise BotStartupError(
            "Обычный запуск бота запрещён: включён диагностический режим Ozon. "
            "Запустите диагностический сценарий или отключите OZON_DIAGNOSTICS_ENABLED."
        )

    with ProcessLock():
        await _run_bot()


if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (BotStartupError, ProcessLockError) as error:
        logger.error(str(error))
        raise SystemExit(1) from None
