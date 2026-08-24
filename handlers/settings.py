import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from core.database import (
    get_notification_settings,
    get_product_details,
    register_user,
    set_drop_threshold,
    set_product_target_price,
    update_notification_mode,
)
from keyboards.inline import (
    get_notification_mode_keyboard,
    get_notification_settings_keyboard,
)
from loggers import logger
from utils.states import NotificationSettings


settings_router = Router()

_MODE_NAMES = {
    "standard": "Стандарт",
    "drop_bounds": "Границы снижения",
    "target_price": "Целевая цена",
}


def _format_threshold(settings: dict) -> str:
    """Форматирует текущий порог для описания режима."""
    threshold_type = settings.get("drop_threshold_type")
    threshold_value = settings.get("drop_threshold_value")
    if not threshold_type or threshold_value is None:
        return "не задан"

    if threshold_type == "percent":
        return f"{float(threshold_value):g}%"

    return f"{int(threshold_value)} ₽"


def _mode_status(settings: dict, mode: str) -> str:
    """Возвращает текст текущего состояния режима."""
    return "включен" if settings.get(f"{mode}_enabled") else "выключен"


def _settings_menu_text(settings: dict) -> str:
    """Формирует текст общего меню настроек."""
    return (
        "⚙️ <b>Настройки уведомлений</b>\n\n"
        f"Стандарт: {_mode_status(settings, 'standard')}\n"
        f"Границы снижения: {_mode_status(settings, 'drop_bounds')}\n"
        f"Целевая цена: {_mode_status(settings, 'target_price')}\n\n"
        "Выберите режим для просмотра описания и изменения его состояния."
    )


def _mode_text(mode: str, settings: dict) -> str:
    """Формирует описание выбранного режима уведомлений."""
    status = _mode_status(settings, mode)

    match mode:
        case "standard":
            description = (
                "Уведомляет о снижении цены относительно предыдущего замера. "
                "Уведомления о пропаже и возвращении товаров этим режимом "
                "не отключаются."
            )
        case "drop_bounds":
            description = (
                "Фильтрует уведомления о снижении цены по одному выбранному порогу. "
                f"Текущий порог: {_format_threshold(settings)}. "
                "Процент считается от предыдущего замера."
            )
        case "target_price":
            description = (
                "Позволяет задать ожидаемую цену в карточке каждого товара. "
                "При достижении цели бот отправит уведомление один раз. "
                "При превышении цены и обратном понижении уведомление "
                "отправится снова."
            )
        case _:
            description = "Неизвестный режим уведомлений."

    return (
        f"⚙️ <b>{_MODE_NAMES.get(mode, 'Настройки')}</b>\n\n"
        f"Состояние: <b>{status}</b>\n\n"
        f"{description}"
    )


async def _show_settings_menu(message: types.Message, settings: dict) -> None:
    """Показывает общее меню настроек."""
    await message.answer(
        _settings_menu_text(settings),
        parse_mode="HTML",
        reply_markup=get_notification_settings_keyboard(settings),
    )


async def _show_settings_mode(callback: types.CallbackQuery, mode: str) -> None:
    """Обновляет сообщение описанием выбранного режима."""
    settings = await get_notification_settings(callback.from_user.id)
    if mode not in _MODE_NAMES:
        await callback.answer("Неизвестный режим", show_alert=True)
        return

    await callback.message.edit_text(
        _mode_text(mode, settings),
        parse_mode="HTML",
        reply_markup=get_notification_mode_keyboard(mode, settings),
    )
    await callback.answer()


@settings_router.message(F.text == "Настройки")
async def open_settings(message: types.Message, state: FSMContext):
    """Открывает пользовательские настройки уведомлений."""
    user = message.from_user
    await state.clear()
    await register_user(user.id, user.username, user.first_name)
    settings = await get_notification_settings(user.id)
    logger.info(f"Пользователь {user.id} открыл настройки уведомлений")
    await _show_settings_menu(message, settings)


@settings_router.callback_query(F.data == "settings:menu")
async def settings_menu_callback(callback: types.CallbackQuery):
    """Возвращает пользователя в общее меню настроек."""
    settings = await get_notification_settings(callback.from_user.id)
    await callback.message.edit_text(
        _settings_menu_text(settings),
        parse_mode="HTML",
        reply_markup=get_notification_settings_keyboard(settings),
    )
    await callback.answer()


@settings_router.callback_query(F.data.startswith("settings:mode:"))
async def settings_mode_callback(callback: types.CallbackQuery):
    """Показывает описание выбранного режима."""
    mode = callback.data.split(":", 2)[2]
    await _show_settings_mode(callback, mode)


@settings_router.callback_query(F.data.startswith("settings:toggle:"))
async def settings_toggle_callback(callback: types.CallbackQuery):
    """Переключает выбранный режим уведомлений."""
    mode = callback.data.split(":", 2)[2]
    if mode not in _MODE_NAMES:
        await callback.answer("Неизвестный режим", show_alert=True)
        return

    settings = await get_notification_settings(callback.from_user.id)
    enabled = not bool(settings.get(f"{mode}_enabled"))
    await update_notification_mode(callback.from_user.id, mode, enabled)
    logger.info(
        f"Пользователь {callback.from_user.id} {'включил' if enabled else 'выключил'} "
        f"режим уведомлений {mode}"
    )
    await _show_settings_mode(callback, mode)


