import logging
import re

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from utils.states import LoginState
from core.database import (
    save_auth_request,
    get_auth_request,
    update_auth_request_status
)


logger = logging.getLogger(__name__)
auth_ozon_router = Router()


# Обработчик кнопки "🔐 Авторизация"
@auth_ozon_router.message(F.text == "🔐 Авторизация")
async def authorization_button(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} нажал кнопку Авторизация")
    await state.clear()

    await message.answer("📱 Отправьте ваш номер телефона в формате +79991234567")
    await state.set_state(LoginState.waiting_for_phone)


# Обработчик команды /login
@auth_ozon_router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /login от пользователя {user.id} (@{user.username})")
    await state.clear()

    await message.answer("📱 Отправьте ваш номер телефона в формате +79991234567")
    await state.set_state(LoginState.waiting_for_phone)


# Обработчик приёма номера телефона
@auth_ozon_router.message(LoginState.waiting_for_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext):
    user = message.from_user
    phone = message.text.strip()

    logger.info(f"Пользователь {user.id} отправил номер телефона: {phone}")

    # Валидация номера
    if not re.match(r'^\+7\d{10}$', phone):
        await message.answer(
            "❌ Неверный формат номера телефона.\n"
            "Используйте формат: +79991234567"
        )
        return

    # Сохраняем запрос в БД
    await save_auth_request(
        user_id=user.id,
        phone=phone,
        username=user.username
    )

    # Уведомляем админа
    await notify_admin_about_auth_request(
        bot=message.bot,
        user_id=user.id,
        username=user.username,
        phone=phone
    )

    await message.answer(
        "✅ Запрос на авторизацию отправлен администратору.\n"
        "Ожидайте QR-код для авторизации."
    )

    logger.info(f"Запрос на авторизацию от пользователя {user.id} сохранён")
    await state.clear()


async def notify_admin_about_auth_request(bot, user_id: int, username: str | None, phone: str):
    """Отправляет админу уведомление о запросе авторизации с кнопками"""
    text = (
        f"🔐 <b>Запрос на авторизацию Ozon</b>\n\n"
        f"Пользователь: @{username or 'без username'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Телефон: <code>{phone}</code>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Авторизовать", callback_data=f"auth_ozon_approve:{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"auth_ozon_reject:{user_id}")
        ]
    ])

    await bot.send_message(config.ADMIN_USER_ID, text, reply_markup=keyboard, parse_mode="HTML")


# Обработчик одобрения авторизации (админ)
@auth_ozon_router.callback_query(F.data.startswith("auth_ozon_approve:"))
async def approve_authorization(callback: types.CallbackQuery):
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} одобрил авторизацию Ozon для пользователя {user_id}")

    # Получаем данные запроса
    request = await get_auth_request(user_id)

    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ Запускаю процесс авторизации Ozon для пользователя {user_id}...\n"
        f"Телефон: {request['phone']}"
    )

    try:
        # Запускаем процесс авторизации
        from scripts.authorize_user import authorize_user
        await authorize_user(user_id, request['phone'], callback.bot)

        await callback.message.edit_text(
            f"✅ Пользователь {user_id} успешно авторизован в Ozon"
        )

    except TimeoutError as e:
        logger.error(f"Таймаут авторизации Ozon для пользователя {user_id}: {e}")
        await callback.message.edit_text(
            f"❌ Таймаут: пользователь {user_id} не завершил авторизацию вовремя"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Время авторизации истекло. Попробуйте позже или обратитесь к администратору."
        )

    except Exception as e:
        logger.error(f"Ошибка авторизации Ozon для пользователя {user_id}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка авторизации: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Не удалось завершить авторизацию. Попробуйте позже или обратитесь к администратору."
        )


# Обработчик отклонения авторизации (админ)
@auth_ozon_router.callback_query(F.data.startswith("auth_ozon_reject:"))
async def reject_authorization(callback: types.CallbackQuery):
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
