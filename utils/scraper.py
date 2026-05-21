import asyncio
import json
import logging
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


logger = logging.getLogger(__name__)


async def scrape_ozon_product(url: str) -> tuple[str | None, str | None]:
    """
    Парсит данные товара Ozon, используя реальный профиль Chrome для учета региона и аккаунта.
    """
    logger.info(f"Начало процесса для ссылки: {url}")

    # Путь к папке профиля Chrome/Chromium
    # Приоритет: 1) скопированный профиль, 2) системный профиль, 3) новый профиль
    user_data_dir = Path("./user_data").absolute()

    # Если локальный профиль не существует, пробуем использовать системный
    if not (user_data_dir / "Default").exists():
        # Пробуем найти профиль в разных местах
        possible_profiles = [
            Path.home() / ".config" / "chromium",  # Chromium на Linux
            Path.home() / ".config" / "google-chrome",  # Chrome на Linux
            Path.home() / "snap" / "chromium" / "common" / "chromium",  # Chromium snap
        ]

        system_profile = None
        for profile_path in possible_profiles:
            if profile_path.exists() and (profile_path / "Default").exists():
                system_profile = profile_path
                logger.info(f"Найден системный профиль: {system_profile}")
                break

        if system_profile:
            user_data_dir = system_profile
        else:
            logger.warning(f"Системный профиль не найден. Создаю новый в {user_data_dir}")
            user_data_dir.mkdir(parents=True, exist_ok=True)

    lock_file = user_data_dir / "Default" / "SingletonLock"
    if lock_file.exists():
        lock_file.unlink()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            args=['--disable-blink-features=AutomationControlled'],
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            window.chrome = { runtime: {} };
        """)
        page = context.pages[0] if context.pages else await context.new_page()

        # Сначала переходим на домен, потом добавляем cookies
        await page.goto("https://www.ozon.ru", timeout=30000)

        cookies = _load_cookies(Path("ozon_cookies.json"))  # твоя функция уже есть!
        await context.add_cookies(cookies)

        # Теперь переходим на нужный URL
        await page.goto(url, timeout=45000)

        # --- Логика ожидания (защита от капчи/заглушек) ---
        max_wait = 30
        waited = 0
        while waited < max_wait:
            content = await page.content()
            if "captcha" in content.lower() or "challenge" in content.lower() or "проверка системы" in content.lower():
                logger.warning(f"Обнаружена проверка (капча), ждем... ({waited}/{max_wait}с)")
                await asyncio.sleep(2)
                waited += 2
            else:
                break

        if waited >= max_wait:
            logger.error("Не удалось пройти проверку системы за отведенное время")
            await context.close()
            return None, None

        # Ожидание стабилизации страницы
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем парсинг")

        await asyncio.sleep(2)  # Доп. пауза для рендеринга цен

        # --- Извлечение названия ---
        product_name = None
        try:
            title_element = page.locator('h1').first
            product_name = await title_element.text_content(timeout=5000)
            if product_name:
                product_name = product_name.strip()
            logger.info(f"Название: {product_name}")
        except Exception as e:
            logger.error(f"Не удалось найти название: {e}")

        # --- Извлечение цены ---
        price = None
        try:
            # Озон часто меняет классы, используем ваш селектор + запасной вариант
            price_locator = page.locator('span.tsHeadline600Large').first

            # Если ваш старый селектор не сработал, пробуем универсальный по значку рубля
            if await price_locator.count() == 0:
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

        except Exception as e:
            logger.error(f"Не удалось найти цену: {e}")

        await context.close()
        return product_name, price


def _load_cookies(filepath: Path) -> list[dict]:
    """Загружает куки из JSON файла и преобразует в формат Playwright"""
    with open(filepath, 'r', encoding='utf-8') as f:
        cookies = json.load(f)

    playwright_cookies = []
    for cookie in cookies:
        playwright_cookie = {
            'name': cookie.get('name'),
            'value': cookie.get('value'),
            'domain': cookie.get('domain', '.ozon.ru'),
            'path': cookie.get('path', '/'),
        }

        # Добавляем опциональные поля
        if 'expirationDate' in cookie:
            playwright_cookie['expires'] = cookie['expirationDate']
        if 'httpOnly' in cookie:
            playwright_cookie['httpOnly'] = cookie['httpOnly']
        if 'secure' in cookie:
            playwright_cookie['secure'] = cookie['secure']

        # Исправляем sameSite
        if 'sameSite' in cookie:
            same_site = cookie['sameSite']
            if same_site in ['Strict', 'Lax', 'None']:
                playwright_cookie['sameSite'] = same_site
            elif same_site == 'no_restriction':
                playwright_cookie['sameSite'] = 'None'
            elif same_site == 'unspecified':
                playwright_cookie['sameSite'] = 'Lax'

        playwright_cookies.append(playwright_cookie)

    return playwright_cookies

