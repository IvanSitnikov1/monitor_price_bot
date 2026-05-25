import logging
from pathlib import Path

from core.database import get_all_user_ids
from config import config


logger = logging.getLogger(__name__)

MAINTENANCE_FLAG_FILE = Path("maintenance.flag")


class MaintenanceMode:
    """Управление режимом технических работ"""

    @staticmethod
    def is_active() -> bool:
        """Проверяет, активен ли режим техработ"""
        return MAINTENANCE_FLAG_FILE.exists()

    @staticmethod
    def enable():
        """Включает режим техработ"""
        MAINTENANCE_FLAG_FILE.touch()
        logger.warning("🚨 Режим техработ ВКЛЮЧЕН")

    @staticmethod
    def disable():
        """Выключает режим техработ"""
        if MAINTENANCE_FLAG_FILE.exists():
            MAINTENANCE_FLAG_FILE.unlink()
        logger.info("✅ Режим техработ ВЫКЛЮЧЕН")

    @staticmethod
    async def notify_all_users(bot, message: str):
        """Отправляет сообщение всем пользователям бота"""
        user_ids = await get_all_user_ids()
        logger.info(f"Рассылка сообщения {len(user_ids)} пользователям")

        success_count = 0
        fail_count = 0

        for user_id in user_ids:
            try:
                await bot.send_message(user_id, message)
                success_count += 1
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
                fail_count += 1

        logger.info(
            f"Рассылка завершена: успешно={success_count}, ошибок={fail_count}"
        )
        return success_count, fail_count

    @staticmethod
    async def notify_admin(bot, message: str):
        """Отправляет сообщение админу бота"""
        await bot.send_message(config.ADMIN_USER_ID, message)
