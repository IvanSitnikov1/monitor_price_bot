import asyncio
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Response
from playwright_stealth import Stealth

from loggers import logger


class DiagnosticNavigationError(RuntimeError):
    """Безопасная ошибка одиночного диагностического перехода."""


class BrowserIdentityError(RuntimeError):
    """Ошибка проверки профиля, процесса или CDP-порта Chromium."""


class BrowserInstance:
    """
    Экземпляр браузера для одного пользователя.
    """

    def __init__(
        self,
        user_id: int,
        port: int,
        diagnostic_mode: bool = False,
        refuse_existing_profile: bool = False,
    ):
        self.user_id = user_id
        self.port = port
        self.diagnostic_mode = diagnostic_mode
        self.refuse_existing_profile = refuse_existing_profile
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._cart_pages: dict[str, Page] = {}
        self._chrome_process: subprocess.Popen | None = None
        self._chrome_pid: int | None = None
        self._current_cart_urls: dict[str, str] = {}

    @property
    def _log_scope(self) -> str:
        """Возвращает безопасную область логирования экземпляра."""
        if self.diagnostic_mode:
            return "[Диагностика Ozon]"

        return f"[User {self.user_id}]"

    @property
    def user_data_dir(self) -> Path:
        """Возвращает абсолютный путь постоянного профиля пользователя."""
        return Path(f"user_data/{self.user_id}").absolute()

    def _start_chrome_manually(
        self,
        user_data_dir: Path,
        port: int = 9222
    ) -> subprocess.Popen | None:
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
        display = os.environ.get("DISPLAY")
        if self.diagnostic_mode:
            display_state = "установлен" if display else "не установлен"
            logger.info(f"{self._log_scope} DISPLAY {display_state}")
        else:
            logger.info(f"DISPLAY переменная окружения: {display or 'не установлен'}")

        chrome_cmd = [
            "chromium",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            # "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--window-size=1920,1080",
            "--start-maximized",
        ]

        try:
            if self.diagnostic_mode:
                logger.info(
                    f"{self._log_scope} Запуск Chromium с приватным профилем "
                    f"на CDP-порту {port}"
                )
            else:
                logger.info(f"Запуск Chrome вручную через subprocess: {' '.join(chrome_cmd)}")
            process = subprocess.Popen(
                chrome_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True  # Отделяем от родительского процесса
            )
            logger.info(f"{self._log_scope} Chrome запущен вручную, PID: {process.pid}")
            # Даём Chrome время на запуск
            time.sleep(3)
            return process
        except Exception as e:
            if self.diagnostic_mode:
                logger.error(
                    f"{self._log_scope} Не удалось запустить Chrome: {type(e).__name__}"
                )
            else:
                logger.error(f"Не удалось запустить Chrome вручную: {e}")
            return None

    @staticmethod
    def _read_process_args(pid: int) -> list[str] | None:
        """Читает аргументы процесса без объединения и неразличимых подстрок."""
        try:
            raw_args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")
            return [arg.decode() for arg in raw_args if arg]
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _get_process_option(args: list[str], option: str) -> str | None:
        """Возвращает точное значение аргумента Chromium в двух допустимых формах."""
        prefix = f"{option}="
        for index, argument in enumerate(args):
            if argument.startswith(prefix):
                return argument[len(prefix):]
            if argument == option and index + 1 < len(args):
                return args[index + 1]

        return None

    @staticmethod
    def _is_chromium_process(args: list[str]) -> bool:
        """Исключает чужие процессы с похожими аргументами командной строки."""
        executable_name = Path(args[0]).name.casefold()
        return executable_name in {
            "chrome",
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
        }

    @staticmethod
    def _resolve_process_path(pid: int, raw_path: str) -> Path | None:
        """Приводит путь из аргумента процесса к абсолютному в его рабочем каталоге."""
        process_path = Path(raw_path)
        if not process_path.is_absolute():
            try:
                process_cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                return None
            process_path = process_cwd / process_path

        try:
            return process_path.resolve()
        except OSError:
            return None

    @staticmethod
    def _listening_socket_inodes(port: int) -> set[str] | None:
        """Возвращает inode слушающих TCP-сокетов указанного локального порта."""
        inodes: set[str] = set()
        table_read = False
        for table_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            try:
                lines = table_path.read_text().splitlines()[1:]
            except OSError:
                continue

            table_read = True
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":
                    continue

                try:
                    local_port = int(fields[1].rsplit(":", 1)[1], 16)
                except (IndexError, ValueError):
                    continue

                if local_port == port:
                    inodes.add(fields[9])

        return inodes if table_read else None

    @staticmethod
    def _process_group_pids(process_group: int) -> set[int]:
        """Возвращает процессы из указанной группы текущего пользователя."""
        pids = set()
        for process_dir in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(process_dir.name)
                if os.getpgid(pid) == process_group:
                    pids.add(pid)
            except (OSError, ValueError):
                continue

        return pids

    @staticmethod
    def _process_socket_inodes(pids: set[int]) -> set[str]:
        """Возвращает inode сокетов, открытых указанными процессами."""
        inodes = set()
        for pid in pids:
            for descriptor in Path(f"/proc/{pid}/fd").glob("*"):
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue

                if target.startswith("socket:[") and target.endswith("]"):
                    inodes.add(target[8:-1])

        return inodes

    def _owns_listening_port(self, process_group: int) -> bool:
        """Проверяет принадлежность CDP-сокета группе запущенного Chromium."""
        listening_inodes = self._listening_socket_inodes(self.port)
        if listening_inodes is None or not listening_inodes:
            return False

        process_pids = self._process_group_pids(process_group)
        process_inodes = self._process_socket_inodes(process_pids)
        return bool(listening_inodes & process_inodes)

    @classmethod
    def _find_main_processes(
        cls,
        user_data_dir: Path | None = None,
        port: int | None = None,
        require_remote_debugging: bool = False,
    ) -> list[int]:
        """Находит основные Chromium-процессы по точным аргументам профиля и порта."""
        expected_profile = user_data_dir.resolve() if user_data_dir is not None else None

        processes = []
        for process_dir in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(process_dir.name)
            except ValueError:
                continue

            args = cls._read_process_args(pid)
            if (
                not args
                or not cls._is_chromium_process(args)
                or cls._get_process_option(args, "--type") is not None
            ):
                continue

            process_profile = cls._get_process_option(args, "--user-data-dir")
            if expected_profile is not None and (
                process_profile is None
                or cls._resolve_process_path(pid, process_profile) != expected_profile
            ):
                continue

            process_port = cls._get_process_option(args, "--remote-debugging-port")
            if port is not None and process_port != str(port):
                continue
            if require_remote_debugging and process_port is None:
                continue

            processes.append(pid)

        return sorted(processes)

    @staticmethod
    def _find_process_for_profile(user_data_dir: Path) -> int | None:
        """Находит основной Chromium-процесс, использующий профиль бота."""
        processes = BrowserInstance._find_main_processes(
            user_data_dir=user_data_dir,
            require_remote_debugging=True,
        )
        return processes[0] if processes else None

    def _resolve_chrome_pid(self, user_data_dir: Path) -> int:
        """Проверяет единственный основной процесс с ожидаемыми профилем и портом."""
        if not self._chrome_process:
            raise BrowserIdentityError("Процесс Chromium не был запущен")

        candidates = self._find_main_processes(user_data_dir, self.port)
        port_candidates = self._find_main_processes(port=self.port)
        owned_candidates = []
        for pid in candidates:
            try:
                if pid == self._chrome_process.pid or os.getpgid(pid) == self._chrome_process.pid:
                    owned_candidates.append(pid)
            except ProcessLookupError:
                continue

        socket_owned = self._owns_listening_port(self._chrome_process.pid)
        if (
            len(owned_candidates) != 1
            or port_candidates != owned_candidates
            or not socket_owned
        ):
            raise BrowserIdentityError(
                "Не подтвержден единственный Chromium-процесс с ожидаемыми "
                "CDP-портом и профилем"
            )

        return owned_candidates[0]

    def get_runtime_info(self) -> dict[str, object]:
        """Возвращает сведения для приватного диагностического отчета."""
        user_data_dir = self.user_data_dir
        matching_processes = self._find_main_processes(user_data_dir, self.port)
        port_processes = self._find_main_processes(port=self.port)
        pid = self._chrome_pid
        args = self._read_process_args(pid) if pid else None
        process_alive = bool(pid and Path(f"/proc/{pid}").exists())
        process_profile = self._get_process_option(args, "--user-data-dir") if args else None
        profile_matches = bool(
            pid
            and process_profile
            and self._resolve_process_path(pid, process_profile) == user_data_dir.resolve()
        )
        process_port = (
            self._get_process_option(args, "--remote-debugging-port") if args else None
        )
        port_matches = process_port == str(self.port)
        process_group = self._chrome_process.pid if self._chrome_process else None
        listening_socket_owned = bool(
            process_group and self._owns_listening_port(process_group)
        )
        return {
            "pid": pid,
            "port": self.port,
            "process_alive": process_alive,
            "profile_matches": profile_matches,
            "port_matches": port_matches,
            "matching_process_count": len(matching_processes),
            "port_process_count": len(port_processes),
            "listening_socket_owned": listening_socket_owned,
            "context_count": len(self._browser.contexts) if self._browser else 0,
            "page_count": len(self._context.pages) if self._context else 0,
            "browser_version": self._browser.version if self._browser else None,
            "identity_verified": (
                process_alive
                and profile_matches
                and port_matches
                and len(matching_processes) == 1
                and port_processes == matching_processes
                and listening_socket_owned
            ),
        }

    @staticmethod
    def _terminate_process(pid: int) -> None:
        """Завершает только найденный основной процесс Chromium и его дерево."""
        try:
            process_group = os.getpgid(pid)
            if process_group == pid:
                os.killpg(process_group, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not Path(f"/proc/{pid}").exists():
                return
            time.sleep(0.1)

        try:
            process_group = os.getpgid(pid)
            if process_group == pid:
                os.killpg(process_group, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _terminate_started_chromium(self) -> None:
        """Завершает только группу Chromium, созданную этим экземпляром, и ожидает launcher."""
        if not self._chrome_process:
            return

        launcher_pid = self._chrome_process.pid
        try:
            process_group = os.getpgid(launcher_pid)
        except ProcessLookupError:
            process_group = launcher_pid

        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self._process_group_pids(process_group):
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass

        try:
            self._chrome_process.wait(timeout=1)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass

    async def ensure_browser(self) -> tuple[Browser, BrowserContext]:
        """
        Гарантирует, что браузер запущен и подключен.

        Returns:
            Кортеж (browser, context)
        """
        if self._browser and self._context:
            logger.info(
                f"{self._log_scope} Используем существующий браузер "
                f"на порту {self.port}"
            )
            return self._browser, self._context

        # Определяем профиль
        user_profile = self.user_data_dir

        # Новые профили создаются пустыми и не наследуют системное состояние браузера.
        if not user_profile.exists() or not (user_profile / "Default").exists():
            if self.diagnostic_mode:
                raise BrowserIdentityError("Постоянный профиль пользователя не найден")
            logger.info(f"{self._log_scope} Создаём пустой профиль Chromium")
            (user_profile / "Default").mkdir(parents=True, exist_ok=True)

        user_data_dir = user_profile
        if self.diagnostic_mode:
            logger.info(f"{self._log_scope} Используется приватный профиль пользователя")
        else:
            logger.info(f"{self._log_scope} Используется профиль: {user_data_dir}")

        existing_processes = self._find_main_processes(user_data_dir=user_data_dir)
        stale_pid = self._find_process_for_profile(user_data_dir)
        if self.refuse_existing_profile and existing_processes:
            raise BrowserIdentityError(
                "Профиль пользователя уже открыт другим Chromium"
            )
        if stale_pid:
            if self.diagnostic_mode:
                logger.warning(
                    f"{self._log_scope} Завершаем старый Chromium-процесс "
                    f"{stale_pid} для приватного профиля"
                )
            else:
                logger.warning(
                    f"{self._log_scope} Завершаем старый Chromium-процесс "
                    f"{stale_pid} для профиля {user_data_dir}"
                )
            self._terminate_process(stale_pid)

        self._playwright = await async_playwright().start()
        cdp_url = f"http://127.0.0.1:{self.port}"
        logger.info(f"{self._log_scope} Запускаем собственный Chrome на {cdp_url}")
        self._chrome_process = self._start_chrome_manually(user_data_dir, port=self.port)
        if not self._chrome_process:
            raise RuntimeError("Не удалось запустить Chrome")

        logger.info(f"{self._log_scope} Ожидание запуска Chrome")
        await asyncio.sleep(5)

        try:
            if self.diagnostic_mode:
                self._chrome_pid = self._resolve_chrome_pid(user_data_dir)
            else:
                candidates = self._find_main_processes(user_data_dir, self.port)
                self._chrome_pid = candidates[0] if len(candidates) == 1 else None
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("У запущенного Chrome нет доступных контекстов")

            self._context = contexts[0]
            logger.info(
                f"{self._log_scope} Подключились к собственному Chrome, "
                f"контекстов: {len(contexts)}"
            )
        except Exception as error:
            if self.diagnostic_mode:
                logger.error(
                    f"{self._log_scope} Не удалось подключиться к Chrome: "
                    f"{type(error).__name__}"
                )
            else:
                logger.error(f"{self._log_scope} Не удалось подключиться к Chrome: {error}")
            if self._chrome_process:
                if self.diagnostic_mode:
                    self._terminate_started_chromium()
                else:
                    self._terminate_process(self._chrome_process.pid)
                self._chrome_process = None
                self._chrome_pid = None
            raise

        return self._browser, self._context

    async def open_diagnostic_page(
        self,
        url: str,
        apply_stealth: bool,
    ) -> tuple[Page, Response | None]:
        """Открывает ровно одну диагностическую вкладку и выполняет один переход."""
        _, context = await self.ensure_browser()
        page = await context.new_page()
        if apply_stealth:
            await self._prepare_page(page, "ozon")

        logger.info(f"{self._log_scope} Выполняется одиночный переход на Ozon")
        try:
            response = await page.goto(url, timeout=45000, wait_until="domcontentloaded")
        except Exception as error:
            raise DiagnosticNavigationError(
                f"Диагностический переход не выполнен: {type(error).__name__}"
            ) from None
        logger.info(f"{self._log_scope} Одиночный переход на Ozon завершен")
        return page, response

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
            navigator_languages=False,
            navigator_platform=False,
            navigator_user_agent=False,
            navigator_user_agent_data=False,
            navigator_vendor=False,
            webgl_vendor=False,
        )
        await stealth.apply_stealth_async(page)
        logger.info(f"{self._log_scope} Stealth-патчи применены для {marketplace}")

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

    async def close_marketplace_page(self, marketplace: str):
        """Закрывает вкладку маркетплейса, не затрагивая профиль и другие вкладки."""
        page = self._cart_pages.pop(marketplace, None)
        self._current_cart_urls.pop(marketplace, None)

        if not page or page.is_closed():
            return

        try:
            await page.close()
            logger.info(
                f"[User {self.user_id}] Вкладка {marketplace} закрыта для сброса сессии"
            )
        except Exception as e:
            logger.warning(
                f"[User {self.user_id}] Не удалось закрыть вкладку {marketplace}: {e}"
            )

    async def close(self):
        """Закрывает браузер и очищает состояние экземпляра пользователя."""
        self._cart_pages.clear()
        self._current_cart_urls.clear()

        if self._browser:
            try:
                await self._browser.close()
                logger.info(f"{self._log_scope} Браузер закрыт")
            except Exception as e:
                if self.diagnostic_mode:
                    logger.warning(
                        f"{self._log_scope} Не удалось закрыть браузер: {type(e).__name__}"
                    )
                else:
                    logger.warning(f"{self._log_scope} Не удалось закрыть браузер: {e}")
            finally:
                self._browser = None
                self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
                logger.info(f"{self._log_scope} Playwright остановлен")
            except Exception as e:
                if self.diagnostic_mode:
                    logger.warning(
                        f"{self._log_scope} Не удалось остановить Playwright: "
                        f"{type(e).__name__}"
                    )
                else:
                    logger.warning(f"{self._log_scope} Не удалось остановить Playwright: {e}")
            finally:
                self._playwright = None

        if self._chrome_process:
            try:
                if self.diagnostic_mode:
                    self._terminate_started_chromium()
                else:
                    self._terminate_process(self._chrome_process.pid)
                logger.info(f"{self._log_scope} Процесс Chrome остановлен")
            except Exception as e:
                if self.diagnostic_mode:
                    logger.warning(
                        f"{self._log_scope} Не удалось остановить Chrome: {type(e).__name__}"
                    )
                else:
                    logger.warning(f"{self._log_scope} Не удалось остановить Chrome: {e}")
            finally:
                self._chrome_process = None
                self._chrome_pid = None


class BrowserManager:
    """
    Менеджер браузеров для нескольких пользователей.
    Каждый пользователь получает свой Chrome процесс на отдельном порту.
    """

    def __init__(
        self,
        base_port: int = 9223,
        diagnostic_mode: bool = False,
        refuse_existing_profiles: bool = False,
    ):
        self.base_port = base_port
        self.diagnostic_mode = diagnostic_mode
        self.refuse_existing_profiles = refuse_existing_profiles
        self._instances: dict[int, BrowserInstance] = {}
        self._port_counter = 0

    def _get_next_port(self) -> int:
        """Возвращает свободный localhost-порт для CDP."""
        while True:
            port = self.base_port + self._port_counter
            self._port_counter += 1

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError as error:
                logger.warning(f"Не удалось проверить CDP-порт {port}: {error}")
                return port
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            finally:
                sock.close()

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
            if self.diagnostic_mode:
                logger.info(f"Создание диагностического Chromium на порту {port}")
            else:
                logger.info(
                    f"Создание нового экземпляра браузера для user_id={user_id} "
                    f"на порту {port}"
                )
            self._instances[user_id] = BrowserInstance(
                user_id,
                port,
                diagnostic_mode=self.diagnostic_mode,
                refuse_existing_profile=self.refuse_existing_profiles,
            )

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
            if self.diagnostic_mode:
                logger.info("Активный диагностический браузер не найден")
            else:
                logger.info(f"Активный браузер для user_id={user_id} не найден")
            return

        await instance.close()
        if self.diagnostic_mode:
            logger.info("Активный диагностический браузер закрыт")
        else:
            logger.info(f"Активный браузер для user_id={user_id} закрыт")

    async def close_user_marketplace_page(self, user_id: int, marketplace: str):
        """Закрывает вкладку маркетплейса конкретного пользователя."""
        instance = self._instances.get(user_id)
        if not instance:
            return

        await instance.close_marketplace_page(marketplace)

    async def close_marketplace_pages(self, marketplace: str):
        """Закрывает вкладки маркетплейса во всех активных браузерах бота."""
        for instance in list(self._instances.values()):
            await instance.close_marketplace_page(marketplace)

    async def close_all(self):
        """Закрывает все браузеры, запущенные текущим менеджером."""
        user_ids = list(self._instances)
        for user_id in user_ids:
            await self.close_user_browser(user_id)

    async def connect_user_browser(
        self,
        user_id: int,
    ) -> tuple[Browser, BrowserContext]:
        """Подключает браузер пользователя без создания вкладки и навигации."""
        instance = self._get_instance(user_id)
        return await instance.ensure_browser()

    def get_browser_runtime_info(self, user_id: int) -> dict[str, object]:
        """Возвращает проверенные сведения о процессе браузера пользователя."""
        if user_id not in self._instances:
            raise RuntimeError("Браузер пользователя еще не запущен")

        return self._instances[user_id].get_runtime_info()

    async def open_diagnostic_page(
        self,
        url: str,
        user_id: int,
        apply_stealth: bool,
    ) -> tuple[Page, Response | None]:
        """Выполняет единственный безопасно логируемый диагностический переход."""
        instance = self._get_instance(user_id)
        return await instance.open_diagnostic_page(url, apply_stealth)

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

    async def get_product_page(
        self,
        product_url: str,
        user_id: int,
        marketplace: str
    ) -> Page:
        """Возвращает переиспользуемую вкладку страницы товара маркетплейса."""
        instance = self._get_instance(user_id)
        return await instance.open_cart_page(product_url, marketplace)

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
