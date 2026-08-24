import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from core import database
from core.models import Marketplace
from handlers.callbacks import _format_cart_history
from keyboards.inline import get_cart_history_keyboard


class CartHistoryTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет хранение истории ссылок общих корзин."""

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

    async def test_history_is_limited_per_marketplace(self):
        """Для каждого маркетплейса сохраняются только пять последних ссылок."""
        for index in range(6):
            await database.save_user_cart(
                1,
                f"https://www.ozon.ru/cart?share={index}",
                Marketplace.OZON,
            )

        for index in range(2):
            await database.save_user_cart(
                1,
                f"https://www.wildberries.ru/basket?shareId={index}",
                Marketplace.WILDBERRIES,
            )

        history = await database.get_user_cart_history(1)
        ozon_urls = {
            entry["cart_url"] for entry in history if entry["marketplace"] == "ozon"
        }
        wildberries_urls = {
            entry["cart_url"]
            for entry in history
            if entry["marketplace"] == "wildberries"
        }

        self.assertEqual(len(history), 7)
        self.assertEqual(
            ozon_urls,
            {
                f"https://www.ozon.ru/cart?share={index}"
                for index in range(1, 6)
            },
        )
        self.assertEqual(
            wildberries_urls,
            {
                f"https://www.wildberries.ru/basket?shareId={index}"
                for index in range(2)
            },
        )

    async def test_existing_current_carts_are_migrated_once(self):
        """Текущие ссылки старой базы добавляются в историю без дублей."""
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                UPDATE users
                SET ozon_cart_url = ?, wildberries_cart_url = ?
                WHERE user_id = ?
                """,
                (
                    "https://www.ozon.ru/cart?share=current",
                    "https://www.wildberries.ru/basket?shareId=current",
                    1,
                ),
            )
            await db.commit()

        await database.setup_database()
        await database.setup_database()

        history = await database.get_user_cart_history(1)
        self.assertEqual(len(history), 2)
        self.assertEqual(
            {entry["cart_url"] for entry in history},
            {
                "https://www.ozon.ru/cart?share=current",
                "https://www.wildberries.ru/basket?shareId=current",
            },
        )

    async def test_repeated_link_is_moved_without_duplicate(self):
        """Повторная ссылка становится новой записью без дублирования."""
        old_url = "https://www.ozon.ru/cart?share=old"
        new_url = "https://www.ozon.ru/cart?share=new"

        await database.save_user_cart(1, old_url, Marketplace.OZON)
        await asyncio.sleep(0.001)
        await database.save_user_cart(1, new_url, Marketplace.OZON)
        await asyncio.sleep(0.001)
        await database.save_user_cart(1, old_url, Marketplace.OZON)

        history = await database.get_user_cart_history(1)
        self.assertEqual([entry["cart_url"] for entry in history], [old_url, new_url])

    async def test_history_is_private(self):
        """История пользователя не содержит ссылки другого пользователя."""
        await database.register_user(2, "other", "Другой пользователь")
        await database.save_user_cart(
            2,
            "https://www.ozon.ru/cart?share=other",
            Marketplace.OZON,
        )

        history = await database.get_user_cart_history(1)
        self.assertEqual(history, [])

    def test_history_keyboard_and_html_links(self):
        """Кнопка и ссылки истории имеют ожидаемый формат."""
        keyboard = get_cart_history_keyboard()
        button = keyboard.inline_keyboard[0][0]
        self.assertEqual(button.text, "История")
        self.assertEqual(button.callback_data, "cart_history")

        messages = _format_cart_history([
            {
                "marketplace": "ozon",
                "cart_url": "https://www.ozon.ru/cart?share=one&x=2",
            }
        ])
        self.assertIn(
            '<a href="https://www.ozon.ru/cart?share=one&amp;x=2">',
            messages[0],
        )


if __name__ == "__main__":
    unittest.main()
