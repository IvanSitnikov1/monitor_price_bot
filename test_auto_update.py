"""
Тестовый скрипт для проверки автоматического обновления цен.
Имитирует работу планировщика APScheduler.
"""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import config
from core.database import setup_database
from utils.price_updater import update_all_prices
from utils.logger import setup_logging
from utils.queue_worker import TaskQueue


logger = logging.getLogger(__name__)


async def test_auto_update():
    """Тестирует автоматическое обновление цен"""
    # Настраиваем логирование
    log_file = Path("logs/test_auto_update.log")
    setup_logging(level="INFO", log_file=log_file)

    logger.info("=" * 60)
    logger.info("ТЕСТ: Автоматическое обновление цен")
    logger.info("=" * 60)

    # Инициализируем базу данных
    logger.info("Инициализация базы данных...")
    await setup_database()
    logger.info("База данных готова")

    # Создаем бота с прокси
    if config.PROXY_URL:
        session = AiohttpSession(proxy=config.PROXY_URL)
        bot = Bot(token=config.BOT_TOKEN, session=session)
    else:
        bot = Bot(token=config.BOT_TOKEN)

    logger.info("Бот инициализирован")

    # Инициализируем очередь задач
    logger.info("Инициализация очереди задач...")
    task_queue = TaskQueue(bot)
    await task_queue.start()
    logger.info("Очередь задач запущена")

    try:
        # Запускаем обновление цен (аналогично планировщику)
        logger.info("Запуск обновления цен...")
        await update_all_prices(task_queue)
        logger.info("Обновление цен запущено, задачи добавлены в очередь")

        # Ждем, пока очередь обработает все задачи
        logger.info("Ожидание обработки всех задач...")

        # Проверяем размер очереди каждые 5 секунд
        while True:
            queue_size = task_queue.queue.qsize()
            logger.info(f"Задач в очереди: {queue_size}")

            if queue_size == 0:
                # Даем воркеру время завершить текущую задачу
                logger.info("Очередь пуста, ждем завершения текущей задачи...")
                await asyncio.sleep(20)

                # Проверяем еще раз
                if task_queue.queue.qsize() == 0:
                    logger.info("Все задачи обработаны!")
                    break

            await asyncio.sleep(5)

    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"Ошибка при тестировании: {e}", exc_info=True)
    finally:
        # Останавливаем очередь
        logger.info("Остановка очереди задач...")
        await task_queue.stop()
        logger.info("Очередь задач остановлена")

        # Закрываем сессию бота
        await bot.session.close()
        logger.info("Сессия бота закрыта")

    logger.info("=" * 60)
    logger.info("ТЕСТ ЗАВЕРШЕН")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_auto_update())
