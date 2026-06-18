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

        # Эмулируем ручной ввод URL (более естественный способ)
        logger.info("Эмуляция ручного ввода URL в адресную строку")

        # Фокусируемся на адресной строке (Ctrl+L)
        await new_page.keyboard.press('Control+L')
        await asyncio.sleep(random.uniform(0.3, 0.7))

        # Набираем URL посимвольно с человеческими задержками
        url_to_type = "ozon.ru"
        for char in url_to_type:
            await new_page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

        # Нажимаем Enter
        await asyncio.sleep(random.uniform(0.3, 0.8))
        await new_page.keyboard.press('Enter')
        logger.info("URL введён, ожидаем загрузки")

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


async def _close_product_pages(cart_page):
    """Возвращает фокус на вкладку корзины после работы с карточкой товара."""
    try:
        await cart_page.bring_to_front()
    except Exception as e:
        logger.warning(f"Не удалось вернуть фокус на вкладку корзины: {e}")


async def _extract_product_url_from_block(page, block, product_index: int) -> str | None:
    """
    Открывает товар из блока корзины и возвращает URL страницы товара.

    Использует обычный клик после короткой человеческой паузы и движения мыши.
    Если товар открылся в текущей вкладке, URL берется оттуда и выполняется возврат
    назад в корзину.
    """
    click_target = block.locator('span.tsCompact400Large').first
    original_url = page.url
    product_page = None

    try:
        await click_target.scroll_into_view_if_needed(timeout=5000)
        await asyncio.sleep(random.uniform(0.4, 1.1))
        box = await click_target.bounding_box()
        if not box:
            logger.warning(f"Товар {product_index + 1}: не удалось определить позицию элемента")
            return None

        await page.mouse.move(
            box["x"] + random.uniform(3, 12),
            box["y"] + random.uniform(3, 12),
        )
        await asyncio.sleep(random.uniform(0.2, 0.6))

        popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=8000))
        await click_target.click(timeout=5000)

        try:
            product_page = await popup_task
            try:
                await product_page.wait_for_load_state('domcontentloaded', timeout=30000)
            except PlaywrightTimeoutError:
                logger.warning(f"Товар {product_index + 1}: timeout загрузки страницы товара")

            await asyncio.sleep(random.uniform(1.8, 4.2))
            product_url = product_page.url
            logger.info(f"URL товара {product_index + 1}: {product_url}")
            return product_url
        except Exception:
            if not popup_task.done():
                popup_task.cancel()
            logger.warning(
                f"Товар {product_index + 1}: popup не появился, проверяем навигацию в текущей вкладке"
            )

        try:
            await page.wait_for_load_state('domcontentloaded', timeout=30000)
            await asyncio.sleep(random.uniform(1.8, 4.2))

            if page.url == original_url:
                logger.warning(f"Товар {product_index + 1}: URL не изменился после клика")
                return None

            product_url = page.url
            logger.info(f"URL товара {product_index + 1}: {product_url}")

            await page.go_back(timeout=30000, wait_until='domcontentloaded')
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Networkidle timeout после возврата в корзину")

            return product_url

        except Exception as e:
            logger.warning(f"Не удалось получить URL товара {product_index + 1}: {e}")
            return None

    finally:
        if product_page:
            try:
                await product_page.close()
            except Exception as e:
                logger.warning(f"Не удалось закрыть вкладку товара {product_index + 1}: {e}")

        if not product_page and page.url != original_url:
            try:
                await page.goto(original_url, timeout=30000, wait_until='domcontentloaded')
            except Exception as e:
                logger.warning(f"Не удалось вернуться в корзину после товара {product_index + 1}: {e}")

        await _close_product_pages(page)


def _normalize_image_url(image_url: str | None) -> str | None:
    """Приводит URL изображения к абсолютному виду."""
    if not image_url:
        return None

    image_url = image_url.strip()
    if not image_url:
        return None

    if "," in image_url:
        image_url = image_url.split(",")[0].strip()

    if " " in image_url:
        image_url = image_url.split(" ")[0].strip()

    if image_url.startswith("//"):
        return f"https:{image_url}"

    return image_url


