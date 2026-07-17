import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from loggers import logger


__all__ = [
    "AuthorizationState",
    "CookieDiff",
    "CookieModification",
    "CookieSnapshot",
    "DiagnosticCounter",
    "DiagnosticResult",
    "DiagnosticStage",
    "NavigationAction",
    "OzonAuthDiagnostics",
    "SanitizedUrl",
    "sanitize_url",
    "verify_report_permissions",
]


_OZON_DOMAIN = "ozon.ru"
_REFERENCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{7,79}$", re.IGNORECASE)
_SAFE_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$", re.IGNORECASE)
_SAFE_BROWSER_VERSION_PATTERN = re.compile(r"^[a-zA-Z0-9 ./_()+-]{1,80}$")
_SAFE_PATH_SEGMENTS = frozenset(
    {
        "auth",
        "captcha",
        "cart",
        "challenge",
        "error",
        "login",
        "my",
        "ozonid",
        "security",
    }
)


class DiagnosticStage(StrEnum):
    """Этап безопасной диагностики авторизации Ozon."""

    BEFORE_BROWSER_CLOSE = "before_browser_close"
    AFTER_BROWSER_START = "after_browser_start"
    BEFORE_NAVIGATION = "before_navigation"
    AFTER_NAVIGATION = "after_navigation"
    AFTER_SETTLE = "after_settle"
    FULL_SCENARIO = "full_scenario"


class NavigationAction(StrEnum):
    """Действие, способное изменить основной документ страницы."""

    GOTO = "goto"
    RELOAD = "reload"
    HISTORY_BACK = "history_back"
    REUSE_WITHOUT_NAVIGATION = "reuse_without_navigation"
    DOM_REPARSE = "dom_reparse"


class DiagnosticCounter(StrEnum):
    """Счетчик событий одного диагностического инцидента."""

    GOTO = "goto_count"
    RELOAD = "reload_count"
    HISTORY_NAVIGATION = "history_navigation_count"
    REUSE_WITHOUT_NAVIGATION = "reuse_without_navigation_count"
    DOCUMENT_REQUEST = "document_request_count"
    DOCUMENT_RESPONSE = "document_response_count"
    DOCUMENT_FAILURE = "document_failure_count"
    REDIRECT = "redirect_count"
    OZON_HTTP_401 = "ozon_401_count"
    OZON_HTTP_403 = "ozon_403_count"
    AUTH_PROBE = "auth_probe_count"


class DiagnosticResult(StrEnum):
    """Итог контролируемой диагностики авторизации Ozon."""

    SESSION_PRESERVED = "session_preserved"
    IDENTITY_MISMATCH = "identity_mismatch"
    EXPLICIT_BLOCK = "explicit_block"
    NETWORK_OR_DOM_ERROR = "network_or_dom_error"
    COOKIES_ABSENT_BEFORE_NAVIGATION = "cookies_absent_before_navigation"
    COOKIES_CHANGED_AFTER_NAVIGATION = "cookies_changed_after_navigation"
    ANONYMOUS_WITH_PRESERVED_COOKIES = "anonymous_with_preserved_cookies"
    AUTH_STATE_UNKNOWN = "auth_state_unknown"
    INCONCLUSIVE = "inconclusive"


class AuthorizationState(StrEnum):
    """Результат одной проверки элементов авторизации страницы."""

    AUTHORIZED = "authorized"
    ANONYMOUS = "anonymous"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SanitizedUrl:
    """URL без query, fragment, userinfo и идентифицирующих сегментов пути."""

    host: str | None
    path: str | None
    is_ozon: bool

    def as_report_fields(self) -> dict[str, str | bool | None]:
        """Возвращает безопасные поля URL для диагностического события."""
        return {
            "final_host": self.host,
            "final_path": self.path,
            "final_host_is_ozon": self.is_ozon,
        }


