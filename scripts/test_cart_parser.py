"""
Тестовый скрипт для парсинга корзины Ozon.
Запускается каждые 1-2 часа в рандомное время и сохраняет данные в файл.
"""
import asyncio
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Директория для сохранения результатов
RESULTS_DIR = Path("cart_results")
RESULTS_DIR.mkdir(exist_ok=True)

# URL корзины
CART_URL = "https://www.ozon.ru/cart"

# CDP endpoint
CDP_URL = "http://localhost:9222"


async def parse_cart() -> list[dict]:
    """
    Парсит корзину Ozon и возвращает список товаров с ценами.

    Returns:
        Список словарей с данными товаров: [{"name": str, "price": str}, ...]
    """
    logger.info("Начало парсинга корзины")

    async with async_playwright() as p:
        try:
            # Подключаемся к существующему Chrome через CDP
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            logger.info("Подключились к Chrome через CDP")

            contexts = browser.contexts
            if not contexts:
                logger.error("Нет доступных контекстов")
                return []

            context = contexts[0]

        except Exception as e:
            logger.error(f"Не удалось подключиться к Chrome: {e}")
            logger.info("Убедитесь, что Chrome запущен с --remote-debugging-port=9222")
            return []

        # Получаем или создаём страницу
        if context.pages:
            # Закрываем все существующие страницы
            for existing_page in context.pages:
                await existing_page.close()
            logger.info("Закрыты существующие вкладки")

        # Создаём новую чистую страницу
        page = await context.new_page()

        # Применяем stealth ДО перехода на Ozon
        stealth = Stealth(
            navigator_languages_override=('ru-RU', 'ru', 'en-US', 'en'),
            navigator_platform_override='Linux x86_64',
        )
        await stealth.apply_stealth_async(page)
        logger.info("Stealth-патчи применены к новой вкладке")

        # Переходим на страницу корзины
        logger.info(f"Переход на {CART_URL}")
        await page.goto(CART_URL, timeout=45000, wait_until='domcontentloaded')
        logger.info("Страница корзины загружена")

        # Ждём загрузки товаров
        await asyncio.sleep(3)

        # Сохраняем скриншот для отладки
        screenshot_path = RESULTS_DIR / f"cart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await page.screenshot(path=str(screenshot_path))
        logger.info(f"Скриншот сохранён: {screenshot_path}")

        # Парсим товары
        products = []

        # Ищем все блоки товаров (div.checkout_s6)
        product_blocks = page.locator('div.checkout_s6')
        count = await product_blocks.count()
        logger.info(f"Найдено товаров в корзине: {count}")

        for i in range(count):
            try:
                block = product_blocks.nth(i)

                # Название товара (span.tsCompact500Medium)
                name_elem = block.locator('span.tsCompact500Medium').first
                name = await name_elem.text_content(timeout=3000)
                name = name.strip() if name else None

                # Цена со скидкой (span.tsHeadline400Small внутри div.checkout_t)
                # Это первая цена в блоке checkout_t
                price_elem = block.locator('div.checkout_t span.tsHeadline400Small').first
                price_text = await price_elem.text_content(timeout=3000)

                # Очищаем цену (убираем пробелы и оставляем только цифры и ₽)
                if price_text:
                    match = re.search(r'([\d\s\xa0]+)\s*₽', price_text)
                    if match:
                        price_str = re.sub(r'[\s\xa0]', '', match.group(1))
                        price = f"{price_str} ₽"
                    else:
                        price = None
                else:
                    price = None

                if name and price:
                    products.append({"name": name, "price": price})
                    logger.info(f"Товар {i+1}: {name} - {price}")
                else:
                    logger.warning(f"Товар {i+1}: не удалось извлечь данные (name={name}, price={price})")

            except Exception as e:
                logger.error(f"Ошибка при парсинге товара {i+1}: {e}")

        # Закрываем вкладку с корзиной
        await page.close()
        logger.info("Вкладка с корзиной закрыта")

        logger.info(f"Парсинг завершён, найдено товаров: {len(products)}")
        return products


def save_results(products: list[dict]):
    """
    Сохраняет результаты парсинга в общий CSV файл.

    Args:
        products: Список товаров с ценами
    """
    if not products:
        logger.warning("Нет товаров для сохранения")
        return

    now = datetime.now()
    csv_file = RESULTS_DIR / "cart_history.csv"

    # Создаём файл с заголовками, если его нет
    if not csv_file.exists():
        with open(csv_file, 'w', encoding='utf-8') as f:
            f.write("Дата,Время,Название товара,Цена (руб)\n")
        logger.info(f"Создан файл {csv_file}")

    # Добавляем данные
    with open(csv_file, 'a', encoding='utf-8') as f:
        for product in products:
            date_str = now.strftime('%Y-%m-%d')
            time_str = now.strftime('%H:%M:%S')
            name = product['name'].replace(',', ';')  # Заменяем запятые для CSV
            price = product['price'].replace(' ₽', '').replace(' ', '')  # Только цифры
            f.write(f"{date_str},{time_str},{name},{price}\n")

    logger.info(f"Данные добавлены в {csv_file} ({len(products)} товаров)")


async def main_loop():
    """
    Основной цикл: парсит корзину каждые 1-2 часа в рандомное время.
    """
    logger.info("Запуск тестового скрипта парсинга корзины")
    logger.info("Убедитесь, что Chrome запущен с --remote-debugging-port=9222")

    iteration = 0

    while True:
        iteration += 1
        logger.info(f"=== Итерация {iteration} ===")

        # Парсим корзину
        products = await parse_cart()

        # Сохраняем результаты
        save_results(products)

        # Рандомная задержка: 1-2 часа + рандомные минуты и секунды
        hours = random.uniform(1, 2)
        minutes = random.randint(0, 59)
        seconds = random.randint(0, 59)
        total_seconds = int(hours * 3600 + minutes * 60 + seconds)

        next_run = datetime.now().timestamp() + total_seconds
        next_run_str = datetime.fromtimestamp(next_run).strftime('%Y-%m-%d %H:%M:%S')

        logger.info(f"Следующий запуск через {hours:.2f}ч {minutes}м {seconds}с")
        logger.info(f"Следующий запуск в: {next_run_str}")
        logger.info("=" * 80)

        await asyncio.sleep(total_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Скрипт остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
