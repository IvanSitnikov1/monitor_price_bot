import aiosqlite
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from core.models import Marketplace
from loggers import logger


DB_PATH = Path("bot_database.db")


async def _add_column_if_missing(db, table: str, column_definition: str):
    """Добавляет колонку и игнорирует только штатную ошибку дубля."""
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")
        await db.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            return

        logger.error(f"Ошибка миграции таблицы {table}: {e}")
        raise


async def setup_database():
    """Создает таблицы в базе данных, если они не существуют"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                ozon_cart_url TEXT,
                wildberries_cart_url TEXT,
                date_registered TEXT NOT NULL
            )
        """)

        await _add_column_if_missing(db, "users", "wildberries_cart_url TEXT")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                url TEXT,
                image_url TEXT,
                image_path TEXT,
                variant_text TEXT,
                cart_item_key TEXT,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                in_cart INTEGER NOT NULL DEFAULT 1,
                date_added TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        await _add_column_if_missing(db, "products", "in_cart INTEGER NOT NULL DEFAULT 1")
        await _add_column_if_missing(db, "products", "image_url TEXT")
        await _add_column_if_missing(db, "products", "image_path TEXT")
        await _add_column_if_missing(db, "products", "url TEXT")
        await _add_column_if_missing(db, "products", "variant_text TEXT")
        await _add_column_if_missing(db, "products", "cart_item_key TEXT")

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
            CREATE TABLE IF NOT EXISTS auth_requests (
                user_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                username TEXT,
                request_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agreement_accepted INTEGER NOT NULL DEFAULT 0,
                agreement_date TEXT
            )
        """)

        cursor = await db.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'marketplace_authorizations'
            """
        )
        marketplace_authorizations_exists = await cursor.fetchone() is not None

        await db.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_authorizations (
                user_id INTEGER NOT NULL,
                marketplace TEXT NOT NULL,
                authorized_at TEXT NOT NULL,
                PRIMARY KEY (user_id, marketplace),
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        if not marketplace_authorizations_exists:
            for marketplace in (Marketplace.OZON.value, Marketplace.WILDBERRIES.value):
                await db.execute(
                    """
                    INSERT OR IGNORE INTO marketplace_authorizations (
                        user_id, marketplace, authorized_at
                    )
                    SELECT ar.user_id, ?, COALESCE(ar.agreement_date, ar.request_date)
                    FROM auth_requests AS ar
                    INNER JOIN users AS u ON u.user_id = ar.user_id
                    WHERE ar.status = 'approved' AND ar.agreement_accepted = 1
                    """,
                    (marketplace,)
                )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'free',
                paid_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        date_now = datetime.now().isoformat()
        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (
                user_id, status, created_at, updated_at
            )
            SELECT user_id, 'free', ?, ?
            FROM users
        """, (date_now, date_now))

        await db.commit()


async def add_product(
    name: str,
    price: int,
    user_id: int,
    marketplace: Marketplace,
    url: str | None = None,
    image_url: str | None = None,
    image_path: str | None = None,
    variant_text: str | None = None,
    cart_item_key: str | None = None
) -> int | None:
    """
    Добавляет новый товар для пользователя.

    Args:
        name: Название товара
        price: Цена товара (число без символа рубля)
        user_id: ID пользователя Telegram
        marketplace: Маркетплейс товара
        url: Ссылка на страницу товара
        image_url: Ссылка на изображение товара
        image_path: Локальный путь к изображению товара
        variant_text: Характеристики варианта товара
        cart_item_key: Стабильный ключ позиции корзины

    Returns:
        ID добавленного товара
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        date_now = datetime.now().isoformat()

        # Создаем новый товар
        cursor = await db.execute(
            """
            INSERT INTO products (
                user_id, name, url, image_url, image_path, variant_text, cart_item_key,
                marketplace, date_added
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name,
                url,
                image_url,
                image_path,
                variant_text,
                cart_item_key,
                marketplace.value,
                date_now
            )
        )
        product_id = cursor.lastrowid

        # Добавляем первую цену
        await db.execute(
            "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
            (product_id, price, date_now)
        )

        await db.commit()
        return product_id


async def get_all_products(
    user_id: int,
    marketplace: str | None = None,
    in_cart_only: bool = True
) -> list[dict]:
    """
    Получает список всех товаров пользователя с их текущими ценами.

    Args:
        user_id: ID пользователя Telegram
        marketplace: Фильтр по маркетплейсу (опционально)
        in_cart_only: Если True, возвращает только товары в корзине (по умолчанию)

    Returns:
        Список словарей с полями: id, name, marketplace, current_price, in_cart
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT
                p.id,
                p.name,
                p.url,
                p.image_url,
                p.image_path,
                p.variant_text,
                p.cart_item_key,
                p.marketplace,
                p.in_cart,
                ph.price as current_price
            FROM products p
            LEFT JOIN price_history ph ON p.id = ph.product_id
            WHERE p.user_id = ?
            AND ph.date_checked = (
                SELECT MAX(date_checked)
                FROM price_history
                WHERE product_id = p.id
            )
        """

        params = [user_id]

        if in_cart_only:
            query += " AND p.in_cart = 1"

        if marketplace:
            query += " AND p.marketplace = ?"
            params.append(marketplace)

        query += " ORDER BY p.date_added DESC"

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
        Словарь с полями: id, name, date_added, min_price, min_price_date,
        max_price, max_price_date, current_price или None если товар не принадлежит пользователю
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT
                p.id,
                p.name,
                p.url,
                p.image_url,
                p.image_path,
                p.variant_text,
                p.cart_item_key,
                p.in_cart,
                p.date_added,
                (SELECT price FROM price_history
                 WHERE product_id = p.id
                 ORDER BY date_checked DESC LIMIT 1) as current_price,
                (SELECT MIN(price) FROM price_history WHERE product_id = p.id) as min_price,
                (SELECT date_checked
                 FROM price_history
                 WHERE product_id = p.id
                 AND price = (
                    SELECT MIN(price)
                    FROM price_history
                    WHERE product_id = p.id
                 )
                 LIMIT 1) as min_price_date,
                (SELECT MAX(price) FROM price_history WHERE product_id = p.id) as max_price,
                (SELECT date_checked
                 FROM price_history
                 WHERE product_id = p.id
                 AND price = (
                    SELECT MAX(price)
                    FROM price_history
                    WHERE product_id = p.id
                 )
                 LIMIT 1) as max_price_date
            FROM products p
            WHERE p.id = ? AND p.user_id = ?
        """, (product_id, user_id))

        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_products_for_update(marketplace: str | None = None) -> list[dict]:
    """
    Получает список всех товаров для фонового обновления цен.

    Args:
        marketplace: Фильтр по маркетплейсу

    Returns:
        Список словарей с полями: id, user_id, name, marketplace
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT id, user_id, name, url, image_url, image_path, variant_text,
                   cart_item_key, marketplace
            FROM products
            WHERE in_cart = 1 AND url IS NOT NULL
        """
        params = []

        if marketplace:
            query += " AND marketplace = ?"
            params.append(marketplace)

        query += " ORDER BY id"
        cursor = await db.execute(query, params)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_product_by_url(
    user_id: int,
    url: str,
    marketplace: Marketplace | str
) -> dict | None:
    """Получает товар пользователя по URL и маркетплейсу."""
    marketplace_value = (
        marketplace.value if isinstance(marketplace, Marketplace) else marketplace
    )

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, name, url, image_url, image_path, variant_text, cart_item_key,
                   marketplace, in_cart
            FROM products
            WHERE user_id = ? AND url = ? AND marketplace = ?
            """,
            (user_id, url, marketplace_value)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_price_history(product_id: int, user_id: int) -> list[dict]:
    """
    Получает полную историю проверок цены товара пользователя.

    Args:
        product_id: ID товара
        user_id: ID пользователя Telegram

    Returns:
        Список словарей с полями: price, date_checked
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT ph.price, ph.date_checked
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE ph.product_id = ? AND p.user_id = ?
            ORDER BY ph.date_checked DESC, ph.id DESC
        """, (product_id, user_id))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_price_changes(product_id: int, user_id: int) -> list[dict]:
    """Получает только фактические изменения цены товара пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            WITH ordered_history AS (
                SELECT
                    ph.id,
                    ph.price,
                    ph.date_checked,
                    LAG(ph.price) OVER (
                        ORDER BY ph.date_checked ASC, ph.id ASC
                    ) AS previous_price
                FROM price_history ph
                JOIN products p ON p.id = ph.product_id
                WHERE ph.product_id = ? AND p.user_id = ?
            )
            SELECT price, date_checked
            FROM ordered_history
            WHERE previous_price IS NULL OR price != previous_price
            ORDER BY date_checked DESC, id DESC
        """, (product_id, user_id))

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
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        date_now = datetime.now().isoformat()

        await db.execute(
            "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
            (product_id, price, date_now)
        )

        await db.commit()


async def update_product_url(product_id: int, url: str | None):
    """
    Обновляет ссылку на страницу товара.

    Args:
        product_id: ID товара
        url: Ссылка на страницу товара
    """
    if not url:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE products SET url = ? WHERE id = ?",
            (url, product_id)
        )

        await db.commit()


async def update_product_image_url(product_id: int, image_url: str | None):
    """
    Обновляет ссылку на изображение товара.

    Args:
        product_id: ID товара
        image_url: Ссылка на изображение товара
    """
    if not image_url:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE products SET image_url = ? WHERE id = ?",
            (image_url, product_id)
        )

        await db.commit()


async def update_product_image_path(product_id: int, image_path: str | None):
    """
    Обновляет локальный путь к изображению товара.

    Args:
        product_id: ID товара
        image_path: Локальный путь к изображению товара
    """
    if not image_path:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE products SET image_path = ? WHERE id = ?",
            (image_path, product_id)
        )

        await db.commit()


async def update_product_cart_identity(
    product_id: int,
    variant_text: str | None,
    cart_item_key: str | None
):
    """
    Обновляет характеристики варианта и ключ позиции корзины.

    Args:
        product_id: ID товара
        variant_text: Характеристики варианта товара
        cart_item_key: Стабильный ключ позиции корзины
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE products SET variant_text = ?, cart_item_key = ? WHERE id = ?",
            (variant_text, cart_item_key, product_id)
        )

        await db.commit()


