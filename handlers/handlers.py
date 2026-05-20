import asyncio
import logging
import re
from datetime import datetime

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from keyboards.reply import main_kb
from keyboards.inline import get_products_keyboard, get_product_detail_keyboard
from utils.states import AddPosition
from utils.scraper import scrape_ozon_product
from core.database import (
    add_product,
    get_all_products,
    get_product_details,
    delete_product,
    get_price_history,
    check_user_has_product
)


logger = logging.getLogger(__name__)
main_router = Router()


# Обработчик команды /start
@main_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /start от пользователя {user.id} (@{user.username})")
    await state.clear()
    await message.answer(
        "Привет! Я бот для мониторинга цен. Выбери действие:",
        reply_markup=main_kb,
    )


# Обработчик кнопки "Добавить позицию"
@main_router.message(F.text == "Добавить позицию")
async def add_position(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} начал добавление товара")
    await message.answer("Отправь мне ссылку на товар, цену которого ты хочешь отслеживать.")
    await state.set_state(AddPosition.waiting_for_link)


@main_router.message(AddPosition.waiting_for_link, F.text)
async def process_link(message: types.Message, state: FSMContext):
    user = message.from_user
    url = message.text

    logger.info(f"Пользователь {user.id} отправил ссылку: {url}")

    # Проверяем, не подписан ли пользователь уже на этот товар
    if await check_user_has_product(user.id, url):
        logger.warning(f"Пользователь {user.id} уже подписан на товар: {url}")
        await message.answer("⚠️ Этот товар уже добавлен в твой список.")
        await state.clear()
        return

    await state.update_data(link=url)

    status_msg = await message.answer(
        "⏳ Обрабатываю ссылку...\n"
        "Это может занять до 1 минуты, пожалуйста подожди."
    )

    logger.info(f"Начало парсинга товара: {url}")

    # Пытаемся спарсить товар до 3 раз
    max_attempts = 3
    product_name = None
    price = None

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Попытка парсинга {attempt}/{max_attempts}")

        if attempt > 1:
            await status_msg.edit_text(
                f"⏳ Попытка {attempt}/{max_attempts}...\n"
                "Пожалуйста подожди."
            )

        product_name, price = await scrape_ozon_product(url)

        if product_name and price:
            logger.info(f"Товар успешно спарсен на попытке {attempt}: {product_name}, цена: {price}")
            break
        else:
            logger.warning(f"Попытка {attempt} не удалась")
            if attempt < max_attempts:
                await asyncio.sleep(2)

    if product_name and price:
        # Извлекаем число из строки цены "12345 ₽"
        price_match = re.search(r'(\d+)', price)
        if price_match:
            price_int = int(price_match.group(1))

            # Сохраняем в БД
            logger.info(f"Сохранение товара в БД: {product_name}")
            product_id = await add_product(url, product_name, price_int, user.id)

            if product_id:
                logger.info(f"Товар успешно добавлен в БД с ID: {product_id}")
                await status_msg.edit_text(
                    f"✅ Товар добавлен!\n\n"
                    f"Название: {product_name}\n"
                    f"Цена: {price}"
                )
            else:
                logger.warning(f"Товар уже существует в БД для пользователя {user.id}: {url}")
                await status_msg.edit_text("⚠️ Этот товар уже добавлен в твой список.")
        else:
            logger.error(f"Ошибка при обработке цены: {price}")
            await status_msg.edit_text("Ошибка при обработке цены.")
    else:
        logger.error(f"Не удалось спарсить товар после {max_attempts} попыток: {url}")
        await status_msg.edit_text(
            f"❌ Не удалось получить данные о товаре после {max_attempts} попыток.\n\n"
            "Возможные причины:\n"
            "- Обнаружена капча (требуется ручное решение)\n"
            "- Неверная ссылка\n"
            "- Товар недоступен"
        )

    await state.clear()


# Обработчик кнопки "Список позиций"
@main_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message):
    user = message.from_user
    logger.info(f"Пользователь {user.id} запросил список товаров")

    products = await get_all_products(user.id)
    logger.info(f"Найдено товаров в БД для пользователя {user.id}: {len(products)}")

    if not products:
        await message.answer("Список позиций пуст. Добавьте товары для отслеживания.")
        return

    await message.answer(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products)
    )


# Эхо-обработчик (теперь он должен быть последним)
@main_router.message()
async def echo(message: types.Message):
    await message.answer(f"Неизвестная команда. Ты написал: {message.text}")


# Обработчик нажатия на товар в списке
@main_router.callback_query(F.data.startswith("product:"))
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


# Обработчик кнопки "Назад"
@main_router.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery):
    products = await get_all_products()

    if not products:
        await callback.message.edit_text("Список позиций пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products)
    )
    await callback.answer()


# Обработчик удаления товара
@main_router.callback_query(F.data.startswith("delete:"))
async def delete_product_handler(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} удаляет товар ID: {product_id}")

    success = await delete_product(product_id, user.id)

    if success:
        logger.info(f"Товар ID {product_id} успешно удален для пользователя {user.id}")
        await callback.message.delete()
        await callback.answer("✅ Товар удален", show_alert=False)
    else:
        logger.warning(f"Товар ID {product_id} не найден для пользователя {user.id}")
        await callback.answer("❌ Товар не найден", show_alert=True)


# Обработчик просмотра истории цен
@main_router.callback_query(F.data.startswith("history:"))
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
