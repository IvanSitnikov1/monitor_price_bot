import unittest
from types import SimpleNamespace
from unittest.mock import patch

import scripts.diagnose_ozon_session as diagnostic_cli
from utils.browser_manager import DiagnosticNavigationError
from utils.ozon_auth_diagnostics import (
    CookieDiff,
    CookieSnapshot,
    DiagnosticResult,
)


class FakeDiagnostics:
    """Собирает вызовы CLI без создания диагностического файла."""

    instances = []

    def __init__(self):
        self.finished_result = None
        self.navigation_events = []
        self.__class__.instances.append(self)

    @classmethod
    def create(cls, *args, **kwargs):
        """Создает поддельный отчет одного запуска."""
        return cls()

    def make_profile_ref(self, profile_path):
        """Возвращает безопасную тестовую ссылку на профиль."""
        return "p_1234567890abcdef1234"

    def record_stage(self, *args, **kwargs):
        """Принимает диагностический этап."""

    def record_browser_binding(self, **kwargs):
        """Проверяет обязательные признаки привязки браузера."""
        assert kwargs["profile_match_verified"] is True
        assert kwargs["port_match_verified"] is True
        assert kwargs["identity_verified"] is True

    def capture_cookie_snapshot(self, stage, cookies):
        """Возвращает снимок с одной условной Ozon-cookie."""
        return CookieSnapshot("incident", stage, "time", {"c_ref": object()})

    def record_cookie_diff(self, before, after):
        """Возвращает разницу без изменений cookies."""
        return CookieDiff(before.stage, after.stage, (), (), ())

    def record_navigation(self, action, **kwargs):
        """Сохраняет только агрегаты перехода для утверждений теста."""
        self.navigation_events.append(kwargs)

    def record_auth_probe(self, *args, **kwargs):
        """Принимает результат DOM-проверки."""

    def finish(self, result):
        """Сохраняет итоговую измеряемую классификацию."""
        self.finished_result = result

    def close(self):
        """Имитирует безопасное закрытие отчета."""


class FakeRequest:
    """Представляет запрос одной поддельной вкладки."""

    def __init__(
        self,
        frame,
        resource_type="document",
        redirected_from=None,
        navigation=True,
    ):
        self.frame = frame
        self.resource_type = resource_type
        self.redirected_from = redirected_from
        self.navigation = navigation

    def is_navigation_request(self):
        """Возвращает признак навигационного запроса."""
        return self.navigation


class FakeContext:
    """Имитирует события Playwright BrowserContext."""

    def __init__(self):
        self.pages = []
        self.cookie_queries = []
        self.handlers = {
            event: []
            for event in ("page", "request", "response", "requestfailed")
        }

    def on(self, event, handler):
        """Подписывает обработчик события."""
        self.handlers[event].append(handler)

    def remove_listener(self, event, handler):
        """Удаляет обработчик события."""
        self.handlers[event].remove(handler)

    async def cookies(self, urls=None):
        """Возвращает условную cookie без сетевого обращения."""
        self.cookie_queries.append(urls)
        return [
            {
                "name": "private-cookie-name",
                "value": "private-cookie-value",
                "domain": ".ozon.ru",
            }
        ]

    def emit(self, event, value):
        """Синхронно передает событие подписанным обработчикам."""
        for handler in tuple(self.handlers[event]):
            handler(value)


def make_fake_page(url="https://www.ozon.ru/cart?share=private"):
    """Создает поддельную страницу с главным фреймом."""
    page = SimpleNamespace(url=url)
    main_frame = SimpleNamespace(page=page)
    page.main_frame = main_frame
    return page


class FakeBrowserManager:
    """Имитирует один BrowserManager без запуска Chromium."""

    instances = []

    def __init__(self, **kwargs):
        self.context = FakeContext()
        self.open_calls = []
        self.close_calls = 0
        self.__class__.instances.append(self)

    async def connect_user_browser(self, user_id):
        """Возвращает поддельные браузер и контекст."""
        browser = SimpleNamespace(contexts=[self.context], version="Chromium 1")
        return browser, self.context

    def get_browser_runtime_info(self, user_id):
        """Возвращает полностью подтвержденную привязку процесса."""
        return {
            "pid": 111,
            "port": 9223,
            "profile_matches": True,
            "port_matches": True,
            "identity_verified": True,
            "listening_socket_owned": True,
            "matching_process_count": 1,
            "port_process_count": 1,
        }

    async def open_diagnostic_page(self, cart_url, user_id, apply_stealth):
        """Имитирует ровно один goto и события его основного документа."""
        self.open_calls.append(apply_stealth)
        page = make_fake_page(cart_url)
        self.context.emit("page", page)
        request = FakeRequest(page.main_frame)
        self.context.emit("request", request)
        response = SimpleNamespace(request=request, url=cart_url, status=200)
        self.context.emit("response", response)
        return page, response

    async def close_all(self):
        """Учитывает обязательное закрытие созданного менеджера."""
        self.close_calls += 1


