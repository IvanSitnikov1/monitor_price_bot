import logging

from aiogram import F, types, Router
from aiogram.fsm.context import FSMContext

from keyboards.inline import get_products_keyboard
from utils.states import AddPosition
from core.database import (
    add_product,
    get_all_products,
    check_user_has_product,
)
from utils.link_parser import parse_product_link
from core.models import Marketplace


logger = logging.getLogger(__name__)
products_router = Router()


# Обработчик кнопки "Добавить позицию"
@products_router.message(F.text == "Добавить позицию")
async def add_position(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} начал добавление товара")
    await message.answer("Отправь мне ссылку на товар, цену которого ты хочешь отслеживать.")
    await state.set_state(AddPosition.waiting_for_link)


# Обработчик кнопки "Список позиций"
@products_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} запросил список товаров")

    # Очищаем состояние FSM, если пользователь был в процессе добавления товара
    await state.clear()

    products = await get_all_products(user.id)
    logger.info(f"Найдено товаров в БД для пользователя {user.id}: {len(products)}")

    if not products:
        await message.answer("Список позиций пуст. Добавьте товары для отслеживания.")
        return

    await message.answer(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products, marketplace_filter=None)
    )


@products_router.message(AddPosition.waiting_for_link, F.text)
async def process_link(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text

    logger.info(f"Пользователь {user.id} отправил текст: {text}")

    # Извлекаем URL и определяем маркетплейс
    url, marketplace = parse_product_link(text)

    if not url or not marketplace:
        logger.warning(f"Не удалось распарсить ссылку от пользователя {user.id}: {text}")
        await message.answer(
            "❌ Не удалось распознать ссылку на товар.\n"
            "Поддерживаются маркетплейсы: Ozon, Wildberries, Avito.\n"
            "Попробуй еще раз."
        )
        return

    logger.info(f"Распознан маркетплейс: {marketplace.value}, URL: {url}")

    # Проверяем, поддерживается ли маркетплейс (пока только Ozon)
    if marketplace != Marketplace.OZON:
        logger.info(f"Маркетплейс {marketplace.value} пока не поддерживается")
        await message.answer(
            f"🚧 Функционал для {marketplace.value.capitalize()} находится в разработке.\n"
            f"Пока доступен только Ozon."
        )
        await state.clear()
        return

    # Проверяем, не подписан ли пользователь уже на этот товар
    if await check_user_has_product(user.id, url):
        logger.warning(f"Пользователь {user.id} уже подписан на товар: {url}")
        await message.answer("⚠️ Этот товар уже добавлен в твой список.")
        await state.clear()
        return

    # Добавляем задачу в очередь с высоким приоритетом
    task_queue = message.bot.task_queue
    await task_queue.add_task(
        task_type="add_product",
        url=url,
        marketplace=marketplace.value,
        priority=1,  # Высокий приоритет
        user_id=user.id
    )

    await message.answer(
        "⏳ Товар добавлен в очередь обработки.\n"
        "Это может занять до 5 минут, ожидайте уведомления."
    )

    logger.info(f"Задача добавления товара добавлена в очередь для пользователя {user.id}")
    await state.clear()
