import asyncio
import logging
import random
import re
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


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


async def scrape_ozon_product(url: str, user_id: int) -> tuple[str | None, str | None]:
    """
    Парсит данные товара Ozon, используя профиль пользователя или системный профиль.

    Args:
        url: URL товара
        user_id: ID пользователя Telegram (опционально)

    Returns:
        Кортеж (название, цена) или (None, None) при ошибке
    """
    logger.info(f"Начало процесса для ссылки: {url}, user_id: {user_id}")

    # Проверяем наличие профиля пользователя
    user_profile = Path(f"user_data/{user_id}").absolute()
    if user_profile.exists() and (user_profile / "Default").exists():
        user_data_dir = user_profile
        logger.info(f"Используется профиль пользователя: {user_data_dir}")
    else:
        # Профиль пользователя не найден, используем системный
        user_data_dir = _get_system_profile()
        logger.info(f"Используется системный профиль: {user_data_dir}")

    # Удаляем все возможные lock-файлы
    lock_files = [
        user_data_dir / "Default" / "SingletonLock",
        user_data_dir / "SingletonLock",
        user_data_dir / "lockfile"
    ]
    for lock_file in lock_files:
        if lock_file.exists():
            try:
                lock_file.unlink()
                logger.info(f"Удалён lock-файл: {lock_file}")
            except Exception as e:
                logger.warning(f"Не удалось удалить lock-файл {lock_file}: {e}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu',
                '--window-size=1920,1080',
                '--start-maximized',
            ],
            locale="ru-RU",
            timezone_id="Europe/Moscow",
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
            
            // Canvas fingerprint рандомизация
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                const dataURL = originalToDataURL.apply(this, arguments);
                return dataURL; // + небольшой шум через getImageData
            };
            
            // WebGL fingerprint
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter.apply(this, arguments);
            };
            
            // AudioContext fingerprint
            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function() {
                const array = originalGetChannelData.apply(this, arguments);
                // добавить микро-шум
                for (let i = 0; i < array.length; i += 100) {
                    array[i] += (Math.random() - 0.5) * 0.0000001;
                }
                return array;
            };
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
        await _human_delay()

        # Имитируем человеческое поведение
        await _human_like_behavior(page)

        # Ожидание стабилизации страницы
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("Networkidle timeout - продолжаем парсинг")

        # Ещё одна имитация поведения перед парсингом
        await _human_like_behavior(page)
        await asyncio.sleep(random.uniform(1, 2))

        Path(f"debug_data").absolute().mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=f'debug_data/{uuid4()}.png')

        # --- Извлечение названия и цены ---
        product_name = None
        price = None

        # проверяем страницу ошибки сети Ozon
        no_connection = page.locator(
            'div:has-text("Похоже, нет соединения")'
        ).first

        # сначала проверяем, не закончился ли товар
        out_of_stock = page.locator(
            'h2:has-text("Этот товар закончился")'
        ).first

        if await no_connection.count() > 0:
            product_name = "no_connection"
        elif await out_of_stock.count() > 0:
            product_name = "end"
        else:
            try:
                title_element = page.locator('h1').first
                product_name = await title_element.text_content(timeout=5000)
                if product_name:
                    product_name = product_name.strip()
                logger.info(f"Название: {product_name}")
            except Exception as e:
                logger.error(f"Не удалось найти название: {e}")

            # --- Извлечение цены ---
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
