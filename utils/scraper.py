import asyncio
import random
import re
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from loggers import logger


AVITO_CLOSED_MARKER = "avito_closed"
AVITO_DELETED_MARKER = "avito_deleted"

_COMMON_BLOCK_SELECTORS = (
    '[class*="captcha"]',
    '[id*="captcha"]',
    'iframe[src*="captcha"]',
    '[data-marker*="captcha"]',
)
_COMMON_BLOCK_TEXTS = (
    "Доступ ограничен",
    "Access denied",
    "Подтвердите, что вы не робот",
    "Проверяем, что вы не робот",
    "Verify you are human",
)
_COMMON_BLOCK_PATTERNS = (
    r"доступ.{0,80}ограничен",
    r"(?:подтвердите|проверяем|пройдите).{0,60}(?:не робот|проверку)",
    r"access.{0,40}denied",
    r"verify.{0,40}human",
    r"слишком много запросов",
    r"too many requests",
)


async def _check_explicit_block(
    page,
    marketplace: str,
    selectors: tuple[str, ...] = (),
    texts: tuple[str, ...] = (),
) -> bool:
    """Проверяет явные признаки антибот-блокировки без догадок по отсутствию DOM."""
    for selector in (*_COMMON_BLOCK_SELECTORS, *selectors):
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible(timeout=500):
                logger.warning(
                    f"Обнаружена блокировка {marketplace} по селектору: {selector}"
                )
                return True
        except Exception:
            pass

    try:
        body_text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        body_text = ""

    for text in (*_COMMON_BLOCK_TEXTS, *texts):
        if text.casefold() in body_text.casefold():
            logger.warning(f"Обнаружена блокировка {marketplace} по тексту: {text}")
            return True

    for pattern in _COMMON_BLOCK_PATTERNS:
        if re.search(pattern, body_text, re.IGNORECASE | re.DOTALL):
            logger.warning(
                f"Обнаружена блокировка {marketplace} по шаблону текста: {pattern}"
            )
            return True

    try:
        title = await page.title()
    except Exception:
        title = ""

    if re.search(
        r"captcha|access denied|доступ ограничен|проверка.{0,20}робот",
        title,
        re.IGNORECASE,
    ):
        logger.warning(f"Обнаружена блокировка {marketplace} по заголовку: {title}")
        return True

    return False


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


async def _check_ozon_block(page) -> bool:
    """
    Проверяет явные признаки блокировки Ozon.

    Args:
        page: Страница Playwright

    Returns:
        True если блокировка обнаружена, False иначе
    """
    return await _check_explicit_block(
        page,
        marketplace="Ozon",
        selectors=(
            'text="Сопоставьте пазл"',
            'text="двигая ползунок"',
        ),
        texts=("Сопоставьте пазл", "двигая ползунок"),
    )


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
                f"Товар {product_index + 1}: popup не появился, "
                "проверяем навигацию в текущей вкладке"
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
                logger.warning(
                    f"Не удалось вернуться в корзину после товара "
                    f"{product_index + 1}: {e}"
                )

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


async def _extract_product_variant_from_block(block, product_index: int) -> str | None:
    """Извлекает характеристики варианта товара из блока корзины Ozon."""
    ignored_texts = {
        "распродажа",
        "количество ограничено",
    }

    try:
        spans = block.locator("span.tsBodyControl300XSmall")
        count = await spans.count()

        for index in range(count):
            text = await spans.nth(index).text_content(timeout=1000)
            normalized_text = re.sub(r"\s+", " ", text or "").strip()
            lower_text = normalized_text.lower()

            if not normalized_text:
                continue

            if lower_text in ignored_texts:
                continue

            if re.fullmatch(r"\d+\s*ед", lower_text):
                continue

            if re.fullmatch(r"осталось\s+\d+\s*шт", lower_text):
                continue

            logger.info(f"Вариант товара {product_index + 1}: {normalized_text}")
            return normalized_text

    except Exception as e:
        logger.warning(f"Не удалось извлечь вариант товара {product_index + 1}: {e}")

    return None


def _normalize_ozon_price_text(price_text: str | None) -> str | None:
    """Извлекает цену Ozon из текста, где явно указаны рубли."""
    if not price_text or "₽" not in price_text:
        return None

    match = re.search(r"(\d[\d\s\xa0]*)\s*₽", price_text)
    if not match:
        return None

    price_digits = re.sub(r"[\s\xa0]", "", match.group(1))
    if not price_digits:
        return None

    return f"{price_digits} ₽"


