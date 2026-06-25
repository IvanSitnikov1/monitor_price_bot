from aiogram import F, types, Router
from aiogram.fsm.context import FSMContext
from datetime import datetime

from config import config
from utils.states import BroadcastState
from core.database import (
    broadcast_to_users,
    extend_user_subscription,
    get_user_subscription_details,
    get_users_with_subscriptions,
)
from keyboards.inline import (
    get_subscription_period_keyboard,
    get_subscription_user_keyboard,
    get_subscription_users_keyboard,
)
from utils.price_updater import update_all_prices
from loggers import logger


admin_router = Router()


def _format_date(date_value: str | None) -> str:
    """Форматирует дату для сообщений администратора."""
    if not date_value:
        return "отсутствует"

    try:
        return datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    except ValueError:
        return date_value


def _format_subscription_status(subscription: dict) -> str:
    """Формирует текстовый статус подписки."""
    match subscription["status"]:
        case "active":
            return "активна"
        case "expired":
            return "истекла"
        case _:
            return "без подписки"


def _format_user_subscription_card(subscription: dict) -> str:
    """Формирует карточку пользователя для управления подпиской."""
    username = subscription.get("username")
    username_text = f"@{username}" if username else "отсутствует"
    first_name = subscription.get("first_name") or "отсутствует"
    paid_until = subscription.get("paid_until")
    status = _format_subscription_status(subscription)

    if paid_until:
        paid_until_text = _format_date(paid_until)
    else:
        paid_until_text = "подписка отсутствует"

    return (
        "💳 Подписка пользователя\n\n"
        f"Telegram ID: {subscription['user_id']}\n"
        f"Username: {username_text}\n"
        f"Имя: {first_name}\n"
        f"Дата регистрации: {_format_date(subscription.get('date_registered'))}\n\n"
        f"Статус: {status}\n"
        f"Дата окончания: {paid_until_text}"
    )


async def _show_subscriptions_list(message: types.Message):
    """Показывает список пользователей для управления подписками."""
    users = await get_users_with_subscriptions()

    if not users:
        await message.answer("Пользователи не найдены.")
        return

    await message.answer(
        "💳 Управление подписками\n\nВыберите пользователя:",
        reply_markup=get_subscription_users_keyboard(users)
    )


async def _edit_or_answer(message: types.Message, text: str, reply_markup=None):
    """Обновляет сообщение, если возможно, иначе отправляет новое."""
    if message.text:
        await message.edit_text(text, reply_markup=reply_markup)
        return

    await message.answer(text, reply_markup=reply_markup)


# Обработчик кнопки "🔄 Обновить цены" (только для админа)
@admin_router.message(F.text == "🔄 Обновить цены")
async def manual_update_prices(message: types.Message, state: FSMContext):
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к админ-функции от пользователя {user.id}")
        return

    # Очищаем состояние FSM
    await state.clear()

    logger.info(f"Админ {user.id} запустил ручное обновление цен")

    task_queue = message.bot.task_queue
    await update_all_prices(task_queue)

    await message.answer(
        "✅ Все товары добавлены в очередь обновления!\n"
        "Обновление будет выполнено в фоновом режиме."
    )
    logger.info(f"Ручное обновление цен: товары добавлены в очередь")


# Обработчик кнопки "📢 Рассылка" (только для админа)
@admin_router.message(F.text == "📢 Рассылка")
async def broadcast_command(message: types.Message, state: FSMContext):
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к админ-функции от пользователя {user.id}")
        return

    # Очищаем состояние FSM
    await state.clear()

    logger.info(f"Админ {user.id} запустил рассылку")

    await message.answer("📧 Введите сообщение для рассылки всем пользователям:")
    await state.set_state(BroadcastState.waiting_for_message)


# Обработчик ввода сообщения для рассылки
@admin_router.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к админ-функции от пользователя {user.id}")
        await state.clear()
        return

    broadcast_message = message.text
    logger.info(f"Админ {user.id} ввел сообщение для рассылки: '{broadcast_message[:50]}...'")

    # Отправляем сообщение всем пользователям
    success_count = await broadcast_to_users(message.bot, broadcast_message)

    await state.clear()

    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"Отправлено сообщений: {success_count}"
    )
    logger.info(f"Рассылка завершена. Отправлено {success_count} сообщений")


@admin_router.message(F.text == "💳 Подписки")
async def subscriptions_menu(message: types.Message, state: FSMContext):
    """Открывает меню управления подписками."""
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к управлению подписками от пользователя {user.id}")
        return

    await state.clear()
    logger.info(f"Админ {user.id} открыл управление подписками")
    await _show_subscriptions_list(message)


@admin_router.callback_query(F.data == "admin_sub_list")
async def subscriptions_list_callback(callback: types.CallbackQuery):
    """Показывает список пользователей из inline-кнопки."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к списку подписок от пользователя {user.id}")
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    users = await get_users_with_subscriptions()
    if not users:
        await callback.answer("Пользователи не найдены", show_alert=True)
        return

    await _edit_or_answer(
        callback.message,
        "💳 Управление подписками\n\nВыберите пользователя:",
        reply_markup=get_subscription_users_keyboard(users)
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_sub_user:"))
async def subscription_user_card(callback: types.CallbackQuery):
    """Показывает карточку подписки пользователя."""
    admin = callback.from_user

    if admin.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к карточке подписки от пользователя {admin.id}")
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    subscription = await get_user_subscription_details(user_id)

    if not subscription:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _edit_or_answer(
        callback.message,
        _format_user_subscription_card(subscription),
        reply_markup=get_subscription_user_keyboard(user_id)
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_sub_extend:"))
async def subscription_extend_menu(callback: types.CallbackQuery):
    """Показывает выбор срока продления подписки."""
    admin = callback.from_user

    if admin.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к продлению подписки от пользователя {admin.id}")
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    subscription = await get_user_subscription_details(user_id)

    if not subscription:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    await _edit_or_answer(
        callback.message,
        (
            "Выберите срок продления подписки\n\n"
            f"Пользователь: {subscription['user_id']}\n"
            f"Текущая дата окончания: {_format_date(subscription.get('paid_until'))}"
        ),
        reply_markup=get_subscription_period_keyboard(user_id)
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_sub_period:"))
async def subscription_extend_period(callback: types.CallbackQuery):
    """Продлевает подписку на выбранный срок."""
    admin = callback.from_user

    if admin.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка изменения подписки от пользователя {admin.id}")
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    _, user_id_text, days_text = callback.data.split(":")
    user_id = int(user_id_text)
    period_days = int(days_text)

    subscription = await extend_user_subscription(
        user_id=user_id,
        period_days=period_days
    )

    if not subscription:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    logger.info(
        f"Админ {admin.id} продлил подписку пользователя {user_id} на {period_days} дней"
    )

    await _edit_or_answer(
        callback.message,
        (
            "✅ Подписка продлена\n\n"
            f"Пользователь: {subscription['user_id']}\n"
            f"Новая дата окончания: {_format_date(subscription.get('paid_until'))}\n\n"
            f"{_format_user_subscription_card(subscription)}"
        ),
        reply_markup=get_subscription_user_keyboard(user_id)
    )
    await callback.answer("Подписка продлена")