@dataclass(slots=True)
class _CookieRecord:
    """Хранит cookie только в памяти до завершения инцидента."""

    cookie_ref: str
    name_ref: str
    domain: str
    host_only: bool
    path_ref: str
    value: str = field(repr=False)
    session: bool
    expires_at_utc: str | None
    secure: bool
    http_only: bool
    same_site: str | None

    def report_metadata(self) -> dict[str, str | bool | None]:
        """Возвращает метаданные cookie без имени, пути и значения."""
        return {
            "cookie_ref": self.cookie_ref,
            "name_ref": self.name_ref,
            "domain": self.domain,
            "host_only": self.host_only,
            "path_ref": self.path_ref,
            "session": self.session,
            "expires_at_utc": self.expires_at_utc,
            "secure": self.secure,
            "http_only": self.http_only,
            "same_site": self.same_site,
        }


@dataclass(slots=True)
class CookieSnapshot:
    """Снимок Ozon-cookies с чувствительными значениями только в памяти."""

    incident_id: str
    stage: DiagnosticStage
    captured_at_utc: str
    _cookies: dict[str, _CookieRecord] = field(repr=False)

    @property
    def cookie_count(self) -> int:
        """Возвращает число уникальных Ozon-cookies в снимке."""
        return len(self._cookies)

    @property
    def session_count(self) -> int:
        """Возвращает число сессионных Ozon-cookies."""
        return sum(cookie.session for cookie in self._cookies.values())

    @property
    def persistent_count(self) -> int:
        """Возвращает число постоянных Ozon-cookies."""
        return self.cookie_count - self.session_count

    @property
    def has_cookies(self) -> bool:
        """Показывает, содержит ли снимок хотя бы одну Ozon-cookie."""
        return self.cookie_count > 0

    def clear_sensitive(self) -> None:
        """Удаляет значения cookies из памяти после завершения сравнения."""
        for cookie in self._cookies.values():
            cookie.value = ""
        self._cookies.clear()


@dataclass(frozen=True, slots=True)
class CookieModification:
    """Описывает только типы изменений одной cookie."""

    cookie_ref: str
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CookieDiff:
    """Безопасная разница между двумя снимками Ozon-cookies."""

    before_stage: DiagnosticStage
    after_stage: DiagnosticStage
    added: tuple[str, ...]
    removed: tuple[str, ...]
    modified: tuple[CookieModification, ...]

    @property
    def added_count(self) -> int:
        """Возвращает число добавленных cookies."""
        return len(self.added)

    @property
    def removed_count(self) -> int:
        """Возвращает число удаленных cookies."""
        return len(self.removed)

    @property
    def modified_count(self) -> int:
        """Возвращает число измененных cookies."""
        return len(self.modified)

    @property
    def changed_count(self) -> int:
        """Возвращает общее число добавленных, удаленных и измененных cookies."""
        return self.added_count + self.removed_count + self.modified_count

    @property
    def values_changed_count(self) -> int:
        """Возвращает число cookies с измененным значением без раскрытия значений."""
        return sum("value" in modification.changed_fields for modification in self.modified)

    @property
    def metadata_changed_count(self) -> int:
        """Возвращает число cookies с измененными метаданными."""
        return sum(
            any(field_name != "value" for field_name in modification.changed_fields)
            for modification in self.modified
        )


