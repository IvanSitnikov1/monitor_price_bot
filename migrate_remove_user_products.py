"""
Миграция: Изменение архитектуры БД - каждый товар принадлежит только одному пользователю.

Дата: 22.05.2026

Изменения:
1. Добавляет поле user_id в таблицу products
2. Переносит данные из user_products в products
3. Удаляет таблицу user_products
4. Удаляет constraint UNIQUE на url в products
"""

import asyncio
import aiosqlite
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def migrate():
    """Выполняет миграцию БД"""
    if not DB_PATH.exists():
        print("База данных не найдена. Миграция не требуется.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, существует ли таблица user_products
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_products'"
        )
        if not await cursor.fetchone():
            print("Таблица user_products не найдена. Миграция уже выполнена или не требуется.")
            return

        print("Начало миграции...")

        # Шаг 1: Создаём новую таблицу products_new с user_id
        await db.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                date_added TEXT NOT NULL
            )
        """)
        print("✅ Создана новая таблица products_new")

        # Шаг 2: Копируем данные из products и user_products
        # Для каждой связки user-product создаём отдельную запись
        await db.execute("""
            INSERT INTO products_new (user_id, url, name, marketplace, date_added)
            SELECT
                up.user_id,
                p.url,
                p.name,
                p.marketplace,
                p.date_added
            FROM products p
            INNER JOIN user_products up ON p.id = up.product_id
        """)
        print("✅ Данные скопированы в products_new")

        # Шаг 3: Получаем маппинг старых ID на новые
        # Для обновления price_history нужно знать соответствие
        cursor = await db.execute("""
            SELECT
                p.id as old_id,
                pn.id as new_id,
                up.user_id
            FROM products p
            INNER JOIN user_products up ON p.id = up.product_id
            INNER JOIN products_new pn ON pn.url = p.url AND pn.user_id = up.user_id
        """)
        id_mapping = await cursor.fetchall()
        print(f"✅ Создан маппинг ID: {len(id_mapping)} записей")

        # Шаг 4: Создаём новую таблицу price_history_new
        await db.execute("""
            CREATE TABLE price_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                date_checked TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products_new (id) ON DELETE CASCADE
            )
        """)
        print("✅ Создана новая таблица price_history_new")

        # Шаг 5: Копируем историю цен для каждого нового товара
        for old_id, new_id, user_id in id_mapping:
            await db.execute("""
                INSERT INTO price_history_new (product_id, price, date_checked)
                SELECT ?, price, date_checked
                FROM price_history
                WHERE product_id = ?
            """, (new_id, old_id))
        print("✅ История цен скопирована в price_history_new")

        # Шаг 6: Удаляем старые таблицы
        await db.execute("DROP TABLE price_history")
        await db.execute("DROP TABLE user_products")
        await db.execute("DROP TABLE products")
        print("✅ Старые таблицы удалены")

        # Шаг 7: Переименовываем новые таблицы
        await db.execute("ALTER TABLE products_new RENAME TO products")
        await db.execute("ALTER TABLE price_history_new RENAME TO price_history")
        print("✅ Новые таблицы переименованы")

        await db.commit()
        print("\n🎉 Миграция успешно завершена!")
        print(f"Создано товаров: {len(id_mapping)}")


if __name__ == "__main__":
    asyncio.run(migrate())
