import asyncio

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from keyboards.reply import main_kb
from utils.states import AddPosition
from utils.scraper import scrape_ozon_product


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

    # Await the scraping task
    product_name, price = await scrape_ozon_product(message.text)

    if product_name and price:
        await message.answer(f"Название: {product_name}\nЦена: {price}")
    else:
        await message.answer("Не удалось получить данные о товаре. Попробуйте другую ссылку.")

    await state.clear()


# Обработчик кнопки "Список позиций"
@main_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message):
    await message.answer("Этот функционал пока в разработке.")


# Эхо-обработчик (теперь он должен быть последним)
@main_router.message()
async def echo(message: types.Message):
    await message.answer(f"Неизвестная команда. Ты написал: {message.text}")
