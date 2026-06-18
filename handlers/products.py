from urllib.parse import parse_qs, urlparse

from aiogram import F, types, Router
from aiogram.fsm.context import FSMContext

from keyboards.inline import get_products_keyboard
from utils.states import AddCart
from core.database import get_all_products, register_user
from core.models import Marketplace
from loggers import logger


products_router = Router()


def _detect_cart_marketplace(url: str) -> Marketplace | None:
    """Определяет маркетплейс по ссылке на расшаренную корзину."""
    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    host = parsed_url.netloc.lower()

    match host:
        case "www.ozon.ru" | "ozon.ru":
            if parsed_url.path == "/cart" and query.get("share"):
                return Marketplace.OZON
        case "www.wildberries.ru" | "wildberries.ru":
            if parsed_url.path == "/basket" and query.get("shareId"):
                return Marketplace.WILDBERRIES

    return None


# Обработчик кнопки "Добавить товары"
@products_router.message(F.text == "Добавить товары")
async def add_cart(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} начал добавление товаров")

    # Регистрируем пользователя, если его нет в БД
    await register_user(user.id, user.username, user.first_name)

    await message.answer(
        "Отправь ссылку на общую корзину Ozon или Wildberries либо прямую ссылку на "
        "объявление Avito.\n"
        "Форматы:\n"
        "<code>https://www.ozon.ru/cart?share=...</code>\n"
        "<code>https://www.wildberries.ru/basket?shareId=...</code>\n"
        "<code>https://www.avito.ru/...</code>",
        parse_mode="HTML",
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
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
        await message.answer("Список позиций пуст. Добавьте товары для отслеживания.")
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

    # Регистрируем пользователя перед добавлением товаров (на случай, если его ещё нет)
    await register_user(user.id, user.username, user.first_name)

    marketplace = _detect_cart_marketplace(text)

    # Проверяем формат ссылки на корзину
    if not marketplace:
        logger.warning(f"Неверный формат ссылки на корзину от пользователя {user.id}")
        await message.answer(
            "❌ Неверный формат ссылки.\n"
            "Ожидаются ссылки на общую корзину или объявление вида:\n"
            "<code>https://www.ozon.ru/cart?share=...</code>\n"
            "<code>https://www.wildberries.ru/basket?shareId=...</code>\n"
            "<code>https://www.avito.ru/...</code>",
            parse_mode="HTML",
            link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )
        return

    # Добавляем задачу в очередь с высоким приоритетом
    task_queue = message.bot.task_queue
    await task_queue.add_task(
        task_type="add_cart",
        url=text,
        marketplace=marketplace.value,
        priority=1,  # Высокий приоритет
        user_id=user.id
    )

    await message.answer(
        "⏳ Ссылка добавлена в очередь обработки.\n"
        "Это может занять некоторое время, ожидайте уведомления."
    )

    logger.info(f"Задача добавления корзины добавлена в очередь для пользователя {user.id}")
    await state.clear()