async def _extract_ozon_price_from_block(block, product_index: int) -> str | None:
    """Извлекает цену товара Ozon из текущего блока с резервными селекторами."""
    price_selectors = [
        "div.checkout_t span.tsHeadline400Small",
        "div.checkout_t span:has-text('₽')",
        "span.tsHeadline400Small:has-text('₽')",
        "span:has-text('₽')",
    ]

    for selector in price_selectors:
        try:
            elements = block.locator(selector)
            count = await elements.count()
            for index in range(count):
                price_text = await elements.nth(index).text_content(timeout=1000)
                price = _normalize_ozon_price_text(price_text)
                if price:
                    if selector != price_selectors[0]:
                        logger.info(
                            f"Цена товара {product_index + 1} извлечена "
                            f"резервным селектором: {selector}"
                        )
                    return price
        except Exception as e:
            logger.warning(
                f"Не удалось извлечь цену товара {product_index + 1} "
                f"селектором {selector}: {e}"
            )

    try:
        block_text = await block.inner_text(timeout=2000)
        ignored_fragments = ("итого", "доставка", "скидка", "баллы", "бонус")
        for line in block_text.splitlines():
            normalized_line = re.sub(r"\s+", " ", line).strip()
            lower_line = normalized_line.lower()
            if any(fragment in lower_line for fragment in ignored_fragments):
                continue

            price = _normalize_ozon_price_text(normalized_line)
            if price:
                logger.info(
                    f"Цена товара {product_index + 1} извлечена из текста блока"
                )
                return price
    except Exception as e:
        logger.warning(f"Не удалось извлечь текст блока товара {product_index + 1}: {e}")

    return None


async def _is_ozon_product_unavailable(block, product_index: int) -> bool:
    """Проверяет, что товар Ozon в корзине закончился или недоступен."""
    unavailable_markers = (
        "товар закончился",
        "закончился",
        "нет в наличии",
        "товар недоступен",
        "сообщить о поступлении",
        "похожий товар",
    )

    try:
        block_text = await block.inner_text(timeout=2000)
    except Exception as e:
        logger.warning(
            f"Не удалось проверить доступность товара {product_index + 1}: {e}"
        )
        return False

    normalized_text = re.sub(r"\s+", " ", block_text).lower()
    if any(marker in normalized_text for marker in unavailable_markers):
        logger.info(f"Товар {product_index + 1}: распознан как закончившийся")
        return True

    return False


