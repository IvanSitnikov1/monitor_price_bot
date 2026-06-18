from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from core.database import get_auth_request, update_auth_request_status
from loggers import logger
from utils.states import AuthorizationInProgress


auth_wildberries_router = Router()


async def notify_admin_about_wildberries_auth_request(
    bot,
    user_id: int,
    username: str | None,
    phone: str
):
    """Отправляет админу уведомление о запросе авторизации Wildberries."""
    text = (
        f"🔐 <b>Запрос на авторизацию Wildberries</b>\n\n"
        f"Пользователь: @{username or 'без username'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Телефон: <code>{phone}</code>\n\n"
        f"✅ Пользователь принял соглашение"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Авторизовать Wildberries",
                callback_data=f"auth_wb_approve:{user_id}"
            ),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"auth_wb_reject:{user_id}")
        ]
    ])

    await bot.send_message(config.ADMIN_USER_ID, text, reply_markup=keyboard, parse_mode="HTML")


@auth_wildberries_router.callback_query(F.data.startswith("auth_wb_approve:"))
async def approve_wildberries_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Открывает браузер Wildberries для ручной авторизации админом."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} одобрил авторизацию Wildberries для пользователя {user_id}")

    request = await get_auth_request(user_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ Открываю Wildberries для пользователя {user_id}...\n"
        f"Телефон: {request['phone']}"
    )

    try:
        task_queue = getattr(callback.bot, "task_queue", None)
        if not task_queue:
            raise RuntimeError("Очередь задач недоступна")

        from scripts.authorize_wildberries_user import start_wildberries_authorization

        auth_context = await start_wildberries_authorization(
            user_id,
            request["phone"],
            callback.bot,
            task_queue.browser_manager
        )
        await state.update_data(
            auth_user_id=user_id,
            auth_phone=request["phone"],
            auth_marketplace="wildberries",
            auth_context=auth_context
        )
        await state.set_state(AuthorizationInProgress.waiting_for_confirmation)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить авторизацию Wildberries",
                    callback_data=f"auth_wb_confirm:{user_id}"
                )
            ]
        ])

        await callback.message.edit_text(
            f"✅ Браузер Wildberries открыт для пользователя {user_id}\n\n"
            "Выполните вход вручную: нажмите вход, введите телефон и код "
            "из уведомления приложения Wildberries.\n"
            "После успешного входа нажмите кнопку ниже.",
            reply_markup=keyboard
        )
        await callback.answer()

    except Exception as e:
        logger.error(
            f"Ошибка авторизации Wildberries для пользователя {user_id}: {e}",
            exc_info=True
        )
        await callback.message.edit_text(f"❌ Ошибка при открытии браузера: {str(e)}")
        await callback.bot.send_message(
            user_id,
            "❌ Не удалось начать авторизацию Wildberries. Обратитесь к администратору."
        )


@auth_wildberries_router.callback_query(F.data.startswith("auth_wb_reject:"))
async def reject_wildberries_authorization(callback: types.CallbackQuery):
    """Отклоняет запрос авторизации Wildberries."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} отклонил авторизацию Wildberries для пользователя {user_id}")

    await callback.message.edit_text(f"❌ Запрос Wildberries от пользователя {user_id} отклонён")
    await callback.bot.send_message(
        user_id,
        "❌ Ваш запрос на авторизацию Wildberries отклонён администратором."
    )

    await update_auth_request_status(user_id, "rejected")
    await callback.answer()


@auth_wildberries_router.callback_query(F.data.startswith("auth_wb_confirm:"))
async def confirm_wildberries_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Сохраняет профиль после ручной авторизации Wildberries."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} подтвердил авторизацию Wildberries для пользователя {user_id}")

    data = await state.get_data()
    auth_context = data.get("auth_context")

    if not auth_context:
        await callback.answer("❌ Ошибка: контекст авторизации не найден", show_alert=True)
        await state.clear()
        return

    try:
        browser_manager = auth_context.get("browser_manager")

        if browser_manager:
            await browser_manager.close_user_browser(user_id)
            logger.info(f"Браузер Wildberries для пользователя {user_id} закрыт")

        await update_auth_request_status(user_id, "approved")

        await callback.bot.send_message(
            user_id,
            "✅ <b>Авторизация Wildberries успешно завершена!</b>\n\n"
            "Ваш профиль сохранён.",
            parse_mode="HTML"
        )
        await callback.message.edit_text(
            f"✅ Пользователь {user_id} успешно авторизован в Wildberries\n"
            "Профиль сохранён."
        )
        await state.clear()
        logger.info(f"Авторизация Wildberries пользователя {user_id} завершена")

    except Exception as e:
        logger.error(
            f"Ошибка сохранения профиля Wildberries для пользователя {user_id}: {e}",
            exc_info=True
        )
        await callback.message.edit_text(f"❌ Ошибка при сохранении профиля: {str(e)}")
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при сохранении профиля Wildberries."
        )

    await callback.answer()
