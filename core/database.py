import aiosqlite
from datetime import datetime
from pathlib import Path

from core.models import Marketplace


DB_PATH = Path("bot_database.db")


async def setup_database():
    """Создает таблицы в базе данных, если они не существуют"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                date_added TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                date_checked TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_products (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                date_subscribed TEXT NOT NULL,
                PRIMARY KEY (user_id, product_id),
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
        """)

        await db.commit()


async def check_user_has_product(user_id: int, url: str) -> bool:
    """
    Проверяет, подписан ли пользователь на товар с данным URL.

    Args:
        user_id: ID пользователя Telegram
        url: URL товара

    Returns:
        True если пользователь уже подписан на этот товар, False иначе
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT 1
            FROM user_products up
            INNER JOIN products p ON up.product_id = p.id
            WHERE up.user_id = ? AND p.url = ?
        """, (user_id, url))

        return await cursor.fetchone() is not None


async def add_product(
    url: str,
    name: str,
    price: int,
    user_id: int,
    marketplace: Marketplace
) -> int | None:
    """
    Добавляет новый товар или подписывает пользователя на существующий.

    Args:
        url: URL товара
        name: Название товара
        price: Цена товара (число без символа рубля)
        user_id: ID пользователя Telegram
        marketplace: Маркетплейс товара

    Returns:
        ID товара или None, если пользователь уже подписан на этот товар
    """
    async with aiosqlite.connect(DB_PATH) as db:
        date_now = datetime.now().isoformat()

        # Проверяем, существует ли товар
        cursor = await db.execute(
            "SELECT id FROM products WHERE url = ?",
            (url,)
        )
        existing_product = await cursor.fetchone()

        if existing_product:
            product_id = existing_product[0]

            # Проверяем, подписан ли уже пользователь
            cursor = await db.execute(
                "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            )
            if await cursor.fetchone():
                return None

            # Подписываем пользователя на существующий товар
            await db.execute(
                "INSERT INTO user_products (user_id, product_id, date_subscribed) VALUES (?, ?, ?)",
                (user_id, product_id, date_now)
            )
            await db.commit()
            return product_id

        # Создаем новый товар
        cursor = await db.execute(
            "INSERT INTO products (url, name, marketplace, date_added) VALUES (?, ?, ?, ?)",
            (url, name, marketplace.value, date_now)
        )
        product_id = cursor.lastrowid

        # Добавляем первую цену
        await db.execute(
            "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
            (product_id, price, date_now)
        )

        # Подписываем пользователя
        await db.execute(
            "INSERT INTO user_products (user_id, product_id, date_subscribed) VALUES (?, ?, ?)",
            (user_id, product_id, date_now)
        )

        await db.commit()
        return product_id


async def get_all_products(user_id: int, marketplace: str | None = None) -> list[dict]:
    """
    Получает список всех товаров пользователя с их текущими ценами.

    Args:
        user_id: ID пользователя Telegram
        marketplace: Фильтр по маркетплейсу (опционально)

    Returns:
        Список словарей с полями: id, url, name, marketplace, current_price
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT
                p.id,
                p.url,
                p.name,
                p.marketplace,
                ph.price as current_price
            FROM products p
            INNER JOIN user_products up ON p.id = up.product_id
            LEFT JOIN price_history ph ON p.id = ph.product_id
            WHERE up.user_id = ?
            AND ph.date_checked = (
                SELECT MAX(date_checked)
                FROM price_history
                WHERE product_id = p.id
            )
        """

        params = [user_id]

        if marketplace:
            query += " AND p.marketplace = ?"
            params.append(marketplace)

        query += " ORDER BY up.date_subscribed DESC"

        cursor = await db.execute(query, params)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_product_details(product_id: int, user_id: int) -> dict | None:
    """
    Получает детальную информацию о товаре с минимальной и максимальной ценой.

    Args:
        product_id: ID товара
        user_id: ID пользователя Telegram

    Returns:
        Словарь с полями: id, url, name, date_added, min_price, min_price_date,
        max_price, max_price_date, current_price или None если товар не принадлежит пользователю
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Проверяем, что товар принадлежит пользователю
        cursor = await db.execute(
            "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
            (user_id, product_id)
        )
        if not await cursor.fetchone():
            return None

        cursor = await db.execute("""
            SELECT
                p.id,
                p.url,
                p.name,
                up.date_subscribed as date_added,
                (SELECT price FROM price_history
                 WHERE product_id = p.id
                 ORDER BY date_checked DESC LIMIT 1) as current_price,
                (SELECT MIN(price) FROM price_history WHERE product_id = p.id) as min_price,
                (SELECT date_checked FROM price_history
                 WHERE product_id = p.id AND price = (SELECT MIN(price) FROM price_history WHERE product_id = p.id)
                 LIMIT 1) as min_price_date,
                (SELECT MAX(price) FROM price_history WHERE product_id = p.id) as max_price,
                (SELECT date_checked FROM price_history
                 WHERE product_id = p.id AND price = (SELECT MAX(price) FROM price_history WHERE product_id = p.id)
                 LIMIT 1) as max_price_date
            FROM products p
            INNER JOIN user_products up ON p.id = up.product_id
            WHERE p.id = ? AND up.user_id = ?
        """, (product_id, user_id))

        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_products_for_update() -> list[dict]:
    """
    Получает список всех товаров для фонового обновления цен.

    Returns:
        Список словарей с полями: id, url, name, marketplace
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT id, url, name, marketplace
            FROM products
            ORDER BY id
        """)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_price_history(product_id: int) -> list[dict]:
    """
    Получает всю историю цен товара.

    Args:
        product_id: ID товара

    Returns:
        Список словарей с полями: price, date_checked
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT price, date_checked
            FROM price_history
            WHERE product_id = ?
            ORDER BY date_checked DESC
        """, (product_id,))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_product_price(product_id: int, price: int):
    """
    Добавляет новую цену в историю цен товара.

    Args:
        product_id: ID товара
        price: Новая цена
    """
    async with aiosqlite.connect(DB_PATH) as db:
        date_now = datetime.now().isoformat()

        await db.execute(
            "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
            (product_id, price, date_now)
        )

        await db.commit()


async def delete_product(product_id: int, user_id: int) -> bool:
    """
    Отписывает пользователя от товара. Если это последний пользователь, удаляет товар.

    Args:
        product_id: ID товара
        user_id: ID пользователя Telegram

    Returns:
        True если пользователь был отписан, False если товар не найден или не принадлежит пользователю
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Удаляем подписку пользователя
        cursor = await db.execute(
            "DELETE FROM user_products WHERE user_id = ? AND product_id = ?",
            (user_id, product_id)
        )

        if cursor.rowcount == 0:
            return False

        # Проверяем, остались ли другие подписчики
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_products WHERE product_id = ?",
            (product_id,)
        )
        count = (await cursor.fetchone())[0]

        # Если подписчиков нет, удаляем товар
        if count == 0:
            await db.execute("DELETE FROM products WHERE id = ?", (product_id,))

        await db.commit()
        return True


async def get_product_subscribers(product_id: int) -> list[int]:
    """
    Получает список ID пользователей, подписанных на товар.

    Args:
        product_id: ID товара

    Returns:
        Список ID пользователей Telegram
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM user_products WHERE product_id = ?",
            (product_id,)
        )

        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def get_min_price(product_id: int) -> int | None:
    """
    Получает минимальную цену товара за всю историю.

    Args:
        product_id: ID товара

    Returns:
        Минимальная цена или None если истории нет
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT MIN(price) FROM price_history WHERE product_id = ?",
            (product_id,)
        )

        result = await cursor.fetchone()
        return result[0] if result else None


async def get_all_user_ids() -> list[int]:
    """
    Получает список всех уникальных ID пользователей бота.

    Returns:
        Список ID пользователей Telegram
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT user_id FROM user_products ORDER BY user_id"
        )

        rows = await cursor.fetchall()
        return [row[0] for row in rows]
