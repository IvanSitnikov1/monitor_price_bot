from playwright.sync_api import sync_playwright
import re
import time
import json


def load_cookies(filepath):
    """Загружает куки из JSON файла"""
    with open(filepath, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    
    # Преобразуем в формат Playwright
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


def parse_ozon_price(url, cookies_file='ozon_cookies.json'):
    """
    Парсит страницу товара на Ozon и извлекает текущую цену.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='ru-RU',
        )
        
        # Загружаем куки
        try:
            cookies = load_cookies(cookies_file)
            context.add_cookies(cookies)
            print(f"Загружено {len(cookies)} кук")
        except FileNotFoundError:
            print(f"Файл {cookies_file} не найден. Работаем без кук.")
        except Exception as e:
            print(f"Ошибка загрузки кук: {e}")
        
        page = context.new_page()
        
        try:
            page.goto(url, timeout=30000)
            
            # Проверяем наличие капчи и ждем её решения
            print("Проверка капчи...")
            max_wait = 120
            waited = 0
            captcha_shown = False
            
            while waited < max_wait:
                content = page.content()
                
                if "Доступ ограничен" in content or "captcha" in content.lower() or "challenge" in content.lower():
                    if not captcha_shown:
                        print("Обнаружена капча. Решите её вручную в браузере...")
                        captcha_shown = True
                    time.sleep(2)
                    waited += 2
                else:
                    if captcha_shown:
                        print("Капча решена! Загружаем страницу...")
                    break
            
            if waited >= max_wait:
                print("Превышено время ожидания капчи")
                browser.close()
                return
            
            # Ждем загрузки контента после капчи
            print("Ожидание загрузки страницы...")
            page.wait_for_load_state('networkidle', timeout=15000)
            time.sleep(5)
            
            # Ищем цену с классом tsHeadline600Large (основная цена)
            try:
                price_element = page.locator('span.tsHeadline600Large').first
                price_text = price_element.text_content(timeout=5000)
                
                # Извлекаем число (учитываем все виды пробелов)
                match = re.search(r'([\d\s\xa0\u00a0\u2009\u202f]+)\s*₽', price_text)
                if match:
                    price_str = match.group(1)
                    # Удаляем все виды пробелов
                    price_str = re.sub(r'[\s\xa0\u00a0\u2009\u202f]', '', price_str)
                    
                    if price_str.isdigit():
                        price = int(price_str)
                        print(f"Текущая цена: {price} ₽")
                    else:
                        print(f"Не удалось распарсить цену: {price_text}")
                else:
                    print(f"Не найден паттерн цены в тексте: {price_text}")
                    
            except Exception as e:
                print(f"Не удалось найти элемент с ценой: {e}")
                
        except Exception as e:
            print(f"Ошибка: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()


if __name__ == "__main__":
    url = input("Введите URL товара на Ozon: ").strip()
    if url:
        parse_ozon_price(url)