@settings_router.callback_query(F.data.startswith("settings:threshold:"))
async def settings_threshold_callback(callback: types.CallbackQuery, state: FSMContext):
    """Запрашивает новое значение порога снижения."""
    threshold_type = callback.data.split(":", 2)[2]
    if threshold_type == "percent":
        await state.set_state(NotificationSettings.waiting_for_drop_percent)
        prompt = (
            "Введите процент снижения от 0 до 100.\n"
            "Введите 0, чтобы очистить текущий порог."
        )
    elif threshold_type == "amount":
        await state.set_state(NotificationSettings.waiting_for_drop_amount)
        prompt = (
            "Введите сумму снижения в рублях.\n"
            "Введите 0, чтобы очистить текущий порог."
        )
    else:
        await callback.answer("Неизвестный тип порога", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(prompt)


@settings_router.callback_query(F.data.startswith("target_price:"))
async def target_price_callback(callback: types.CallbackQuery, state: FSMContext):
    """Запрашивает целевую цену принадлежащего пользователю товара."""
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный товар", show_alert=True)
        return

    user_id = callback.from_user.id
    product = await get_product_details(product_id, user_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    settings = await get_notification_settings(user_id)
    if not settings.get("target_price_enabled"):
        await callback.answer("Сначала включите режим «Целевая цена»", show_alert=True)
        return

    await state.update_data(target_product_id=product_id)
    await state.set_state(NotificationSettings.waiting_for_target_price)
    current_target = product.get("target_price")
    current_text = (
        f"Текущая цель: {current_target} ₽\n" if current_target is not None else "Цель не задана.\n"
    )
    await callback.answer()
    await callback.message.answer(
        f"🎯 {current_text}"
        "Введите целевую цену в рублях.\n"
        "Введите 0, чтобы очистить цель."
    )


@settings_router.message(NotificationSettings.waiting_for_drop_percent, F.text)
async def process_drop_percent(message: types.Message, state: FSMContext):
    """Сохраняет введенный процентный порог."""
    value_text = message.text.strip().replace(",", ".")
    try:
        value = float(value_text)
    except ValueError:
        await message.answer("Введите число от 0 до 100.")
        return

    if value < 0 or value > 100:
        await message.answer("Процент должен быть от 0 до 100.")
        return

    try:
        await set_drop_threshold(message.from_user.id, "percent", value)
    except ValueError as error:
        await message.answer(str(error))
        return

    await state.clear()
    settings = await get_notification_settings(message.from_user.id)
    await message.answer(
        "✅ Процентный порог сохранен.\n\n"
        + _mode_text("drop_bounds", settings),
        parse_mode="HTML",
        reply_markup=get_notification_mode_keyboard("drop_bounds", settings),
    )


@settings_router.message(NotificationSettings.waiting_for_drop_amount, F.text)
async def process_drop_amount(message: types.Message, state: FSMContext):
    """Сохраняет введенный порог суммы."""
    value_text = re.sub(r"\s+", "", message.text.strip())
    if not value_text.isdigit():
        await message.answer("Введите положительное целое число рублей или 0.")
        return

    value = int(value_text)
    try:
        await set_drop_threshold(message.from_user.id, "amount", value)
    except ValueError as error:
        await message.answer(str(error))
        return

    await state.clear()
    settings = await get_notification_settings(message.from_user.id)
    await message.answer(
        "✅ Порог суммы сохранен.\n\n"
        + _mode_text("drop_bounds", settings),
        parse_mode="HTML",
        reply_markup=get_notification_mode_keyboard("drop_bounds", settings),
    )


@settings_router.message(NotificationSettings.waiting_for_target_price, F.text)
async def process_target_price(message: types.Message, state: FSMContext):
    """Сохраняет или очищает целевую цену товара."""
    value_text = re.sub(r"\s+", "", message.text.strip())
    if not value_text.isdigit():
        await message.answer("Введите положительное целое число рублей или 0.")
        return

    value = int(value_text)
    data = await state.get_data()
    product_id = data.get("target_product_id")
    if not product_id:
        await state.clear()
        await message.answer("Товар для настройки не найден.")
        return

    settings = await get_notification_settings(message.from_user.id)
    if not settings.get("target_price_enabled"):
        await state.clear()
        await message.answer("Режим «Целевая цена» выключен.")
        return

    target_price = value or None
    success = await set_product_target_price(
        product_id,
        message.from_user.id,
        target_price,
    )
    await state.clear()

    if not success:
        await message.answer("Товар не найден или он вам не принадлежит.")
        return

    if target_price is None:
        await message.answer("✅ Целевая цена очищена.")
        return

    await message.answer(f"✅ Целевая цена установлена: {target_price} ₽")
