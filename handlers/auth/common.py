import re
import shutil
from io import BytesIO
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from core.database import delete_user_auth_data, get_auth_request, save_auth_request
from core.models import Marketplace
from keyboards.inline import get_auth_menu_keyboard
from loggers import logger
from utils.states import LoginState


auth_common_router = Router()


async def notify_admin_auth_request(
    bot,
    user_id: int,
    username: str | None,
    phone: str,
    marketplace: Marketplace,
):
    """Отправляет админу уведомление о запросе авторизации Wildberries."""
    text = (
        f"🔐 <b>Запрос на авторизацию {marketplace}</b>\n\n"
        f"Пользователь: @{username or 'без username'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Телефон: <code>{phone}</code>\n\n"
        f"✅ Пользователь принял соглашение"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Авторизовать {marketplace}",
                callback_data=f"auth_{marketplace}_approve:{user_id}"
            ),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"auth_{marketplace}_reject:{user_id}")
        ]
    ])

    await bot.send_message(config.ADMIN_USER_ID, text, reply_markup=keyboard, parse_mode="HTML")


async def _show_auth_marketplace_menu(message: types.Message):
    """Показывает меню выбора маркетплейса для авторизации."""
    auth_menu_text = (
        "🔐 <b>Управление авторизацией</b>\n\n"
        "Выберите маркетплейс для авторизации.\n"
        "Для Avito авторизация не требуется."
    )
    await message.answer(
        auth_menu_text,
        reply_markup=get_auth_menu_keyboard(),
        parse_mode="HTML"
    )


async def _send_common_agreement(message: types.Message):
    """Отправляет общее соглашение перед выбором маркетплейса."""
    agreement_text = (
        "🔐 <b>Авторизация в маркетплейсах</b>\n\n"
        "Для корректного отображения цен бот может использовать вашу авторизацию "
        "в Ozon и Wildberries.\n\n"
        "<b>Что нужно знать:</b>\n"
        "• Это неофициальный сервис\n"
        "• Данные авторизации хранятся на сервере администратора\n"
        "• Авторизацию Wildberries и Ozon администратор выполняет вручную\n"
        "• Для Avito авторизация не требуется\n"
        "• Бот и администратор не запрашивают и не хранят платежные реквизиты и не имеют "
        "к ним доступа в рамках авторизации\n"
        "• Вы можете удалить данные авторизации в любой момент\n\n"
        "📄 Подробная информация о рисках и обработке данных во вложенном файле.\n\n"
        "📞 Вопросы: @Ivanches1\n\n"
        "❓ <b>Продолжить?</b>"
    )

    disclaimer_text = (
        "⚖️ ЮРИДИЧЕСКИЙ DISCLAIMER И ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ\n"
        "Дата: 17.06.2026\n\n"
        "Этот бот является независимым сервисом для мониторинга цен и не является "
        "официальным продуктом Ozon, Wildberries или Avito.\n\n"
        "При авторизации обрабатываются:\n"
        "• Номер телефона\n"
        "• Cookies сессии Ozon и/или Wildberries\n"
        "• Содержимое корзин, цены товаров и история изменений\n\n"
        "Бот и администратор не запрашивают и не хранят номера банковских карт, "
        "сроки их действия, CVC/CVV и иные платежные реквизиты. В рамках авторизации "
        "администратор не получает доступ к этим данным.\n\n"
        "Данные хранятся на сервере бота. Сервер принадлежит администратору. "
        "Администратор технически имеет доступ к профилям браузера и cookies.\n\n"
        "Используя авторизацию, вы принимаете риски технических сбоев, временных "
        "ограничений доступа, неверного отображения цен и возможной компрометации "
        "данных при компрометации сервера.\n\n"
        "Вы можете удалить данные авторизации через кнопку "
        "\"🗑 Удалить мои данные\" в разделе авторизации.\n\n"
        "Сервис предоставляется \"как есть\". Используя его, вы действуете "
        "добровольно и принимаете риски на себя.\n\n"
        "Контакты: @Ivanches1"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принимаю условия", callback_data="auth_agreement_accept"),
            InlineKeyboardButton(text="❌ Отклоняю", callback_data="auth_agreement_decline")
        ]
    ])

    disclaimer_file = BytesIO(disclaimer_text.encode("utf-8"))
    disclaimer_file.name = "Пользовательское_соглашение.txt"

    await message.answer_document(
        document=types.BufferedInputFile(
            disclaimer_file.getvalue(),
            filename="Пользовательское_соглашение.txt"
        ),
        caption="📄 Детальная информация о рисках и условиях использования"
    )
    await message.answer(agreement_text, reply_markup=keyboard, parse_mode="HTML")


