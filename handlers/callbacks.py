import csv
from datetime import datetime
from io import StringIO
from pathlib import Path

from aiogram import F, types, Router
from aiogram.types import FSInputFile

from keyboards.inline import (
    get_price_history_keyboard,
    get_product_detail_keyboard,
    get_products_keyboard,
)
from core.database import (
    get_all_products,
    get_product_details,
    get_price_changes,
    delete_product,
    get_price_history,
)
from loggers import logger


callbacks_router = Router()
HISTORY_MESSAGE_LIMIT = 3900


def _format_price_history(history: list[dict]) -> list[str]:
    """Формирует сообщения с историей изменений в пределах лимита Telegram."""
    header = "📊 История изменений цен:\n\n"
    continuation_header = "📊 История изменений цен (продолжение):\n\n"
    messages: list[str] = []
    current_message = header

    for entry in history:
        date_time = datetime.fromisoformat(entry["date_checked"]).strftime("%d.%m.%Y %H:%M")
        line = f"• {date_time} — {entry['price']} ₽\n"

        if len(current_message) + len(line) > HISTORY_MESSAGE_LIMIT:
            messages.append(current_message.rstrip())
            current_message = continuation_header

        current_message += line

    messages.append(current_message.rstrip())
    return messages


def _build_price_history_csv(history: list[dict]) -> bytes:
    """Формирует CSV с полной историей проверок цены."""
    csv_file = StringIO(newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["Дата проверки", "Цена, ₽"])

    for entry in history:
        writer.writerow([entry["date_checked"], entry["price"]])

    return csv_file.getvalue().encode("utf-8-sig")


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
        f"📈 Максимальная цена: {product['max_price']} ₽ ({max_price_date})"
    )

    if product.get("url"):
        text += f"\n\n🔗 Ссылка на товар:\n{product['url']}"

    reply_markup = get_product_detail_keyboard(product_id)

    image_path = product.get("image_path")
    if image_path:
        local_path = Path(image_path)
        if local_path.exists():
            try:
                await callback.message.answer_photo(
                    photo=FSInputFile(local_path),
                    caption=text,
                    reply_markup=reply_markup
                )
                await callback.answer()
                return
            except Exception as e:
                logger.warning(f"Не удалось отправить локальное изображение товара {product_id}: {e}")
        else:
            logger.warning(f"Локальный файл изображения не найден: {local_path}")

    await callback.message.answer(text, reply_markup=reply_markup)
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

    history = await get_price_changes(product_id, user.id)

    if not history:
        await callback.answer("История цен пуста или товар не найден", show_alert=True)
        return

    messages = _format_price_history(history)
    keyboard = get_price_history_keyboard(product_id)
    await callback.answer()

    for index, text in enumerate(messages):
        reply_markup = keyboard if index == len(messages) - 1 else None
        await callback.message.answer(text, reply_markup=reply_markup)


@callbacks_router.callback_query(F.data.startswith("history_download:"))
async def download_price_history(callback: types.CallbackQuery):
    """Отправляет полную подробную историю цен в формате CSV."""
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} скачивает историю цен товара ID: {product_id}")

    history = await get_price_history(product_id, user.id)

    if not history:
        await callback.answer("История цен пуста или товар не найден", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer_document(
        document=types.BufferedInputFile(
            _build_price_history_csv(history),
            filename=f"price_history_{product_id}.csv"
        ),
        caption="📊 Полная подробная история цен"
    )
