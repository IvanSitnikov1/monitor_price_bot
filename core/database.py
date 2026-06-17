import aiosqlite
from datetime import datetime
from pathlib import Path

from core.models import Marketplace


DB_PATH = Path("bot_database.db")


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

        # Миграция: добавляем поле корзины Wildberries для существующих таблиц
        try:
            await db.execute("ALTER TABLE users ADD COLUMN wildberries_cart_url TEXT")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                url TEXT,
                image_url TEXT,
                image_path TEXT,
                marketplace TEXT NOT NULL DEFAULT 'ozon',
                in_cart INTEGER NOT NULL DEFAULT 1,
                date_added TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        # Миграция: добавляем поле in_cart для существующих таблиц
        try:
            await db.execute("ALTER TABLE products ADD COLUMN in_cart INTEGER NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

        # Миграция: добавляем поле image_url для существующих таблиц
        try:
            await db.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

        # Миграция: добавляем поле image_path для существующих таблиц
        try:
            await db.execute("ALTER TABLE products ADD COLUMN image_path TEXT")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

        # Миграция: добавляем поле url для существующих таблиц
        try:
            await db.execute("ALTER TABLE products ADD COLUMN url TEXT")
            await db.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

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

        await db.commit()


async def add_product(
    name: str,
    price: int,
    user_id: int,
    marketplace: Marketplace,
    url: str | None = None,
    image_url: str | None = None,
    image_path: str | None = None
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
            INSERT INTO products (user_id, name, url, image_url, image_path, marketplace, date_added)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, url, image_url, image_path, marketplace.value, date_now)
        )
        product_id = cursor.lastrowid

        # Добавляем первую цену
        await db.execute(
            "INSERT INTO price_history (product_id, price, date_checked) VALUES (?, ?, ?)",
            (product_id, price, date_now)
        )

        await db.commit()
        return product_id


async def get_all_products(user_id: int, marketplace: str | None = None, in_cart_only: bool = True) -> list[dict]:
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
                p.date_added,
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
            WHERE p.id = ? AND p.user_id = ?
        """, (product_id, user_id))

        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_products_for_update() -> list[dict]:
    """
    Получает список всех товаров для фонового обновления цен.

    Returns:
        Список словарей с полями: id, user_id, name, marketplace
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT id, user_id, name, url, image_url, image_path, marketplace
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
            SELECT id, name, url, image_url, image_path, marketplace, in_cart
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
            INSERT OR REPLACE INTO auth_requests (user_id, phone, username, request_date, status, agreement_accepted, agreement_date)
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

        # Удаляем запрос на авторизацию
        await db.execute(
            "DELETE FROM auth_requests WHERE user_id = ?",
            (user_id,)
        )

        await db.commit()

        from loggers import logger
        logger.info(f"Данные авторизации пользователя {user_id} удалены из БД (отзыв согласия в {date_now})")


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
        date_now = datetime.now().isoformat()

        # Используем INSERT OR IGNORE чтобы не перезаписывать существующих пользователей
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, date_registered)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, date_now))

        await db.commit()


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