@auth_common_router.message(F.text == "🔐 Авторизация")
async def authorization_button(message: types.Message, state: FSMContext):
    """Открывает раздел управления авторизацией."""
    user = message.from_user
    logger.info(f"Пользователь {user.id} открыл раздел авторизации")
    await state.clear()

    auth_request = await get_auth_request(user.id)
    if auth_request and auth_request.get("agreement_accepted"):
        await _show_auth_marketplace_menu(message)
        return

    await _send_common_agreement(message)


@auth_common_router.callback_query(F.data == "auth_start")
async def start_authorization(callback: types.CallbackQuery, state: FSMContext):
    """Начинает общий сценарий авторизации."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} начал процесс авторизации")

    await _send_common_agreement(callback.message)
    await callback.message.delete()
    await callback.answer()


@auth_common_router.callback_query(F.data == "auth_agreement_accept")
async def accept_agreement(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает принятие общего соглашения."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} принял соглашение авторизации")

    await callback.message.edit_text(
        "✅ Соглашение принято.\n\n"
        "Теперь выберите маркетплейс для авторизации.\n"
        "Для Avito авторизация не требуется.",
        reply_markup=get_auth_menu_keyboard(),
        parse_mode="HTML"
    )

    await callback.answer()


@auth_common_router.callback_query(F.data == "auth_agreement_decline")
async def decline_agreement(callback: types.CallbackQuery):
    """Обрабатывает отказ от общего соглашения."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} отклонил соглашение авторизации")

    await callback.message.edit_text(
        "❌ Вы отклонили условия авторизации.\n\n"
        "⚠️ Без авторизации цены товаров Ozon и Wildberries могут отображаться "
        "некорректно. Для Avito авторизация не требуется.\n\n"
        "Вы можете вернуться к авторизации позже через кнопку 🔐 Авторизация."
    )
    await callback.answer()


@auth_common_router.callback_query(F.data == "auth_ozon_start")
async def start_ozon_phone_request(callback: types.CallbackQuery, state: FSMContext):
    """Запрашивает телефон для авторизации Ozon."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} выбрал авторизацию Ozon")

    await state.update_data(auth_marketplace="ozon")
    await state.set_state(LoginState.waiting_for_phone)

    await callback.message.edit_text(
        "🔐 <b>Авторизация в Ozon</b>\n\n"
        "📱 Отправьте ваш номер телефона в формате +79991234567",
        parse_mode="HTML"
    )
    await callback.answer()


@auth_common_router.callback_query(F.data == "auth_wb_start")
async def start_wildberries_phone_request(callback: types.CallbackQuery, state: FSMContext):
    """Запрашивает телефон для авторизации Wildberries."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} выбрал авторизацию Wildberries")

    await state.update_data(auth_marketplace="wildberries")
    await state.set_state(LoginState.waiting_for_phone)

    await callback.message.edit_text(
        "🔐 <b>Авторизация в Wildberries</b>\n\n"
        "📱 Отправьте ваш номер телефона в формате +79991234567.\n\n"
        "После этого администратор вручную выполнит вход в Wildberries.",
        parse_mode="HTML"
    )
    await callback.answer()


@auth_common_router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    """Открывает раздел авторизации по команде."""
    await authorization_button(message, state)


