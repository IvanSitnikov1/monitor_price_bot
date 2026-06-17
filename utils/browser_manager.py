import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

from utils.scraper import _get_system_profile
from loggers import logger


class BrowserInstance:
    """
    Экземпляр браузера для одного пользователя.
    """

    def __init__(self, user_id: int, port: int):
        self.user_id = user_id
        self.port = port
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._cart_pages: dict[str, Page] = {}
        self._chrome_process = None
        self._current_cart_urls: dict[str, str] = {}

    def _start_chrome_manually(self, user_data_dir: Path, port: int = 9222) -> subprocess.Popen | None:
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
            # "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            # "--no-sandbox",
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

    async def ensure_browser(self) -> tuple[Browser, BrowserContext]:
        """
        Гарантирует, что браузер запущен и подключен.

        Returns:
            Кортеж (browser, context)
        """
        if self._browser and self._context:
            logger.info(f"[User {self.user_id}] Используем существующий браузер на порту {self.port}")
            return self._browser, self._context

        # Определяем профиль
        user_profile = Path(f"user_data/{self.user_id}").absolute()

        # Если профиля пользователя нет — копируем системный профиль как шаблон
        if not user_profile.exists() or not (user_profile / "Default").exists():
            logger.info(f"[User {self.user_id}] Профиль не найден, создаём на основе системного")

            # Получаем системный профиль
            system_profile = _get_system_profile()

            if system_profile.exists():
                try:
                    # Копируем системный профиль как шаблон
                    logger.info(f"[User {self.user_id}] Копирование системного профиля: {system_profile} -> {user_profile}")
                    shutil.copytree(system_profile, user_profile, dirs_exist_ok=True, ignore_dangling_symlinks=True)
                    logger.info(f"[User {self.user_id}] Системный профиль успешно скопирован")
                except Exception as e:
                    logger.warning(f"[User {self.user_id}] Не удалось скопировать системный профиль: {e}")
                    logger.info(f"[User {self.user_id}] Создаём пустой профиль")
                    user_profile.mkdir(parents=True, exist_ok=True)
            else:
                logger.warning(f"[User {self.user_id}] Системный профиль не найден, создаём пустой")
                user_profile.mkdir(parents=True, exist_ok=True)

        user_data_dir = user_profile
        logger.info(f"[User {self.user_id}] Используется профиль: {user_data_dir}")

        cdp_url = f"http://localhost:{self.port}"

        self._playwright = await async_playwright().start()
        logger.info(f"[User {self.user_id}] Попытка подключения к Chrome через CDP: {cdp_url}")

        try:
            # Пытаемся подключиться к уже запущенному Chrome
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            logger.info(f"[User {self.user_id}] Успешно подключились к существующему Chrome")

            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                logger.info(f"[User {self.user_id}] Используем существующий контекст, всего контекстов: {len(contexts)}")
            else:
                logger.error(f"[User {self.user_id}] Нет доступных контекстов в браузере")
                await self._browser.close()
                raise RuntimeError("Нет доступных контекстов")

        except Exception as e:
            logger.warning(f"[User {self.user_id}] Не удалось подключиться к Chrome через CDP: {e}")
            logger.info(f"[User {self.user_id}] Запускаем Chrome вручную через subprocess на порту {self.port}...")

            self._chrome_process = self._start_chrome_manually(user_data_dir, port=self.port)
            if not self._chrome_process:
                raise RuntimeError("Не удалось запустить Chrome")

            logger.info(f"[User {self.user_id}] Ожидание запуска Chrome...")
            await asyncio.sleep(5)

            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
                logger.info(f"[User {self.user_id}] Успешно подключились к Chrome, запущенному через subprocess")

                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    logger.info(f"[User {self.user_id}] Используем контекст, всего контекстов: {len(contexts)}")
                else:
                    logger.error(f"[User {self.user_id}] Нет доступных контекстов")
                    await self._browser.close()
                    if self._chrome_process:
                        self._chrome_process.terminate()
                    raise RuntimeError("Нет доступных контекстов")

            except Exception as e2:
                logger.error(f"[User {self.user_id}] Не удалось подключиться к Chrome после запуска: {e2}")
                if self._chrome_process:
                    self._chrome_process.terminate()
                raise

        return self._browser, self._context

    def has_cart_page(self, marketplace: str, cart_url: str) -> bool:
        """
        Проверяет, открыта ли вкладка корзины маркетплейса с нужным URL.

        Args:
            marketplace: Маркетплейс корзины
            cart_url: URL корзины

        Returns:
            True если вкладка открыта и URL совпадает
        """
        return (
            marketplace in self._cart_pages and
            not self._cart_pages[marketplace].is_closed() and
            self._current_cart_urls.get(marketplace) == cart_url
        )

    def set_cart_page(self, marketplace: str, cart_url: str, page: Page):
        """
        Сохраняет актуальную вкладку корзины маркетплейса.

        Args:
            marketplace: Маркетплейс корзины
            cart_url: URL корзины
            page: Страница Playwright
        """
        self._cart_pages[marketplace] = page
        self._current_cart_urls[marketplace] = cart_url

    async def _prepare_page(self, page: Page, marketplace: str):
        """
        Применяет настройки страницы перед первым использованием.

        Args:
            page: Страница Playwright
            marketplace: Маркетплейс корзины
        """
        stealth = Stealth(
            navigator_languages_override=('ru-RU', 'ru'),
            navigator_platform_override='Linux x86_64',
            navigator_user_agent_override='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        )
        await stealth.apply_stealth_async(page)
        logger.info(f"[User {self.user_id}] Stealth-патчи применены для {marketplace}")

        await page.set_viewport_size({"width": 1920, "height": 1080})

    async def get_cart_page(self, cart_url: str, marketplace: str = "ozon") -> Page:
        """
        Возвращает страницу корзины. При первом вызове открывает страницу,
        при последующих — переиспользует существующую.
        Если URL изменился — переходит на новый URL во вкладке этого маркетплейса.

        Args:
            cart_url: URL корзины
            marketplace: Маркетплейс корзины

        Returns:
            Страница Playwright с открытой корзиной
        """
        browser, context = await self.ensure_browser()

        cart_page = self._cart_pages.get(marketplace)
        current_url = self._current_cart_urls.get(marketplace)
        if cart_page and cart_page.is_closed():
            logger.warning(f"[User {self.user_id}] Вкладка {marketplace} закрыта, создаём новую")
            self._cart_pages.pop(marketplace, None)
            self._current_cart_urls.pop(marketplace, None)
            cart_page = None
            current_url = None

        # Если вкладка маркетплейса уже открыта и URL совпадает — переиспользуем
        if cart_page and current_url == cart_url:
            logger.info(
                f"[User {self.user_id}] Переиспользуем вкладку корзины {marketplace}"
            )
            await cart_page.bring_to_front()
            return cart_page

        # Если вкладка маркетплейса открыта, но URL другой — переходим в ней
        if cart_page and current_url != cart_url:
            logger.info(
                f"[User {self.user_id}] URL корзины {marketplace} изменился: {cart_url}"
            )
            await cart_page.bring_to_front()
            await cart_page.goto(cart_url, timeout=45000, wait_until='domcontentloaded')
            logger.info(f"[User {self.user_id}] Переход на корзину {marketplace} завершён")
            self._current_cart_urls[marketplace] = cart_url
            return cart_page

        page = await context.new_page()
        logger.info(f"[User {self.user_id}] Создана новая вкладка для {marketplace}")
        await self._prepare_page(page, marketplace)

        # Переходим на страницу корзины
        logger.info(f"[User {self.user_id}] Первое открытие корзины {marketplace}: {cart_url}")
        await page.goto(cart_url, timeout=45000, wait_until='domcontentloaded')
        logger.info(f"[User {self.user_id}] Корзина {marketplace} загружена")

        self._cart_pages[marketplace] = page
        self._current_cart_urls[marketplace] = cart_url

        return page

    async def open_cart_page(self, cart_url: str, marketplace: str = "ozon") -> Page:
        """
        Открывает актуальный URL корзины во вкладке маркетплейса.

        В отличие от get_cart_page(), всегда выполняет переход по URL, даже если
        вкладка уже открыта. Это нужно для Wildberries, где URL страницы может
        измениться после загрузки расшаренной корзины.

        Args:
            cart_url: URL корзины
            marketplace: Маркетплейс корзины

        Returns:
            Страница Playwright с открытой корзиной
        """
        browser, context = await self.ensure_browser()

        cart_page = self._cart_pages.get(marketplace)
        if cart_page and cart_page.is_closed():
            logger.warning(f"[User {self.user_id}] Вкладка {marketplace} закрыта, создаём новую")
            self._cart_pages.pop(marketplace, None)
            self._current_cart_urls.pop(marketplace, None)
            cart_page = None

        if cart_page:
            page = cart_page
            await page.bring_to_front()
        else:
            page = await context.new_page()
            logger.info(f"[User {self.user_id}] Создана новая вкладка для {marketplace}")
            await self._prepare_page(page, marketplace)
            self._cart_pages[marketplace] = page

        logger.info(f"[User {self.user_id}] Открываем актуальный URL {marketplace}: {cart_url}")
        await page.goto(cart_url, timeout=45000, wait_until='domcontentloaded')
        logger.info(f"[User {self.user_id}] Актуальный URL {marketplace} загружен")

        self._current_cart_urls[marketplace] = cart_url
        return page

    async def open_authorization_page(self, url: str, marketplace: str) -> Page:
        """
        Открывает страницу авторизации в новой вкладке текущего браузера пользователя.

        Args:
            url: URL страницы авторизации
            marketplace: Маркетплейс для логирования и stealth-настроек

        Returns:
            Новая страница Playwright.
        """
        browser, context = await self.ensure_browser()
        page = await context.new_page()
        logger.info(f"[User {self.user_id}] Создана вкладка авторизации {marketplace}")
        await self._prepare_page(page, marketplace)
        await page.goto(url, timeout=45000, wait_until="domcontentloaded")
        logger.info(f"[User {self.user_id}] Страница авторизации {marketplace} загружена")
        return page

    async def refresh_cart_page(self, marketplace: str = "ozon") -> Page:
        """
        Обновляет страницу корзины (имитирует F5).
        Должна быть вызвана после get_cart_page().

        Args:
            marketplace: Маркетплейс корзины

        Returns:
            Обновленная страница корзины
        """
        cart_page = self._cart_pages.get(marketplace)
        if not cart_page or cart_page.is_closed():
            raise RuntimeError(
                f"Страница корзины {marketplace} не открыта. "
                "Вызовите get_cart_page() сначала."
            )

        logger.info(f"[User {self.user_id}] Обновление корзины {marketplace} (имитация F5)")
        await cart_page.bring_to_front()
        await cart_page.reload(timeout=45000, wait_until='domcontentloaded')
        logger.info(f"[User {self.user_id}] Корзина {marketplace} обновлена")

        return cart_page

    async def close(self):
        """Закрывает браузер и очищает состояние экземпляра пользователя."""
        self._cart_pages.clear()
        self._current_cart_urls.clear()

        if self._browser:
            try:
                await self._browser.close()
                logger.info(f"[User {self.user_id}] Браузер закрыт")
            except Exception as e:
                logger.warning(f"[User {self.user_id}] Не удалось закрыть браузер: {e}")
            finally:
                self._browser = None
                self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
                logger.info(f"[User {self.user_id}] Playwright остановлен")
            except Exception as e:
                logger.warning(f"[User {self.user_id}] Не удалось остановить Playwright: {e}")
            finally:
                self._playwright = None

        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                logger.info(f"[User {self.user_id}] Процесс Chrome остановлен")
            except Exception as e:
                logger.warning(f"[User {self.user_id}] Не удалось остановить Chrome: {e}")
            finally:
                self._chrome_process = None


class BrowserManager:
    """
    Менеджер браузеров для нескольких пользователей.
    Каждый пользователь получает свой Chrome процесс на отдельном порту.
    """

    def __init__(self, base_port: int = 9223):
        self.base_port = base_port
        self._instances: dict[int, BrowserInstance] = {}
        self._port_counter = 0

    def _get_next_port(self) -> int:
        """Возвращает следующий свободный порт"""
        port = self.base_port + self._port_counter
        self._port_counter += 1
        return port

    def _get_instance(self, user_id: int) -> BrowserInstance:
        """
        Возвращает экземпляр браузера для пользователя.
        Создаёт новый, если не существует.

        Args:
            user_id: ID пользователя

        Returns:
            BrowserInstance для данного пользователя
        """
        if user_id not in self._instances:
            port = self._get_next_port()
            logger.info(f"Создание нового экземпляра браузера для user_id={user_id} на порту {port}")
            self._instances[user_id] = BrowserInstance(user_id, port)

        return self._instances[user_id]

    def has_cart_page(self, user_id: int, marketplace: str, cart_url: str) -> bool:
        """
        Проверяет, открыта ли вкладка корзины пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс корзины
            cart_url: URL корзины

        Returns:
            True если вкладка открыта и URL совпадает
        """
        if user_id not in self._instances:
            return False

        return self._instances[user_id].has_cart_page(marketplace, cart_url)

    def set_cart_page(self, user_id: int, marketplace: str, cart_url: str, page: Page):
        """
        Сохраняет актуальную вкладку корзины пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс корзины
            cart_url: URL корзины
            page: Страница Playwright
        """
        instance = self._get_instance(user_id)
        instance.set_cart_page(marketplace, cart_url, page)

    async def close_user_browser(self, user_id: int):
        """
        Закрывает активный браузер пользователя и освобождает профиль.

        Args:
            user_id: ID пользователя
        """
        instance = self._instances.pop(user_id, None)
        if not instance:
            logger.info(f"Активный браузер для user_id={user_id} не найден")
            return

        await instance.close()
        logger.info(f"Активный браузер для user_id={user_id} закрыт")

    async def get_cart_page(
        self,
        cart_url: str,
        user_id: int,
        marketplace: str = "ozon"
    ) -> Page:
        """
        Возвращает страницу корзины для пользователя.

        Args:
            cart_url: URL корзины
            user_id: ID пользователя
            marketplace: Маркетплейс корзины

        Returns:
            Страница Playwright с открытой корзиной
        """
        instance = self._get_instance(user_id)
        return await instance.get_cart_page(cart_url, marketplace)

    async def open_cart_page(
        self,
        cart_url: str,
        user_id: int,
        marketplace: str = "ozon"
    ) -> Page:
        """
        Открывает актуальный URL корзины пользователя во вкладке маркетплейса.

        Args:
            cart_url: URL корзины
            user_id: ID пользователя
            marketplace: Маркетплейс корзины

        Returns:
            Страница Playwright с открытой корзиной
        """
        instance = self._get_instance(user_id)
        return await instance.open_cart_page(cart_url, marketplace)

    async def open_authorization_page(
        self,
        user_id: int,
        marketplace: str,
        url: str
    ) -> Page:
        """
        Открывает страницу авторизации пользователя в новой вкладке.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс
            url: URL страницы авторизации

        Returns:
            Новая страница Playwright.
        """
        instance = self._get_instance(user_id)
        return await instance.open_authorization_page(url, marketplace)

    async def refresh_cart_page(self, user_id: int, marketplace: str = "ozon") -> Page:
        """
        Обновляет страницу корзины для пользователя (имитирует F5).

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс корзины

        Returns:
            Обновленная страница корзины
        """
        if user_id not in self._instances:
            raise RuntimeError(
                f"Нет активного браузера для user_id={user_id}. "
                "Вызовите get_cart_page() сначала."
            )

        instance = self._instances[user_id]
        return await instance.refresh_cart_page(marketplace)
