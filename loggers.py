"""
Модуль настройки логирования для бота.
"""
import logging
import sys
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Форматтер с цветным выводом для консоли."""

    # ANSI коды цветов
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'

    def format(self, record):
        """Форматирует запись лога с цветами."""
        log_color = self.COLORS.get(record.levelname, self.RESET)

        # Форматируем время
        time_str = self.formatTime(record, '%Y-%m-%d %H:%M:%S')

        # Форматируем уровень с цветом
        level_str = f"{log_color}{self.BOLD}{record.levelname:8}{self.RESET}"

        # Форматируем имя модуля
        module_str = f"{self.BOLD}{record.name}{self.RESET}"

        # Собираем итоговое сообщение
        message = f"{time_str} | {level_str} | {module_str} | {record.getMessage()}"

        # Добавляем информацию об исключении, если есть
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)

        return message


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """
    Настраивает логирование для всего приложения.

    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Путь к файлу для записи логов (опционально)
    """
    # Преобразуем строку уровня в константу
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Создаем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Удаляем существующие обработчики
    root_logger.handlers.clear()

    # Консольный обработчик с цветным форматированием
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColoredFormatter())
    root_logger.addHandler(console_handler)

    # Файловый обработчик (если указан путь)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Настраиваем уровни для сторонних библиотек
    logging.getLogger('aiogram').setLevel(logging.INFO)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.INFO)

    root_logger.info("Логирование настроено")
    root_logger.info(f"Уровень логирования: {level.upper()}")
    if log_file:
        root_logger.info(f"Логи сохраняются в файл: {log_file}")


log_file = Path("logs/bot.log")
setup_logging(level="DEBUG", log_file=log_file)
logger = logging.getLogger(__name__)
