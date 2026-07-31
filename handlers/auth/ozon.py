from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from core.database import (
    get_auth_request,
    save_marketplace_authorization,
    update_auth_request_status,
)
from core.models import Marketplace
from loggers import logger
from utils.states import AuthorizationInProgress


auth_ozon_router = Router()


@auth_ozon_router.callback_query(F.data.startswith("auth_ozon_approve:"))
async def approve_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Открывает браузер Ozon для ручной авторизации админом."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} одобрил авторизацию Ozon для пользователя {user_id}")

    request = await get_auth_request(user_id)

    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ Запускаю процесс авторизации Ozon для пользователя {user_id}...\n"
        f"Телефон: {request['phone']}"
    )

    browser_manager = None
    authorization_started = False
    try:
        task_queue = getattr(callback.bot, "task_queue", None)
        if not task_queue:
            raise RuntimeError("Очередь задач недоступна")

        browser_manager = task_queue.browser_manager
        await browser_manager.begin_user_authorization(user_id)
        authorization_started = True

        from scripts.authorize_user import authorize_user

        auth_context = await authorize_user(
            user_id,
            request["phone"],
            callback.bot,
            browser_manager
        )

        await state.update_data(
            auth_user_id=user_id,
            auth_phone=request["phone"],
            auth_marketplace="ozon",
            auth_context=auth_context
        )
        await state.set_state(AuthorizationInProgress.waiting_for_qr_link)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить авторизацию",
                    callback_data=f"auth_ozon_cancel:{user_id}",
                )
            ]
        ])
        await callback.message.edit_text(
            f"✅ Браузер открыт для пользователя {user_id}\n\n"
            f"Теперь отсканируйте QR-код и отправьте мне ссылку из него.",
            reply_markup=keyboard,
        )

    except Exception as e:
        if authorization_started and browser_manager:
            try:
                await browser_manager.cancel_user_authorization(user_id)
            except Exception as cleanup_error:
                logger.error(
                    f"Не удалось освободить профиль Ozon пользователя {user_id}: "
                    f"{cleanup_error}",
                    exc_info=True,
                )
        logger.error(f"Ошибка авторизации Ozon для пользователя {user_id}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка при открытии браузера: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Не удалось начать авторизацию. Попробуйте позже или обратитесь к администратору."
        )


@auth_ozon_router.callback_query(F.data.startswith("auth_ozon_reject:"))
async def reject_authorization(callback: types.CallbackQuery):
    """Отклоняет запрос авторизации Ozon."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} отклонил авторизацию Ozon для пользователя {user_id}")

    await callback.message.edit_text(f"❌ Запрос от пользователя {user_id} отклонён")

    await callback.bot.send_message(
        user_id,
        "❌ Ваш запрос на авторизацию Ozon отклонён администратором."
    )

    await update_auth_request_status(user_id, "rejected")
    await callback.answer()


@auth_ozon_router.message(AuthorizationInProgress.waiting_for_qr_link, F.text)
async def process_qr_link(message: types.Message, state: FSMContext):
    """Принимает ссылку из QR-кода Ozon от администратора."""
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        return

    qr_link = message.text.strip()
    logger.info(f"Админ {user.id} отправил ссылку из QR-кода: {qr_link[:50]}...")

    data = await state.get_data()
    auth_user_id = data.get("auth_user_id")

    if not auth_user_id:
        await message.answer("❌ Ошибка: данные авторизации не найдены. Начните процесс заново.")
        await state.clear()
        return

    try:
        await message.bot.send_message(
            auth_user_id,
            f"🔐 <b>Ссылка для авторизации в Ozon:</b>\n\n"
            f"{qr_link}\n\n"
            f"Откройте эту ссылку в приложении Ozon для завершения авторизации.",
            parse_mode="HTML"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить авторизацию",
                    callback_data=f"auth_confirm:{auth_user_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить авторизацию",
                    callback_data=f"auth_ozon_cancel:{auth_user_id}",
                )
            ]
        ])

        await message.answer(
            f"✅ Ссылка отправлена пользователю {auth_user_id}\n\n"
            f"После успешной авторизации нажмите кнопку ниже:",
            reply_markup=keyboard
        )

        await state.set_state(AuthorizationInProgress.waiting_for_confirmation)

        logger.info(f"Ссылка для авторизации отправлена пользователю {auth_user_id}")

    except Exception as e:
        logger.error(f"Ошибка при отправке ссылки пользователю {auth_user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при отправке ссылки: {str(e)}")


@auth_ozon_router.callback_query(F.data.startswith("auth_ozon_cancel:"))
async def cancel_ozon_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Отменяет ручную авторизацию Ozon и освобождает профиль."""
    user = callback.from_user
    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    if (
        data.get("auth_user_id") != user_id
        or data.get("auth_marketplace") != "ozon"
    ):
        await callback.answer("❌ Активная авторизация Ozon не найдена", show_alert=True)
        return

    auth_context = data.get("auth_context") or {}
    browser_manager = auth_context.get("browser_manager")
    if browser_manager:
        await browser_manager.cancel_user_authorization(user_id)

    await state.clear()
    await callback.message.edit_text(
        f"❌ Авторизация Ozon пользователя {user_id} отменена.\n"
        "Профиль браузера освобождён."
    )
    await callback.answer()


@auth_ozon_router.callback_query(F.data.startswith("auth_confirm:"))
async def confirm_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждает успешную авторизацию Ozon."""
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} подтвердил авторизацию Ozon для пользователя {user_id}")

    data = await state.get_data()
    auth_context = data.get("auth_context")

    if not auth_context:
        await callback.answer("❌ Ошибка: контекст авторизации не найден", show_alert=True)
        await state.clear()
        return

    try:
        browser_manager = auth_context.get("browser_manager")

        if browser_manager:
            await browser_manager.finish_user_authorization(user_id)
            logger.info(f"Браузер Ozon для пользователя {user_id} закрыт после авторизации")

        await save_marketplace_authorization(user_id, Marketplace.OZON)
        await update_auth_request_status(user_id, "approved")

        await callback.bot.send_message(
            user_id,
            "✅ <b>Авторизация успешно завершена!</b>\n\n"
            "Ваш профиль сохранён. Теперь бот будет показывать актуальные цены "
            "из вашей корзины Ozon.",
            parse_mode="HTML"
        )

        await callback.message.edit_text(
            f"✅ Пользователь {user_id} успешно авторизован в Ozon\n"
            f"Профиль сохранён."
        )

        await state.clear()

        logger.info(f"Авторизация пользователя {user_id} в Ozon успешно завершена")

    except Exception as e:
        logger.error(
            f"Ошибка при подтверждении авторизации Ozon для пользователя {user_id}: {e}",
            exc_info=True
        )
        await callback.message.edit_text(
            f"❌ Ошибка при сохранении профиля: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при сохранении профиля. Попробуйте позже "
            "или обратитесь к администратору."
        )

    await callback.answer()