class FailingBrowserManager(FakeBrowserManager):
    """Имитирует ошибку до фактического вызова `page.goto`."""

    instances = []

    async def open_diagnostic_page(self, cart_url, user_id, apply_stealth):
        """Завершает создание вкладки ошибкой до перехода."""
        self.open_calls.append(apply_stealth)
        raise RuntimeError("ошибка создания вкладки")


class GotoFailingBrowserManager(FakeBrowserManager):
    """Имитирует ошибку после фактического вызова `page.goto`."""

    instances = []

    async def open_diagnostic_page(self, cart_url, user_id, apply_stealth):
        """Возвращает безопасную ошибку уже начатого перехода."""
        self.open_calls.append(apply_stealth)
        raise DiagnosticNavigationError("Диагностический переход не выполнен")


async def fake_get_user_cart(user_id, marketplace):
    """Возвращает сохраненную приватную ссылку только в память CLI."""
    return "https://www.ozon.ru/cart?share=private"


PROBE_CALLS = []


async def fake_probe(page, emulate_full_update):
    """Возвращает положительно подтвержденное состояние авторизации."""
    PROBE_CALLS.append(emulate_full_update)
    return {
        "anonymous_control_visible_initial": False,
        "anonymous_control_visible": False,
        "anonymous_text_matches": False,
        "authorized_control_visible": True,
        "document_ready_state": "complete",
        "explicit_block_detected": False,
        "settle_wait_ms": 4000,
        "probe_error": None,
    }


class DiagnosticCliStageTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет изолированные этапы диагностического CLI."""

    def setUp(self):
        """Очищает собранные вызовы перед каждым тестом."""
        FakeDiagnostics.instances.clear()
        FakeBrowserManager.instances.clear()
        FailingBrowserManager.instances.clear()
        GotoFailingBrowserManager.instances.clear()
        PROBE_CALLS.clear()

    async def _run_stage(self, stage, manager_class=FakeBrowserManager):
        """Запускает этап с поддельными внешними зависимостями."""
        with (
            patch.object(diagnostic_cli, "BrowserManager", manager_class),
            patch.object(diagnostic_cli, "OzonAuthDiagnostics", FakeDiagnostics),
            patch.object(diagnostic_cli, "get_user_cart", fake_get_user_cart),
            patch.object(
                diagnostic_cli,
                "probe_ozon_diagnostic_state",
                fake_probe,
            ),
        ):
            return await diagnostic_cli._run_diagnostics(123, stage)

    async def test_three_stages_use_expected_navigation(self):
        """Проверяет число переходов, stealth и закрытие каждого менеджера."""
        connect_exit_code = await self._run_stage("connect")
        connect_manager = FakeBrowserManager.instances[-1]
        self.assertEqual(connect_exit_code, 6)
        self.assertEqual(connect_manager.open_calls, [])
        self.assertEqual(connect_manager.close_calls, 1)
        self.assertEqual(connect_manager.context.cookie_queries, [None])
        self.assertIs(
            FakeDiagnostics.instances[-1].finished_result,
            DiagnosticResult.AUTH_STATE_UNKNOWN,
        )

        navigate_exit_code = await self._run_stage("navigate")
        navigate_manager = FakeBrowserManager.instances[-1]
        self.assertEqual(navigate_exit_code, 0)
        self.assertEqual(navigate_manager.open_calls, [False])
        self.assertEqual(navigate_manager.close_calls, 1)
        self.assertEqual(navigate_manager.context.cookie_queries, [None, None, None])

        full_exit_code = await self._run_stage("full")
        full_manager = FakeBrowserManager.instances[-1]
        self.assertEqual(full_exit_code, 0)
        self.assertEqual(full_manager.open_calls, [True])
        self.assertEqual(full_manager.close_calls, 1)
        self.assertEqual(full_manager.context.cookie_queries, [None, None, None])
        self.assertEqual(PROBE_CALLS, [False, True])

    async def test_close_all_after_navigation_error(self):
        """Проверяет закрытие менеджера после ошибки перехода."""
        with self.assertRaises(diagnostic_cli.DiagnosticScriptError):
            await self._run_stage("navigate", FailingBrowserManager)

        manager = FailingBrowserManager.instances[-1]
        self.assertEqual(manager.open_calls, [False])
        self.assertEqual(manager.close_calls, 1)
        self.assertEqual(FakeDiagnostics.instances[-1].navigation_events, [])

    async def test_goto_error_is_counted_as_started_navigation(self):
        """Учитывает `goto` только после фактического вызова Playwright."""
        with self.assertRaises(diagnostic_cli.DiagnosticScriptError):
            await self._run_stage("navigate", GotoFailingBrowserManager)

        navigation_events = FakeDiagnostics.instances[-1].navigation_events
        self.assertEqual(len(navigation_events), 1)
        self.assertFalse(navigation_events[0]["completed"])


class NavigationEventCollectorTests(unittest.TestCase):
    """Проверяет фильтрацию сетевых агрегатов по целевой вкладке."""

    def test_excludes_background_page_and_counts_target_events(self):
        """Исключает фон и считает document, failure и Ozon 401/403."""
        context = FakeContext()
        collector = diagnostic_cli.NavigationEventCollector()
        collector.attach(context)

        target_page = make_fake_page()
        background_page = make_fake_page("https://www.ozon.ru/background")
        context.emit("page", target_page)

        background_request = FakeRequest(background_page.main_frame)
        context.emit("request", background_request)
        context.emit(
            "response",
            SimpleNamespace(
                request=background_request,
                url="https://www.ozon.ru/background",
                status=403,
            ),
        )

        document_request = FakeRequest(target_page.main_frame)
        context.emit("request", document_request)
        context.emit(
            "response",
            SimpleNamespace(
                request=document_request,
                url=target_page.url,
                status=200,
            ),
        )
        failed_request = FakeRequest(target_page.main_frame)
        context.emit("request", failed_request)
        context.emit("requestfailed", failed_request)

        api_request = FakeRequest(
            target_page.main_frame,
            resource_type="xhr",
            navigation=False,
        )
        for status in (401, 403):
            context.emit(
                "response",
                SimpleNamespace(
                    request=api_request,
                    url="https://api.ozon.ru/check?private=query",
                    status=status,
                ),
            )

        collector.detach(context)

        self.assertEqual(collector.document_request_count, 2)
        self.assertEqual(collector.document_response_count, 1)
        self.assertEqual(collector.document_failure_count, 1)
        self.assertEqual(collector.ozon_401_count, 1)
        self.assertEqual(collector.ozon_403_count, 1)
        self.assertIsNone(collector.target_page)
        self.assertTrue(all(not handlers for handlers in context.handlers.values()))


class DiagnosticClassificationTests(unittest.TestCase):
    """Проверяет классификацию измеряемых HTTP-результатов."""

    def test_main_document_http_errors_have_priority(self):
        """Отделяет явный отказ доступа от прочей сетевой ошибки."""
        snapshot = CookieSnapshot(
            "incident",
            diagnostic_cli.DiagnosticStage.BEFORE_NAVIGATION,
            "time",
            {"c_ref": object()},
        )
        probe = {
            "explicit_block_detected": False,
            "probe_error": None,
            "anonymous_control_visible": False,
            "anonymous_text_matches": False,
            "authorized_control_visible": False,
        }
        empty_diff = CookieDiff(
            snapshot.stage,
            diagnostic_cli.DiagnosticStage.AFTER_NAVIGATION,
            (),
            (),
            (),
        )

        forbidden_result, _ = diagnostic_cli._classify_probe(
            probe,
            snapshot,
            (empty_diff,),
            403,
            0,
            0,
        )
        server_error_result, _ = diagnostic_cli._classify_probe(
            probe,
            snapshot,
            (empty_diff,),
            500,
            0,
            0,
        )
        xhr_unauthorized_result, _ = diagnostic_cli._classify_probe(
            probe,
            snapshot,
            (empty_diff,),
            200,
            1,
            0,
        )
        xhr_forbidden_result, _ = diagnostic_cli._classify_probe(
            probe,
            snapshot,
            (empty_diff,),
            200,
            0,
            1,
        )

        self.assertIs(forbidden_result, DiagnosticResult.EXPLICIT_BLOCK)
        self.assertIs(server_error_result, DiagnosticResult.NETWORK_OR_DOM_ERROR)
        self.assertIs(
            xhr_unauthorized_result,
            DiagnosticResult.NETWORK_OR_DOM_ERROR,
        )
        self.assertIs(xhr_forbidden_result, DiagnosticResult.EXPLICIT_BLOCK)

    def test_saved_cart_url_is_restricted_to_ozon(self):
        """Не допускает внешний host, credentials или нештатный порт."""
        self.assertTrue(
            diagnostic_cli._is_valid_saved_ozon_cart(
                "https://www.ozon.ru/cart?share=private"
            )
        )
        invalid_urls = (
            "https://example.com/cart?share=private",
            "https://user:password@www.ozon.ru/cart?share=private",
            "https://www.ozon.ru:8443/cart?share=private",
            "https://www.ozon.ru/product?share=private",
        )

        for invalid_url in invalid_urls:
            self.assertFalse(diagnostic_cli._is_valid_saved_ozon_cart(invalid_url))


if __name__ == "__main__":
    unittest.main()
