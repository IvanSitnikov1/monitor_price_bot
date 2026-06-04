import asyncio
import random
import re
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from loggers import logger


def _get_system_profile() -> Path:
    """
    Определяет путь к системному профилю Chrome/Chromium.

    Returns:
        Path к профилю
    """
    system_profile = Path.home() / ".config" / "chromium"
    logger.info(f"Найден системный профиль: {system_profile}")

    return system_profile


async def _human_like_behavior(page):
    """Имитирует человеческое поведение: скроллинг, движения мыши, паузы"""
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


async def _human_delay(min_s=0.5, max_s=2.0):
    # Гамма-распределение ближе к реальному поведению человека
    delay = random.gammavariate(2, 0.5)
    delay = max(min_s, min(max_s, delay))
    await asyncio.sleep(delay)


async def _unblock_ozon(page, cart_url: str):
    """
    Разблокирует доступ к Озону через открытие новой вкладки и переход на корзину.

    Логика:
    1. Ждёт 5 минут
    2. Открывает новую вкладку
    3. Применяет полный набор stealth-патчей
    4. Открывает главную страницу Ozon
    5. Ждёт полной загрузки
    6. Проверяет наличие блокировки
    7. Если блокировки нет — открывает корзину в той же вкладке
    8. Закрывает старую вкладку

    Args:
        page: Страница Playwright (старая вкладка с блокировкой)
        cart_url: URL корзины для открытия после разблокировки

    Returns:
        Кортеж (успех, новая_страница):
        - (True, new_page) если разблокировка успешна (страница уже на корзине)
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

        # Задержка перед навигацией (5-10 секунд)
        delay = random.uniform(5, 10)
        logger.info(f"Задержка перед навигацией: {delay:.2f}s")
        await asyncio.sleep(delay)

        # Эмулируем ввод URL в адресную строку через CDP
        logger.info("Эмуляция ввода URL через CDP (как ручной ввод в адресную строку)")
        cdp = await new_page.context.new_cdp_session(new_page)

        # Навигация через CDP Page.navigate (ближе к ручному вводу)
        await cdp.send('Page.navigate', {'url': 'https://www.ozon.ru/'})
        logger.info("CDP навигация отправлена")

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

            # Закрываем старую вкладку
            await page.close()
            logger.info("Старая вкладка закрыта")

            # Открываем корзину в новой вкладке
            logger.info(f"Открываем корзину в новой вкладке: {cart_url}")
            await asyncio.sleep(random.uniform(2, 4))
            await new_page.goto(cart_url, timeout=45000, wait_until='domcontentloaded')
            logger.info("Корзина открыта в новой вкладке")

            # Задержка и имитация поведения
            await asyncio.sleep(random.uniform(3, 5))

            try:
                await new_page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout при открытии корзины")

            return True, new_page

    except Exception as e:
        logger.error(f"Ошибка при попытке разблокировки: {e}")
        return False, None


async def _check_captcha(page) -> bool:
    """
    Проверяет наличие капчи на странице.

    Args:
        page: Страница Playwright

    Returns:
        True если капча обнаружена, False иначе
    """
    captcha_selectors = [
        'text="Сопоставьте пазл"',
        'text="двигая ползунок"',
        '[class*="captcha"]',
        '[id*="captcha"]',
    ]

    for selector in captcha_selectors:
        try:
            count = await page.locator(selector).count()
            if count > 0:
                logger.warning("Обнаружена капча на странице")
                return True
        except Exception:
            pass

    return False


async def _check_bot_detection(page):
    """
    Проверяет детект автоматизации через bot.sannysoft.com

    Returns:
        Словарь с результатами проверки
    """
    logger.info("Проверка детекта автоматизации через bot.sannysoft.com")

    try:
        await page.goto("https://bot.sannysoft.com/", timeout=30000)
        await asyncio.sleep(3)

        # Скриншот для визуальной проверки
        screenshot_path = Path(f"debug_data/bot_detection_{uuid4()}.png").absolute()
        await page.screenshot(path=str(screenshot_path))
        logger.info(f"Скриншот детекта сохранён: {screenshot_path}")

        # Извлекаем ключевые параметры
        results = {}

        # WebDriver
        webdriver_elem = page.locator('tr:has-text("navigator.webdriver")').first
        if await webdriver_elem.count() > 0:
            webdriver_text = await webdriver_elem.text_content()
            results['webdriver'] = webdriver_text
            logger.info(f"WebDriver: {webdriver_text}")

        # Chrome
        chrome_elem = page.locator('tr:has-text("window.chrome")').first
        if await chrome_elem.count() > 0:
            chrome_text = await chrome_elem.text_content()
            results['chrome'] = chrome_text
            logger.info(f"Chrome: {chrome_text}")

        # Permissions
        permissions_elem = page.locator('tr:has-text("Permissions")').first
        if await permissions_elem.count() > 0:
            permissions_text = await permissions_elem.text_content()
            results['permissions'] = permissions_text
            logger.info(f"Permissions: {permissions_text}")

        # Plugins
        plugins_elem = page.locator('tr:has-text("Plugins")').first
        if await plugins_elem.count() > 0:
            plugins_text = await plugins_elem.text_content()
            results['plugins'] = plugins_text
            logger.info(f"Plugins: {plugins_text}")

        # Languages
        languages_elem = page.locator('tr:has-text("Languages")').first
        if await languages_elem.count() > 0:
            languages_text = await languages_elem.text_content()
            results['languages'] = languages_text
            logger.info(f"Languages: {languages_text}")

        return results

    except Exception as e:
        logger.error(f"Ошибка при проверке детекта: {e}")
        return {}


async def scrape_ozon_cart(
    url: str,
    user_id: int,
    browser_manager,
    is_refresh: bool = False,
    maintenance_callback=None
) -> list[dict[str, str]] | str | None:
    """
    Парсит корзину Ozon и извлекает список товаров.

    Args:
        url: URL корзины (формат: https://www.ozon.ru/cart?share=...)
        user_id: ID пользователя Telegram
        browser_manager: Экземпляр BrowserManager для переиспользования браузера
        is_refresh: Если True, обновляет существующую страницу вместо открытия новой
        maintenance_callback: Callback для активации maintenance mode при неудачной разблокировке

    Returns:
        Список словарей с данными товаров [{"name": "...", "price": "..."}],
        строка "maintenance_required" при неудачной разблокировке,
        или None при других ошибках
    """
    logger.info(f"Начало парсинга корзины: {url}, user_id: {user_id}, is_refresh: {is_refresh}")

    try:
        if is_refresh:
            page = await browser_manager.refresh_cart_page(user_id)
        else:
            page = await browser_manager.get_cart_page(url, user_id)
    except Exception as e:
        logger.error(f"Ошибка при работе с BrowserManager: {e}")
        return None

    # Общая логика парсинга
    await _human_delay()
    await _human_like_behavior(page)

    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except PlaywrightTimeoutError:
        logger.warning("Networkidle timeout - продолжаем парсинг")

    # Сохраняем скриншот с указанием user_id и времени
    Path(f"debug_data").absolute().mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    screenshot_path = f'debug_data/cart_user{user_id}_{timestamp}.png'
    await page.screenshot(path=screenshot_path)
    logger.info(f"Скриншот корзины сохранён: {screenshot_path}")

    # Проверка капчи
    logger.info("Проверка наличия капчи")
    if await _check_captcha(page):
        logger.error("Обнаружена капча! Возвращаем ошибку для активации maintenance mode")
        return None

    # Парсинг товаров из корзины (с возможностью повторной попытки после разблокировки)
    logger.info("Начинаем извлечение товаров из корзины")
    products = []
    unblock_attempted = False

    while True:
        try:
            # Ищем все блоки товаров (div.checkout_s6.checkout_s8)
            product_blocks = page.locator('div.checkout_s6.checkout_s8')
            count = await product_blocks.count()
            logger.info(f"Найдено блоков товаров: {count}")

            for i in range(count):
                block = product_blocks.nth(i)

                # Извлекаем название (span.tsCompact400Large)
                try:
                    name_element = block.locator('span.tsCompact400Large').first
                    name = await name_element.text_content(timeout=3000)
                    name = name.strip() if name else None
                except Exception as e:
                    logger.warning(f"Не удалось извлечь название товара {i}: {e}")
                    name = None

                # Извлекаем цену (span.tsHeadline400Small внутри div.checkout_t)
                try:
                    price_element = block.locator('div.checkout_t span.tsHeadline400Small').first
                    price_text = await price_element.text_content(timeout=3000)
                    if price_text:
                        # Очищаем цену от пробелов и оставляем только цифры + ₽
                        match = re.search(r'([\d\s\xa0]+)', price_text)
                        if match:
                            price_str = re.sub(r'[\s\xa0]', '', match.group(1))
                            price = f"{price_str} ₽"
                        else:
                            price = None
                    else:
                        price = None
                except Exception as e:
                    logger.warning(f"Не удалось извлечь цену товара {i}: {e}")
                    price = None

                if name and price:
                    products.append({"name": name, "price": price})
                    logger.info(f"Товар {i+1}: {name} - {price}")
                else:
                    logger.warning(f"Товар {i+1}: пропущен (name={name}, price={price})")

        except Exception as e:
            logger.error(f"Ошибка при парсинге товаров: {e}")
            return None

        # Если товары не найдены, проверяем на блокировку
        if not products:
            logger.info("Проверка на страницу 'нет соединения'")
            no_connection = page.locator('div:has-text("Похоже, нет соединения")').first

            if await no_connection.count() > 0:
                logger.warning("Обнаружена страница блокировки 'нет соединения'")

                # Если уже пытались разблокировать — прерываем
                if unblock_attempted:
                    logger.error("Разблокировка не помогла, прерываем парсинг")
                    return None

                # Пытаемся разблокировать
                unblock_attempted = True
                unblock_success, new_page = await _unblock_ozon(page, url)

                if not unblock_success:
                    logger.error("Не удалось разблокировать доступ к Озону")

                    # Активируем maintenance mode через callback
                    if maintenance_callback:
                        logger.warning("Активация maintenance mode после неудачной разблокировки")
                        await maintenance_callback()

                    # Возвращаем специальный маркер для прерывания повторных попыток
                    return "maintenance_required"

                # Используем новую страницу после разблокировки
                # Корзина уже открыта в новой вкладке
                page = new_page
                logger.info("Переключились на новую вкладку, корзина уже загружена")

                # Продолжаем парсинг без повторного перехода
                await _human_like_behavior(page)

                # Сохраняем скриншот после разблокировки
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                screenshot_path_unblock = f'debug_data/cart_user{user_id}_after_unblock_{timestamp}.png'
                await page.screenshot(path=screenshot_path_unblock)
                logger.info(f"Скриншот после разблокировки: {screenshot_path_unblock}")

                # Повторяем парсинг (continue вернёт в начало while True)
                logger.info("Повторная попытка парсинга после разблокировки")
                products = []  # Сбрасываем список
                continue

        # Если товары найдены или блокировки нет — выходим из цикла
        break

    return products if products else None
