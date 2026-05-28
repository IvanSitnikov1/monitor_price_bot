import asyncio
import logging
import os
import random
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth


logger = logging.getLogger(__name__)


def _get_system_profile() -> Path:
    """
    Определяет путь к системному профилю Chrome/Chromium.

    Returns:
        Path к профилю
    """
    system_profile = Path.home() / ".config" / "chromium"
    logger.info(f"Найден системный профиль: {system_profile}")

    return system_profile


def _start_chrome_manually(user_data_dir: Path, port: int = 9222) -> subprocess.Popen | None:
    """
    Запускает Chrome вручную через subprocess (без Playwright).
    Это обходит детект автоматизации, т.к. Chrome запускается как обычный процесс.

    Args:
        user_data_dir: Путь к профилю Chrome
        port: Порт для remote debugging

    Returns:
        Процесс Chrome или None при ошибке
    """
    # Логируем DISPLAY для диагностики
    display = os.environ.get("DISPLAY", "не установлен")
    logger.info(f"DISPLAY переменная окружения: {display}")

    chrome_cmd = [
        "chromium",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--start-maximized",
    ]

    try:
        logger.info(f"Запуск Chrome вручную через subprocess: {' '.join(chrome_cmd)}")
        process = subprocess.Popen(
            chrome_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # Отделяем от родительского процесса
        )
        logger.info(f"Chrome запущен вручную, PID: {process.pid}")
        # Даём Chrome время на запуск
        time.sleep(3)
        return process
    except Exception as e:
        logger.error(f"Не удалось запустить Chrome вручную: {e}")
        return None


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


async def _wait_for_captcha_solve(page, timeout: int = 120) -> bool:
    """
    Ожидает решения капчи пользователем.

    Args:
        page: Страница Playwright
        timeout: Максимальное время ожидания в секундах

    Returns:
        True если капча решена, False если таймаут
    """
    logger.info("Обнаружена капча, ожидаем решения пользователем...")

    try:
        # Ждём исчезновения элементов капчи
        # Озон использует разные типы капч, проверяем все известные селекторы
        captcha_selectors = [
            'text="Сопоставьте пазл"',
            'text="двигая ползунок"',
            '[class*="captcha"]',
            '[id*="captcha"]',
        ]

        start_time = asyncio.get_event_loop().time()
        check_interval = 2  # проверяем каждые 2 секунды

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            # Проверяем, исчезла ли капча
            captcha_found = False
            for selector in captcha_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        captcha_found = True
                        break
                except Exception:
                    pass

            if not captcha_found:
                logger.info("Капча решена!")
                # Дополнительная пауза для стабилизации страницы
                await asyncio.sleep(2)
                return True

            # Ждём перед следующей проверкой
            await asyncio.sleep(check_interval)
            logger.debug(f"Ожидание решения капчи... ({int(asyncio.get_event_loop().time() - start_time)}s)")

        logger.warning(f"Таймаут ожидания решения капчи ({timeout}s)")
        return False

    except Exception as e:
        logger.error(f"Ошибка при ожидании решения капчи: {e}")
        return False


async def _check_bot_detection(page) -> dict[str, any]:
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


