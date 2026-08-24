import unittest
from unittest.mock import AsyncMock, patch

from keyboards.reply import get_reply_keyboard_version
from utils import reply_keyboard_sync


class ReplyKeyboardSyncTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет одноразовую синхронизацию reply-клавиатур."""

    def setUp(self):
        self.admin_patch = patch.object(reply_keyboard_sync.config, "ADMIN_USER_ID", 2)
        self.admin_patch.start()

    def tearDown(self):
        self.admin_patch.stop()

    async def test_keyboard_is_sent_once_for_each_role(self):
        """Устаревшее меню отправляется один раз с учетом роли пользователя."""
        bot = AsyncMock()
        current_users = [
            {"user_id": 1, "reply_keyboard_version": ""},
            {"user_id": 2, "reply_keyboard_version": ""},
        ]
        updated_users = [
            {
                "user_id": 1,
                "reply_keyboard_version": get_reply_keyboard_version(False),
            },
            {
                "user_id": 2,
                "reply_keyboard_version": get_reply_keyboard_version(True),
            },
        ]

        with (
            patch.object(
                reply_keyboard_sync,
                "get_users_with_keyboard_versions",
                new=AsyncMock(side_effect=[current_users, updated_users]),
            ),
            patch.object(
                reply_keyboard_sync,
                "mark_reply_keyboard_version",
                new=AsyncMock(),
            ) as mark_version,
        ):
            await reply_keyboard_sync.sync_reply_keyboards(bot)
            await reply_keyboard_sync.sync_reply_keyboards(bot)

        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(mark_version.await_count, 2)

        first_markup = bot.send_message.await_args_list[0].kwargs["reply_markup"]
        second_markup = bot.send_message.await_args_list[1].kwargs["reply_markup"]
        self.assertEqual(first_markup.keyboard[1][0].text, "Настройки")
        self.assertEqual(second_markup.keyboard[1][0].text, "📢 Рассылка")

    async def test_error_for_one_user_does_not_stop_sync(self):
        """Ошибка Telegram для одного пользователя не останавливает остальных."""
        bot = AsyncMock()
        bot.send_message.side_effect = [RuntimeError("ошибка отправки"), None]
        users = [
            {"user_id": 1, "reply_keyboard_version": ""},
            {"user_id": 2, "reply_keyboard_version": ""},
        ]

        with (
            patch.object(
                reply_keyboard_sync,
                "get_users_with_keyboard_versions",
                new=AsyncMock(return_value=users),
            ),
            patch.object(
                reply_keyboard_sync,
                "mark_reply_keyboard_version",
                new=AsyncMock(),
            ) as mark_version,
        ):
            await reply_keyboard_sync.sync_reply_keyboards(bot)

        self.assertEqual(bot.send_message.await_count, 2)
        mark_version.assert_awaited_once_with(2, get_reply_keyboard_version(True))


if __name__ == "__main__":
    unittest.main()
