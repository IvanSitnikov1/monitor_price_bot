import asyncio
import json
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


async def scrape_ozon_product(url: str) -> tuple[str | None, str | None]:
    """
    Асинхронно парсит страницу товара на Ozon с помощью Playwright.

    Args:
        url: URL страницы товара.

    Returns:
        Кортеж (название, цена) или (None, None) в случае ошибки.
    """
    cookies_file = Path("ozon_cookies.json")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='ru-RU',
        )

        # Загружаем куки, если файл существует
        if cookies_file.exists():
            try:
                cookies = _load_cookies(cookies_file)
                await context.add_cookies(cookies)
            except Exception as e:
                print(f"Ошибка загрузки кук: {e}")

        page = await context.new_page()

        try:
            await page.goto(url, timeout=30000)

            # Проверяем наличие капчи (ждём до 30 секунд)
            max_wait = 30
            waited = 0

            while waited < max_wait:
                content = await page.content()

                if "Доступ ограничен" in content or "captcha" in content.lower() or "challenge" in content.lower():
                    await asyncio.sleep(2)
                    waited += 2
                else:
                    break

            if waited >= max_wait:
                await browser.close()
                return None, None

            # Ждем загрузки контента
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
            except PlaywrightTimeoutError:
                pass

            await asyncio.sleep(2)

            # Извлекаем название товара
            product_name = None
            try:
                title_element = page.locator('h1').first
                product_name = await title_element.text_content(timeout=5000)
                product_name = product_name.strip() if product_name else None
            except Exception as e:
                print(f"Не удалось найти название товара: {e}")

            # Извлекаем цену
            price = None
            try:
                price_element = page.locator('span.tsHeadline600Large').first
                price_text = await price_element.text_content(timeout=5000)

                # Извлекаем число (учитываем все виды пробелов)
                match = re.search(r'([\d\s\xa0   ]+)\s*₽', price_text)
                if match:
                    price_str = match.group(1)
                    # Удаляем все виды пробелов
                    price_str = re.sub(r'[\s\xa0   ]', '', price_str)

                    if price_str.isdigit():
                        price = f"{price_str} ₽"

            except Exception as e:
                print(f"Не удалось найти цену: {e}")

            await browser.close()
            return product_name, price

        except Exception as e:
            print(f"Общая ошибка при парсинге: {e}")
            await browser.close()
            return None, None


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

