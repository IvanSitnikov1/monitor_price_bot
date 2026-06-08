"""
Вариант 5: Подключаемся к УЖЕ запущенному браузеру через CDP.
Не запускаем новый процесс, а используем существующий chromium с профилем.
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


async def test_unblock_v5():
    """
    Вариант 5: Подключаемся к существующему браузеру через CDP.

    Запустите chromium вручную:
    chromium --remote-debugging-port=9222 --user-data-dir=/home/ivans/monitor_price_bot/user_data/1189033386

    Затем запустите этот скрипт — он подключится к уже работающему браузеру.
    """
    debug_dir = Path("debug_data").absolute()
    debug_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"=== Вариант 5: Подключение к существующему браузеру ===")
    logger.info("ВАЖНО: Сначала запустите chromium вручную:")
    logger.info("chromium --remote-debugging-port=9222 --user-data-dir=/home/ivans/monitor_price_bot/user_data/1189033386")
    logger.info("")
    logger.info("Нажмите Enter когда браузер будет запущен...")
    input()

    async with async_playwright() as p:
        try:
            logger.info("Подключаемся к существующему браузеру на порту 9222...")

            # Подключаемся к существующему браузеру
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            logger.info("Подключение успешно!")

            # Получаем контекст
            contexts = browser.contexts
            if not contexts:
                logger.error("Нет доступных контекстов")
                return

            context = contexts[0]
            logger.info(f"Найдено контекстов: {len(contexts)}")

            # Получаем страницы
            pages = context.pages
            logger.info(f"Найдено страниц: {len(pages)}")

            if not pages:
                logger.info("Создаём новую страницу...")
                page = await context.new_page()
            else:
                page = pages[0]
                logger.info("Используем существующую страницу")

            # Применяем stealth
            stealth = Stealth(
                navigator_languages_override=('ru-RU', 'ru'),
                navigator_platform_override='Linux x86_64',
                navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            )
            await stealth.apply_stealth_async(page)
            logger.info("Stealth-патчи применены")

            # Переход через window.location
            logger.info("Переход на ozon.ru...")
            await page.evaluate("window.location.href = 'https://www.ozon.ru/'")
            await _human_delay()

            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout")

            # Скриншот
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = debug_dir / f'v5_cdp_{timestamp}.png'
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

            logger.info("")
            logger.info("Нажмите Enter для отключения от браузера...")
            logger.info("(Браузер останется запущенным)")
            input()

            # Отключаемся (браузер не закрывается)
            await browser.close()
            logger.info("Отключились от браузера")

        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_unblock_v5())
