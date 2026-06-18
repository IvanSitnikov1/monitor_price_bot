from datetime import datetime

from core.database import get_all_products_for_update, get_all_user_carts
from core.models import Marketplace
from loggers import logger


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
    avito_products = await get_all_products_for_update(Marketplace.AVITO.value)

    if not user_carts and not avito_products:
        logger.info("Список корзин и товаров Avito пуст. Обновление не требуется.")
        return

    logger.info(f"Найдено корзин для обновления: {len(user_carts)}")

    # Добавляем все корзины в очередь с низким приоритетом
    for cart in user_carts:
        await task_queue.add_task(
            task_type="update_cart",
            url=cart['cart_url'],
            priority=2,  # Низкий приоритет
            user_id=cart['user_id'],
            marketplace=cart['marketplace']
        )

    logger.info(f"Найдено товаров Avito для обновления: {len(avito_products)}")
    for product in avito_products:
        await task_queue.add_task(
            task_type="update_product",
            url=product["url"],
            priority=2,
            user_id=product["user_id"],
            marketplace=Marketplace.AVITO.value,
            product_id=product["id"]
        )

    logger.info("Все корзины и товары Avito добавлены в очередь обновления")
    logger.info("=" * 60)
