import logging

from aiogram import F, types, Router
from aiogram.fsm.context import FSMContext

from keyboards.inline import get_products_keyboard
from utils.states import AddCart
from core.database import (
    add_product,
    get_all_products,
    check_user_has_product,
    delete_product,
)
from core.models import Marketplace


logger = logging.getLogger(__name__)
products_router = Router()


# Обработчик кнопки "Добавить корзину"
@products_router.message(F.text == "Добавить корзину")
async def add_cart(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} начал добавление корзины")
    await message.answer(
        "Отправь мне ссылку на корзину Ozon.\n"
        "Формат: https://www.ozon.ru/cart?share=..."
    )
    await state.set_state(AddCart.waiting_for_link)


# Обработчик кнопки "Список позиций"
@products_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} запросил список товаров")

    # Очищаем состояние FSM, если пользователь был в процессе добавления товара
    await state.clear()

    # Получаем ВСЕ товары пользователя (включая удалённые из корзины)
    products = await get_all_products(user.id, in_cart_only=False)
    logger.info(f"Найдено товаров в БД для пользователя {user.id}: {len(products)}")

    if not products:
        await message.answer("Список позиций пуст. Добавьте корзину для отслеживания.")
        return

    await message.answer(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products, marketplace_filter=None)
    )


@products_router.message(AddCart.waiting_for_link, F.text)
async def process_cart_link(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text.strip()

    logger.info(f"Пользователь {user.id} отправил ссылку на корзину: {text}")

    # Проверяем формат ссылки на корзину
    if not text.startswith("https://www.ozon.ru/cart?share="):
        logger.warning(f"Неверный формат ссылки на корзину от пользователя {user.id}")
        await message.answer(
            "❌ Неверный формат ссылки.\n"
            "Ожидается ссылка вида: https://www.ozon.ru/cart?share=..."
        )
        return

    # Добавляем задачу в очередь с высоким приоритетом
    task_queue = message.bot.task_queue
    await task_queue.add_task(
        task_type="add_cart",
        url=text,
        marketplace=Marketplace.OZON.value,
        priority=1,  # Высокий приоритет
        user_id=user.id
    )

    await message.answer(
        "⏳ Корзина добавлена в очередь обработки.\n"
        "Это может занять до 5 минут, ожидайте уведомления."
    )

    logger.info(f"Задача добавления корзины добавлена в очередь для пользователя {user.id}")
    await state.clear()