async def mark_product_not_in_cart(product_id: int) -> bool:
    """
    Помечает товар как удалённый из корзины (но сохраняет в БД с историей).

    Args:
        product_id: ID товара

    Returns:
        True если товар был обновлён, False если товар не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE products SET in_cart = 0 WHERE id = ?",
            (product_id,)
        )

        if cursor.rowcount == 0:
            return False

        await db.commit()
        return True


async def mark_product_in_cart(product_id: int) -> bool:
    """
    Помечает товар как находящийся в корзине.

    Args:
        product_id: ID товара

    Returns:
        True если товар был обновлён, False если товар не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE products SET in_cart = 1 WHERE id = ?",
            (product_id,)
        )

        if cursor.rowcount == 0:
            return False

        await db.commit()
        return True


async def get_product_by_name(user_id: int, name: str) -> dict | None:
    """
    Получает товар пользователя по названию.

    Args:
        user_id: ID пользователя Telegram
        name: Название товара

    Returns:
        Словарь с полями: id, name, marketplace, in_cart или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT id, name, url, image_url, image_path, variant_text, cart_item_key,
                   marketplace, in_cart
            FROM products
            WHERE user_id = ? AND name = ?
            """,
            (user_id, name)
        )

        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_product(product_id: int, user_id: int) -> bool:
    """
    Удаляет товар пользователя.

    Args:
        product_id: ID товара
        user_id: ID пользователя Telegram

    Returns:
        True если товар был удалён, False если товар не найден или не принадлежит пользователю
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        # Удаляем товар (CASCADE удалит связанную историю цен)
        cursor = await db.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id)
        )

        if cursor.rowcount == 0:
            return False

        await db.commit()
        return True


async def get_product_owner(product_id: int) -> int | None:
    """
    Получает ID владельца товара.

    Args:
        product_id: ID товара

    Returns:
        ID пользователя Telegram или None если товар не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM products WHERE id = ?",
            (product_id,)
        )

        result = await cursor.fetchone()
        return result[0] if result else None


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


