"""
Вариант 3: Не очищаем cookies, используем существующую сессию.
Открываем новую вкладку через browser.new_page() без очистки состояния.
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


async def test_unblock_v3():
    """
    Вариант 3: НЕ очищаем cookies, используем существующую сессию.
    Просто ждём и открываем новую вкладку без агрессивной очистки.
    """
    user_id = 1189033386
    profile_path = Path("user_data") / str(user_id)

    logger.info(f"=== Вариант 3: Без очистки cookies ===")
    logger.info(f"Путь к профилю: {profile_path}")

    if not profile_path.exists():
        logger.error(f"Профиль не найден: {profile_path}")
        return

    debug_dir = Path("debug_data").absolute()
    debug_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        logger.info("Запуск браузера...")

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
            logger.info("Браузер запущен")

            pages = browser.pages
            if pages:
                page = pages[0]
            else:
                page = await browser.new_page()

            # Применяем stealth
            stealth = Stealth(
                navigator_languages_override=('ru-RU', 'ru'),
                navigator_platform_override='Linux x86_64',
                navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            )
            await stealth.apply_stealth_async(page)

            # Переход через window.location
            logger.info("Переход на ozon.ru через window.location...")
            await page.evaluate("window.location.href = 'https://www.ozon.ru/'")
            await _human_delay()

            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout")

            # Скриншот начального состояния
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_initial = debug_dir / f'v3_initial_{timestamp}.png'
            await page.screenshot(path=str(screenshot_initial))
            logger.info(f"Скриншот: {screenshot_initial}")

            # Проверка блокировки
            no_connection = page.locator('div:has-text("Похоже, нет соединения")').first

            if await no_connection.count() > 0:
                logger.warning("🔴 Блокировка обнаружена, начинаем разблокировку...")

                # Ждём 5 минут
                logger.info("Ожидание 5 минут...")
                await asyncio.sleep(300)

                # Открываем НОВУЮ вкладку БЕЗ очистки cookies
                logger.info("Открываем новую вкладку (cookies сохранены)")
                new_page = await browser.new_page()

                # Применяем stealth на новую вкладку
                await stealth.apply_stealth_async(new_page)

                # Задержка
                delay = random.uniform(5, 10)
                logger.info(f"Задержка: {delay:.2f}s")
                await asyncio.sleep(delay)

                # Переход через window.location
                logger.info("Переход на ozon.ru в новой вкладке...")
                await new_page.evaluate("window.location.href = 'https://www.ozon.ru/'")

                await asyncio.sleep(random.uniform(3, 5))

                try:
                    await new_page.wait_for_load_state('networkidle', timeout=15000)
                except PlaywrightTimeoutError:
                    logger.warning("Networkidle timeout")

                await asyncio.sleep(random.uniform(2, 4))

                # Скриншот новой вкладки
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                screenshot_new = debug_dir / f'v3_new_tab_{timestamp}.png'
                await new_page.screenshot(path=str(screenshot_new))
                logger.info(f"Скриншот новой вкладки: {screenshot_new}")

                # Проверка блокировки на новой вкладке
                no_connection_new = new_page.locator('div:has-text("Похоже, нет соединения")').first

                if await no_connection_new.count() > 0:
                    logger.warning("❌ Блокировка всё ещё присутствует")
                else:
                    logger.info("✅ Разблокировка успешна!")
                    await page.close()  # Закрываем старую вкладку
                    page = new_page

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
    asyncio.run(test_unblock_v3())