def _utc_now_text() -> str:
    """Возвращает текущее время UTC в формате с миллисекундами."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sanitize_path(path: str) -> str:
    """Удаляет потенциальные идентификаторы из сегментов URL-пути."""
    if not path or path == "/":
        return "/"

    sanitized_segments = []
    for segment in path.split("/"):
        if not segment:
            continue

        normalized_segment = segment.casefold()
        if normalized_segment in _SAFE_PATH_SEGMENTS:
            sanitized_segments.append(normalized_segment)
        else:
            sanitized_segments.append("<redacted>")

    return f"/{'/'.join(sanitized_segments)}" if sanitized_segments else "/"


def _is_ozon_domain(domain: str) -> bool:
    """Проверяет принадлежность домена Ozon без слабого совпадения по подстроке."""
    normalized_domain = domain.casefold().lstrip(".")
    return normalized_domain == _OZON_DOMAIN or normalized_domain.endswith(f".{_OZON_DOMAIN}")


def sanitize_url(url: str | None) -> SanitizedUrl:
    """Удаляет из URL query, fragment, userinfo и идентифицирующие части пути."""
    if not url:
        return SanitizedUrl(host=None, path=None, is_ozon=False)

    try:
        parsed_url = urlsplit(url)
        host = parsed_url.hostname.casefold() if parsed_url.hostname else None
    except (TypeError, ValueError):
        return SanitizedUrl(host=None, path=None, is_ozon=False)

    if parsed_url.scheme not in ("http", "https") or not host:
        return SanitizedUrl(host=None, path=None, is_ozon=False)

    is_ozon = _is_ozon_domain(host)
    safe_host = host if is_ozon else "external"
    safe_path = _sanitize_path(parsed_url.path) if is_ozon else "/<redacted>"
    return SanitizedUrl(host=safe_host, path=safe_path, is_ozon=is_ozon)


def _validate_uuid(value: str | UUID | None) -> str:
    """Возвращает нормализованный UUID или создает новый."""
    if value is None:
        return str(uuid4())
    return str(UUID(str(value)))


def _validate_non_negative(value: int, field_name: str) -> int:
    """Проверяет неотрицательное целое значение диагностического счетчика."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Поле {field_name} должно быть неотрицательным целым числом")
    return value