async def get_price_snapshot(product_id: int) -> dict[str, int | None]:
    """
    Получает последнюю, минимальную и максимальную цену товара.

    Args:
        product_id: ID товара

    Returns:
        Словарь с полями previous_price, min_price, max_price
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT
                (SELECT price FROM price_history
                 WHERE product_id = ?
                 ORDER BY date_checked DESC, id DESC
                 LIMIT 1) as previous_price,
                MIN(price) as min_price,
                MAX(price) as max_price
            FROM price_history
            WHERE product_id = ?
            """,
            (product_id, product_id)
        )

        row = await cursor.fetchone()
        return dict(row) if row else {
            "previous_price": None,
            "min_price": None,
            "max_price": None,
        }


async def get_all_user_ids() -> list[int]:
    """
    Получает список всех уникальных ID пользователей бота.

    Returns:
        Список ID пользователей Telegram
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT user_id FROM products ORDER BY user_id"
        )

        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def save_auth_request(user_id: int, phone: str, username: str | None):
    """
    Сохраняет запрос на авторизацию от пользователя.

    Args:
        user_id: ID пользователя Telegram
        phone: Номер телефона
        username: Username пользователя (опционально)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        date_now = datetime.now().isoformat()

        await db.execute("""
            INSERT OR REPLACE INTO auth_requests (
                user_id, phone, username, request_date, status, agreement_accepted,
                agreement_date
            )
            VALUES (?, ?, ?, ?, 'pending', 1, ?)
        """, (user_id, phone, username, date_now, date_now))

        await db.commit()


