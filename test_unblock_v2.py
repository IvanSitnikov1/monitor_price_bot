"""
Вариант 2: Открываем браузер напрямую на ozon.ru БЕЗ промежуточных about:blank.
Используем launch вместо launch_persistent_context для подключения к существующему профилю.
"""

import asyncio
import random
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from loggers import logger


async def _human_delay(min_s=0.5, max_s=2.0):
    delay = random.gammavariate(2, 0.5)
    delay = max(min_s, min(max_s, delay))
    await asyncio.sleep(delay)


async def test_unblock_v2():
    """
    Вариант 2: Открываем браузер сразу на странице ozon.ru.
    Без промежуточных навигаций и about:blank.
    """
    user_id = 1189033386
    profile_path = Path("user_data") / str(user_id)

    logger.info(f"=== Вариант 2: Прямое открытие ozon.ru ===")
    logger.info(f"Путь к профилю: {profile_path}")

    if not profile_path.exists():
        logger.error(f"Профиль не найден: {profile_path}")
        return

    debug_dir = Path("debug_data").absolute()
    debug_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        logger.info("Запуск браузера с прямым открытием ozon.ru...")

        try:
            # Открываем контекст напрямую с URL
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path.absolute()),
                headless=False,
                channel='chromium',
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--no-first-run',
                ],
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow',
                ignore_default_args=['--enable-automation'],
                # Открываем сразу на нужной странице
                args=['https://www.ozon.ru/'],
            )
            logger.info("Браузер запущен с URL в аргументах")

            # Ждем немного
            await asyncio.sleep(3)

            # Получаем страницу
            pages = browser.pages
            if pages:
                page = pages[0]
                logger.info("Получена страница")

                # Применяем stealth
                stealth = Stealth(
                    navigator_languages_override=('ru-RU', 'ru'),
                    navigator_platform_override='Linux x86_64',
                    navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
                )
                await stealth.apply_stealth_async(page)

                # Ждем загрузки
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except PlaywrightTimeoutError:
                    logger.warning("Networkidle timeout")

                # Скриншот
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                screenshot_path = debug_dir / f'v2_direct_{timestamp}.png'
                await page.screenshot(path=str(screenshot_path))
                logger.info(f"Скриншот: {screenshot_path}")

                # Проверка блокировки
                no_connection = page.locator('div:has-text("Похоже, нет соединения")').first
                if await no_connection.count() > 0:
                    logger.warning("🔴 Блокировка обнаружена")
                else:
                    logger.info("✅ Блокировка не обнаружена")
                    title = await page.title()
                    logger.info(f"Заголовок: {title}")

            logger.info("Нажмите Enter для завершения...")
            input()

            await browser.close()
            logger.info("Браузер закрыт")

        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_unblock_v2())