async def _extract_product_details_from_block(
    page,
    block,
    product_index: int
) -> dict[str, str | None]:
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
) -> list[dict[str, str | None]] | dict[str, object] | str | None:
    """
    Парсит корзину Ozon и извлекает список товаров.

    Args:
        url: URL корзины (формат: https://www.ozon.ru/cart?share=...)
        user_id: ID пользователя Telegram
        browser_manager: Экземпляр BrowserManager для переиспользования браузера
        product_details_extractor: Функция для извлечения дополнительных данных товара
        is_refresh: Если True, обновляет существующую страницу вместо открытия новой
        maintenance_callback: Callback для активации maintenance mode при блокировке Ozon

    Returns:
        Список словарей с данными товаров,
        строка "maintenance_required" при явной блокировке,
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

    # Проверка явной блокировки
    logger.info("Проверка явных признаков блокировки Ozon")
    if await _check_ozon_block(page):
        logger.error("Обнаружена блокировка Ozon, требуется общая пауза")
        if maintenance_callback:
            await maintenance_callback()
        return "maintenance_required"

    # Парсинг товаров из корзины
    logger.info("Начинаем извлечение товаров из корзины")
    products = []
    skipped_products_count = 0

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
                    logger.warning(f"Не удалось извлечь название товара {i + 1}: {e}")
                    name = None

                price = await _extract_ozon_price_from_block(block, i)
                if not price:
                    logger.warning(f"Не удалось извлечь цену товара {i + 1}")

                if name and price:
                    variant_text = await _extract_product_variant_from_block(block, i)
                    product = {
                        "name": name,
                        "price": price,
                        "variant_text": variant_text,
                    }
                    if product_details_extractor:
                        product.update(await product_details_extractor(page, block, i))

                    products.append(product)
                    logger.info(
                        f"Товар {i+1}: {name} - {price} - "
                        f"{product.get('url')} - {product.get('image_url')}"
                    )
                elif name and await _is_ozon_product_unavailable(block, i):
                    variant_text = await _extract_product_variant_from_block(block, i)
                    product = {
                        "name": name,
                        "price": None,
                        "variant_text": variant_text,
                        "unavailable": True,
                    }
                    products.append(product)
                    logger.info(f"Товар {i+1}: {name} - закончился")
                else:
                    skipped_products_count += 1
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

                if maintenance_callback:
                    logger.warning("Активация maintenance mode после блокировки Ozon")
                    await maintenance_callback()

                return "maintenance_required"

        # Если товары найдены или блокировки нет — выходим из цикла
        break

    if not products:
        return None

    if skipped_products_count > 0:
        logger.warning(
            f"Корзина Ozon распарсена частично: товаров={len(products)}, "
            f"пропущено={skipped_products_count}"
        )
        return {
            "products": products,
            "partial": True,
            "skipped_count": skipped_products_count,
        }

    return products


async def _check_wildberries_block(page) -> bool:
    """Проверяет явные признаки блокировки Wildberries."""
    return await _check_explicit_block(
        page,
        marketplace="Wildberries",
        selectors=(
            '[class*="robot"]',
            '[data-testid*="captcha"]',
        ),
    )


async def _check_avito_block(page) -> bool:
    """Проверяет явные признаки блокировки Avito."""
    return await _check_explicit_block(
        page,
        marketplace="Avito",
        selectors=(
            '[data-marker*="captcha"]',
            'form[action*="captcha"]',
        ),
        texts=("Пройдите проверку, чтобы продолжить",),
    )


def _normalize_avito_page_text(text: str | None) -> str:
    """Нормализует текст служебного состояния страницы Avito."""
    normalized = (text or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


async def _check_avito_deleted(page) -> bool:
    """Проверяет штатную страницу удаленного пользователем объявления Avito."""
    try:
        heading_texts = await page.locator("h1").all_text_contents()
        paragraph_texts = await page.locator("p").all_text_contents()

        has_deleted_heading = any(
            "объявление не посмотреть" in _normalize_avito_page_text(text)
            for text in heading_texts
        )
        has_deleted_paragraph = any(
            "пользователь его удалил" in _normalize_avito_page_text(text)
            for text in paragraph_texts
        )
        if has_deleted_heading and has_deleted_paragraph:
            return True

        body_text = await page.locator("body").inner_text(timeout=2000)
        normalized_body_text = _normalize_avito_page_text(body_text)
        return (
            "объявление не посмотреть" in normalized_body_text
            and "пользователь его удалил" in normalized_body_text
        )
    except Exception as e:
        logger.debug(f"Avito: не удалось проверить страницу удаленного объявления: {e}")
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
        is_refresh: Если True, повторно разбирает уже загруженную страницу
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
        can_reuse_page = (
            is_refresh
            and hasattr(browser_manager, "has_cart_page")
            and hasattr(browser_manager, "get_cart_page")
            and browser_manager.has_cart_page(user_id, "wildberries", url)
        )
        if can_reuse_page:
            page = await browser_manager.get_cart_page(
                url,
                user_id,
                marketplace="wildberries"
            )
            logger.info("Повторно используем загруженную страницу Wildberries")
        elif hasattr(browser_manager, "open_cart_page"):
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
                    const ignoredVariantTexts = new Set([name, priceText, '']);
                    const variantText = Array.from(
                        node.querySelectorAll(
                            '[class*="productDescription"], [class*="productOption"], ' +
                            '[class*="size"], [class*="color"]'
                        )
                    )
                        .map((item) => normalize(item.textContent))
                        .filter((text) => (
                            text &&
                            !ignoredVariantTexts.has(text) &&
                            text.length <= 120 &&
                            !/^\\d+\\s*шт$/i.test(text)
                        ))
                        .filter((text, index, items) => items.indexOf(text) === index)
                        .join(' • ') || null;
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
                        variant_text: variantText,
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
            "variant_text": product.get("variant_text"),
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
    maintenance_callback=None,
    reuse_page: bool = False,
) -> dict[str, str | int | None] | str | None:
    """Парсит объявление Avito с возможностью повторно использовать загруженную страницу."""
    try:
        can_reuse_page = (
            reuse_page
            and hasattr(browser_manager, "has_cart_page")
            and hasattr(browser_manager, "get_cart_page")
            and browser_manager.has_cart_page(user_id, "avito", url)
        )
        if can_reuse_page:
            page = await browser_manager.get_cart_page(
                url,
                user_id,
                marketplace="avito",
            )
            logger.info("Повторно используем загруженную страницу Avito")
        else:
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

        if await _check_avito_deleted(page):
            logger.warning(f"Объявление Avito удалено пользователем: {url}")
            return AVITO_DELETED_MARKER

        await _human_like_behavior(page)
        if await _check_avito_block(page):
            logger.warning(f"Avito заблокировал доступ к объявлению: {url}")
            if maintenance_callback:
                await maintenance_callback()
            return "maintenance_required"

        data = await page.evaluate("""
            () => {
                const bodyText = document.body?.innerText || '';
                const closedWarning = document.querySelector(
                    '[data-marker="item-view/closed-warning"]'
                );
                const closedWarningText = closedWarning?.textContent || '';
                const closed = closedWarningText.includes('Объявление снято с публикации');
                const unavailable = bodyText.includes('Такой страницы не существует');
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

                return {closed, unavailable, title, price, imageUrl};
            }
        """)

        if data.get("closed"):
            logger.warning(f"Объявление Avito снято с публикации: {url}")
            return AVITO_CLOSED_MARKER

        if data.get("unavailable"):
            logger.warning(f"Объявление Avito недоступно: {url}")
            return None

        normalized_price = _normalize_price(data.get("price"))
        if not data.get("title") or not normalized_price:
            logger.warning(f"Avito: не удалось извлечь название или цену: {url}")
            return None

        price = int(re.sub(r"\D", "", normalized_price))
        logger.info(f"Avito: объявление распарсено: {data['title']} - {price} ₽")

        return {
            "name": data["title"],
            "price": price,
            "url": url,
            "image_url": _normalize_image_url(data.get("imageUrl")),
        }
    except Exception as e:
        logger.error(f"Ошибка при парсинге объявления Avito {url}: {e}", exc_info=True)
        return None