async def get_auth_request(user_id: int) -> dict | None:
    """
    Получает запрос на авторизацию по ID пользователя.

    Args:
        user_id: ID пользователя Telegram

    Returns:
        Словарь с полями: user_id, phone, username, request_date, status или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM auth_requests WHERE user_id = ?",
            (user_id,)
        )

        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_auth_request_status(user_id: int, status: str):
    """
    Обновляет статус запроса на авторизацию.

    Args:
        user_id: ID пользователя Telegram
        status: Новый статус (pending | approved | rejected)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        await db.execute(
            "UPDATE auth_requests SET status = ? WHERE user_id = ?",
            (status, user_id)
        )

        await db.commit()


async def save_marketplace_authorization(
    user_id: int,
    marketplace: Marketplace | str
) -> None:
    """Сохраняет факт успешной авторизации пользователя в маркетплейсе."""
    marketplace_value = (
        marketplace.value if isinstance(marketplace, Marketplace) else marketplace
    )
    if marketplace_value not in (
        Marketplace.OZON.value,
        Marketplace.WILDBERRIES.value,
    ):
        raise ValueError(f"Неподдерживаемый маркетплейс авторизации: {marketplace_value}")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
            INSERT INTO marketplace_authorizations (
                user_id, marketplace, authorized_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, marketplace) DO UPDATE SET
                authorized_at = excluded.authorized_at
            """,
            (user_id, marketplace_value, datetime.now().isoformat())
        )
        await db.commit()


async def has_marketplace_authorization(
    user_id: int,
    marketplace: Marketplace | str
) -> bool:
    """Проверяет, подтверждалась ли авторизация пользователя в маркетплейсе."""
    marketplace_value = (
        marketplace.value if isinstance(marketplace, Marketplace) else marketplace
    )
    if marketplace_value not in (
        Marketplace.OZON.value,
        Marketplace.WILDBERRIES.value,
    ):
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT 1
            FROM marketplace_authorizations
            WHERE user_id = ? AND marketplace = ?
            """,
            (user_id, marketplace_value)
        )
        return await cursor.fetchone() is not None


