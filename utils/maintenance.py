from pathlib import Path

from loggers import logger
from config import config


class MaintenanceMode:
    """Управление режимом технических работ"""
    maintenance_flag_file = Path(config.MAINTENANCE_FLAG_FILE)

    def is_active(self) -> bool:
        """Проверяет, активен ли режим техработ"""
        return self.maintenance_flag_file.exists()

    def enable(self):
        """Включает режим техработ"""
        self.maintenance_flag_file.touch()
        logger.warning("🚨 Режим техработ ВКЛЮЧЕН")

    def disable(self):
        """Выключает режим техработ"""
        if self.maintenance_flag_file.exists():
            self.maintenance_flag_file.unlink()
        logger.info("✅ Режим техработ ВЫКЛЮЧЕН")

maintenance_mode = MaintenanceMode()
