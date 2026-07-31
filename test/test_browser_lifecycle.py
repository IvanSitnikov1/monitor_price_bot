import asyncio
import unittest
from unittest.mock import AsyncMock

from utils.browser_manager import BrowserAuthorizationInProgress, BrowserManager


class BrowserLifecycleGateTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет синхронизацию очереди и ручной авторизации."""

    async def test_authorization_waits_for_active_browser_operation(self):
        """Авторизация ожидает завершения текущей задачи пользователя."""
        manager = BrowserManager()
        close_browser = AsyncMock()
        manager._close_user_browser_unlocked = close_browser
        events = []

        async with manager.user_browser_operation(42):
            authorization_task = asyncio.create_task(
                manager.begin_user_authorization(42)
            )
            await asyncio.sleep(0)
            self.assertFalse(authorization_task.done())
            events.append("операция завершена")

        await authorization_task

        self.assertEqual(events, ["операция завершена"])
        close_browser.assert_awaited_once_with(42)
        self.assertTrue(manager.is_authorization_in_progress(42))

    async def test_browser_operations_are_blocked_until_authorization_finishes(self):
        """Новые задачи не используют профиль до завершения авторизации."""
        manager = BrowserManager()
        manager._close_user_browser_unlocked = AsyncMock()

        await manager.begin_user_authorization(42)

        with self.assertRaises(BrowserAuthorizationInProgress):
            async with manager.user_browser_operation(42):
                pass

        await manager.finish_user_authorization(42)

        async with manager.user_browser_operation(42):
            pass

        self.assertFalse(manager.is_authorization_in_progress(42))


if __name__ == "__main__":
    unittest.main()
