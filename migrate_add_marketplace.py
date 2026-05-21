"""
Миграция: добавление поля marketplace в таблицу products.

Запуск: python migrate_add_marketplace.py
"""
import asyncio
import aiosqlite
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def migrate():
    """Добавляет поле marketplace в таблицу products"""
    if not DB_PATH.exists():
        print("База данных не найдена. Миграция не требуется.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, существует ли уже поле marketplace
        cursor = await db.execute("PRAGMA table_info(products)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        if "marketplace" in column_names:
            print("✅ Поле 'marketplace' уже существует. Миграция не требуется.")
            return

        print("Начинаем миграцию...")

        # Добавляем поле marketplace со значением по умолчанию 'ozon'
        await db.execute("""
            ALTER TABLE products
            ADD COLUMN marketplace TEXT NOT NULL DEFAULT 'ozon'
        """)

        await db.commit()
        print("✅ Миграция завершена успешно!")
        print("   Поле 'marketplace' добавлено в таблицу 'products'")
        print("   Все существующие записи получили значение 'ozon'")


if __name__ == "__main__":
    asyncio.run(migrate())
