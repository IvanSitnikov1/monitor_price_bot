import asyncio
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


async def scrape_ozon_product(url: str) -> tuple[str | None, str | None]:
    """
    Асинхронно парсит страницу товара на Ozon с помощью undetected-chromedriver.

    Args:
        url: URL страницы товара.

    Returns:
        Кортеж (название, цена) или (None, None) в случае ошибки.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _scrape_ozon_product_sync, url)


def _scrape_ozon_product_sync(url: str) -> tuple[str | None, str | None]:
    """
    Синхронная функция для парсинга данных о товаре с Ozon.
    """
    options = uc.ChromeOptions()
    options.add_argument("--no-proxy-server")
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36")
    
    # Эта опция пытается скрыть факт использования автоматизированного ПО
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = None
    try:
        driver = uc.Chrome(options=options, use_subprocess=False, version_main=147)
        driver.get(url)

        wait = WebDriverWait(driver, 20)
        wait = WebDriverWait(driver, 20)

        try:
            # Ждем загрузки основного контента, например, заголовка
            wait.until(EC.visibility_of_element_located((By.TAG_NAME, "h1")))
        except TimeoutException:
            print("Не удалось дождаться заголовка h1, но страница все равно будет сохранена.")

        # Сохраняем HTML-код страницы в файл для отладки
        with open("ozon_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        print("HTML-код страницы сохранен в файл ozon_page.html")

        # Возвращаем заглушки
        product_name = "Смотри ozon_page.html"
        price = "Смотри ozon_page.html"

        driver.quit()
        return product_name, price

    except Exception as e:
        print(f"Общая ошибка при парсинге: {e}")
        if driver:
            driver.quit()
        return "Доступ ограничен", "Цена не найдена"

