import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from core.database import get_auth_request, update_auth_request_status
from loggers import logger
from utils.states import AuthorizationInProgress


auth_wildberries_router = Router()


@auth_wildberries_router.callback_query(F.data.startswith("auth_wildberries_approve:"))
async def approve_wildberries_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Открывает браузер Wildberries для ручной авторизации админом."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} одобрил авторизацию Wildberries для пользователя {user_id}")

    state_data = await state.get_data()
    pending_wb_code = (
        state_data.get("pending_wb_code")
        if user_id == config.ADMIN_USER_ID
        else None
    )

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
            pending_wb_code=pending_wb_code,
            auth_context=auth_context
        )
        await state.set_state(AuthorizationInProgress.waiting_for_wb_code)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить авторизацию Wildberries",
                    callback_data=f"auth_wb_confirm:{user_id}"
                )
            ]
        ])

        code_text = (
            f"\n\nКод уже получен: <code>{pending_wb_code}</code>\n"
            "Введите его в открытом браузере."
            if pending_wb_code
            else "\n\nВыполните вход вручную и дождитесь кода от пользователя."
        )

        await callback.message.edit_text(
            f"✅ Браузер Wildberries открыт для пользователя {user_id}"
            f"{code_text}",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

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


@auth_wildberries_router.callback_query(F.data.startswith("auth_wildberries_reject:"))
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


@auth_wildberries_router.message(AuthorizationInProgress.waiting_for_wb_code, F.text)
async def process_admin_wildberries_code(message: types.Message, state: FSMContext):
    """Принимает код Wildberries в админском сценарии авторизации."""
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(
            f"Пользователь {user.id} попытался отправить код в админском сценарии Wildberries"
        )
        await message.answer("❌ Доступ запрещён")
        return

    data = await state.get_data()
    auth_user_id = data.get("auth_user_id")
    auth_marketplace = data.get("auth_marketplace")
    auth_context = data.get("auth_context")
    code = message.text.strip()

    if auth_marketplace != "wildberries" or not auth_user_id or not auth_context:
        logger.warning(
            f"Админ {user.id} отправил код Wildberries без активного контекста авторизации"
        )
        await message.answer(
            "❌ Активная авторизация Wildberries не найдена.\n"
            "Запустите сценарий авторизации заново."
        )
        await state.clear()
        return

    if not re.fullmatch(r"\d{4,8}", code):
        await message.answer(
            "❌ Отправьте только цифровой код Wildberries без пробелов и другого текста."
        )
        return

    logger.info(
        f"Админ {user.id} получил код Wildberries для пользователя {auth_user_id}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить авторизацию Wildberries",
                callback_data=f"auth_wb_confirm:{auth_user_id}"
            )
        ]
    ])
    await message.answer(
        "📩 <b>Код авторизации Wildberries получен</b>\n\n"
        f"Пользователь: <code>{auth_user_id}</code>\n"
        f"Код: <code>{code}</code>\n\n"
        "Введите код в открытом браузере Wildberries. После успешного входа нажмите "
        "кнопку подтверждения авторизации.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


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
