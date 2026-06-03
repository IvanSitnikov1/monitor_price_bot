from pathlib import Path

from loggers import logger


class MaintenanceMode:
    """Управление персональным режимом технических работ для пользователей"""

    def __init__(self):
        self.maintenance_dir = Path("maintenance_flags")
        self.maintenance_dir.mkdir(exist_ok=True)

    def _get_flag_file(self, user_id: int) -> Path:
        """Возвращает путь к файлу флага для пользователя"""
        return self.maintenance_dir / f"maintenance_{user_id}.flag"

    def is_active(self, user_id: int) -> bool:
        """
        Проверяет, активен ли режим техработ для пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            True если пользователь заблокирован, False иначе
        """
        return self._get_flag_file(user_id).exists()

    def enable(self, user_id: int):
        """
        Включает режим техработ для пользователя.

        Args:
            user_id: ID пользователя
        """
        flag_file = self._get_flag_file(user_id)
        flag_file.touch()
        logger.warning(f"🚨 Режим техработ ВКЛЮЧЕН для пользователя {user_id}")

    def disable(self, user_id: int):
        """
        Выключает режим техработ для пользователя.

        Args:
            user_id: ID пользователя
        """
        flag_file = self._get_flag_file(user_id)
        if flag_file.exists():
            flag_file.unlink()
            logger.info(f"✅ Режим техработ ВЫКЛЮЧЕН для пользователя {user_id}")

    def get_blocked_users(self) -> list[int]:
        """
        Возвращает список ID заблокированных пользователей.

        Returns:
            Список ID пользователей в режиме техработ
        """
        blocked = []
        for flag_file in self.maintenance_dir.glob("maintenance_*.flag"):
            try:
                user_id = int(flag_file.stem.split("_")[1])
                blocked.append(user_id)
            except (ValueError, IndexError):
                logger.warning(f"Некорректное имя файла флага: {flag_file}")
        return blocked


maintenance_mode = MaintenanceMode()
