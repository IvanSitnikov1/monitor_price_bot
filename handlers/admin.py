from aiogram import F, types, Router
from aiogram.fsm.context import FSMContext

from config import config
from utils.states import BroadcastState
from core.database import broadcast_to_users
from utils.price_updater import update_all_prices
from loggers import logger


admin_router = Router()


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