async def scrape_ozon_product(url: str, user_id: int) -> tuple[str | None, str | None]:
    """
    Парсит данные товара Ozon, используя профиль пользователя или системный профиль.
    Подключается к уже запущенному Chrome через CDP (Chrome DevTools Protocol).

    Args:
        url: URL товара
        user_id: ID пользователя Telegram (опционально)

    Returns:
        Кортеж (название, цена) или (None, None) при ошибке
    """
    logger.info(f"Начало процесса для ссылки: {url}, user_id: {user_id}")
    logger.info(f"Детальное логирование включено")

    # Проверяем наличие профиля пользователя
    user_profile = Path(f"user_data/{user_id}").absolute()
    if user_profile.exists() and (user_profile / "Default").exists():
        user_data_dir = user_profile
        logger.info(f"Используется профиль пользователя: {user_data_dir}")
    else:
        # Профиль пользователя не найден, используем системный
        user_data_dir = _get_system_profile()
        logger.info(f"Используется системный профиль: {user_data_dir}")

    # CDP endpoint
    cdp_url = "http://localhost:9222"
    chrome_process = None

    async with async_playwright() as p:
        logger.info(f"Попытка подключения к Chrome через CDP: {cdp_url}")

        try:
            # Пытаемся подключиться к уже запущенному Chrome
            browser = await p.chromium.connect_over_cdp(cdp_url)
            logger.info("Успешно подключились к существующему Chrome")

            # Получаем существующий контекст (первый)
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                logger.info(f"Используем существующий контекст, всего контекстов: {len(contexts)}")
            else:
                logger.error("Нет доступных контекстов в браузере")
                await browser.close()
                return None, None

        except Exception as e:
            logger.warning(f"Не удалось подключиться к Chrome через CDP: {e}")
            logger.info("Запускаем Chrome вручную через subprocess...")

            # Запускаем Chrome вручную через subprocess (НЕ через Playwright!)
            chrome_process = _start_chrome_manually(user_data_dir, port=9222)
            if not chrome_process:
                logger.error("Не удалось запустить Chrome")
                return None, None

            # Ждём, пока Chrome запустится и откроет CDP порт
            logger.info("Ожидание запуска Chrome...")
            await asyncio.sleep(5)

            # Теперь подключаемся к запущенному Chrome
            try:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                logger.info("Успешно подключились к Chrome, запущенному через subprocess")

                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    logger.info(f"Используем контекст, всего контекстов: {len(contexts)}")
                else:
                    logger.error("Нет доступных контекстов")
                    await browser.close()
                    if chrome_process:
                        chrome_process.terminate()
                    return None, None

            except Exception as e2:
                logger.error(f"Не удалось подключиться к Chrome после запуска: {e2}")
                if chrome_process:
                    chrome_process.terminate()
                return None, None

            logger.info("Chrome запущен с remote debugging на порту 9222")

        # Закрываем все лишние вкладки, оставляем только одну
        if context.pages:
            page = context.pages[0]
            logger.info(f"Используем существующую страницу, всего страниц: {len(context.pages)}")
            # Закрываем все остальные вкладки
            for i, extra_page in enumerate(context.pages[1:], 1):
                await extra_page.close()
                logger.info(f"Закрыта лишняя вкладка {i}")
        else:
            page = await context.new_page()
            logger.info("Создана новая страница")

        # Применяем playwright-stealth с настройками под русский профиль
        stealth = Stealth(
            navigator_languages_override=('ru-RU', 'ru', 'en-US', 'en'),
            navigator_platform_override='Linux x86_64',
            navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        )
        await stealth.apply_stealth_async(page)
        logger.info("Stealth-патчи применены с русской локалью")

        # Устанавливаем реалистичный viewport
        await page.set_viewport_size({"width": 1920, "height": 1080})
        logger.info("Viewport установлен: 1920x1080")

        # Переходим на страницу товара
        logger.info(f"Переход на страницу товара: {url}")
        await page.goto(url, timeout=45000, wait_until='domcontentloaded')
        logger.info("Страница загружена (domcontentloaded)")

        logger.info("Страница загружена")

        # Случайная пауза после загрузки
        logger.info("Пауза после загрузки")
        await _human_delay()

        # Имитируем человеческое поведение
        logger.info("Имитация человеческого поведения")
        await _human_like_behavior(page)

        # Ожидание стабилизации страницы
        try:
            logger.info("Ожидание networkidle")
            await page.wait_for_load_state('networkidle', timeout=10000)
            logger.info("Networkidle достигнут")
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем парсинг")

        # Ещё одна имитация поведения перед парсингом
        logger.info("Дополнительная имитация поведения")
        await _human_like_behavior(page)
        await asyncio.sleep(random.uniform(1, 2))

        # Сохраняем скриншот для отладки
        Path(f"debug_data").absolute().mkdir(parents=True, exist_ok=True)
        screenshot_path = f'debug_data/ozon_{uuid4()}.png'
        await page.screenshot(path=screenshot_path)
        logger.info(f"Скриншот страницы товара сохранён: {screenshot_path}")

        # --- Проверка и ожидание решения капчи ---
        logger.info("Проверка наличия капчи")
        captcha_element = page.locator('text="Сопоставьте пазл"').first
        if await captcha_element.count() > 0:
            logger.warning("Обнаружена капча! Ожидаем решения пользователем...")
            captcha_solved = await _wait_for_captcha_solve(page, timeout=120)
            if not captcha_solved:
                logger.error("Капча не была решена в течение 120 секунд")
                await context.close()
                return None, None
            logger.info("Капча решена, продолжаем парсинг")
            # Дополнительная пауза после решения капчи
            await asyncio.sleep(2)
            # Сохраняем скриншот после решения капчи
            screenshot_path_after = f'debug_data/ozon_after_captcha_{uuid4()}.png'
            await page.screenshot(path=screenshot_path_after)
            logger.info(f"Скриншот после капчи сохранён: {screenshot_path_after}")
        else:
            logger.info("Капча не обнаружена")

        # --- Извлечение названия и цены ---
        product_name = None
        price = None

        # проверяем страницу ошибки сети Ozon
        logger.info("Проверка на страницу 'нет соединения'")
        no_connection = page.locator(
            'div:has-text("Похоже, нет соединения")'
        ).first

        # сначала проверяем, не закончился ли товар
        logger.info("Проверка на 'товар закончился'")
        out_of_stock = page.locator(
            'h2:has-text("Этот товар закончился")'
        ).first

        if await no_connection.count() > 0:
            logger.warning("Обнаружена страница 'нет соединения' - возможна блокировка")
            product_name = "no_connection"
        elif await out_of_stock.count() > 0:
            logger.info("Товар закончился")
            product_name = "end"
        else:
            logger.info("Начинаем извлечение данных товара")
            try:
                logger.info("Поиск элемента h1 с названием")
                title_element = page.locator('h1').first
                product_name = await title_element.text_content(timeout=5000)
                if product_name:
                    product_name = product_name.strip()
                logger.info(f"Название найдено: {product_name}")
            except Exception as e:
                logger.error(f"Не удалось найти название: {e}")

            # --- Извлечение цены ---
            try:
                logger.info("Поиск элемента с ценой (span.tsHeadline600Large)")
                # Озон часто меняет классы, используем ваш селектор + запасной вариант
                price_locator = page.locator('span.tsHeadline600Large').first

                # Если ваш старый селектор не сработал, пробуем универсальный по значку рубля
                if await price_locator.count() == 0:
                    logger.info("Основной селектор не сработал, пробуем [data-attribute='price']")
                    price_locator = page.locator('[data-attribute="price"]').first

                price_text = await price_locator.text_content(timeout=5000)
                logger.debug(f"Сырой текст цены: {price_text}")

                # Очистка цены (учитываем пробелы, символы рубля и спецсимволы)
                if price_text:
                    # Ищем цифры, разделенные пробелами
                    match = re.search(r'([\d\s\xa0]+)', price_text)
                    if match:
                        price_str = re.sub(r'[\s\xa0]', '', match.group(1))
                        if price_str.isdigit():
                            price = f"{price_str} ₽"
                            logger.info(f"Итоговая цена: {price}")
                        else:
                            logger.warning(f"Цена не является числом: {price_str}")
                    else:
                        logger.warning(f"Не удалось извлечь цифры из текста цены: {price_text}")

            except Exception as e:
                logger.error(f"Не удалось найти цену: {e}")

        logger.info(f"Закрытие контекста браузера")

        # Если подключались через CDP, не закрываем браузер (он должен остаться запущенным)
        try:
            # Это CDP-подключение, просто отключаемся
            await browser.close()
            logger.info("Отключились от CDP браузера (браузер остался запущенным)")
        except Exception as e:
            logger.warning(f"Ошибка при закрытии: {e}")

        # Если запускали Chrome сами, НЕ убиваем процесс — оставляем работать для следующих запросов
        # Chrome будет переиспользоваться между запросами
        if chrome_process:
            logger.info(f"Chrome процесс (PID: {chrome_process.pid}) оставлен запущенным для переиспользования")

        logger.info(f"Результат парсинга: название='{product_name}', цена='{price}'")
        return product_name, price


