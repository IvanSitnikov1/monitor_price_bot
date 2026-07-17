import fcntl
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESS_LOCK_PATH = PROJECT_ROOT / ".monitor_price_bot.lock"


class ProcessLockError(RuntimeError):
    """Ошибка получения общей межпроцессной блокировки приложения."""


class ProcessLock:
    """Удерживает неблокирующую Linux-блокировку рабочего или диагностического процесса."""

    def __init__(self, path: Path = PROCESS_LOCK_PATH):
        self.path = path
        self._file_descriptor: int | None = None

    def acquire(self) -> None:
        """Получает эксклюзивную блокировку или немедленно завершает попытку."""
        if self._file_descriptor is not None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            file_descriptor = os.open(self.path, flags, 0o600)
            os.fchmod(file_descriptor, 0o600)
        except OSError as error:
            raise ProcessLockError(
                "Не удалось открыть файл межпроцессной блокировки: "
                f"{type(error).__name__}"
            ) from error

        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(file_descriptor)
            raise ProcessLockError(
                "Другой экземпляр бота или диагностики уже запущен. "
                "Остановите его и повторите попытку."
            ) from error
        except OSError as error:
            os.close(file_descriptor)
            raise ProcessLockError(
                "Не удалось получить межпроцессную блокировку: "
                f"{type(error).__name__}"
            ) from error

        self._file_descriptor = file_descriptor
        try:
            os.ftruncate(file_descriptor, 0)
            os.write(file_descriptor, f"{os.getpid()}\n".encode())
        except OSError as error:
            self.release()
            raise ProcessLockError(
                "Не удалось записать владельца межпроцессной блокировки: "
                f"{type(error).__name__}"
            ) from error

    def release(self) -> None:
        """Освобождает блокировку и закрывает файловый дескриптор."""
        if self._file_descriptor is None:
            return

        file_descriptor = self._file_descriptor
        self._file_descriptor = None
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(file_descriptor)

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
