"""
Вариант 4: Имитируем максимально человеческое поведение - случайные клики и скроллы.
Добавляем активность на странице перед переходом на Ozon.
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


async def _simulate_human_activity(page):
    """Имитирует активность человека на странице"""
    logger.info("Имитация активности пользователя...")

    # Случайные движения мыши
    viewport = page.viewport_size
    if viewport:
        for _ in range(3):
            x = random.randint(100, viewport['width'] - 100)
            y = random.randint(100, viewport['height'] - 100)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.5, 1.5))

    # Скроллинг
    for _ in range(2):
        await page.mouse.wheel(0, random.randint(100, 300))
        await asyncio.sleep(random.uniform(0.5, 1.0))

    # Ещё немного движений мыши
    for _ in range(2):
        x = random.randint(100, viewport['width'] - 100)
        y = random.randint(100, viewport['height'] - 100)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.3, 0.8))

    logger.info("Активность завершена")


async def test_unblock_v4():
    """
    Вариант 4: Максимально человеческое поведение.
    Сначала открываем нейтральную страницу, имитируем активность, потом переходим на Ozon.
    """
    user_id = 1189033386
    profile_path = Path("user_data") / str(user_id)

    logger.info(f"=== Вариант 4: Максимально человеческое поведение ===")
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
                    '--disable-infobars',
                    '--no-first-run',
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

            # Сначала открываем Google (нейтральная страница)
            logger.info("Открываем Google для разогрева...")
            await page.goto('https://www.google.com/', wait_until='domcontentloaded', timeout=30000)
            await _human_delay(2, 4)

            # Имитируем активность
            await _simulate_human_activity(page)

            # Теперь переходим на Ozon через адресную строку (Ctrl+L)
            logger.info("Переход на Ozon через эмуляцию адресной строки...")
            await asyncio.sleep(random.uniform(1, 2))

            # Фокус на адресную строку
            await page.keyboard.press('Control+L')
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # Набираем URL посимвольно
            url_to_type = "ozon.ru"
            for char in url_to_type:
                await page.keyboard.type(char)
                await asyncio.sleep(random.uniform(0.08, 0.18))

            # Enter
            await asyncio.sleep(random.uniform(0.5, 1.0))
            await page.keyboard.press('Enter')
            logger.info("URL введён")

            # Ждём загрузки
            await asyncio.sleep(3)
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout")

            # Скриншот
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = debug_dir / f'v4_human_{timestamp}.png'
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
    asyncio.run(test_unblock_v4())