async def scrape_ozon_cart(url: str, user_id: int) -> list[dict[str, str]] | None:
    """
    Парсит корзину Ozon и извлекает список товаров.

    Args:
        url: URL корзины (формат: https://www.ozon.ru/cart?share=...)
        user_id: ID пользователя Telegram

    Returns:
        Список словарей с данными товаров [{"name": "...", "price": "..."}] или None при ошибке
    """
    logger.info(f"Начало парсинга корзины: {url}, user_id: {user_id}")

    # Проверяем наличие профиля пользователя
    user_profile = Path(f"user_data/{user_id}").absolute()
    if user_profile.exists() and (user_profile / "Default").exists():
        user_data_dir = user_profile
        logger.info(f"Используется профиль пользователя: {user_data_dir}")
    else:
        # Профиль пользователя не найден, используем системный
        user_data_dir = _get_system_profile()
        logger.info(f"Используется системный профиль: {user_data_dir}")

    # CDP endpoint
    cdp_url = "http://localhost:9222"
    chrome_process = None

    async with async_playwright() as p:
        logger.info(f"Попытка подключения к Chrome через CDP: {cdp_url}")

        try:
            # Пытаемся подключиться к уже запущенному Chrome
            browser = await p.chromium.connect_over_cdp(cdp_url)
            logger.info("Успешно подключились к существующему Chrome")

            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                logger.info(f"Используем существующий контекст, всего контекстов: {len(contexts)}")
            else:
                logger.error("Нет доступных контекстов в браузере")
                await browser.close()
                return None

        except Exception as e:
            logger.warning(f"Не удалось подключиться к Chrome через CDP: {e}")
            logger.info("Запускаем Chrome вручную через subprocess...")

            chrome_process = _start_chrome_manually(user_data_dir, port=9222)
            if not chrome_process:
                logger.error("Не удалось запустить Chrome")
                return None

            logger.info("Ожидание запуска Chrome...")
            await asyncio.sleep(5)

            try:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                logger.info("Успешно подключились к Chrome, запущенному через subprocess")

                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    logger.info(f"Используем контекст, всего контекстов: {len(contexts)}")
                else:
                    logger.error("Нет доступных контекстов")
                    await browser.close()
                    if chrome_process:
                        chrome_process.terminate()
                    return None

            except Exception as e2:
                logger.error(f"Не удалось подключиться к Chrome после запуска: {e2}")
                if chrome_process:
                    chrome_process.terminate()
                return None

        # Закрываем все лишние вкладки, оставляем только одну
        if context.pages:
            page = context.pages[0]
            logger.info(f"Используем существующую страницу, всего страниц: {len(context.pages)}")
            for i, extra_page in enumerate(context.pages[1:], 1):
                await extra_page.close()
                logger.info(f"Закрыта лишняя вкладка {i}")
        else:
            page = await context.new_page()
            logger.info("Создана новая страница")

        # Применяем playwright-stealth
        stealth = Stealth(
            navigator_languages_override=('ru-RU', 'ru', 'en-US', 'en'),
            navigator_platform_override='Linux x86_64',
            navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        )
        await stealth.apply_stealth_async(page)
        logger.info("Stealth-патчи применены")

        await page.set_viewport_size({"width": 1920, "height": 1080})

        # Переходим на страницу корзины
        logger.info(f"Переход на страницу корзины: {url}")
        await page.goto(url, timeout=45000, wait_until='domcontentloaded')
        logger.info("Страница корзины загружена")

        await _human_delay()
        await _human_like_behavior(page)

        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем парсинг")

        # Сохраняем скриншот
        Path(f"debug_data").absolute().mkdir(parents=True, exist_ok=True)
        screenshot_path = f'debug_data/cart_{uuid4()}.png'
        await page.screenshot(path=screenshot_path)
        logger.info(f"Скриншот корзины сохранён: {screenshot_path}")

        # Проверка капчи
        logger.info("Проверка наличия капчи")
        captcha_element = page.locator('text="Сопоставьте пазл"').first
        if await captcha_element.count() > 0:
            logger.warning("Обнаружена капча! Ожидаем решения пользователем...")
            captcha_solved = await _wait_for_captcha_solve(page, timeout=120)
            if not captcha_solved:
                logger.error("Капча не была решена в течение 120 секунд")
                await browser.close()
                return None
            logger.info("Капча решена, продолжаем парсинг")
            await asyncio.sleep(2)

        # Парсинг товаров из корзины
        logger.info("Начинаем извлечение товаров из корзины")
        products = []

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
            await browser.close()
            return None

        await browser.close()
        logger.info(f"Парсинг завершён, найдено товаров: {len(products)}")

        if chrome_process:
            logger.info(f"Chrome процесс (PID: {chrome_process.pid}) оставлен запущенным")

        return products if products else None
