import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from config import config


logger = logging.getLogger(__name__)


async def _human_like_behavior(page):
    """Имитирует человеческое поведение: скроллинг, движения мыши, паузы"""
    try:
        # Случайный скроллинг вниз
        scroll_distance = random.randint(300, 800)
        await page.evaluate(f"window.scrollBy(0, {scroll_distance})")
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Скроллинг вверх
        scroll_back = random.randint(100, 300)
        await page.evaluate(f"window.scrollBy(0, -{scroll_back})")
        await asyncio.sleep(random.uniform(0.3, 0.8))

        # Случайное движение мыши
        viewport_size = page.viewport_size
        if viewport_size:
            x = random.randint(100, viewport_size['width'] - 100)
            y = random.randint(100, viewport_size['height'] - 100)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.2, 0.5))

    except Exception as e:
        logger.debug(f"Ошибка при имитации поведения: {e}")


async def _check_captcha(page) -> bool:
    """Проверяет наличие капчи на странице"""
    content = await page.content()
    return any(
        keyword in content.lower()
        for keyword in ["captcha", "challenge", "проверка системы", "security check"]
    )


async def scrape_ozon_product(url: str, bot=None) -> tuple[str | None, str | None]:
    """
    Парсит данные товара Ozon, используя реальный профиль Chrome для учета региона и аккаунта.

    Args:
        url: URL товара
        bot: Экземпляр бота (для отправки уведомлений при капче)

    Returns:
        Кортеж (название, цена) или (None, None) при ошибке
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
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ],
        )

        # Расширенные stealth-скрипты
        await context.add_init_script("""
            // Базовые переопределения
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
            window.chrome = { runtime: {} };

            // Переопределяем permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Скрываем автоматизацию через CDP
            delete navigator.__proto__.webdriver;

            // Переопределяем navigator.platform
            Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });

            // Переопределяем navigator.hardwareConcurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

            // Переопределяем navigator.deviceMemory
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // Переопределяем screen параметры
            Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
            Object.defineProperty(screen, 'availHeight', { get: () => 1080 });

            // Добавляем реалистичные плагины
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    return [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin' }
                    ];
                }
            });

            // Переопределяем getBattery (для мобильных устройств)
            if (navigator.getBattery) {
                navigator.getBattery = () => Promise.resolve({
                    charging: true,
                    chargingTime: 0,
                    dischargingTime: Infinity,
                    level: 1
                });
            }
        """)

        # Закрываем все лишние вкладки, оставляем только одну
        if context.pages:
            page = context.pages[0]
            # Закрываем все остальные вкладки
            for extra_page in context.pages[1:]:
                await extra_page.close()
        else:
            page = await context.new_page()

        # Устанавливаем реалистичный viewport
        await page.set_viewport_size({"width": 1920, "height": 1080})

        # Сразу переходим на страницу товара (cookies уже есть в user_data_dir)
        await page.goto(url, timeout=45000)

        # Случайная пауза после загрузки
        await asyncio.sleep(random.uniform(1.5, 3))

        # Имитируем человеческое поведение
        await _human_like_behavior(page)

        # --- Логика обработки капчи ---
        if await _check_captcha(page):
            if config.DEBUG_CAPTCHA:
                # Дебаг-режим: ждём решения капчи
                logger.warning("Обнаружена капча. DEBUG_CAPTCHA=True, ожидаем решения...")
                max_wait = 60
                waited = 0
                while waited < max_wait:
                    if not await _check_captcha(page):
                        logger.info("Капча решена!")
                        break
                    logger.warning(f"Ожидание решения капчи... ({waited}/{max_wait}с)")
                    await asyncio.sleep(2)
                    waited += 2

                if waited >= max_wait:
                    logger.error("Не удалось пройти капчу за отведенное время")
                    await context.close()
                    return None, None
            else:
                # Продакшн-режим: включаем maintenance mode и выходим
                logger.warning("Обнаружена капча. Включаем режим техработ.")
                await context.close()

                # Импортируем здесь, чтобы избежать циклических импортов
                from utils.maintenance import MaintenanceMode

                # Включаем режим техработ
                MaintenanceMode.enable()

                # Отправляем уведомление всем пользователям
                if bot:
                    await MaintenanceMode.notify_all_users(
                        bot,
                        "🚧 Ведутся технические работы.\n\n"
                        "Обновление цен временно приостановлено. "
                        "Мы уведомим вас, когда всё заработает."
                    )

                return None, None

        # Ожидание стабилизации страницы
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем парсинг")

        # Ещё одна имитация поведения перед парсингом
        await _human_like_behavior(page)
        await asyncio.sleep(random.uniform(1, 2))

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