async def delete_user_auth_data(user_id: int):
    """
    Удаляет все данные авторизации пользователя (отзыв согласия).

    Args:
        user_id: ID пользователя Telegram
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку внешних ключей
        await db.execute("PRAGMA foreign_keys = ON")

        date_now = datetime.now().isoformat()

        # Удаляем подтверждения авторизации по маркетплейсам
        await db.execute(
            "DELETE FROM marketplace_authorizations WHERE user_id = ?",
            (user_id,)
        )

        # Удаляем запрос на авторизацию
        await db.execute(
            "DELETE FROM auth_requests WHERE user_id = ?",
            (user_id,)
        )

        await db.commit()

        from loggers import logger
        logger.info(
            f"Данные авторизации пользователя {user_id} удалены из БД "
            f"(отзыв согласия в {date_now})"
        )


async def get_pending_auth_requests() -> list[dict]:
    """
    Получает список всех ожидающих запросов на авторизацию.

    Returns:
        Список словарей с полями: user_id, phone, username, request_date, status
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM auth_requests WHERE status = 'pending' ORDER BY request_date"
        )

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def register_user(user_id: int, username: str | None, first_name: str | None):
    """
    Регистрирует нового пользователя в системе.

    Args:
        user_id: ID пользователя Telegram
        username: Username пользователя (опционально)
        first_name: Имя пользователя (опционально)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        date_now = datetime.now().isoformat()

        # Используем INSERT OR IGNORE чтобы не перезаписывать существующих пользователей
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, date_registered)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, date_now))

        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (
                user_id, status, created_at, updated_at
            )
            VALUES (?, 'free', ?, ?)
        """, (user_id, date_now, date_now))

        await db.commit()


async def get_users_with_subscriptions() -> list[dict]:
    """
    Получает список пользователей с краткой информацией о подписке.

    Returns:
        Список словарей с данными пользователя и подписки
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.date_registered,
                s.status,
                s.paid_until
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.user_id
            ORDER BY u.date_registered DESC
        """)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_user_subscription_details(user_id: int) -> dict | None:
    """
    Получает карточку пользователя с состоянием подписки.

    Args:
        user_id: ID пользователя Telegram

    Returns:
        Словарь с данными пользователя и подписки или None
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")

        cursor = await db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        )
        if not await cursor.fetchone():
            return None

        date_now = datetime.now().isoformat()
        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (
                user_id, status, created_at, updated_at
            )
            VALUES (?, 'free', ?, ?)
        """, (user_id, date_now, date_now))
        await db.commit()

        cursor = await db.execute("""
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.date_registered,
                s.status,
                s.paid_until,
                s.created_at,
                s.updated_at
            FROM users u
            JOIN subscriptions s ON s.user_id = u.user_id
            WHERE u.user_id = ?
        """, (user_id,))

        row = await cursor.fetchone()
        return dict(row) if row else None


