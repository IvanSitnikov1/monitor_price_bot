"""
Вариант 1: Минимальная автоматизация — открываем пустую страницу и ждем ручных действий.
Пользователь сам открывает Ozon, мы только предоставляем браузер.
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from loggers import logger


async def test_unblock_v1():
    """
    Вариант 1: Открываем браузер с профилем, НЕ делаем автоматических переходов.
    Пользователь сам открывает новую вкладку и вводит ozon.ru вручную.
    """
    user_id = 1189033386
    profile_path = Path("user_data") / str(user_id)

    logger.info(f"=== Вариант 1: Минимальная автоматизация ===")
    logger.info(f"Путь к профилю: {profile_path}")

    if not profile_path.exists():
        logger.error(f"Профиль не найден: {profile_path}")
        return

    async with async_playwright() as p:
        logger.info("Запуск браузера без автоматических переходов...")

        try:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path.absolute()),
                headless=False,
                channel='chromium',
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                ],
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow',
                ignore_default_args=['--enable-automation'],
            )
            logger.info("Браузер запущен успешно")

            # НЕ делаем никаких автоматических переходов
            logger.info("Браузер открыт. Откройте вкладку и введите ozon.ru ВРУЧНУЮ.")
            logger.info("Нажмите Enter когда проверите результат...")
            input()

            await browser.close()
            logger.info("Браузер закрыт")

        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_unblock_v1())
