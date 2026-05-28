import logging
from datetime import datetime

from core.database import get_all_user_carts


logger = logging.getLogger(__name__)


async def update_all_prices(task_queue):
    """
    Добавляет корзины всех пользователей в очередь для обновления цен.

    Args:
        task_queue: Экземпляр TaskQueue для добавления задач
    """
    logger.info("=" * 60)
    logger.info(f"Начало обновления цен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    user_carts = await get_all_user_carts()

    if not user_carts:
        logger.info("Список корзин пуст. Обновление не требуется.")
        return

    logger.info(f"Найдено корзин для обновления: {len(user_carts)}")

    # Добавляем все корзины в очередь с низким приоритетом
    for cart in user_carts:
        await task_queue.add_task(
            task_type="update_cart",
            url=cart['cart_url'],
            priority=2,  # Низкий приоритет
            user_id=cart['user_id']
        )

    logger.info(f"Все корзины добавлены в очередь обновления")
    logger.info("=" * 60)