def _validate_optional_positive(value: int | None, field_name: str) -> int | None:
    """Проверяет необязательное положительное целое значение."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Поле {field_name} должно быть положительным целым числом")
    return value


def _sanitize_code(value: str | None, fallback: str) -> str | None:
    """Оставляет только заранее безопасный технический код без текста исключения."""
    if value is None:
        return None
    if not isinstance(value, str):
        return fallback
    return value if _SAFE_CODE_PATTERN.fullmatch(value) else fallback


def _optional_bool(value: bool | None) -> bool | None:
    """Оставляет только явное логическое значение без произвольного содержимого."""
    return value if isinstance(value, bool) else None


def _sanitize_browser_version(value: str | None) -> str | None:
    """Ограничивает формат версии браузера безопасным набором символов."""
    if value is None:
        return None
    return value if _SAFE_BROWSER_VERSION_PATTERN.fullmatch(value) else "redacted"


def _normalize_expires(value: object) -> tuple[bool, str | None]:
    """Нормализует срок cookie и определяет session/persistent без утечки значения."""
    try:
        expires = float(value)
    except (TypeError, ValueError):
        return True, None

    if not math.isfinite(expires) or expires <= 0:
        return True, None

    try:
        expires_at = datetime.fromtimestamp(expires, UTC).replace(second=0, microsecond=0)
    except (OverflowError, OSError, ValueError):
        return False, None

    return False, expires_at.isoformat(timespec="minutes").replace("+00:00", "Z")


class OzonAuthDiagnostics:
    """Создает приватный JSONL-отчет об одном инциденте авторизации Ozon."""

    def __init__(
        self,
        report_path: Path,
        report_file,
        hmac_key: bytes,
        incident_id: str,
        task_id: str,
        bot_instance_id: str,
        task_scope_ref: str | None,
    ) -> None:
        self.report_path = report_path
        self.incident_id = incident_id
        self.task_id = task_id
        self.bot_instance_id = bot_instance_id
        self.task_scope_ref = task_scope_ref
        self._report_file = report_file
        self._hmac_key = hmac_key
        self._started_ns = time.monotonic_ns()
        self._event_seq = 0
        self._lock = threading.RLock()
        self._closed = False
        self._finished = False
        self._snapshots: list[CookieSnapshot] = []
        self._counters = {counter: 0 for counter in DiagnosticCounter}

        self._write_event(
            "incident.start",
            {
                "diagnostic_mode": True,
                "task_scope_ref": self.task_scope_ref,
            },
        )

    @classmethod
    def create(
        cls,
        report_dir: Path,
        *,
        task_id: str | UUID | None = None,
        bot_instance_id: str | UUID | None = None,
        task_scope: str | None = None,
        hmac_key: bytes | None = None,
        incident_id: str | UUID | None = None,
    ) -> Self:
        """Создает новый отчет с правами `0600` и случайным incident ID."""
        normalized_incident_id = _validate_uuid(incident_id)
        normalized_task_id = _validate_uuid(task_id)
        normalized_bot_instance_id = _validate_uuid(bot_instance_id)
        diagnostic_key = hmac_key or secrets.token_bytes(32)
        if len(diagnostic_key) < 32:
            raise ValueError("Ключ HMAC для диагностики должен содержать не менее 32 байт")

        report_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(report_dir, 0o700)
        report_path = report_dir / f"ozon_auth_{normalized_incident_id}.jsonl"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        file_descriptor = os.open(report_path, flags, 0o600)
        os.fchmod(file_descriptor, 0o600)
        report_file = os.fdopen(file_descriptor, "w", encoding="utf-8", buffering=1)

        task_scope_ref = None
        if task_scope:
            task_scope_ref = cls._make_ref_with_key(
                diagnostic_key,
                "s",
                task_scope,
            )

        diagnostics = cls(
            report_path=report_path,
            report_file=report_file,
            hmac_key=diagnostic_key,
            incident_id=normalized_incident_id,
            task_id=normalized_task_id,
            bot_instance_id=normalized_bot_instance_id,
            task_scope_ref=task_scope_ref,
        )
        logger.info(
            f"Создан приватный отчет диагностики Ozon: incident_id={normalized_incident_id}"
        )
        return diagnostics

    @staticmethod
    def _make_ref_with_key(key: bytes, prefix: str, value: str) -> str:
        """Создает короткую необратимую ссылку с помощью HMAC."""
        digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        return f"{prefix}_{digest}"

    def _make_ref(self, prefix: str, value: str) -> str:
        """Создает HMAC-ссылку, стабильную только в рамках диагностического ключа."""
        return self._make_ref_with_key(self._hmac_key, prefix, value)

    def make_profile_ref(self, profile_path: Path) -> str:
        """Обезличивает абсолютный путь профиля для последующей передачи в binding."""
        return self._make_ref("p", str(profile_path.resolve()))

    def _elapsed_ms(self) -> int:
        """Возвращает время от начала инцидента по монотонным часам."""
        return (time.monotonic_ns() - self._started_ns) // 1_000_000

    def _write_event(
        self,
        event: str,
        payload: Mapping[str, object],
        *,
        attempt: int | None = None,
    ) -> None:
        """Записывает только внутренне сформированное событие одной строкой JSONL."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Отчет диагностики Ozon уже закрыт")

            self._event_seq += 1
            event_data = {
                "schema_version": 1,
                "event": event,
                "event_seq": self._event_seq,
                "ts_utc": _utc_now_text(),
                "elapsed_ms": self._elapsed_ms(),
                "incident_id": self.incident_id,
                "task_id": self.task_id,
                "bot_instance_id": self.bot_instance_id,
                "marketplace": "ozon",
                "attempt": attempt,
            }
            event_data.update(payload)
            json.dump(
                event_data,
                self._report_file,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self._report_file.write("\n")
            self._report_file.flush()

    def record_stage(
        self,
        stage: DiagnosticStage,
        *,
        attempt: int | None = None,
    ) -> None:
        """Фиксирует переход к контролируемому этапу диагностики."""
        self._write_event("incident.stage", {"stage": stage.value}, attempt=attempt)

    def record_browser_binding(
        self,
        *,
        pid: int | None,
        cdp_port: int | None,
        profile_ref: str,
        profile_match_verified: bool,
        port_match_verified: bool,
        identity_verified: bool,
        process_owned_by_bot: bool,
        browser_reused: bool,
        context_reused: bool,
        page_reused: bool,
        matching_process_count: int | None = None,
        port_process_count: int | None = None,
        context_count: int | None = None,
        page_count: int | None = None,
        browser_version: str | None = None,
        attempt: int | None = None,
    ) -> None:
        """Фиксирует проверенную связь PID, CDP-порта и обезличенного профиля."""
        if not _REFERENCE_PATTERN.fullmatch(profile_ref):
            raise ValueError("profile_ref имеет небезопасный формат")

        safe_pid = _validate_optional_positive(pid, "pid")
        safe_port = _validate_optional_positive(cdp_port, "cdp_port")
        if safe_port is not None and safe_port > 65535:
            raise ValueError("CDP-порт должен находиться в диапазоне 1..65535")

        safe_context_count = None
        if context_count is not None:
            safe_context_count = _validate_non_negative(context_count, "context_count")

        safe_matching_process_count = None
        if matching_process_count is not None:
            safe_matching_process_count = _validate_non_negative(
                matching_process_count,
                "matching_process_count",
            )

        safe_port_process_count = None
        if port_process_count is not None:
            safe_port_process_count = _validate_non_negative(
                port_process_count,
                "port_process_count",
            )

        safe_page_count = None
        if page_count is not None:
            safe_page_count = _validate_non_negative(page_count, "page_count")

        self._write_event(
            "browser.binding",
            {
                "pid": safe_pid,
                "cdp_port": safe_port,
                "profile_ref": profile_ref,
                "profile_match_verified": bool(profile_match_verified),
                "port_match_verified": bool(port_match_verified),
                "identity_verified": bool(identity_verified),
                "process_owned_by_bot": bool(process_owned_by_bot),
                "browser_reused": bool(browser_reused),
                "context_reused": bool(context_reused),
                "page_reused": bool(page_reused),
                "matching_process_count": safe_matching_process_count,
                "port_process_count": safe_port_process_count,
                "context_count": safe_context_count,
                "page_count": safe_page_count,
                "browser_version": _sanitize_browser_version(browser_version),
            },
            attempt=attempt,
        )

    def _cookie_record(self, cookie: Mapping[str, object]) -> _CookieRecord | None:
        """Преобразует Playwright-cookie во внутреннюю запись без записи секретов на диск."""
        name = cookie.get("name")
        domain = cookie.get("domain")
        if not isinstance(name, str) or not isinstance(domain, str):
            return None

        normalized_domain = domain.casefold()
        if not _is_ozon_domain(normalized_domain):
            return None

        path = cookie.get("path")
        normalized_path = path if isinstance(path, str) else "/"
        cookie_identity = f"{normalized_domain}\0{normalized_path}\0{name}"
        session, expires_at_utc = _normalize_expires(cookie.get("expires"))
        same_site = cookie.get("sameSite")
        if same_site not in ("Strict", "Lax", "None"):
            same_site = None

        raw_value = cookie.get("value")
        value = raw_value if isinstance(raw_value, str) else str(raw_value or "")
        return _CookieRecord(
            cookie_ref=self._make_ref("c", cookie_identity),
            name_ref=self._make_ref("n", name),
            domain=normalized_domain,
            host_only=not normalized_domain.startswith("."),
            path_ref=self._make_ref("cp", normalized_path),
            value=value,
            session=session,
            expires_at_utc=expires_at_utc,
            secure=bool(cookie.get("secure")),
            http_only=bool(cookie.get("httpOnly")),
            same_site=same_site,
        )

    def capture_cookie_snapshot(
        self,
        stage: DiagnosticStage,
        cookies: Sequence[Mapping[str, object]],
        *,
        attempt: int | None = None,
    ) -> CookieSnapshot:
        """Снимает Ozon-cookies, сохраняя значения только в памяти объекта."""
        cookie_records = {}
        for cookie in cookies:
            cookie_record = self._cookie_record(cookie)
            if cookie_record:
                cookie_records[cookie_record.cookie_ref] = cookie_record

        snapshot = CookieSnapshot(
            incident_id=self.incident_id,
            stage=stage,
            captured_at_utc=_utc_now_text(),
            _cookies=cookie_records,
        )
        self._snapshots.append(snapshot)
        report_cookies = [
            cookie.report_metadata()
            for cookie in sorted(cookie_records.values(), key=lambda item: item.cookie_ref)
        ]
        self._write_event(
            "cookie.snapshot",
            {
                "stage": stage.value,
                "cookie_count": snapshot.cookie_count,
                "session_count": snapshot.session_count,
                "persistent_count": snapshot.persistent_count,
                "cookies": report_cookies,
            },
            attempt=attempt,
        )
        return snapshot

    @staticmethod
    def _changed_cookie_fields(
        before_cookie: _CookieRecord,
        after_cookie: _CookieRecord,
    ) -> tuple[str, ...]:
        """Сравнивает cookie в памяти и возвращает только названия изменившихся полей."""
        changed_fields = []
        before_value = before_cookie.value.encode("utf-8")
        after_value = after_cookie.value.encode("utf-8")
        if not hmac.compare_digest(before_value, after_value):
            changed_fields.append("value")
        if before_cookie.session != after_cookie.session:
            changed_fields.append("session")
        if before_cookie.expires_at_utc != after_cookie.expires_at_utc:
            changed_fields.append("expires")
        if before_cookie.secure != after_cookie.secure:
            changed_fields.append("secure")
        if before_cookie.http_only != after_cookie.http_only:
            changed_fields.append("http_only")
        if before_cookie.same_site != after_cookie.same_site:
            changed_fields.append("same_site")
        return tuple(changed_fields)

    def record_cookie_diff(
        self,
        before: CookieSnapshot,
        after: CookieSnapshot,
        *,
        attempt: int | None = None,
    ) -> CookieDiff:
        """Сравнивает два снимка без записи значений или их отпечатков."""
        if before.incident_id != self.incident_id or after.incident_id != self.incident_id:
            raise ValueError("Снимки cookies принадлежат другому инциденту")

        before_refs = set(before._cookies)
        after_refs = set(after._cookies)
        added = tuple(sorted(after_refs - before_refs))
        removed = tuple(sorted(before_refs - after_refs))
        modifications = []
        for cookie_ref in sorted(before_refs & after_refs):
            changed_fields = self._changed_cookie_fields(
                before._cookies[cookie_ref],
                after._cookies[cookie_ref],
            )
            if changed_fields:
                modifications.append(
                    CookieModification(
                        cookie_ref=cookie_ref,
                        changed_fields=changed_fields,
                    )
                )

        cookie_diff = CookieDiff(
            before_stage=before.stage,
            after_stage=after.stage,
            added=added,
            removed=removed,
            modified=tuple(modifications),
        )
        self._write_event(
            "cookie.diff",
            {
                "before_stage": before.stage.value,
                "after_stage": after.stage.value,
                "before_count": before.cookie_count,
                "after_count": after.cookie_count,
                "added_count": cookie_diff.added_count,
                "removed_count": cookie_diff.removed_count,
                "modified_count": cookie_diff.modified_count,
                "changed_count": cookie_diff.changed_count,
                "values_changed_count": cookie_diff.values_changed_count,
                "metadata_changed_count": cookie_diff.metadata_changed_count,
                "added": list(added),
                "removed": list(removed),
                "modified": [
                    {
                        "cookie_ref": modification.cookie_ref,
                        "changed_fields": list(modification.changed_fields),
                    }
                    for modification in modifications
                ],
            },
            attempt=attempt,
        )
        return cookie_diff

    def increment_counter(self, counter: DiagnosticCounter, amount: int = 1) -> None:
        """Увеличивает один разрешенный счетчик диагностических событий."""
        safe_amount = _validate_non_negative(amount, "amount")
        with self._lock:
            if self._closed:
                raise RuntimeError("Отчет диагностики Ozon уже закрыт")
            self._counters[counter] += safe_amount

    def _increment_navigation_action(self, action: NavigationAction) -> None:
        """Увеличивает счетчик явного действия навигации."""
        action_counter = {
            NavigationAction.GOTO: DiagnosticCounter.GOTO,
            NavigationAction.RELOAD: DiagnosticCounter.RELOAD,
            NavigationAction.HISTORY_BACK: DiagnosticCounter.HISTORY_NAVIGATION,
            NavigationAction.REUSE_WITHOUT_NAVIGATION: (
                DiagnosticCounter.REUSE_WITHOUT_NAVIGATION
            ),
        }.get(action)
        if action_counter:
            self.increment_counter(action_counter)

    def record_navigation(
        self,
        action: NavigationAction,
        *,
        final_url: str | None,
        main_document_status: int | None,
        completed: bool,
        duration_ms: int,
        redirect_count: int = 0,
        document_request_count: int = 0,
        document_response_count: int = 0,
        document_failure_count: int = 0,
        ozon_401_count: int = 0,
        ozon_403_count: int = 0,
        attempt: int | None = None,
    ) -> None:
        """Фиксирует одно действие и счетчики основного документа без полного URL."""
        safe_duration = _validate_non_negative(duration_ms, "duration_ms")
        safe_redirects = _validate_non_negative(redirect_count, "redirect_count")
        safe_requests = _validate_non_negative(
            document_request_count,
            "document_request_count",
        )
        safe_responses = _validate_non_negative(
            document_response_count,
            "document_response_count",
        )
        safe_failures = _validate_non_negative(
            document_failure_count,
            "document_failure_count",
        )
        safe_401 = _validate_non_negative(ozon_401_count, "ozon_401_count")
        safe_403 = _validate_non_negative(ozon_403_count, "ozon_403_count")

        if main_document_status is not None:
            if isinstance(main_document_status, bool) or not isinstance(
                main_document_status,
                int,
            ):
                raise ValueError("HTTP-статус должен быть целым числом")
            if not 100 <= main_document_status <= 599:
                raise ValueError("HTTP-статус должен находиться в диапазоне 100..599")

        self._increment_navigation_action(action)
        counter_values = {
            DiagnosticCounter.DOCUMENT_REQUEST: safe_requests,
            DiagnosticCounter.DOCUMENT_RESPONSE: safe_responses,
            DiagnosticCounter.DOCUMENT_FAILURE: safe_failures,
            DiagnosticCounter.REDIRECT: safe_redirects,
            DiagnosticCounter.OZON_HTTP_401: safe_401,
            DiagnosticCounter.OZON_HTTP_403: safe_403,
        }
        for counter, value in counter_values.items():
            self.increment_counter(counter, value)

        sanitized_url = sanitize_url(final_url)
        payload = {
            "action": action.value,
            "completed": bool(completed),
            "duration_ms": safe_duration,
            "main_document_status": main_document_status,
            "redirect_count": safe_redirects,
            "document_request_count": safe_requests,
            "document_response_count": safe_responses,
            "document_failure_count": safe_failures,
            "ozon_401_count": safe_401,
            "ozon_403_count": safe_403,
        }
        payload.update(sanitized_url.as_report_fields())
        self._write_event("navigation.action", payload, attempt=attempt)

    @staticmethod
    def _authorization_state(
        anonymous_control_visible: bool | None,
        anonymous_text_matches: bool | None,
        authorized_control_visible: bool | None,
    ) -> AuthorizationState:
        """Классифицирует только явно наблюдаемые сигналы страницы."""
        anonymous = anonymous_control_visible is True and anonymous_text_matches is True
        authorized = authorized_control_visible is True
        if anonymous and authorized:
            return AuthorizationState.AMBIGUOUS
        if anonymous:
            return AuthorizationState.ANONYMOUS
        if authorized:
            return AuthorizationState.AUTHORIZED
        return AuthorizationState.UNKNOWN

    def record_auth_probe(
        self,
        stage: DiagnosticStage,
        *,
        anonymous_control_visible: bool | None,
        anonymous_text_matches: bool | None,
        authorized_control_visible: bool | None,
        document_ready_state: str | None,
        settle_wait_ms: int = 0,
        explicit_block_detected: bool = False,
        networkidle_timeout: bool = False,
        probe_error: str | None = None,
        attempt: int | None = None,
    ) -> AuthorizationState:
        """Фиксирует DOM-сигналы авторизации без текста страницы и селекторов."""
        safe_settle_wait = _validate_non_negative(settle_wait_ms, "settle_wait_ms")
        safe_ready_state = document_ready_state
        if safe_ready_state not in ("loading", "interactive", "complete", None):
            safe_ready_state = "unknown"

        safe_anonymous_visible = _optional_bool(anonymous_control_visible)
        safe_anonymous_text = _optional_bool(anonymous_text_matches)
        safe_authorized_visible = _optional_bool(authorized_control_visible)
        authorization_state = self._authorization_state(
            safe_anonymous_visible,
            safe_anonymous_text,
            safe_authorized_visible,
        )
        self.increment_counter(DiagnosticCounter.AUTH_PROBE)
        self._write_event(
            "auth.probe",
            {
                "stage": stage.value,
                "settle_wait_ms": safe_settle_wait,
                "document_ready_state": safe_ready_state,
                "anonymous_control_visible": safe_anonymous_visible,
                "anonymous_text_matches": safe_anonymous_text,
                "authorized_control_visible": safe_authorized_visible,
                "auth_state": authorization_state.value,
                "explicit_block_detected": bool(explicit_block_detected),
                "networkidle_timeout": bool(networkidle_timeout),
                "probe_error": _sanitize_code(probe_error, "probe_error"),
            },
            attempt=attempt,
        )
        return authorization_state

    @property
    def counters(self) -> dict[str, int]:
        """Возвращает копию текущих счетчиков с безопасными ключами."""
        with self._lock:
            return {counter.value: value for counter, value in self._counters.items()}

    def _clear_sensitive_data(self) -> None:
        """Удаляет все сохраненные в памяти значения cookies."""
        for snapshot in self._snapshots:
            snapshot.clear_sensitive()
        self._snapshots.clear()
        self._hmac_key = b""

    def finish(self, result: DiagnosticResult, *, attempts: int = 1) -> None:
        """Записывает итог, синхронизирует файл и удаляет чувствительные данные из памяти."""
        safe_attempts = _validate_non_negative(attempts, "attempts")
        with self._lock:
            if self._finished:
                return

            try:
                self._write_event(
                    "incident.finish",
                    {
                        "result_type": result.value,
                        "attempts": safe_attempts,
                        **self.counters,
                    },
                )
                self._finished = True
                self._report_file.flush()
                os.fsync(self._report_file.fileno())
            finally:
                self._clear_sensitive_data()
                self._report_file.close()
                self._closed = True

        logger.info(
            f"Диагностика Ozon завершена: incident_id={self.incident_id}, "
            f"результат={result.value}"
        )

    def close(self) -> None:
        """Безопасно закрывает незавершенный отчет с результатом `inconclusive`."""
        if self._closed:
            return
        self.finish(DiagnosticResult.INCONCLUSIVE)

    def __enter__(self) -> Self:
        """Возвращает открытый диагностический отчет."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Закрывает отчет без записи текста возникшего исключения."""
        if self._closed:
            return
        result = (
            DiagnosticResult.NETWORK_OR_DOM_ERROR
            if exc_type
            else DiagnosticResult.INCONCLUSIVE
        )
        self.finish(result)


def verify_report_permissions(report_path: Path) -> bool:
    """Проверяет, что диагностический JSONL доступен только владельцу."""
    try:
        return stat.S_IMODE(report_path.stat().st_mode) == 0o600
    except OSError:
        return False
