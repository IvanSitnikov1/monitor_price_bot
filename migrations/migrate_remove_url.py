"""
Миграция: удаление поля url из таблицы products.

Дата: 2026-06-03
"""
import asyncio
import aiosqlite
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def migrate():
    """Удаляет поле url из таблицы products"""
    async with aiosqlite.connect(DB_PATH) as db:
        print("Начало миграции: удаление поля url из таблицы products")

        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = OFF")

        # Создаём новую таблицу без поля url
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                in_cart INTEGER NOT NULL DEFAULT 1,
                date_added TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        # Копируем данные из старой таблицы в новую (без поля url)
        await db.execute("""
            INSERT INTO products_new (id, user_id, name, marketplace, in_cart, date_added)
            SELECT id, user_id, name, marketplace, in_cart, date_added
            FROM products
        """)

        # Удаляем старую таблицу
        await db.execute("DROP TABLE products")

        # Переименовываем новую таблицу
        await db.execute("ALTER TABLE products_new RENAME TO products")

        # Включаем обратно поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        await db.commit()

        print("✅ Миграция завершена успешно")
        print("Поле url удалено из таблицы products")


if __name__ == "__main__":
    asyncio.run(migrate())