async def extend_user_subscription(
    user_id: int,
    period_days: int
) -> dict | None:
    """
    Продлевает подписку пользователя.

    Args:
        user_id: ID пользователя Telegram
        period_days: Срок продления в днях

    Returns:
        Обновленная карточка подписки или None если пользователь не найден
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")

        cursor = await db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        )
        if not await cursor.fetchone():
            return None

        date_now = datetime.now()
        date_now_text = date_now.isoformat()

        await db.execute("""
            INSERT OR IGNORE INTO subscriptions (
                user_id, status, created_at, updated_at
            )
            VALUES (?, 'free', ?, ?)
        """, (user_id, date_now_text, date_now_text))

        cursor = await db.execute(
            "SELECT paid_until FROM subscriptions WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        paid_until = row["paid_until"] if row else None

        start_date = date_now
        if paid_until:
            try:
                paid_until_date = datetime.fromisoformat(paid_until)
                if paid_until_date > date_now:
                    start_date = paid_until_date
            except ValueError:
                pass

        paid_until_after = start_date + timedelta(days=period_days)
        paid_until_after_text = paid_until_after.isoformat()

        await db.execute("""
            UPDATE subscriptions
            SET status = 'active',
                paid_until = ?,
                updated_at = ?
            WHERE user_id = ?
        """, (paid_until_after_text, date_now_text, user_id))

        await db.commit()

    return await get_user_subscription_details(user_id)


async def has_active_access(user_id: int) -> bool:
    """
    Проверяет доступ пользователя к функциям бота.

    Args:
        user_id: ID пользователя Telegram

    Returns:
        True если доступ разрешен
    """
    from config import config

    subscription = await get_user_subscription_details(user_id)
    if not subscription:
        return True

    if not config.SUBSCRIPTIONS_ENFORCED:
        return True

    date_now = datetime.now()
    paid_until = subscription.get("paid_until")
    if not paid_until:
        return False

    try:
        return datetime.fromisoformat(paid_until) > date_now
    except ValueError:
        return False


def _get_cart_column(marketplace: Marketplace | str) -> str:
    """Возвращает имя колонки с корзиной для маркетплейса."""
    marketplace_value = (
        marketplace.value if isinstance(marketplace, Marketplace) else marketplace
    )

    match marketplace_value:
        case Marketplace.OZON.value:
            return "ozon_cart_url"
        case Marketplace.WILDBERRIES.value:
            return "wildberries_cart_url"
        case _:
            raise ValueError(f"Неподдерживаемый маркетплейс корзины: {marketplace_value}")


async def save_user_cart(
    user_id: int,
    cart_url: str,
    marketplace: Marketplace | str = Marketplace.OZON
):
    """
    Сохраняет или обновляет ссылку на корзину пользователя.

    Args:
        user_id: ID пользователя Telegram
        cart_url: URL корзины
        marketplace: Маркетплейс корзины
    """
    cart_column = _get_cart_column(marketplace)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE users SET {cart_column} = ? WHERE user_id = ?",
            (cart_url, user_id)
        )

        await db.commit()


async def get_user_cart(
    user_id: int,
    marketplace: Marketplace | str = Marketplace.OZON
) -> str | None:
    """
    Получает ссылку на корзину пользователя.

    Args:
        user_id: ID пользователя Telegram
        marketplace: Маркетплейс корзины

    Returns:
        URL корзины или None если корзина не сохранена
    """
    cart_column = _get_cart_column(marketplace)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"SELECT {cart_column} FROM users WHERE user_id = ?",
            (user_id,)
        )

        result = await cursor.fetchone()
        return result[0] if result and result[0] else None


async def get_all_user_carts() -> list[dict]:
    """
    Получает список всех пользователей с их корзинами.

    Returns:
        Список словарей с полями: user_id, cart_url, marketplace
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT user_id, ozon_cart_url, wildberries_cart_url
            FROM users
            WHERE ozon_cart_url IS NOT NULL OR wildberries_cart_url IS NOT NULL
            ORDER BY user_id
        """)

        rows = await cursor.fetchall()
        carts = []
        for row in rows:
            if row["ozon_cart_url"]:
                carts.append({
                    "user_id": row["user_id"],
                    "cart_url": row["ozon_cart_url"],
                    "marketplace": Marketplace.OZON.value
                })

            if row["wildberries_cart_url"]:
                carts.append({
                    "user_id": row["user_id"],
                    "cart_url": row["wildberries_cart_url"],
                    "marketplace": Marketplace.WILDBERRIES.value
                })

        return carts


async def broadcast_to_users(bot, message: str):
    """
    Отправляет сообщение всем пользователям из базы данных.

    Args:
        bot: Экземпляр бота
        message: Текст сообщения для рассылки

    Returns:
        Количество успешно отправленных сообщений
    """
    user_ids = await get_all_user_ids()
    success_count = 0

    for user_id in user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=message)
            success_count += 1
        except Exception as e:
            print(f"Ошибка при отправке пользователю {user_id}: {e}")

    return success_count
