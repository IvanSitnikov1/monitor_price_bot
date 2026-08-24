import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from core import database
from core.models import Marketplace
from keyboards.inline import (
    get_notification_mode_keyboard,
    get_notification_settings_keyboard,
    get_product_detail_keyboard,
)
from utils.queue_worker import _should_notify_price_drop


class NotificationSettingsTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет настройки уведомлений и целевую цену товара."""

    async def asyncSetUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "bot.db"
        self.database_patch = patch.object(database, "DB_PATH", self.database_path)
        self.database_patch.start()

        await database.setup_database()
        await database.register_user(1, "user", "Пользователь")

    async def asyncTearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    async def test_default_settings_and_migration_columns(self):
        """Новый пользователь получает только стандартный режим."""
        settings = await database.get_notification_settings(1)

        self.assertEqual(settings["standard_enabled"], 1)
        self.assertEqual(settings["drop_bounds_enabled"], 0)
        self.assertEqual(settings["target_price_enabled"], 0)
        self.assertIsNone(settings["drop_threshold_type"])

        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute("PRAGMA table_info(products)")
            columns = {row[1] for row in await cursor.fetchall()}

        self.assertIn("target_price", columns)
        self.assertIn("target_price_reached", columns)

    async def test_settings_and_threshold_are_saved(self):
        """Режимы и выбранный порог сохраняются отдельно для пользователя."""
        await database.update_notification_mode(1, "standard", False)
        await database.update_notification_mode(1, "drop_bounds", True)
        await database.set_drop_threshold(1, "percent", 12.5)

        settings = await database.get_notification_settings(1)
        self.assertEqual(settings["standard_enabled"], 0)
        self.assertEqual(settings["drop_bounds_enabled"], 1)
        self.assertEqual(settings["drop_threshold_type"], "percent")
        self.assertEqual(settings["drop_threshold_value"], 12.5)

        await database.set_drop_threshold(1, "amount", 300)
        settings = await database.get_notification_settings(1)
        self.assertEqual(settings["drop_threshold_type"], "amount")
        self.assertEqual(settings["drop_threshold_value"], 300)

        await database.set_drop_threshold(1, "amount", 0)
        settings = await database.get_notification_settings(1)
        self.assertIsNone(settings["drop_threshold_type"])
        self.assertIsNone(settings["drop_threshold_value"])

    async def test_price_drop_uses_previous_measurement(self):
        """Порог снижения считается от предыдущей цены, а не от минимума."""
        base_settings = {
            "standard_enabled": 1,
            "drop_bounds_enabled": 0,
            "drop_threshold_type": None,
            "drop_threshold_value": None,
        }
        self.assertTrue(_should_notify_price_drop(base_settings, 1000, 999))
        self.assertFalse(_should_notify_price_drop(base_settings, 1000, 1000))
        self.assertFalse(_should_notify_price_drop(base_settings, None, 900))

        percent_settings = {
            **base_settings,
            "drop_bounds_enabled": 1,
            "drop_threshold_type": "percent",
            "drop_threshold_value": 10,
        }
        self.assertTrue(_should_notify_price_drop(percent_settings, 1000, 900))
        self.assertFalse(_should_notify_price_drop(percent_settings, 1000, 901))

        amount_settings = {
            **base_settings,
            "drop_bounds_enabled": 1,
            "drop_threshold_type": "amount",
            "drop_threshold_value": 50,
        }
        self.assertTrue(_should_notify_price_drop(amount_settings, 1000, 950))
        self.assertFalse(_should_notify_price_drop(amount_settings, 1000, 951))

        disabled_settings = {**base_settings, "standard_enabled": 0}
        self.assertFalse(_should_notify_price_drop(disabled_settings, 1000, 500))

    async def test_target_price_notification_is_sent_once_per_crossing(self):
        """Достижение цели уведомляет один раз и повторяется после роста цены."""
        product_id = await database.add_product(
            name="Товар",
            price=1000,
            user_id=1,
            marketplace=Marketplace.OZON,
            url="https://www.ozon.ru/product/1/",
        )
        self.assertIsNotNone(product_id)
        self.assertFalse(await database.set_product_target_price(product_id, 2, 700))
        await database.set_product_target_price(product_id, 1, 800)

        self.assertIsNone(
            await database.claim_target_price_notification(product_id, 800, False)
        )
        self.assertEqual(
            await database.claim_target_price_notification(product_id, 800, True),
            800,
        )
        self.assertIsNone(
            await database.claim_target_price_notification(product_id, 700, True)
        )
        self.assertIsNone(
            await database.claim_target_price_notification(product_id, 900, True)
        )
        self.assertEqual(
            await database.claim_target_price_notification(product_id, 800, True),
            800,
        )

    async def test_target_button_is_visible_only_when_mode_is_enabled(self):
        """Кнопка цели отображается только при включенном режиме."""
        disabled = get_product_detail_keyboard(7)
        self.assertNotIn(
            "target_price:7",
            [button.callback_data for row in disabled.inline_keyboard for button in row],
        )

        enabled = get_product_detail_keyboard(7, target_price_enabled=True, target_price=500)
        enabled_buttons = [
            button
            for row in enabled.inline_keyboard
            for button in row
        ]
        self.assertIn("target_price:7", [button.callback_data for button in enabled_buttons])
        self.assertIn("🎯 Целевая цена: 500 ₽", [button.text for button in enabled_buttons])

    def test_settings_keyboards_have_required_modes(self):
        """Клавиатуры содержат три режима, чекбокс и два типа порога."""
        settings = {
            "standard_enabled": 1,
            "drop_bounds_enabled": 0,
            "target_price_enabled": 0,
            "drop_threshold_type": None,
            "drop_threshold_value": None,
        }
        menu = get_notification_settings_keyboard(settings)
        menu_texts = [button.text for row in menu.inline_keyboard for button in row]
        self.assertIn("✅ Стандарт", menu_texts)
        self.assertIn("❌ Границы снижения", menu_texts)
        self.assertIn("❌ Целевая цена", menu_texts)

        details = get_notification_mode_keyboard("drop_bounds", settings)
        detail_texts = [button.text for row in details.inline_keyboard for button in row]
        self.assertIn("Процент", detail_texts)
        self.assertIn("Сумма", detail_texts)
        self.assertIn("⬜ Включить", detail_texts)


if __name__ == "__main__":
    unittest.main()
