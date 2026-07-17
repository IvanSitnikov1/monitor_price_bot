import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from utils.browser_manager import BrowserInstance, DiagnosticNavigationError
from utils.process_lock import ProcessLock, ProcessLockError


class ProcessLockTests(unittest.TestCase):
    """Проверяет общую межпроцессную блокировку приложения."""

    def test_conflict_release_and_permissions(self):
        """Проверяет конфликт, освобождение и права файла блокировки."""
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "runtime.lock"
            first_lock = ProcessLock(lock_path)
            second_lock = ProcessLock(lock_path)

            try:
                first_lock.acquire()
                permissions = stat.S_IMODE(lock_path.stat().st_mode)
                self.assertEqual(permissions, 0o600)

                with self.assertRaises(ProcessLockError):
                    second_lock.acquire()

                first_lock.release()
                second_lock.acquire()
            finally:
                first_lock.release()
                second_lock.release()


@unittest.skipUnless(Path("/proc/net/tcp").exists(), "Тест требует Linux /proc")
class SocketOwnershipTests(unittest.TestCase):
    """Проверяет сопоставление слушающего сокета с текущим процессом."""

    def test_listening_socket_belongs_to_current_process(self):
        """Находит общий inode локального listening socket в таблицах /proc."""
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError as error:
            self.skipTest(f"Среда запрещает создание локального сокета: {error}")

        with server_socket:
            server_socket.bind(("127.0.0.1", 0))
            server_socket.listen(1)
            port = server_socket.getsockname()[1]

            listening_inodes = BrowserInstance._listening_socket_inodes(port)
            process_inodes = BrowserInstance._process_socket_inodes({os.getpid()})

            self.assertIsNotNone(listening_inodes)
            self.assertTrue(listening_inodes)
            self.assertTrue(listening_inodes & process_inodes)


class ChromiumProcessIdentityTests(unittest.TestCase):
    """Проверяет точный разбор аргументов Chromium."""

    def test_process_options_support_both_forms(self):
        """Читает аргументы через знак равенства и отдельное значение."""
        args = [
            "/usr/bin/chromium",
            "--remote-debugging-port=19223",
            "--user-data-dir",
            "user_data/123",
        ]

        self.assertEqual(
            BrowserInstance._get_process_option(args, "--remote-debugging-port"),
            "19223",
        )
        self.assertEqual(
            BrowserInstance._get_process_option(args, "--user-data-dir"),
            "user_data/123",
        )
        self.assertIsNone(BrowserInstance._get_process_option(args, "--type"))

    def test_only_chromium_executable_is_accepted(self):
        """Не считает Python-процесс Chromium только из-за похожих аргументов."""
        self.assertTrue(
            BrowserInstance._is_chromium_process(["/usr/lib/chromium/chromium"])
        )
        self.assertFalse(
            BrowserInstance._is_chromium_process(["/usr/bin/python", "chromium"])
        )


class FakeDiagnosticPage:
    """Поддельная страница с ошибкой, содержащей приватный URL."""

    def __init__(self, raw_url: str):
        self.raw_url = raw_url

    async def goto(self, url: str, **kwargs):
        raise RuntimeError(f"Ошибка перехода к {self.raw_url}")


class FakeDiagnosticContext:
    """Поддельный браузерный контекст для диагностического перехода."""

    def __init__(self, page: FakeDiagnosticPage):
        self.page = page

    async def new_page(self):
        return self.page


class DiagnosticNavigationTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет безопасную ошибку диагностической навигации."""

    async def test_navigation_error_does_not_expose_raw_url(self):
        """Исключает приватный URL из текста DiagnosticNavigationError."""
        raw_url = "https://www.ozon.ru/cart?share=private-token"
        page = FakeDiagnosticPage(raw_url)
        context = FakeDiagnosticContext(page)
        browser_instance = BrowserInstance(
            user_id=123456789,
            port=19223,
            diagnostic_mode=True,
        )

        with patch.object(
            browser_instance,
            "ensure_browser",
            new=AsyncMock(return_value=(object(), context)),
        ):
            with self.assertRaises(DiagnosticNavigationError) as raised_error:
                await browser_instance.open_diagnostic_page(raw_url, apply_stealth=False)

        error_text = str(raised_error.exception)
        self.assertNotIn(raw_url, error_text)
        self.assertNotIn("private-token", error_text)
        self.assertIn("RuntimeError", error_text)


if __name__ == "__main__":
    unittest.main()
