import re

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from utils.states import LoginState, AuthorizationInProgress
from core.database import (
    save_auth_request,
    get_auth_request,
    update_auth_request_status
)
from loggers import logger


auth_ozon_router = Router()


# Обработчик кнопки "🔐 Авторизация"
@auth_ozon_router.message(F.text == "🔐 Авторизация")
async def authorization_button(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} открыл раздел авторизации")
    await state.clear()

    # Показываем меню авторизации
    auth_menu_text = (
        "🔐 <b>Управление авторизацией</b>\n\n"
        "Выберите действие:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Авторизоваться в Ozon", callback_data="auth_start")],
        [InlineKeyboardButton(text="🗑 Удалить мои данные", callback_data="auth_delete_data")]
    ])

    await message.answer(auth_menu_text, reply_markup=keyboard, parse_mode="HTML")


# Обработчик начала авторизации
@auth_ozon_router.callback_query(F.data == "auth_start")
async def start_authorization(callback: types.CallbackQuery, state: FSMContext):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} начал процесс авторизации")

    # Показываем пользовательское соглашение
    agreement_text = (
        "⚠️ <b>ВАЖНАЯ ИНФОРМАЦИЯ ПЕРЕД АВТОРИЗАЦИЕЙ</b>\n\n"

        "📋 <b>Что нужно знать:</b>\n\n"

        "1️⃣ <b>Неофициальный сервис</b>\n"
        "Этот бот не является официальным сервисом Ozon и не связан с компанией. "
        "Использование бота может нарушать условия использования Ozon.\n\n"

        "2️⃣ <b>Хранение данных авторизации</b>\n"
        "Ваши cookies авторизации будут храниться на сервере бота для доступа к ценам. "
        "Администратор технически имеет доступ к этим данным.\n\n"

        "3️⃣ <b>Риски безопасности</b>\n"
        "• При утечке данных ваш аккаунт может быть скомпрометирован\n"
        "• Ozon может заблокировать аккаунт за автоматизированный доступ\n"
        "• Администратор сервиса не несёт ответственности за возможные последствия\n\n"

        "4️⃣ <b>Какие данные обрабатываются:</b>\n"
        "• Номер телефона (для авторизации)\n"
        "• Cookies сессии Ozon\n"
        "• Содержимое корзины и цены товаров\n\n"

        "5️⃣ <b>Ваши права:</b>\n"
        "• Вы можете в любой момент удалить свои данные через кнопку \"🗑 Удалить мои данные\"\n"
        "• Вы можете отозвать согласие и прекратить использование бота\n\n"

        "⚖️ <b>Юридический disclaimer:</b>\n"
        "Используя авторизацию, вы принимаете все риски на себя. "
        "Администратор не несёт ответственности за:\n"
        "• Блокировку аккаунта Ozon\n"
        "• Утечку данных\n"
        "• Некорректную работу сервиса\n"
        "• Любые финансовые или иные потери\n\n"

        "Если остались вопросы, пишите в ЛС - @Ivanches1\n\n"

        "❓ <b>Вы согласны с условиями?</b>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принимаю условия", callback_data="auth_agreement_accept"),
            InlineKeyboardButton(text="❌ Отклоняю", callback_data="auth_agreement_decline")
        ]
    ])

    await callback.message.edit_text(agreement_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# Обработчик принятия соглашения
@auth_ozon_router.callback_query(F.data == "auth_agreement_accept")
async def accept_agreement(callback: types.CallbackQuery, state: FSMContext):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} принял соглашение авторизации")

    await callback.message.edit_text(
        "✅ Соглашение принято.\n\n"
        "📱 Отправьте ваш номер телефона в формате +79991234567"
    )

    await state.set_state(LoginState.waiting_for_phone)
    await callback.answer()


