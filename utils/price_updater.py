import logging
from datetime import datetime

from core.database import get_all_products_for_update


logger = logging.getLogger(__name__)


async def update_all_prices(task_queue):
    """
    Добавляет все товары в очередь для обновления цен.

    Args:
        task_queue: Экземпляр TaskQueue для добавления задач
    """
    logger.info("=" * 60)
    logger.info(f"Начало обновления цен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    products = await get_all_products_for_update()

    if not products:
        logger.info("Список товаров пуст. Обновление не требуется.")
        return

    logger.info(f"Найдено товаров для обновления: {len(products)}")

    # Добавляем все товары в очередь с низким приоритетом
    for product in products:
        await task_queue.add_task(
            task_type="update_price",
            url=product['url'],
            priority=2,  # Низкий приоритет
            product_id=product['id'],
            product_name=product['name']
        )

    logger.info(f"Все товары добавлены в очередь обновления")
    logger.info("=" * 60)
