from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.models import Marketplace


def get_marketplace_filter_keyboard(
    current_filter: str | None = None
) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с фильтрами по маркетплейсам.

    Args:
        current_filter: Текущий активный фильтр (маркетплейс или None для "Все")

    Returns:
        InlineKeyboardMarkup с кнопками фильтров
    """
    builder = InlineKeyboardBuilder()

    filters = [
        ("Все", "filter:all"),
        ("Ozon", f"filter:{Marketplace.OZON.value}"),
        ("Wildberries", f"filter:{Marketplace.WILDBERRIES.value}"),
        ("Avito", f"filter:{Marketplace.AVITO.value}"),
    ]

    for label, callback_data in filters:
        # Добавляем галочку к активному фильтру
        filter_value = callback_data.split(":")[1]
        is_active = (
            (current_filter is None and filter_value == "all") or
            (current_filter == filter_value)
        )
        text = f"✅ {label}" if is_active else label

        builder.button(text=text, callback_data=callback_data)

    builder.adjust(4)  # 4 кнопки в ряд

    return builder.as_markup()


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
        ("Ozon", f"filter:{Marketplace.OZON.value}"),
        ("Wildberries", f"filter:{Marketplace.WILDBERRIES.value}"),
        ("Avito", f"filter:{Marketplace.AVITO.value}"),
    ]

    for label, callback_data in filters:
        filter_value = callback_data.split(":")[1]
        is_active = (
            (marketplace_filter is None and filter_value == "all") or
            (marketplace_filter == filter_value)
        )
        text = f"✅ {label}" if is_active else label

        builder.button(text=text, callback_data=callback_data)

    builder.adjust(4)  # 4 фильтра в ряд

    # Добавляем товары
    for product in products:
        # Добавляем префикс 🗑 для товаров, удалённых из корзины
        prefix = "🗑 " if product.get('in_cart', 1) == 0 else ""
        button_text = f"{prefix}{product['name'][:30]}... - {product['current_price']} ₽"
        builder.button(
            text=button_text,
            callback_data=f"product:{product['id']}"
        )
        builder.adjust(4, 1)  # Фильтры в ряд, товары по одному

    return builder.as_markup()


def get_product_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для детального просмотра товара.

    Args:
        product_id: ID товара

    Returns:
        InlineKeyboardMarkup с кнопками "История цен" и "Удалить"
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📊 История цен",
        callback_data=f"history:{product_id}"
    )
    builder.button(
        text="🗑 Удалить",
        callback_data=f"delete:{product_id}"
    )

    builder.adjust(2)

    return builder.as_markup()