async def _extract_product_image_url_from_block(block, product_index: int) -> str | None:
    """Извлекает URL миниатюры товара из блока корзины."""
    image = block.locator("img").first

    try:
        if await image.count() == 0:
            logger.warning(f"Товар {product_index + 1}: изображение не найдено")
            return None

        for attribute in ("src", "data-src", "srcset", "data-srcset"):
            image_url = _normalize_image_url(await image.get_attribute(attribute))
            if image_url:
                logger.info(f"Изображение товара {product_index + 1}: {image_url}")
                return image_url

    except Exception as e:
        logger.warning(f"Не удалось извлечь изображение товара {product_index + 1}: {e}")

    return None


async def _extract_product_details_from_block(page, block, product_index: int) -> dict[str, str | None]:
    """Извлекает дополнительные данные товара при добавлении корзины."""
    image_url = await _extract_product_image_url_from_block(block, product_index)
    product_url = await _extract_product_url_from_block(page, block, product_index)

    return {
        "url": product_url,
        "image_url": image_url,
    }


async def _scrape_ozon_cart(
    url: str,
    user_id: int,
    browser_manager,
    product_details_extractor=None,
    is_refresh: bool = False,
    maintenance_callback=None
) -> list[dict[str, str | None]] | str | None:
    """
    Парсит корзину Ozon и извлекает список товаров.

    Args:
        url: URL корзины (формат: https://www.ozon.ru/cart?share=...)
        user_id: ID пользователя Telegram
        browser_manager: Экземпляр BrowserManager для переиспользования браузера
        product_details_extractor: Функция для извлечения дополнительных данных товара
        is_refresh: Если True, обновляет существующую страницу вместо открытия новой
        maintenance_callback: Callback для активации maintenance mode при неудачной разблокировке

    Returns:
        Список словарей с данными товаров,
        строка "maintenance_required" при неудачной разблокировке,
        или None при других ошибках
    """
    logger.info(f"Начало парсинга корзины: {url}, user_id: {user_id}, is_refresh: {is_refresh}")

    try:
        if is_refresh:
            page = await browser_manager.refresh_cart_page(user_id, marketplace="ozon")
        else:
            page = await browser_manager.get_cart_page(url, user_id, marketplace="ozon")
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
                    product = {"name": name, "price": price}
                    if product_details_extractor:
                        product.update(await product_details_extractor(page, block, i))

                    products.append(product)
                    logger.info(
                        f"Товар {i+1}: {name} - {price} - "
                        f"{product.get('url')} - {product.get('image_url')}"
                    )
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
                if hasattr(browser_manager, "set_cart_page"):
                    browser_manager.set_cart_page(user_id, "ozon", url, page)
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


async def _check_wildberries_block(page) -> bool:
    """Проверяет явные признаки блокировки Wildberries."""
    block_selectors = [
        'text="Проверяем, что вы не робот"',
        'text="Подтвердите, что вы не робот"',
        'text="Доступ ограничен"',
        'text="Access denied"',
        '[class*="captcha"]',
        '[id*="captcha"]',
    ]

    for selector in block_selectors:
        try:
            if await page.locator(selector).count() > 0:
                logger.warning(f"Обнаружена блокировка Wildberries: {selector}")
                return True
        except Exception:
            pass

    try:
        title = await page.title()
        if re.search(r"captcha|access denied|доступ ограничен", title, re.IGNORECASE):
            logger.warning(f"Обнаружена блокировка Wildberries по title: {title}")
            return True
    except Exception:
        pass

    return False


def _normalize_price(price_text: str | None) -> str | None:
    """Приводит текст цены к формату '123 ₽'."""
    if not price_text:
        return None

    match = re.search(r"([\d\s\xa0]+)", price_text)
    if not match:
        return None

    price_digits = re.sub(r"[\s\xa0]", "", match.group(1))
    if not price_digits:
        return None

    return f"{price_digits} ₽"


