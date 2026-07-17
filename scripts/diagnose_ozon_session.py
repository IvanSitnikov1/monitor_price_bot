"""Запускает изолированную диагностику пользовательской сессии Ozon."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "debug_data" / "ozon_auth_diagnostics"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

from playwright.async_api import Browser, BrowserContext, Page, Request, Response

from config import config
from core.database import get_user_cart
from core.models import Marketplace
from utils.browser_manager import (
    BrowserIdentityError,
    BrowserManager,
    DiagnosticNavigationError,
)
from utils.ozon_auth_diagnostics import (
    CookieDiff,
    CookieSnapshot,
    DiagnosticResult,
    DiagnosticStage,
    NavigationAction,
    OzonAuthDiagnostics,
)
from utils.process_lock import ProcessLock, ProcessLockError
from utils.scraper import probe_ozon_diagnostic_state


STAGES = ("connect", "navigate", "full")


class DiagnosticScriptError(RuntimeError):
    """Ошибка безопасного диагностического сценария Ozon."""

    def __init__(
        self,
        message: str,
        result: DiagnosticResult = DiagnosticResult.NETWORK_OR_DOM_ERROR,
    ) -> None:
        super().__init__(message)
        self.result = result


class NavigationEventCollector:
    """Считает безопасные сетевые агрегаты одного диагностического перехода."""

    def __init__(self) -> None:
        self.target_page: Page | None = None
        self.document_request_count = 0
        self.document_response_count = 0
        self.document_failure_count = 0
        self.redirect_count = 0
        self.ozon_401_count = 0
        self.ozon_403_count = 0

    @staticmethod
    def _is_ozon_url(url: str) -> bool:
        """Проверяет домен Ozon, не сохраняя полный URL."""
        try:
            host = urlsplit(url).hostname
        except (TypeError, ValueError):
            return False

        normalized_host = host.casefold() if host else ""
        return normalized_host == "ozon.ru" or normalized_host.endswith(".ozon.ru")

    def _record_page(self, page: Page) -> None:
        """Сохраняет только первую вкладку, созданную после подключения счетчиков."""
        if self.target_page is None:
            self.target_page = page

    def _belongs_to_target_page(self, request: Request) -> bool:
        """Проверяет принадлежность запроса целевой диагностической вкладке."""
        if self.target_page is None:
            return False

        try:
            return request.frame.page == self.target_page
        except Exception:
            return False

    def _is_main_document_request(self, request: Request) -> bool:
        """Проверяет навигационный запрос главного фрейма целевой вкладки."""
        if not self._belongs_to_target_page(request) or request.resource_type != "document":
            return False

        try:
            return request.is_navigation_request() and request.frame == self.target_page.main_frame
        except Exception:
            return False

    def _record_request(self, request: Request) -> None:
        """Учитывает запрос документа и его редирект."""
        if not self._is_main_document_request(request):
            return

        self.document_request_count += 1
        if request.redirected_from is not None:
            self.redirect_count += 1

    def _record_response(self, response: Response) -> None:
        """Учитывает ответ документа и статусы 401/403 от Ozon."""
        request = response.request
        if not self._belongs_to_target_page(request):
            return

        if self._is_main_document_request(request):
            self.document_response_count += 1

        if not self._is_ozon_url(response.url):
            return
        if response.status == 401:
            self.ozon_401_count += 1
        elif response.status == 403:
            self.ozon_403_count += 1

    def _record_failure(self, request: Request) -> None:
        """Учитывает неуспешный запрос документа."""
        if self._is_main_document_request(request):
            self.document_failure_count += 1

    def attach(self, context: BrowserContext) -> None:
        """Подключает счетчики до создания диагностической вкладки."""
        context.on("page", self._record_page)
        context.on("request", self._record_request)
        context.on("response", self._record_response)
        context.on("requestfailed", self._record_failure)

    def detach(self, context: BrowserContext) -> None:
        """Отключает счетчики после завершения контролируемого этапа."""
        context.remove_listener("page", self._record_page)
        context.remove_listener("request", self._record_request)
        context.remove_listener("response", self._record_response)
        context.remove_listener("requestfailed", self._record_failure)
        self.target_page = None


def _positive_user_id(value: str) -> int:
    """Проверяет положительный Telegram ID из аргументов командной строки."""
    try:
        user_id = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("user-id должен быть целым числом") from error

    if user_id <= 0:
        raise argparse.ArgumentTypeError("user-id должен быть положительным числом")

    return user_id


def _is_valid_saved_ozon_cart(url: str) -> bool:
    """Проверяет сохраненную ссылку без ее вывода в журнал."""
    try:
        parsed_url = urlsplit(url)
        port = parsed_url.port
        query = parse_qs(parsed_url.query)
    except (TypeError, ValueError):
        return False

    return (
        parsed_url.scheme == "https"
        and parsed_url.hostname in {"ozon.ru", "www.ozon.ru"}
        and port is None
        and parsed_url.username is None
        and parsed_url.password is None
        and parsed_url.path == "/cart"
        and bool(query.get("share"))
    )


def _parse_args() -> argparse.Namespace:
    """Разбирает аргументы изолированного диагностического запуска."""
    parser = argparse.ArgumentParser(
        description=(
            "Изолированно проверяет сохраненную сессию Ozon без очереди, "
            "повторов и записи данных."
        )
    )
    parser.add_argument(
        "--user-id",
        required=True,
        type=_positive_user_id,
        help="Telegram ID пользователя с сохраненной корзиной Ozon",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=STAGES,
        help="Этап проверки: connect, navigate или full",
    )
    return parser.parse_args()


def _record_browser_binding(
    diagnostics: OzonAuthDiagnostics,
    browser_manager: BrowserManager,
    browser: Browser,
    context: BrowserContext,
    user_id: int,
) -> None:
    """Записывает проверенную привязку процесса, порта и профиля."""
    runtime_info = browser_manager.get_browser_runtime_info(user_id)
    profile_ref = diagnostics.make_profile_ref(
        PROJECT_ROOT / "user_data" / str(user_id)
    )
    identity_verified = bool(runtime_info["identity_verified"])

    diagnostics.record_browser_binding(
        pid=runtime_info.get("pid"),
        cdp_port=runtime_info.get("port"),
        profile_ref=profile_ref,
        profile_match_verified=bool(runtime_info["profile_matches"]),
        port_match_verified=bool(runtime_info["port_matches"]),
        identity_verified=identity_verified,
        process_owned_by_bot=bool(runtime_info["listening_socket_owned"]),
        browser_reused=False,
        context_reused=False,
        page_reused=False,
        matching_process_count=runtime_info.get("matching_process_count"),
        port_process_count=runtime_info.get("port_process_count"),
        context_count=len(browser.contexts),
        page_count=len(context.pages),
        browser_version=browser.version,
    )

    if not identity_verified:
        raise DiagnosticScriptError(
            "Не подтверждено соответствие PID, CDP-порта и профиля Chromium.",
            result=DiagnosticResult.IDENTITY_MISMATCH,
        )


async def _capture_cookies(
    diagnostics: OzonAuthDiagnostics,
    context: BrowserContext,
    stage: DiagnosticStage,
) -> CookieSnapshot:
    """Снимает безопасные метаданные cookies Ozon без записи значений."""
    cookies = await context.cookies()
    return diagnostics.capture_cookie_snapshot(stage, cookies)


def _record_navigation(
    diagnostics: OzonAuthDiagnostics,
    page: Page | None,
    response: Response | None,
    metrics: NavigationEventCollector,
    duration_ms: int,
    completed: bool,
) -> None:
    """Записывает итог единственного перехода без query-параметров URL."""
    status = response.status if response else None

    diagnostics.record_navigation(
        NavigationAction.GOTO,
        final_url=page.url if page else None,
        main_document_status=status,
        completed=completed,
        duration_ms=duration_ms,
        redirect_count=metrics.redirect_count,
        document_request_count=metrics.document_request_count,
        document_response_count=metrics.document_response_count,
        document_failure_count=metrics.document_failure_count,
        ozon_401_count=metrics.ozon_401_count,
        ozon_403_count=metrics.ozon_403_count,
    )


def _record_auth_probes(
    diagnostics: OzonAuthDiagnostics,
    probe: dict[str, object],
) -> None:
    """Записывает начальную и стабилизированную DOM-проверки авторизации."""
    diagnostics.record_auth_probe(
        DiagnosticStage.AFTER_NAVIGATION,
        anonymous_control_visible=probe.get("anonymous_control_visible_initial"),
        anonymous_text_matches=probe.get("anonymous_text_matches_initial"),
        authorized_control_visible=probe.get("authorized_control_visible_initial"),
        document_ready_state=None,
        settle_wait_ms=0,
        explicit_block_detected=bool(probe.get("explicit_block_detected_initial")),
        networkidle_timeout=False,
        probe_error=probe.get("probe_error"),
    )
    diagnostics.record_auth_probe(
        DiagnosticStage.AFTER_SETTLE,
        anonymous_control_visible=probe.get("anonymous_control_visible"),
        anonymous_text_matches=probe.get("anonymous_text_matches"),
        authorized_control_visible=probe.get("authorized_control_visible"),
        document_ready_state=probe.get("document_ready_state"),
        settle_wait_ms=int(probe.get("settle_wait_ms") or 0),
        explicit_block_detected=bool(probe.get("explicit_block_detected")),
        networkidle_timeout=bool(probe.get("networkidle_timeout")),
        probe_error=probe.get("probe_error"),
    )


def _cookie_diff_has_changes(cookie_diff: CookieDiff) -> bool:
    """Проверяет наличие любых безопасно измеренных изменений cookies."""
    return cookie_diff.changed_count > 0


def _classify_probe(
    probe: dict[str, object],
    cookies_before_navigation: CookieSnapshot,
    cookie_diffs: tuple[CookieDiff, ...],
    main_document_status: int | None,
    ozon_401_count: int,
    ozon_403_count: int,
) -> tuple[DiagnosticResult, int]:
    """Классифицирует проверку без предположений об антибот-причине."""
    if probe.get("explicit_block_detected"):
        return DiagnosticResult.EXPLICIT_BLOCK, 4
    if probe.get("probe_error"):
        return DiagnosticResult.NETWORK_OR_DOM_ERROR, 5
    if main_document_status in (401, 403):
        return DiagnosticResult.EXPLICIT_BLOCK, 4
    if main_document_status is None or main_document_status >= 400:
        return DiagnosticResult.NETWORK_OR_DOM_ERROR, 5
    anonymous = (
        probe.get("anonymous_control_visible") is True
        and probe.get("anonymous_text_matches") is True
    )
    authorized = probe.get("authorized_control_visible") is True
    if anonymous and authorized:
        return DiagnosticResult.AUTH_STATE_UNKNOWN, 6
    if authorized:
        return DiagnosticResult.SESSION_PRESERVED, 0
    if not cookies_before_navigation.has_cookies:
        return DiagnosticResult.COOKIES_ABSENT_BEFORE_NAVIGATION, 3
    if any(_cookie_diff_has_changes(cookie_diff) for cookie_diff in cookie_diffs):
        return DiagnosticResult.COOKIES_CHANGED_AFTER_NAVIGATION, 3
    if anonymous:
        return DiagnosticResult.ANONYMOUS_WITH_PRESERVED_COOKIES, 3
    if ozon_403_count > 0:
        return DiagnosticResult.EXPLICIT_BLOCK, 4
    if ozon_401_count > 0:
        return DiagnosticResult.NETWORK_OR_DOM_ERROR, 5

    return DiagnosticResult.AUTH_STATE_UNKNOWN, 6


async def _run_navigation_stage(
    diagnostics: OzonAuthDiagnostics,
    browser_manager: BrowserManager,
    context: BrowserContext,
    user_id: int,
    cart_url: str,
    emulate_full_update: bool,
    cookies_before_navigation: CookieSnapshot,
) -> int:
    """Выполняет ровно один переход и сравнивает состояние сессии."""
    diagnostics.record_stage(DiagnosticStage.BEFORE_NAVIGATION)
    if emulate_full_update:
        diagnostics.record_stage(DiagnosticStage.FULL_SCENARIO)

    metrics = NavigationEventCollector()
    page: Page | None = None
    response: Response | None = None
    goto_started = False
    completed = False
    duration_ms = 0
    started_at = time.monotonic()
    metrics.attach(context)
    try:
        try:
            page, response = await browser_manager.open_diagnostic_page(
                cart_url,
                user_id,
                apply_stealth=emulate_full_update,
            )
            goto_started = True
            completed = True
        except DiagnosticNavigationError as error:
            goto_started = True
            raise DiagnosticScriptError(
                f"Одиночный переход Ozon завершился ошибкой {type(error).__name__}."
            ) from None
        except Exception as error:
            raise DiagnosticScriptError(
                f"Одиночный переход Ozon завершился ошибкой {type(error).__name__}."
            ) from None
        finally:
            duration_ms = round((time.monotonic() - started_at) * 1000)

        diagnostics.record_stage(DiagnosticStage.AFTER_NAVIGATION)
        cookies_after_navigation = await _capture_cookies(
            diagnostics,
            context,
            DiagnosticStage.AFTER_NAVIGATION,
        )
        navigation_cookie_diff = diagnostics.record_cookie_diff(
            cookies_before_navigation,
            cookies_after_navigation,
        )

        probe = await probe_ozon_diagnostic_state(
            page,
            emulate_full_update=emulate_full_update,
        )
        diagnostics.record_stage(DiagnosticStage.AFTER_SETTLE)
        _record_auth_probes(diagnostics, probe)

        cookies_after_settle = await _capture_cookies(
            diagnostics,
            context,
            DiagnosticStage.AFTER_SETTLE,
        )
        settle_cookie_diff = diagnostics.record_cookie_diff(
            cookies_after_navigation,
            cookies_after_settle,
        )

    finally:
        try:
            metrics.detach(context)
        finally:
            if goto_started:
                _record_navigation(
                    diagnostics,
                    page,
                    response,
                    metrics,
                    duration_ms,
                    completed,
                )

    result, exit_code = _classify_probe(
        probe,
        cookies_before_navigation,
        (navigation_cookie_diff, settle_cookie_diff),
        response.status if response else None,
        metrics.ozon_401_count,
        metrics.ozon_403_count,
    )
    diagnostics.finish(result)
    return exit_code


async def _run_diagnostics(user_id: int, stage: str) -> int:
    """Запускает один изолированный этап диагностики Ozon."""
    browser_manager = BrowserManager(
        diagnostic_mode=True,
        refuse_existing_profiles=True,
    )
    diagnostics = OzonAuthDiagnostics.create(
        REPORT_DIR,
        task_scope=f"cli_{stage}",
    )
    finished = False

    try:
        cart_url = await get_user_cart(user_id, Marketplace.OZON)
        if not cart_url:
            raise DiagnosticScriptError(
                "У пользователя не сохранена корзина Ozon.",
                result=DiagnosticResult.INCONCLUSIVE,
            )
        if not _is_valid_saved_ozon_cart(cart_url):
            raise DiagnosticScriptError(
                "Сохраненная ссылка Ozon не прошла безопасную проверку.",
                result=DiagnosticResult.INCONCLUSIVE,
            )

        try:
            browser, context = await browser_manager.connect_user_browser(user_id)
        except BrowserIdentityError:
            raise DiagnosticScriptError(
                "Не подтверждено соответствие профиля, процесса и CDP-порта.",
                result=DiagnosticResult.IDENTITY_MISMATCH,
            ) from None
        diagnostics.record_stage(DiagnosticStage.AFTER_BROWSER_START)
        _record_browser_binding(
            diagnostics,
            browser_manager,
            browser,
            context,
            user_id,
        )
        cookies_after_start = await _capture_cookies(
            diagnostics,
            context,
            DiagnosticStage.AFTER_BROWSER_START,
        )

        if stage == "connect":
            cookies_absent = not cookies_after_start.has_cookies
            result = (
                DiagnosticResult.COOKIES_ABSENT_BEFORE_NAVIGATION
                if cookies_absent
                else DiagnosticResult.AUTH_STATE_UNKNOWN
            )
            diagnostics.finish(result)
            finished = True
            return 3 if cookies_absent else 6

        exit_code = await _run_navigation_stage(
            diagnostics,
            browser_manager,
            context,
            user_id,
            cart_url,
            emulate_full_update=stage == "full",
            cookies_before_navigation=cookies_after_start,
        )
        finished = True
        return exit_code
    except DiagnosticScriptError as error:
        if not finished:
            diagnostics.finish(error.result)
            finished = True
        raise
    except Exception as error:
        if not finished:
            diagnostics.finish(DiagnosticResult.NETWORK_OR_DOM_ERROR)
            finished = True
        raise DiagnosticScriptError(
            f"Диагностика завершилась ошибкой {type(error).__name__}."
        ) from None
    finally:
        try:
            await browser_manager.close_all()
        finally:
            diagnostics.close()


def main() -> int:
    """Проверяет настройки, получает общий lock и запускает диагностику."""
    args = _parse_args()
    if not config.OZON_DIAGNOSTICS_ENABLED:
        print(
            "Диагностика запрещена: включите OZON_DIAGNOSTICS_ENABLED.",
            file=sys.stderr,
        )
        return 2

    try:
        with ProcessLock():
            exit_code = asyncio.run(_run_diagnostics(args.user_id, args.stage))
    except (DiagnosticScriptError, ProcessLockError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Диагностика прервана пользователем.", file=sys.stderr)
        return 130
    except Exception as error:
        print(
            f"Диагностика аварийно завершилась: {type(error).__name__}.",
            file=sys.stderr,
        )
        return 1

    print(
        "Диагностика завершена. Приватный отчет сохранен в "
        "debug_data/ozon_auth_diagnostics."
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
