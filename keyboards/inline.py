from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.models import Marketplace


def get_products_keyboard(
    products: list[dict],
    marketplace_filter: str | None = None
) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру со списком товаров и фильтрами.

    Args:
        products: Список товаров с полями id, name, current_price
        marketplace_filter: Текущий фильтр по маркетплейсу

    Returns:
        InlineKeyboardMarkup с фильтрами и кнопками товаров
    """
    builder = InlineKeyboardBuilder()

    # Добавляем фильтры
    filters = [
        ("Все", "filter:all"),
        ("Ozon", f"filter:{Marketplace.OZON}"),
        ("Wildberries", f"filter:{Marketplace.WILDBERRIES}"),
        ("Avito", f"filter:{Marketplace.AVITO}"),
    ]

    for label, callback_data in filters:
        filter_value = callback_data.split(":")[1]
        is_active = (
            (marketplace_filter is None and filter_value == "all") or
            (marketplace_filter == filter_value)
        )
        text = f"✅ {label}" if is_active else label

        builder.button(text=text, callback_data=callback_data)

    # Добавляем товары
    for product in products:
        # Добавляем префикс для товаров, которые сейчас не отслеживаются
        prefix = "❌ " if product.get('in_cart', 1) == 0 else ""
        button_text = f"{prefix}{product['name'][:30]}... - {product['current_price']} ₽"
        builder.button(text=button_text, callback_data=f"product:{product['id']}")
        builder.adjust(4, 1)  # Фильтры в ряд, товары по одному

    return builder.as_markup()


def get_product_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для детального просмотра товара."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 История цен", callback_data=f"history:{product_id}")
    builder.button(text="🗑 Удалить", callback_data=f"delete:{product_id}")
    return builder.as_markup()


def get_price_history_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для скачивания подробной истории цен."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📥 Скачать подробную историю",
        callback_data=f"history_download:{product_id}",
    )
    return builder.as_markup()


def get_auth_menu_keyboard() -> InlineKeyboardMarkup:
    """Создаёт меню выбора авторизации."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Авторизоваться в Ozon", callback_data="auth_ozon_start")
    builder.button(text="✅ Авторизоваться в Wildberries", callback_data="auth_wb_start")
    builder.button(text="🗑 Удалить мои данные", callback_data="auth_delete_data")
    builder.adjust(1)
    return builder.as_markup()


def get_subscription_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком пользователей для управления подписками."""
    builder = InlineKeyboardBuilder()

    for user in users:
        username = user.get("username")
        first_name = user.get("first_name")
        display_name = f"@{username}" if username else first_name or "без имени"
        paid_until = user.get("paid_until")
        status = "без подписки"

        if paid_until:
            status = f"до {paid_until[:10]}"

        builder.button(
            text=f"{user['user_id']} | {display_name} | {status}",
            callback_data=f"admin_sub_user:{user['user_id']}"
        )

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_user_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру карточки подписки пользователя."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Продлить подписку",
        callback_data=f"admin_sub_extend:{user_id}"
    )
    builder.button(
        text="К списку пользователей",
        callback_data="admin_sub_list"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_subscription_period_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора срока продления подписки."""
    builder = InlineKeyboardBuilder()
    periods = [
        ("1 месяц", 30),
        ("3 месяца", 90),
        ("6 месяцев", 180),
        ("12 месяцев", 365),
    ]

    for label, days in periods:
        builder.button(
            text=label,
            callback_data=f"admin_sub_period:{user_id}:{days}"
        )

    builder.button(
        text="Назад к карточке",
        callback_data=f"admin_sub_user:{user_id}"
    )
    builder.adjust(2, 2, 1)
    return builder.as_markup()