async def scrape_wildberries_cart(
    url: str,
    user_id: int,
    browser_manager,
    is_refresh: bool = False,
    maintenance_callback=None
) -> list[dict[str, str | None]] | str | None:
    """
    Парсит публичную корзину Wildberries без авторизации.

    Args:
        url: URL корзины (формат: https://www.wildberries.ru/basket?shareId=...)
        user_id: ID пользователя Telegram
        browser_manager: Экземпляр BrowserManager для переиспользования браузера
        is_refresh: Если True, обновляет существующую страницу вместо открытия новой
        maintenance_callback: Callback для активации maintenance mode при блокировке

    Returns:
        Список словарей с данными товаров,
        строка "maintenance_required" при явной блокировке,
        или None при других ошибках.
    """
    logger.info(
        f"Начало парсинга корзины Wildberries: {url}, "
        f"user_id: {user_id}, is_refresh: {is_refresh}"
    )

    try:
        if hasattr(browser_manager, "open_cart_page"):
            page = await browser_manager.open_cart_page(
                url,
                user_id,
                marketplace="wildberries"
            )
        else:
            page = await browser_manager.get_cart_page(
                url,
                user_id,
                marketplace="wildberries"
            )
    except Exception as e:
        logger.error(f"Ошибка при работе с BrowserManager для Wildberries: {e}")
        return None

    await _human_delay()
    await _human_like_behavior(page)

    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except PlaywrightTimeoutError:
        logger.warning("Networkidle timeout Wildberries - продолжаем парсинг")

    Path("debug_data").absolute().mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    screenshot_path = f"debug_data/wildberries_cart_user{user_id}_{timestamp}.png"
    await page.screenshot(path=screenshot_path)
    logger.info(f"Скриншот корзины Wildberries сохранён: {screenshot_path}")

    if await _check_wildberries_block(page):
        logger.error("Обнаружена блокировка Wildberries")
        if maintenance_callback:
            await maintenance_callback()
        return "maintenance_required"

    try:
        raw_products = await page.evaluate("""
            () => {
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const absoluteUrl = (value) => {
                    if (!value) return null;
                    if (value.startsWith('//')) return `https:${value}`;
                    if (value.startsWith('/')) return `${location.origin}${value}`;
                    return value;
                };
                const buildProductUrl = (imageUrl, linkUrl) => {
                    const nmId = imageUrl?.match(/\\/part\\d+\\/(\\d+)\\/images\\//)?.[1];
                    if (nmId) {
                        return `https://www.wildberries.ru/catalog/${nmId}/detail.aspx`;
                    }

                    return absoluteUrl(linkUrl);
                };

                const shareContent = document.querySelector('.popup-share-content');
                const shareProducts = shareContent?.querySelector('.popup-share-products');
                if (!shareProducts) {
                    return [];
                }

                const nodes = Array.from(
                    shareProducts.querySelectorAll('[class^="productWrap--"]')
                );
                const products = [];
                const seen = new Set();

                for (const node of nodes) {
                    const nameNode = node.querySelector(
                        '[class^="productDescriptionName--"]'
                    );
                    const lowerPriceNode = node.querySelector(
                        '[class^="productPriceLowerPrice--"]'
                    );
                    const oldPriceNode = node.querySelector(
                        '[class^="productPriceOldPrice--"]'
                    );
                    const linkNode = node.querySelector('a[href*="/catalog/"]');
                    const imageNode = node.querySelector('img');

                    const name = normalize(nameNode?.textContent);
                    const priceText = normalize(
                        lowerPriceNode?.textContent || oldPriceNode?.textContent
                    );
                    const imageUrl = absoluteUrl(
                        imageNode?.getAttribute('data-src-pb') ||
                        imageNode?.getAttribute('src') ||
                        imageNode?.getAttribute('data-src') ||
                        imageNode?.getAttribute('srcset')?.split(' ')[0]
                    );
                    const url = buildProductUrl(imageUrl, linkNode?.getAttribute('href'));

                    if (!name || !priceText || name.length < 5) continue;

                    const key = `${name}|${priceText}`;
                    if (seen.has(key)) continue;
                    seen.add(key);

                    products.push({
                        name,
                        price: priceText,
                        url,
                        image_url: imageUrl
                    });
                }

                return products;
            }
        """)
    except Exception as e:
        logger.error(f"Ошибка при извлечении товаров Wildberries: {e}")
        return None

    products = []
    for index, product in enumerate(raw_products, 1):
        name = product.get("name")
        price = _normalize_price(product.get("price"))

        if not name or not price:
            logger.warning(f"Товар Wildberries {index}: пропущен (name={name}, price={price})")
            continue

        normalized_product = {
            "name": name,
            "price": price,
            "url": _normalize_image_url(product.get("url")),
            "image_url": _normalize_image_url(product.get("image_url")),
        }
        products.append(normalized_product)
        logger.info(
            f"Товар Wildberries {index}: {name} - {price} - "
            f"{normalized_product.get('url')} - {normalized_product.get('image_url')}"
        )

    return products if products else None