# Обработчик отклонения соглашения
@auth_ozon_router.callback_query(F.data == "auth_agreement_decline")
async def decline_agreement(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} отклонил соглашение авторизации")

    await callback.message.edit_text(
        "❌ Вы отклонили условия авторизации.\n\n"
        "⚠️ Без авторизации цены товаров могут отображаться некорректно.\n\n"
        "Вы можете вернуться к авторизации позже через кнопку 🔐 Авторизация."
    )
    await callback.answer()


# Обработчик команды /login
@auth_ozon_router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    # Перенаправляем на обработчик кнопки авторизации
    await authorization_button(message, state)


# Обработчик удаления данных
@auth_ozon_router.callback_query(F.data == "auth_delete_data")
async def delete_user_data(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} запросил удаление данных")

    confirmation_text = (
        "🗑 <b>Удаление данных авторизации</b>\n\n"
        "Будут удалены:\n"
        "• Профиль браузера с cookies Ozon\n"
        "• Запросы на авторизацию\n"
        "• Согласие на обработку данных\n\n"
        "⚠️ После удаления цены могут отображаться некорректно.\n"
        "Ваши товары и история цен НЕ будут удалены.\n\n"
        "Вы уверены?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data="auth_delete_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="auth_delete_cancel")
        ]
    ])

    await callback.message.edit_text(confirmation_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# Обработчик подтверждения удаления
@auth_ozon_router.callback_query(F.data == "auth_delete_confirm")
async def confirm_delete_data(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} подтвердил удаление данных")

    try:
        from core.database import delete_user_auth_data
        from pathlib import Path
        import shutil

        # Удаляем данные авторизации из БД
        await delete_user_auth_data(user.id)

        # Удаляем профиль браузера
        profile_path = Path(f"user_data/{user.id}")
        if profile_path.exists():
            shutil.rmtree(profile_path)
            logger.info(f"Профиль пользователя {user.id} удалён с диска")

        await callback.message.edit_text(
            "✅ <b>Данные успешно удалены</b>\n\n"
            "Удалено:\n"
            "• Профиль браузера с cookies\n"
            "• Запросы на авторизацию\n"
            "• Согласие на обработку данных\n\n"
            "Ваши товары и история цен сохранены.\n\n"
            "Вы можете авторизоваться заново через кнопку 🔐 Авторизация.",
            parse_mode="HTML"
        )

        # Уведомляем админа
        try:
            await callback.bot.send_message(
                config.ADMIN_USER_ID,
                f"🗑 Пользователь {user.id} (@{user.username or 'без username'}) "
                f"отозвал согласие и удалил данные авторизации"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа об удалении данных: {e}")

    except Exception as e:
        logger.error(f"Ошибка при удалении данных пользователя {user.id}: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Ошибка при удалении данных.\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode="HTML"
        )

    await callback.answer()


# Обработчик отмены удаления
@auth_ozon_router.callback_query(F.data == "auth_delete_cancel")
async def cancel_delete_data(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"Пользователь {user.id} отменил удаление данных")

    await callback.message.edit_text(
        "❌ Удаление данных отменено.\n\n"
        "Ваши данные авторизации сохранены.",
        parse_mode="HTML"
    )
    await callback.answer()


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

    # Сохраняем запрос в БД (согласие уже принято)
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
        f"Телефон: <code>{phone}</code>\n\n"
        f"✅ Пользователь принял соглашение"
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
async def approve_authorization(callback: types.CallbackQuery, state: FSMContext):
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
        # Запускаем процесс авторизации (браузер остаётся открытым)
        from scripts.authorize_user import authorize_user
        auth_context = await authorize_user(user_id, request['phone'], callback.bot)

        # Сохраняем контекст в FSM для дальнейшей работы
        await state.update_data(
            auth_user_id=user_id,
            auth_phone=request['phone'],
            auth_context=auth_context
        )

        # Переходим в состояние ожидания ссылки
        await state.set_state(AuthorizationInProgress.waiting_for_qr_link)

        await callback.message.edit_text(
            f"✅ Браузер открыт для пользователя {user_id}\n\n"
            f"Теперь отсканируйте QR-код и отправьте мне ссылку из него."
        )

    except Exception as e:
        logger.error(f"Ошибка авторизации Ozon для пользователя {user_id}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка при открытии браузера: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Не удалось начать авторизацию. Попробуйте позже или обратитесь к администратору."
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


# Обработчик приёма ссылки из QR-кода (админ)
@auth_ozon_router.message(AuthorizationInProgress.waiting_for_qr_link, F.text)
async def process_qr_link(message: types.Message, state: FSMContext):
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        return

    qr_link = message.text.strip()
    logger.info(f"Админ {user.id} отправил ссылку из QR-кода: {qr_link[:50]}...")

    # Получаем данные из FSM
    data = await state.get_data()
    auth_user_id = data.get('auth_user_id')

    if not auth_user_id:
        await message.answer("❌ Ошибка: данные авторизации не найдены. Начните процесс заново.")
        await state.clear()
        return

    # Отправляем ссылку пользователю
    try:
        await message.bot.send_message(
            auth_user_id,
            f"🔐 <b>Ссылка для авторизации в Ozon:</b>\n\n"
            f"{qr_link}\n\n"
            f"Откройте эту ссылку в приложении Ozon для завершения авторизации.",
            parse_mode="HTML"
        )

        # Отправляем кнопку подтверждения админу
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить авторизацию", callback_data=f"auth_confirm:{auth_user_id}")]
        ])

        await message.answer(
            f"✅ Ссылка отправлена пользователю {auth_user_id}\n\n"
            f"После успешной авторизации нажмите кнопку ниже:",
            reply_markup=keyboard
        )

        # Переходим в состояние ожидания подтверждения
        await state.set_state(AuthorizationInProgress.waiting_for_confirmation)

        logger.info(f"Ссылка для авторизации отправлена пользователю {auth_user_id}")

    except Exception as e:
        logger.error(f"Ошибка при отправке ссылки пользователю {auth_user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при отправке ссылки: {str(e)}")


# Обработчик подтверждения авторизации (админ)
@auth_ozon_router.callback_query(F.data.startswith("auth_confirm:"))
async def confirm_authorization(callback: types.CallbackQuery, state: FSMContext):
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} подтвердил авторизацию для пользователя {user_id}")

    # Получаем данные из FSM
    data = await state.get_data()
    auth_context = data.get('auth_context')

    if not auth_context:
        await callback.answer("❌ Ошибка: контекст авторизации не найден", show_alert=True)
        await state.clear()
        return

    try:
        # Закрываем браузер (профиль автоматически сохранится)
        browser = auth_context.get('browser')
        playwright = auth_context.get('playwright')

        if browser:
            await browser.close()
            logger.info(f"Браузер для пользователя {user_id} закрыт, профиль сохранён")

        if playwright:
            await playwright.stop()
            logger.info(f"Playwright экземпляр для пользователя {user_id} остановлен")

        # Обновляем статус в БД
        await update_auth_request_status(user_id, "approved")

        # Уведомляем пользователя об успехе
        await callback.bot.send_message(
            user_id,
            "✅ <b>Авторизация успешно завершена!</b>\n\n"
            "Ваш профиль сохранён. Теперь бот будет показывать актуальные цены из вашей корзины Ozon.",
            parse_mode="HTML"
        )

        # Уведомляем админа
        await callback.message.edit_text(
            f"✅ Пользователь {user_id} успешно авторизован в Ozon\n"
            f"Профиль сохранён."
        )

        # Очищаем состояние FSM
        await state.clear()

        logger.info(f"Авторизация пользователя {user_id} успешно завершена")

    except Exception as e:
        logger.error(f"Ошибка при подтверждении авторизации для пользователя {user_id}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка при сохранении профиля: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при сохранении профиля. Попробуйте позже или обратитесь к администратору."
        )

    await callback.answer()