@auth_common_router.callback_query(F.data == "auth_delete_data")
async def delete_user_data(callback: types.CallbackQuery):
    """Запрашивает подтверждение удаления данных авторизации."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} запросил удаление данных")

    confirmation_text = (
        "🗑 <b>Удаление данных авторизации</b>\n\n"
        "Будут удалены:\n"
        "• Профиль браузера с cookies Ozon и Wildberries\n"
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


@auth_common_router.callback_query(F.data == "auth_delete_confirm")
async def confirm_delete_data(callback: types.CallbackQuery):
    """Удаляет данные авторизации пользователя."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} подтвердил удаление данных")

    try:
        await delete_user_auth_data(user.id)

        profile_path = Path(f"user_data/{user.id}")
        if profile_path.exists():
            shutil.rmtree(profile_path)
            logger.info(f"Профиль пользователя {user.id} удалён с диска")

        await callback.message.edit_text(
            "✅ <b>Данные успешно удалены</b>\n\n"
            "Удалено:\n"
            "• Профиль браузера с cookies Ozon и Wildberries\n"
            "• Запросы на авторизацию\n"
            "• Согласие на обработку данных\n\n"
            "Ваши товары и история цен сохранены.\n\n"
            "Вы можете авторизоваться заново через кнопку 🔐 Авторизация.",
            parse_mode="HTML"
        )

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


@auth_common_router.callback_query(F.data == "auth_delete_cancel")
async def cancel_delete_data(callback: types.CallbackQuery):
    """Отменяет удаление данных авторизации."""
    user = callback.from_user
    logger.info(f"Пользователь {user.id} отменил удаление данных")

    await callback.message.edit_text(
        "❌ Удаление данных отменено.\n\n"
        "Ваши данные авторизации сохранены.",
        parse_mode="HTML"
    )
    await callback.answer()


@auth_common_router.message(LoginState.waiting_for_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext):
    """Принимает телефон и отправляет запрос администратору."""
    user = message.from_user
    phone = message.text.strip()
    state_data = await state.get_data()
    marketplace = state_data.get("auth_marketplace", "ozon")

    logger.info(f"Пользователь {user.id} отправил номер телефона: {phone}")

    if not re.match(r"^\+7\d{10}$", phone):
        await message.answer(
            "❌ Неверный формат номера телефона.\n"
            "Используйте формат: +79991234567"
        )
        return

    await save_auth_request(
        user_id=user.id,
        phone=phone,
        username=user.username
    )

    await notify_admin_auth_request(
        bot=message.bot,
        user_id=user.id,
        username=user.username,
        phone=phone,
        marketplace=marketplace,
    )

    if marketplace == Marketplace.OZON:
        await message.answer(
            "✅ Запрос на авторизацию отправлен администратору.\n"
            "Ожидайте ссылку для авторизации."
        )
        await state.clear()

    if marketplace == Marketplace.WILDBERRIES:
        await message.answer(
            "✅ Запрос на авторизацию отправлен администратору.\n"
            "Ожидайте код авторизации в уведомлениях приложения Wildberries или в смс.\n"
            "Отправьте полученный код в чат."
        )
        await state.set_state(LoginState.waiting_for_wb_code)


@auth_common_router.message(LoginState.waiting_for_wb_code, F.text)
async def process_wb_code(message: types.Message, state: FSMContext):
    """Принимает код авторизации wb и отправляет администратору."""
    user = message.from_user

    await message.bot.send_message(
        chat_id=config.ADMIN_USER_ID,
        text=(
            "📩 <b>Код авторизации Wildberries</b>\n\n"
            f"👤 Пользователь: @{user.username or 'без username'}\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"🔐 Код: <code>{message.text}</code>"
        ),
        parse_mode="HTML",
    )

    await message.answer(
        "✅ Код отправлен администратору.\n"
        "Ожидайте завершения авторизации."
    )

    await state.clear()
