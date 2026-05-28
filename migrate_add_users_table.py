"""
Миграция: Создание таблицы users и перенос данных из user_carts

Изменения:
1. Создаёт таблицу users (user_id, username, first_name, cart_url, date_registered)
2. Переносит данные из user_carts в users
3. Переносит уникальных пользователей из products в users
4. Удаляет таблицу user_carts
5. Добавляет внешний ключ в products на users
"""

import aiosqlite
import asyncio
from datetime import datetime
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def migrate():
    print("Начало миграции: добавление таблицы users")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = OFF")

        # 1. Создаём таблицу users
        print("1. Создание таблицы users...")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                cart_url TEXT,
                date_registered TEXT NOT NULL
            )
        """)

        # 2. Переносим данные из user_carts (если таблица существует)
        print("2. Перенос данных из user_carts...")
        try:
            cursor = await db.execute("SELECT user_id, cart_url, date_added FROM user_carts")
            user_carts = await cursor.fetchall()

            for user_id, cart_url, date_added in user_carts:
                await db.execute("""
                    INSERT OR IGNORE INTO users (user_id, cart_url, date_registered)
                    VALUES (?, ?, ?)
                """, (user_id, cart_url, date_added))

            print(f"   Перенесено записей из user_carts: {len(user_carts)}")
        except Exception as e:
            print(f"   Таблица user_carts не найдена или пуста: {e}")

        # 3. Добавляем пользователей из products (если их ещё нет в users)
        print("3. Добавление пользователей из products...")
        cursor = await db.execute("SELECT DISTINCT user_id FROM products")
        product_users = await cursor.fetchall()

        date_now = datetime.now().isoformat()
        added_count = 0

        for (user_id,) in product_users:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO users (user_id, date_registered) VALUES (?, ?)",
                (user_id, date_now)
            )
            if cursor.rowcount > 0:
                added_count += 1

        print(f"   Добавлено новых пользователей из products: {added_count}")

        # 4. Удаляем таблицу user_carts
        print("4. Удаление таблицы user_carts...")
        try:
            await db.execute("DROP TABLE IF EXISTS user_carts")
            print("   Таблица user_carts удалена")
        except Exception as e:
            print(f"   Ошибка при удалении user_carts: {e}")

        # 5. Пересоздаём таблицу products с внешним ключом на users
        print("5. Обновление таблицы products...")

        # Создаём временную таблицу
        await db.execute("""
            CREATE TABLE products_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                date_added TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        # Копируем данные
        await db.execute("""
            INSERT INTO products_new (id, user_id, url, name, marketplace, date_added)
            SELECT id, user_id, url, name, marketplace, date_added FROM products
        """)

        # Удаляем старую таблицу и переименовываем новую
        await db.execute("DROP TABLE products")
        await db.execute("ALTER TABLE products_new RENAME TO products")

        print("   Таблица products обновлена с внешним ключом")

        await db.commit()
        await db.execute("PRAGMA foreign_keys = ON")

        print("Миграция завершена успешно!")

        # Выводим статистику
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE cart_url IS NOT NULL")
        carts_count = (await cursor.fetchone())[0]

        print(f"\nСтатистика:")
        print(f"  Всего пользователей: {users_count}")
        print(f"  Пользователей с корзинами: {carts_count}")


if __name__ == "__main__":
    asyncio.run(migrate())
