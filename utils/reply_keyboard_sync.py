import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import config
from core.database import (
    get_users_with_keyboard_versions,
    mark_reply_keyboard_version,
)
from keyboards.reply import get_reply_keyboard, get_reply_keyboard_version
from loggers import logger


_MENU_UPDATE_TEXT = (
    "✅ Меню бота обновлено.\n\n"
    "Новые кнопки уже доступны в клавиатуре ниже."
)


async def _send_keyboard_update(
    bot: Bot,
    user_id: int,
    is_admin: bool,
) -> bool:
    """Отправляет пользователю актуальную клавиатуру с одной повторной попыткой."""
    for attempt in range(2):
        try:
            await bot.send_message(
                user_id,
                _MENU_UPDATE_TEXT,
                reply_markup=get_reply_keyboard(is_admin),
            )
            return True
        except TelegramAPIError as error:
            retry_after = getattr(error, "retry_after", None)
            if retry_after is None or attempt:
                logger.warning(
                    f"Не удалось обновить reply-клавиатуру пользователя {user_id}: {error}"
                )
                return False

            await asyncio.sleep(float(retry_after) + 1)
        except Exception as error:
            logger.warning(
                f"Не удалось обновить reply-клавиатуру пользователя {user_id}: {error}"
            )
            return False

    return False


async def sync_reply_keyboards(bot: Bot) -> None:
    """Отправляет новые reply-клавиатуры пользователям с устаревшей версией."""
    users = await get_users_with_keyboard_versions()
    updated_count = 0

    for user in users:
        user_id = user["user_id"]
        is_admin = user_id == config.ADMIN_USER_ID
        current_version = get_reply_keyboard_version(is_admin)

        if user["reply_keyboard_version"] == current_version:
            continue

        if await _send_keyboard_update(bot, user_id, is_admin):
            try:
                await mark_reply_keyboard_version(user_id, current_version)
            except Exception as error:
                logger.error(
                    f"Не удалось сохранить версию клавиатуры пользователя {user_id}: {error}"
                )
                continue

            updated_count += 1

        await asyncio.sleep(0.05)

    logger.info(
        f"Синхронизация reply-клавиатур завершена: обновлено пользователей {updated_count}"
    )
