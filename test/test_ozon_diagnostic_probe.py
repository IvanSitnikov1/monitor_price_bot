import unittest
from unittest.mock import AsyncMock, patch

from utils.scraper import (
    _check_ozon_authorized_profile,
    probe_ozon_diagnostic_state,
)


class FakePage:
    """Минимальная поддельная страница для проверки диагностического probe."""

    def __init__(self, ready_state: str = "complete"):
        self.wait_for_load_state = AsyncMock()
        self.evaluate = AsyncMock(return_value=ready_state)


class FakeAuthorizedControls:
    """Имитирует видимую ссылку на личный кабинет."""

    async def count(self):
        """Возвращает один найденный элемент."""
        return 1

    def nth(self, index):
        """Возвращает единственный элемент."""
        return self

    async def is_visible(self, timeout):
        """Подтверждает видимость элемента."""
        return True


class FakeAuthorizedPage:
    """Запоминает безопасный селектор положительного признака."""

    def __init__(self):
        self.selector = None

    def locator(self, selector):
        """Возвращает видимую ссылку личного кабинета."""
        self.selector = selector
        return FakeAuthorizedControls()


class OzonDiagnosticProbeTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет безопасное определение состояния Ozon без реального браузера."""

    async def test_anonymous_state_after_settle(self):
        page = FakePage()
        with (
            patch(
                "utils.scraper._check_ozon_block",
                AsyncMock(side_effect=[False, False]),
            ),
            patch(
                "utils.scraper._check_ozon_logged_out",
                AsyncMock(side_effect=[False, True]),
            ),
        ):
            result = await probe_ozon_diagnostic_state(
                page,
                emulate_full_update=False,
                settle_wait_ms=0,
            )

        self.assertEqual(result["auth_state"], "anonymous")
        self.assertFalse(result["anonymous_control_visible_initial"])
        self.assertTrue(result["anonymous_control_visible"])
        page.wait_for_load_state.assert_not_awaited()

    async def test_real_positive_selector_requires_personal_account_link(self):
        """Проверяет семантику положительного DOM-признака без текста профиля."""
        page = FakeAuthorizedPage()

        authorized = await _check_ozon_authorized_profile(page, safe_logging=True)

        self.assertTrue(authorized)
        self.assertIn('href^="/my/"', page.selector)
        self.assertIn('data-widget*="Anonymous"', page.selector)

    async def test_explicit_block_has_priority(self):
        page = FakePage()
        with (
            patch(
                "utils.scraper._human_delay",
                AsyncMock(),
            ) as human_delay,
            patch(
                "utils.scraper._human_like_behavior",
                AsyncMock(),
            ) as human_behavior,
            patch(
                "utils.scraper._check_ozon_block",
                AsyncMock(side_effect=[False, True]),
            ),
            patch(
                "utils.scraper._check_ozon_logged_out",
                AsyncMock(side_effect=[True, True]),
            ),
        ):
            result = await probe_ozon_diagnostic_state(
                page,
                emulate_full_update=True,
                settle_wait_ms=0,
            )

        self.assertTrue(result["explicit_block_detected"])
        self.assertEqual(result["auth_state"], "unknown")
        human_delay.assert_awaited_once()
        human_behavior.assert_awaited_once_with(page)
        page.wait_for_load_state.assert_awaited_once_with(
            "networkidle",
            timeout=10000,
        )

    async def test_authorized_profile_has_positive_signal(self):
        """Подтверждает авторизацию только по видимой ссылке личного кабинета."""
        page = FakePage()
        with (
            patch(
                "utils.scraper._check_ozon_block",
                AsyncMock(side_effect=[False, False]),
            ),
            patch(
                "utils.scraper._check_ozon_logged_out",
                AsyncMock(side_effect=[False, False]),
            ),
            patch(
                "utils.scraper._check_ozon_authorized_profile",
                AsyncMock(side_effect=[True, True]),
            ),
        ):
            result = await probe_ozon_diagnostic_state(
                page,
                emulate_full_update=False,
                settle_wait_ms=0,
            )

        self.assertEqual(result["auth_state"], "authorized")
        self.assertTrue(result["authorized_control_visible_initial"])
        self.assertTrue(result["authorized_control_visible"])

    async def test_late_error_preserves_block_result(self):
        page = FakePage()
        page.evaluate.side_effect = RuntimeError("секретный URL не должен попасть в отчет")
        with (
            patch(
                "utils.scraper._check_ozon_block",
                AsyncMock(side_effect=[True, True]),
            ),
            patch(
                "utils.scraper._check_ozon_logged_out",
                AsyncMock(side_effect=[False, False]),
            ),
        ):
            result = await probe_ozon_diagnostic_state(
                page,
                emulate_full_update=False,
                settle_wait_ms=0,
            )

        self.assertTrue(result["explicit_block_detected"])
        self.assertEqual(result["probe_error"], "RuntimeError")
        self.assertNotIn("секретный", str(result))


if __name__ == "__main__":
    unittest.main()
