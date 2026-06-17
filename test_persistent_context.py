"""
Тестовый скрипт для проверки launch_persistent_context с пользовательскими профилями
"""
import asyncio
from utils.browser_manager import BrowserManager
from loggers import logger


async def test_persistent_context():
    """Тестирует запуск браузера через launch_persistent_context"""

    logger.info("=== Тест запуска браузера через launch_persistent_context ===")

    # Создаём менеджер браузеров
    manager = BrowserManager()

    # Тестовый URL корзины
    test_cart_url = "https://www.ozon.ru/cart"
    test_user_id = 999999  # Тестовый пользователь

    try:
        logger.info(f"Запуск браузера для пользователя {test_user_id}")

        # Получаем страницу корзины
        page = await manager.get_cart_page(test_cart_url, test_user_id)

        logger.info("Браузер успешно запущен!")
        logger.info(f"URL страницы: {page.url}")

        # Ждём 10 секунд для визуальной проверки
        logger.info("Ожидание 10 секунд для проверки браузера...")
        await asyncio.sleep(10)

        # Делаем скриншот
        screenshot_path = "debug_data/test_persistent_context.png"
        await page.screenshot(path=screenshot_path)
        logger.info(f"Скриншот сохранён: {screenshot_path}")

        logger.info("Тест завершён успешно!")

    except Exception as e:
        logger.error(f"Ошибка при тестировании: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(test_persistent_context())
