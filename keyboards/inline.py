from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_products_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру со списком товаров.

    Args:
        products: Список товаров с полями id, name, current_price

    Returns:
        InlineKeyboardMarkup с кнопками товаров
    """
    builder = InlineKeyboardBuilder()

    for product in products:
        button_text = f"{product['name'][:30]}... - {product['current_price']} ₽"
        builder.button(
            text=button_text,
            callback_data=f"product:{product['id']}"
        )

    builder.adjust(1)

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
