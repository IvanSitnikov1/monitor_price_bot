"""
Миграция: Добавление таблицы auth_requests для системы авторизации пользователей.

Дата: 22.05.2026
"""

import asyncio
import aiosqlite
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def migrate():
    """Добавляет таблицу auth_requests, если её нет"""
    if not DB_PATH.exists():
        print("База данных не найдена. Миграция не требуется.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, существует ли таблица
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_requests'"
        )
        exists = await cursor.fetchone()

        if exists:
            print("Таблица auth_requests уже существует. Миграция не требуется.")
            return

        # Создаём таблицу
        await db.execute("""
            CREATE TABLE auth_requests (
                user_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                username TEXT,
                request_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)

        await db.commit()
        print("✅ Таблица auth_requests успешно создана")


if __name__ == "__main__":
    asyncio.run(migrate())
