"""
Тестовый скрипт для проверки разблокировки Ozon через Яндекс.
Останавливается на главной странице Ozon для проверки капчи.
"""

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _get_system_profile() -> Path:
    """Определяет путь к системному профилю Chrome/Chromium."""
    system_profile = Path.home() / ".config" / "chromium"
    logger.info(f"Найден системный профиль: {system_profile}")
    return system_profile


def _start_chrome_manually(user_data_dir: Path, port: int = 9222) -> subprocess.Popen | None:
    """
    Запускает Chrome вручную через subprocess (без Playwright).
    Это обходит детект автоматизации, т.к. Chrome запускается как обычный процесс.
    """
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
            start_new_session=True
        )
        logger.info(f"Chrome запущен вручную, PID: {process.pid}")
        time.sleep(3)
        return process
    except Exception as e:
        logger.error(f"Не удалось запустить Chrome вручную: {e}")
        return None


async def test_yandex_unlock():
    """Тестирует переход на Ozon через поиск в Яндексе."""
    logger.info("🚀 Начало теста разблокировки через Яндекс")

    user_data_dir = _get_system_profile()
    cdp_url = "http://localhost:9222"
    chrome_process = None

    async with async_playwright() as p:
        logger.info(f"Попытка подключения к Chrome через CDP: {cdp_url}")

        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            logger.info("✅ Успешно подключились к существующему Chrome")

            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                logger.info(f"Используем существующий контекст, всего контекстов: {len(contexts)}")
            else:
                logger.error("❌ Нет доступных контекстов в браузере")
                await browser.close()
                return

        except Exception as e:
            logger.warning(f"⚠️  Не удалось подключиться к Chrome через CDP: {e}")
            logger.info("Запускаем Chrome вручную через subprocess...")

            chrome_process = _start_chrome_manually(user_data_dir, port=9222)
            if not chrome_process:
                logger.error("❌ Не удалось запустить Chrome")
                return

            logger.info("Ожидание запуска Chrome...")
            await asyncio.sleep(5)

            try:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                logger.info("✅ Успешно подключились к Chrome, запущенному через subprocess")

                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    logger.info(f"Используем контекст, всего контекстов: {len(contexts)}")
                else:
                    logger.error("❌ Нет доступных контекстов")
                    await browser.close()
                    if chrome_process:
                        chrome_process.terminate()
                    return

            except Exception as e2:
                logger.error(f"❌ Не удалось подключиться к Chrome после запуска: {e2}")
                if chrome_process:
                    chrome_process.terminate()
                return

        # Используем существующую страницу или создаём новую
        if context.pages:
            page = context.pages[0]
            logger.info(f"Используем существующую страницу, всего страниц: {len(context.pages)}")
            # Закрываем лишние вкладки (кроме первой)
            for i, extra_page in enumerate(context.pages[1:], 1):
                try:
                    await extra_page.close()
                    logger.info(f"Закрыта лишняя вкладка {i}")
                except Exception as e:
                    logger.warning(f"Не удалось закрыть вкладку {i}: {e}")
        else:
            logger.info("Нет открытых страниц, пропускаем stealth-патчи (CDP-контекст)")
            page = None

        # Если страница недоступна, завершаем
        if not page:
            logger.error("❌ Не удалось получить страницу")
            await browser.close()
            if chrome_process:
                chrome_process.terminate()
            return

        # Для CDP-контекста stealth-патчи уже применены на уровне браузера
        logger.info("✅ Используем существующий контекст (stealth-патчи на уровне браузера)")

        # Устанавливаем реалистичный viewport
        await page.set_viewport_size({"width": 1920, "height": 1080})
        logger.info("✅ Viewport установлен: 1920x1080")

        try:
            # Открываем Яндекс
            logger.info("🔍 Открываю Яндекс...")
            await page.goto("https://ya.ru/", timeout=30000)
            await asyncio.sleep(2)

            # Ищем поле поиска и вводим "ozon"
            logger.info("🔍 Ищу 'ozon' в Яндексе...")
            search_input = page.locator('textarea#text').first
            await search_input.click()
            await asyncio.sleep(0.5)
            await search_input.fill("ozon")
            await asyncio.sleep(1)
            await search_input.press("Enter")

            # Ждём результатов поиска
            await page.wait_for_load_state('networkidle', timeout=10000)
            await asyncio.sleep(2)

            # Кликаем по первой ссылке на ozon.ru
            logger.info("🔗 Ищу ссылку на Ozon в результатах поиска...")
            ozon_link = page.locator('a[href*="ozon.ru"]').first
            await ozon_link.click()

            # Ждём загрузки Озона
            await page.wait_for_load_state('networkidle', timeout=10000)
            await asyncio.sleep(3)

            logger.info("✅ Переход выполнен!")
            logger.info(f"📍 Текущий URL: {page.url}")

            # Сохраняем скриншот
            Path("debug_data").mkdir(parents=True, exist_ok=True)
            screenshot_path = "debug_data/yandex_unlock_test.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"📸 Скриншот сохранён: {screenshot_path}")

            logger.info("\n⏸️  Проверь наличие капчи в браузере...")
            logger.info("⏸️  Нажми Enter для завершения...")
            input()

        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")

        finally:
            # Отключаемся от CDP браузера (браузер остаётся запущенным)
            try:
                await browser.close()
                logger.info("🔚 Отключились от CDP браузера (браузер остался запущенным)")
            except Exception as e:
                logger.warning(f"⚠️  Ошибка при закрытии: {e}")

            if chrome_process:
                logger.info(f"Chrome процесс (PID: {chrome_process.pid}) оставлен запущенным")


if __name__ == "__main__":
    asyncio.run(test_yandex_unlock())
