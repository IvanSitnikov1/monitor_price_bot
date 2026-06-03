from datetime import datetime

from aiogram import F, types, Router

from keyboards.inline import get_products_keyboard, get_product_detail_keyboard
from core.database import (
    get_all_products,
    get_product_details,
    delete_product,
    get_price_history,
)
from loggers import logger


callbacks_router = Router()


# Обработчик нажатия на товар в списке
@callbacks_router.callback_query(F.data.startswith("product:"))
async def show_product_details(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} запросил детали товара ID: {product_id}")

    product = await get_product_details(product_id, user.id)

    if not product:
        logger.warning(f"Товар с ID {product_id} не найден в БД для пользователя {user.id}")
        await callback.answer("Товар не найден", show_alert=True)
        return

    # Форматируем даты
    date_added = datetime.fromisoformat(product['date_added']).strftime('%d.%m.%Y')
    min_price_date = datetime.fromisoformat(product['min_price_date']).strftime('%d.%m.%Y %H:%M')
    max_price_date = datetime.fromisoformat(product['max_price_date']).strftime('%d.%m.%Y %H:%M')

    text = (
        f"📦 {product['name']}\n\n"
        f"📅 Начало отслеживания: {date_added}\n\n"
        f"💰 Текущая цена: {product['current_price']} ₽\n"
        f"📉 Минимальная цена: {product['min_price']} ₽ ({min_price_date})\n"
        f"📈 Максимальная цена: {product['max_price']} ₽ ({max_price_date})\n\n"
        f"🔗 {product['url']}"
    )

    await callback.message.answer(
        text,
        reply_markup=get_product_detail_keyboard(product_id)
    )
    await callback.answer()


# Обработчик фильтров маркетплейсов
@callbacks_router.callback_query(F.data.startswith("filter:"))
async def filter_products(callback: types.CallbackQuery):
    user = callback.from_user
    filter_value = callback.data.split(":")[1]
    logger.info(f"Пользователь {user.id} выбрал фильтр: {filter_value}")

    # Определяем маркетплейс для фильтрации
    marketplace = None if filter_value == "all" else filter_value

    # Получаем все товары (включая удалённые из корзины)
    products = await get_all_products(user.id, marketplace=marketplace, in_cart_only=False)
    logger.info(f"Найдено товаров после фильтрации: {len(products)}")

    if not products:
        await callback.answer(
            "Нет товаров для выбранного маркетплейса",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products, marketplace_filter=marketplace)
    )
    await callback.answer()


# Обработчик удаления товара
@callbacks_router.callback_query(F.data.startswith("delete:"))
async def delete_product_handler(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} удаляет товар ID: {product_id}")

    success = await delete_product(product_id, user.id)

    if success:
        logger.info(f"Товар ID {product_id} успешно удален для пользователя {user.id}")

        # Загружаем обновленный список товаров (включая удалённые из корзины)
        products = await get_all_products(user.id, in_cart_only=False)

        if not products:
            # Если список пуст, показываем соответствующее сообщение
            await callback.message.edit_text("Список позиций пуст. Добавьте товары для отслеживания.")
        else:
            # Показываем обновленный список товаров
            await callback.message.edit_text(
                "📋 Список отслеживаемых товаров:",
                reply_markup=get_products_keyboard(products, marketplace_filter=None)
            )

        await callback.answer("✅ Товар удален", show_alert=False)
    else:
        logger.warning(f"Товар ID {product_id} не найден для пользователя {user.id}")
        await callback.answer("❌ Товар не найден", show_alert=True)


# Обработчик просмотра истории цен
@callbacks_router.callback_query(F.data.startswith("history:"))
async def show_price_history(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} запросил историю цен товара ID: {product_id}")

    history = await get_price_history(product_id)

    if not history:
        await callback.answer("История цен пуста", show_alert=True)
        return

    text = "📊 История цен:\n\n"
    for entry in history:
        date_time = datetime.fromisoformat(entry['date_checked']).strftime('%d.%m.%Y %H:%M')
        text += f"• {date_time} — {entry['price']} ₽\n"

    await callback.message.answer(text)
    await callback.answer()
