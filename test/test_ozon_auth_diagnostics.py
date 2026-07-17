import json
import re
import stat
import tempfile
import unittest
from pathlib import Path

from utils.ozon_auth_diagnostics import (
    DiagnosticResult,
    DiagnosticStage,
    NavigationAction,
    OzonAuthDiagnostics,
)


class OzonAuthDiagnosticsPrivacyTests(unittest.TestCase):
    """Проверяет приватность диагностического отчета авторизации Ozon."""

    @staticmethod
    def _cookie(
        name: str,
        value: str,
        *,
        expires: int = -1,
        secure: bool = True,
        http_only: bool = True,
        same_site: str = "Lax",
    ) -> dict[str, object]:
        """Создает тестовую Ozon-cookie с явно заданными метаданными."""
        return {
            "name": name,
            "value": value,
            "domain": ".ozon.ru",
            "path": "/",
            "expires": expires,
            "secure": secure,
            "httpOnly": http_only,
            "sameSite": same_site,
        }

    @staticmethod
    def _read_events(report_path: Path) -> list[dict[str, object]]:
        """Читает события созданного JSONL-отчета."""
        return [
            json.loads(line)
            for line in report_path.read_text(encoding="utf-8").splitlines()
        ]

    def test_report_permissions_redaction_and_cookie_diff(self):
        """Проверяет права, обезличивание и безопасные типы изменений cookies."""
        raw_user_scope = "telegram-user-918273645"
        raw_profile_path = Path("/srv/private/user_data/telegram-user-918273645")
        raw_url = (
            "https://diagnostic-user:diagnostic-password@www.ozon.ru/"
            "cart/customer-918273645?auth_query=private-query#private-fragment"
        )
        raw_exception = "TimeoutError: private exception token 918273645"
        raw_cookie_names = {
            "raw-cookie-unchanged-918273645",
            "raw-cookie-modified-918273645",
            "raw-cookie-removed-918273645",
            "raw-cookie-added-918273645",
        }
        raw_cookie_values = {
            "raw-value-unchanged-918273645",
            "raw-value-before-918273645",
            "raw-value-after-918273645",
            "raw-value-removed-918273645",
            "raw-value-added-918273645",
        }

        with tempfile.TemporaryDirectory() as directory:
            report_dir = Path(directory) / "reports"
            diagnostics = OzonAuthDiagnostics.create(
                report_dir,
                task_scope=raw_user_scope,
                hmac_key=b"diagnostic-test-key-at-least-32-bytes",
            )
            report_path = diagnostics.report_path
            profile_ref = diagnostics.make_profile_ref(raw_profile_path)
            diagnostics.record_browser_binding(
                pid=12345,
                cdp_port=19223,
                profile_ref=profile_ref,
                profile_match_verified=True,
                port_match_verified=True,
                identity_verified=True,
                process_owned_by_bot=True,
                browser_reused=False,
                context_reused=False,
                page_reused=False,
            )

            before = diagnostics.capture_cookie_snapshot(
                DiagnosticStage.BEFORE_NAVIGATION,
                [
                    self._cookie(
                        "raw-cookie-unchanged-918273645",
                        "raw-value-unchanged-918273645",
                    ),
                    self._cookie(
                        "raw-cookie-modified-918273645",
                        "raw-value-before-918273645",
                    ),
                    self._cookie(
                        "raw-cookie-removed-918273645",
                        "raw-value-removed-918273645",
                    ),
                ],
            )
            after = diagnostics.capture_cookie_snapshot(
                DiagnosticStage.AFTER_NAVIGATION,
                [
                    self._cookie(
                        "raw-cookie-unchanged-918273645",
                        "raw-value-unchanged-918273645",
                    ),
                    self._cookie(
                        "raw-cookie-modified-918273645",
                        "raw-value-after-918273645",
                        expires=2_000_000_000,
                        secure=False,
                        http_only=False,
                        same_site="None",
                    ),
                    self._cookie(
                        "raw-cookie-added-918273645",
                        "raw-value-added-918273645",
                    ),
                ],
            )
            cookie_diff = diagnostics.record_cookie_diff(before, after)
            diagnostics.record_navigation(
                NavigationAction.GOTO,
                final_url=raw_url,
                main_document_status=200,
                completed=True,
                duration_ms=10,
            )
            diagnostics.record_auth_probe(
                DiagnosticStage.AFTER_NAVIGATION,
                anonymous_control_visible=None,
                anonymous_text_matches=None,
                authorized_control_visible=None,
                document_ready_state="complete",
                probe_error=raw_exception,
            )
            diagnostics.finish(DiagnosticResult.INCONCLUSIVE)

            self.assertEqual(stat.S_IMODE(report_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
            self.assertEqual(cookie_diff.added_count, 1)
            self.assertEqual(cookie_diff.removed_count, 1)
            self.assertEqual(cookie_diff.modified_count, 1)
            self.assertEqual(cookie_diff.changed_count, 3)
            self.assertEqual(cookie_diff.values_changed_count, 1)
            self.assertEqual(cookie_diff.metadata_changed_count, 1)
            self.assertEqual(
                cookie_diff.modified[0].changed_fields,
                ("value", "session", "expires", "secure", "http_only", "same_site"),
            )
            for cookie_ref in (*cookie_diff.added, *cookie_diff.removed):
                self.assertRegex(cookie_ref, re.compile(r"^c_[0-9a-f]{20}$"))

            report_text = report_path.read_text(encoding="utf-8")
            secrets = {
                raw_user_scope,
                str(raw_profile_path),
                raw_url,
                "diagnostic-user",
                "diagnostic-password",
                "private-query",
                "private-fragment",
                raw_exception,
                *raw_cookie_names,
                *raw_cookie_values,
            }
            for secret in secrets:
                self.assertNotIn(secret, report_text)

            events = self._read_events(report_path)
            navigation_event = next(
                event for event in events if event["event"] == "navigation.action"
            )
            self.assertEqual(navigation_event["final_host"], "www.ozon.ru")
            self.assertEqual(navigation_event["final_path"], "/cart/<redacted>")

            diff_event = next(event for event in events if event["event"] == "cookie.diff")
            self.assertEqual(diff_event["added_count"], 1)
            self.assertEqual(diff_event["removed_count"], 1)
            self.assertEqual(diff_event["modified_count"], 1)
            self.assertEqual(diff_event["values_changed_count"], 1)
            self.assertEqual(diff_event["metadata_changed_count"], 1)
            self.assertEqual(
                diff_event["modified"][0]["changed_fields"],
                ["value", "session", "expires", "secure", "http_only", "same_site"],
            )

    def test_context_manager_does_not_write_raw_exception(self):
        """Проверяет отсутствие текста необработанного исключения в отчете."""
        raw_exception = "private RuntimeError message 564738291"

        with tempfile.TemporaryDirectory() as directory:
            report_dir = Path(directory) / "reports"
            report_path = None
            with self.assertRaisesRegex(RuntimeError, raw_exception):
                with OzonAuthDiagnostics.create(report_dir) as diagnostics:
                    report_path = diagnostics.report_path
                    raise RuntimeError(raw_exception)

            self.assertIsNotNone(report_path)
            report_text = report_path.read_text(encoding="utf-8")
            self.assertNotIn(raw_exception, report_text)
            events = self._read_events(report_path)
            self.assertEqual(events[-1]["event"], "incident.finish")
            self.assertEqual(
                events[-1]["result_type"],
                DiagnosticResult.NETWORK_OR_DOM_ERROR.value,
            )


if __name__ == "__main__":
    unittest.main()
