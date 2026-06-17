from pathlib import Path

from core.models import Marketplace
from loggers import logger


class MaintenanceMode:
    """Управление персональным режимом технических работ для пользователей"""

    def __init__(self):
        self.maintenance_dir = Path("maintenance_flags")
        self.maintenance_dir.mkdir(exist_ok=True)

    def _get_flag_file(
        self,
        user_id: int,
        marketplace: Marketplace | str | None = None
    ) -> Path:
        """Возвращает путь к файлу флага для пользователя и маркетплейса."""
        if marketplace is None:
            return self.maintenance_dir / f"maintenance_{user_id}.flag"

        marketplace_value = (
            marketplace.value if isinstance(marketplace, Marketplace) else marketplace
        )
        return self.maintenance_dir / f"maintenance_{user_id}_{marketplace_value}.flag"

    def is_active(
        self,
        user_id: int,
        marketplace: Marketplace | str | None = None
    ) -> bool:
        """
        Проверяет, активен ли режим техработ для пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс или None для проверки общего флага

        Returns:
            True если пользователь заблокирован, False иначе
        """
        common_flag = self._get_flag_file(user_id).exists()
        if common_flag:
            return True

        if marketplace is None:
            return False

        return self._get_flag_file(user_id, marketplace).exists()

    def enable(self, user_id: int, marketplace: Marketplace | str | None = None):
        """
        Включает режим техработ для пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс или None для общего флага
        """
        flag_file = self._get_flag_file(user_id, marketplace)
        flag_file.touch()
        logger.warning(
            f"🚨 Режим техработ ВКЛЮЧЕН для пользователя {user_id}, "
            f"маркетплейс={marketplace or 'все'}"
        )

    def disable(self, user_id: int, marketplace: Marketplace | str | None = None):
        """
        Выключает режим техработ для пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс или None для общего флага
        """
        flag_file = self._get_flag_file(user_id, marketplace)
        if flag_file.exists():
            flag_file.unlink()
            logger.info(
                f"✅ Режим техработ ВЫКЛЮЧЕН для пользователя {user_id}, "
                f"маркетплейс={marketplace or 'все'}"
            )

    def get_blocked_users(self) -> list[int]:
        """
        Возвращает список ID заблокированных пользователей.

        Returns:
            Список ID пользователей в режиме техработ
        """
        blocked = set()
        for flag_file in self.maintenance_dir.glob("maintenance_*.flag"):
            try:
                user_id = int(flag_file.stem.split("_")[1])
                blocked.add(user_id)
            except (ValueError, IndexError):
                logger.warning(f"Некорректное имя файла флага: {flag_file}")
        return sorted(blocked)


maintenance_mode = MaintenanceMode()
