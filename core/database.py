import aiosqlite
from datetime import datetime
from pathlib import Path


DB_PATH = Path("bot_database.db")


async def setup_database():
    """Создает таблицы в базе данных, если они не существуют"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
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

        await db.commit()


async def add_product(url: str, name: str, price: int) -> int | None:
    """
    Добавляет новый товар и его первую цену в базу данных.

    Args:
        url: URL товара
        name: Название товара
        price: Цена товара (число без символа рубля)

    Returns:
        ID добавленного товара или None, если товар уже существует
    """
    async with aiosqlite.connect(DB_PATH) as db:
        date_now = datetime.now().isoformat()

        try:
            cursor = await db.execute(
                "INSERT INTO products (url, name, date_added) VALUES (?, ?, ?)",
                (url, name, date_now)
            )
            product_id = cursor.lastrowid

            await db.execute(
                "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
                (product_id, price, date_now)
            )

            await db.commit()
            return product_id

        except aiosqlite.IntegrityError:
            return None


async def get_all_products() -> list[dict]:
    """
    Получает список всех товаров с их текущими ценами.

    Returns:
        Список словарей с полями: id, url, name, current_price
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT
                p.id,
                p.url,
                p.name,
                ph.price as current_price
            FROM products p
            LEFT JOIN price_history ph ON p.id = ph.product_id
            WHERE ph.date_checked = (
                SELECT MAX(date_checked)
                FROM price_history
                WHERE product_id = p.id
            )
            ORDER BY p.date_added DESC
        """)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_product_details(product_id: int) -> dict | None:
    """
    Получает детальную информацию о товаре с минимальной и максимальной ценой.

    Args:
        product_id: ID товара

    Returns:
        Словарь с полями: id, url, name, min_price, max_price, current_price
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT
                p.id,
                p.url,
                p.name,
                MIN(ph.price) as min_price,
                MAX(ph.price) as max_price,
                (SELECT price FROM price_history
                 WHERE product_id = p.id
                 ORDER BY date_checked DESC LIMIT 1) as current_price
            FROM products p
            LEFT JOIN price_history ph ON p.id = ph.product_id
            WHERE p.id = ?
            GROUP BY p.id
        """, (product_id,))

        row = await cursor.fetchone()
        return dict(row) if row else None


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