async def scrape_ozon_cart_with_urls(
    url: str,
    user_id: int,
    browser_manager,
    is_refresh: bool = False,
    maintenance_callback=None
) -> list[dict[str, str | None]] | str | None:
    """Парсит корзину при добавлении и извлекает URL карточек товаров."""
    return await _scrape_ozon_cart(
        url=url,
        user_id=user_id,
        browser_manager=browser_manager,
        product_details_extractor=_extract_product_details_from_block,
        is_refresh=is_refresh,
        maintenance_callback=maintenance_callback
    )


async def scrape_ozon_cart(
    url: str,
    user_id: int,
    browser_manager,
    is_refresh: bool = False,
    maintenance_callback=None
) -> list[dict[str, str | None]] | str | None:
    """Парсит корзину при обновлении цен без открытия карточек товаров."""
    return await _scrape_ozon_cart(
        url=url,
        user_id=user_id,
        browser_manager=browser_manager,
        is_refresh=is_refresh,
        maintenance_callback=maintenance_callback
    )


async def scrape_avito_product(
    url: str,
    user_id: int,
    browser_manager,
    maintenance_callback=None
) -> dict[str, str | int | None] | str | None:
    """Парсит отдельное объявление Avito через Playwright."""
    try:
        page = await browser_manager.get_product_page(
            product_url=url,
            user_id=user_id,
            marketplace="avito"
        )
        await _human_delay(1, 2)

        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            logger.warning("Avito: ожидание networkidle завершилось по тайм-ауту")

        await _human_like_behavior(page)
        data = await page.evaluate("""
            () => {
                const bodyText = document.body?.innerText || '';
                const blockedTexts = [
                    'Доступ ограничен',
                    'Подтвердите, что вы не робот',
                    'Проверяем, что вы не робот'
                ];
                const blocked = blockedTexts.some((text) => bodyText.includes(text));
                const unavailable = bodyText.includes('Объявление снято с публикации') ||
                    bodyText.includes('Такой страницы не существует');
                const title = document.querySelector(
                    '[data-marker="item-view/title-info"]'
                )?.textContent?.trim();
                const priceElement = document.querySelector(
                    '[data-marker="item-view/item-price"]'
                );
                const price = priceElement?.getAttribute('content') ||
                    priceElement?.textContent?.trim();
                const imageElement = document.querySelector(
                    '[data-marker="image-frame/image"]'
                );
                const imageUrl = imageElement?.getAttribute('src') ||
                    document.querySelector(
                        '[data-marker="image-frame/image-wrapper"]'
                    )?.getAttribute('data-url');

                return {blocked, unavailable, title, price, imageUrl};
            }
        """)

        if data.get("blocked"):
            logger.warning(f"Avito заблокировал доступ к объявлению: {url}")
            if maintenance_callback:
                await maintenance_callback()
            return "maintenance_required"

        if data.get("unavailable"):
            logger.warning(f"Объявление Avito недоступно: {url}")
            return None

        normalized_price = _normalize_price(data.get("price"))
        if not data.get("title") or not normalized_price:
            logger.warning(f"Avito: не удалось извлечь название или цену: {url}")
            return None

        price = int(re.sub(r"\D", "", normalized_price))

        return {
            "name": data["title"],
            "price": price,
            "url": url,
            "image_url": _normalize_image_url(data.get("imageUrl")),
        }
    except Exception as e:
        logger.error(f"Ошибка при парсинге объявления Avito {url}: {e}", exc_info=True)
        return None
