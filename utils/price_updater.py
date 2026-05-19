import asyncio
import logging
import random
import re
from datetime import datetime

from utils.scraper import scrape_ozon_product
from core.database import get_all_products, update_product_price


logger = logging.getLogger(__name__)


async def update_all_prices():
    """
    Обновляет цены всех товаров в базе данных.

    - Рандомная задержка 0-5 секунд между запросами
    - До 3 попыток для каждого товара с задержкой 5 секунд между попытками
    - Логирование результатов в консоль
    """
    logger.info("=" * 60)
    logger.info(f"Начало обновления цен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    products = await get_all_products()

    if not products:
        logger.info("Список товаров пуст. Обновление не требуется.")
        return

    logger.info(f"Найдено товаров для обновления: {len(products)}")

    updated_count = 0
    failed_products = []

    for idx, product in enumerate(products, 1):
        logger.info(f"\n[{idx}/{len(products)}] Обрабатываю: {product['name'][:50]}...")

        # Рандомная задержка 0-5 секунд между запросами
        delay = random.uniform(0, 5)
        logger.info(f"Задержка перед запросом: {delay:.2f} сек")
        await asyncio.sleep(delay)

        # До 3 попыток
        success = False
        for attempt in range(1, 4):
            logger.info(f"Попытка {attempt}/3...")

            product_name, price_str = await scrape_ozon_product(product['url'])

            if product_name and price_str:
                # Извлекаем число из строки цены
                price_match = re.search(r'(\d+)', price_str)
                if price_match:
                    price_int = int(price_match.group(1))
                    await update_product_price(product['id'], price_int)
                    logger.info(f"✅ Успешно обновлено! Новая цена: {price_int} ₽")
                    updated_count += 1
                    success = True
                    break
                else:
                    logger.warning(f"⚠️ Не удалось распарсить цену: {price_str}")
            else:
                logger.warning(f"⚠️ Не удалось получить данные (попытка {attempt}/3)")

            # Задержка 5 секунд перед следующей попыткой
            if attempt < 3:
                logger.info("Ожидание 5 секунд перед повторной попыткой...")
                await asyncio.sleep(5)

        if not success:
            logger.error(f"❌ Не удалось обновить после 3 попыток: {product['name'][:50]}")
            failed_products.append(product['name'])

    # Итоговая статистика
    logger.info("\n" + "=" * 60)
    logger.info("ИТОГИ ОБНОВЛЕНИЯ")
    logger.info("=" * 60)
    logger.info(f"Всего товаров: {len(products)}")
    logger.info(f"Успешно обновлено: {updated_count}")
    logger.info(f"Не удалось обновить: {len(failed_products)}")

    if failed_products:
        logger.warning("\nСписок товаров с ошибками:")
        for name in failed_products:
            logger.warning(f"  - {name[:60]}")

    logger.info(f"\nЗавершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
