import asyncio
import re
import random

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from keyboards.reply import main_kb
from keyboards.inline import get_products_keyboard, get_product_detail_keyboard
from utils.states import AddPosition
from utils.scraper import scrape_ozon_product
from core.database import add_product, get_all_products, get_product_details, update_product_price


main_router = Router()


# Обработчик команды /start
@main_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот для мониторинга цен. Выбери действие:",
        reply_markup=main_kb,
    )


# Обработчик кнопки "Добавить позицию"
@main_router.message(F.text == "Добавить позицию")
async def add_position(message: types.Message, state: FSMContext):
    await message.answer("Отправь мне ссылку на товар, цену которого ты хочешь отслеживать.")
    await state.set_state(AddPosition.waiting_for_link)


@main_router.message(AddPosition.waiting_for_link, F.text)
async def process_link(message: types.Message, state: FSMContext):
    await state.update_data(link=message.text)
    await message.answer("Обрабатываю ссылку...")

    product_name, price = await scrape_ozon_product(message.text)

    if product_name and price:
        # Извлекаем число из строки цены "12345 ₽"
        price_match = re.search(r'(\d+)', price)
        if price_match:
            price_int = int(price_match.group(1))

            # Сохраняем в БД
            product_id = await add_product(message.text, product_name, price_int)

            if product_id:
                await message.answer(
                    f"✅ Товар добавлен!\n\n"
                    f"Название: {product_name}\n"
                    f"Цена: {price}"
                )
            else:
                await message.answer("⚠️ Этот товар уже добавлен в список.")
        else:
            await message.answer("Ошибка при обработке цены.")
    else:
        await message.answer(
            "Не удалось получить данные о товаре.\n"
            "Возможные причины:\n"
            "- Обнаружена капча (требуется ручное решение)\n"
            "- Неверная ссылка\n"
            "- Товар недоступен"
        )

    await state.clear()


# Обработчик кнопки "Список позиций"
@main_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message):
    products = await get_all_products()

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
    product_id = int(callback.data.split(":")[1])
    product = await get_product_details(product_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = (
        f"📦 {product['name']}\n\n"
        f"💰 Текущая цена: {product['current_price']} ₽\n"
        f"📉 Минимальная цена: {product['min_price']} ₽\n"
        f"📈 Максимальная цена: {product['max_price']} ₽\n\n"
        f"🔗 {product['url']}"
    )

    await callback.message.edit_text(
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


# Обработчик кнопки "Обновить цены"
@main_router.callback_query(F.data == "update_prices")
async def update_prices(callback: types.CallbackQuery):
    await callback.answer("Начинаю обновление цен...", show_alert=False)
    await callback.message.edit_text("⏳ Обновляю цены, подождите...")

    products = await get_all_products()

    if not products:
        await callback.message.edit_text("Список позиций пуст.")
        return

    updated_count = 0
    failed_products = []

    for product in products:
        # Рандомная задержка 0-5 секунд
        delay = random.uniform(0, 5)
        await asyncio.sleep(delay)

        product_name, price_str = await scrape_ozon_product(product['url'])

        if product_name and price_str:
            # Извлекаем число из строки цены
            price_match = re.search(r'(\d+)', price_str)
            if price_match:
                price_int = int(price_match.group(1))
                await update_product_price(product['id'], price_int)
                updated_count += 1
            else:
                failed_products.append(product['name'])
        else:
            failed_products.append(product['name'])

    # Формируем итоговое сообщение
    result_text = f"✅ Обновление завершено!\n\n"
    result_text += f"Обновлено: {updated_count} из {len(products)}\n"

    if failed_products:
        result_text += f"\n⚠️ Не удалось обновить:\n"
        for name in failed_products:
            result_text += f"- {name[:40]}...\n"

    # Обновляем список
    updated_products = await get_all_products()
    await callback.message.edit_text(
        result_text + "\n📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(updated_products)
    )
