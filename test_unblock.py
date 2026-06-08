"""
Тестовый скрипт для проверки обхода блокировки Ozon.

Использует существующий браузер с профилем пользователя 1189033386
и пытается обойти блокировку через логику из utils/scraper.py.
"""

import asyncio
import random
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from loggers import logger


async def _human_delay(min_s=0.5, max_s=2.0):
    """Имитирует человеческую задержку"""
    delay = random.gammavariate(2, 0.5)
    delay = max(min_s, min(max_s, delay))
    await asyncio.sleep(delay)


async def _human_like_behavior(page):
    """Имитирует человеческое поведение: движения мыши"""
    async def move_mouse_bezier(page, x1, y1, x2, y2, steps=20):
        # Контрольные точки кривой
        cx = random.randint(min(x1, x2), max(x1, x2))
        cy = random.randint(min(y1, y2), max(y1, y2))

        for i in range(steps + 1):
            t = i / steps
            # Квадратичная кривая Безье
            x = int((1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2)
            y = int((1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.01, 0.03))

    viewport = page.viewport_size
    if viewport:
        # Начальная позиция мыши — центр экрана
        await move_mouse_bezier(
            page, 960, 540,
            random.randint(200, viewport['width'] - 200),
            random.randint(200, viewport['height'] - 200)
        )


async def _unblock_ozon(page):
    """
    Разблокирует доступ к Озону через ожидание и проверку главной страницы.

    Логика:
    1. Ждёт 5 минут
    2. Открывает новую вкладку
    3. Применяет полный набор stealth-патчей
    4. Эмулирует ввод URL в адресную строку через CDP
    5. Ждёт полной загрузки (networkidle)
    6. Проверяет наличие блокировки
    7. Закрывает старую вкладку только при успехе

    Args:
        page: Страница Playwright (старая вкладка с блокировкой)

    Returns:
        Кортеж (успех, новая_страница):
        - (True, new_page) если разблокировка успешна
        - (False, None) если блокировка осталась
    """
    logger.info("Попытка разблокировки: ожидание 5 минут перед проверкой...")

    try:
        # Ждём 5 минут
        await asyncio.sleep(300)  # 300 секунд = 5 минут
        logger.info("Ожидание завершено, открываем новую вкладку с главной страницей Ozon")

        # Получаем контекст браузера
        context = page.context

        # Очищаем cookies для ozon.ru перед открытием новой вкладки
        logger.info("Очистка cookies для ozon.ru")
        try:
            cookies = await context.cookies()
            ozon_cookies = [c for c in cookies if 'ozon.ru' in c.get('domain', '')]
            if ozon_cookies:
                logger.info(f"Найдено {len(ozon_cookies)} cookies для ozon.ru, удаляем...")
                await context.clear_cookies()
                logger.info("Cookies очищены")
            else:
                logger.info("Cookies для ozon.ru не найдены")
        except Exception as e:
            logger.warning(f"Не удалось очистить cookies: {e}")

        # Открываем новую вкладку
        new_page = await context.new_page()
        logger.info("Новая вкладка создана")

        # Применяем полный набор stealth-патчей (как в основной функции)
        stealth = Stealth(
            navigator_languages_override=('ru-RU', 'ru'),
            navigator_platform_override='Linux x86_64',
            navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        )
        await stealth.apply_stealth_async(new_page)
        await new_page.set_viewport_size({"width": 1920, "height": 1080})
        logger.info("Stealth-патчи применены к новой вкладке")

        # Дополнительная маскировка через JS
        logger.info("Применяем дополнительные JS-патчи на новую вкладку")
        await new_page.add_init_script("""
            // Удаляем webdriver свойство
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Фиксим permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Добавляем реалистичные plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    {
                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    },
                    {
                        0: {type: "application/pdf", suffixes: "pdf", description: ""},
                        description: "",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        length: 1,
                        name: "Chrome PDF Viewer"
                    }
                ]
            });

            // Фиксим chrome runtime
            Object.defineProperty(window, 'chrome', {
                get: () => ({
                    runtime: {}
                })
            });

            // Переопределяем toString для маскировки
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === window.navigator.permissions.query) {
                    return 'function query() { [native code] }';
                }
                return originalToString.call(this);
            };
        """)
        logger.info("JS-патчи применены к новой вкладке")

        # Задержка перед навигацией (5-10 секунд)
        delay = random.uniform(5, 10)
        logger.info(f"Задержка перед навигацией: {delay:.2f}s")
        await asyncio.sleep(delay)

        # Вариант 1: Через window.location (как если бы пользователь кликнул на ссылку)
        logger.info("Переход на Ozon через window.location.href")
        await new_page.evaluate("window.location.href = 'https://www.ozon.ru/'")
        logger.info("Навигация через window.location отправлена")

        # Ждём загрузки страницы (domcontentloaded)
        await new_page.wait_for_load_state('domcontentloaded', timeout=30000)
        logger.info("Страница загружена (domcontentloaded)")

        # Дополнительная задержка
        await asyncio.sleep(random.uniform(3, 5))

        # Ждём networkidle для полной загрузки
        try:
            logger.info("Ожидание networkidle")
            await new_page.wait_for_load_state('networkidle', timeout=15000)
            logger.info("Networkidle достигнут")
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем проверку")

        # Ещё одна задержка перед проверкой
        await asyncio.sleep(random.uniform(2, 4))

        # Проверяем наличие блокировки на новой вкладке
        logger.info("Проверка на страницу блокировки")
        no_connection = new_page.locator('div:has-text("Похоже, нет соединения")').first

        if await no_connection.count() > 0:
            logger.warning("Блокировка всё ещё присутствует после ожидания")
            # Закрываем новую вкладку при неудаче
            await new_page.close()
            return False, None
        else:
            logger.info("Блокировка снята, доступ к Ozon восстановлен")
            # Закрываем старую вкладку только после успешной проверки
            await page.close()
            logger.info("Старая вкладка закрыта")
            return True, new_page

    except Exception as e:
        logger.error(f"Ошибка при попытке разблокировки: {e}")
        return False, None


async def test_unblock():
    """
    Тестирует обход блокировки Ozon с профилем пользователя 1189033386.
    """
    user_id = 1189033386
    profile_path = Path("user_data") / str(user_id)

    logger.info(f"=== Начало теста обхода блокировки Ozon ===")
    logger.info(f"User ID: {user_id}")
    logger.info(f"Путь к профилю: {profile_path}")

    # Проверяем существование профиля
    if not profile_path.exists():
        logger.error(f"Профиль не найден: {profile_path}")
        return

    # Создаём папку для скриншотов
    debug_dir = Path("debug_data").absolute()
    debug_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        logger.info("Запуск браузера Chromium с профилем пользователя...")

        try:
            # Запускаем браузер с профилем пользователя
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path.absolute()),
                headless=False,
                channel='chromium',  # Используем системный chromium
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--window-size=1920,1080',
                    '--new-window',
                    # Дополнительные флаги для обхода детекта
                    '--disable-infobars',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-features=TranslateUI',
                    '--disable-ipc-flooding-protection',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--disable-component-update',
                ],
                viewport={'width': 1920, 'height': 1080},
                locale='ru-RU',
                timezone_id='Europe/Moscow',
                ignore_default_args=['--enable-automation'],
                bypass_csp=True,  # Обход Content Security Policy
            )
            logger.info("Браузер запущен успешно")

            # Получаем существующие страницы или создаём новую
            pages = browser.pages
            if pages:
                page = pages[0]
                logger.info("Используем существующую страницу")
            else:
                page = await browser.new_page()
                logger.info("Создана новая страница")

            # Применяем stealth-патчи
            stealth = Stealth(
                navigator_languages_override=('ru-RU', 'ru'),
                navigator_platform_override='Linux x86_64',
                navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            )
            await stealth.apply_stealth_async(page)
            logger.info("Stealth-патчи применены")

            # Дополнительная маскировка через JS
            logger.info("Применяем дополнительные JS-патчи для обхода детекта")
            await page.add_init_script("""
                // Удаляем webdriver свойство
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Фиксим permissions API
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Добавляем реалистичные plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {
                            0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                            description: "Portable Document Format",
                            filename: "internal-pdf-viewer",
                            length: 1,
                            name: "Chrome PDF Plugin"
                        },
                        {
                            0: {type: "application/pdf", suffixes: "pdf", description: ""},
                            description: "",
                            filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                            length: 1,
                            name: "Chrome PDF Viewer"
                        }
                    ]
                });

                // Фиксим chrome runtime
                Object.defineProperty(window, 'chrome', {
                    get: () => ({
                        runtime: {}
                    })
                });

                // Переопределяем toString для маскировки
                const originalToString = Function.prototype.toString;
                Function.prototype.toString = function() {
                    if (this === window.navigator.permissions.query) {
                        return 'function query() { [native code] }';
                    }
                    return originalToString.call(this);
                };
            """)
            logger.info("JS-патчи применены")

            # Переходим на главную страницу Ozon
            logger.info("Переход на главную страницу Ozon через window.location...")
            await page.evaluate("window.location.href = 'https://www.ozon.ru/'")
            await _human_delay()
            await _human_like_behavior(page)

            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout - продолжаем")

            # Скриншот начального состояния
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_initial = debug_dir / f'test_unblock_initial_{timestamp}.png'
            await page.screenshot(path=str(screenshot_initial))
            logger.info(f"Скриншот начального состояния: {screenshot_initial}")

            # Проверяем наличие блокировки
            logger.info("Проверка на блокировку...")
            no_connection = page.locator('div:has-text("Похоже, нет соединения")').first

            if await no_connection.count() > 0:
                logger.warning("🔴 Обнаружена блокировка 'нет соединения'")
                logger.info("Начинаем процесс разблокировки...")

                # Пытаемся разблокировать
                unblock_success, new_page = await _unblock_ozon(page)

                if unblock_success:
                    logger.info("✅ Разблокировка успешна!")
                    page = new_page

                    # Скриншот после разблокировки
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    screenshot_after = debug_dir / f'test_unblock_success_{timestamp}.png'
                    await page.screenshot(path=str(screenshot_after))
                    logger.info(f"Скриншот после разблокировки: {screenshot_after}")

                    # Проверяем доступность страницы
                    logger.info("Проверка доступности страницы после разблокировки...")
                    title = await page.title()
                    logger.info(f"Заголовок страницы: {title}")

                else:
                    logger.error("❌ Разблокировка не удалась")

                    # Скриншот неудачи
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    screenshot_fail = debug_dir / f'test_unblock_fail_{timestamp}.png'
                    await page.screenshot(path=str(screenshot_fail))
                    logger.info(f"Скриншот неудачи: {screenshot_fail}")

            else:
                logger.info("✅ Блокировка не обнаружена, страница доступна")
                title = await page.title()
                logger.info(f"Заголовок страницы: {title}")

            # Ждём ввода пользователя перед закрытием
            logger.info("Браузер открыт. Нажмите Enter для завершения теста...")
            input()

            # Закрываем браузер
            await browser.close()
            logger.info("Браузер закрыт")

        except Exception as e:
            logger.error(f"Ошибка при выполнении теста: {e}", exc_info=True)

    logger.info("=== Тест завершён ===")


if __name__ == "__main__":
    asyncio.run(test_unblock())
