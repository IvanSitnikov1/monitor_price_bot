from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from datetime import datetime

from config import config
from keyboards.reply import main_kb, admin_kb
from core.database import get_user_subscription_details, register_user
from loggers import logger


common_router = Router()


def _format_subscription_date(date_value: str | None) -> str:
    """Форматирует дату подписки для пользователя."""
    if not date_value:
        return "отсутствует"

    try:
        return datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    except ValueError:
        return date_value


def _format_subscription_status(subscription: dict) -> str:
    """Формирует пользовательский статус подписки."""
    match subscription["status"]:
        case "active":
            return "активна"
        case "expired":
            return "истекла"
        case _:
            return "без подписки"


# Обработчик команды /start
@common_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /start от пользователя {user.id} (@{user.username})")
    await state.clear()

    # Регистрируем пользователя в БД
    await register_user(user.id, user.username, user.first_name)
    logger.info(f"Пользователь {user.id} зарегистрирован в системе")

    keyboard = admin_kb if user.id == config.ADMIN_USER_ID else main_kb

    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического мониторинга цен на Ozon, Wildberries и Avito.\n\n"
        "📋 <b>Как это работает:</b>\n\n"
        "1️⃣ <b>Добавьте товары</b>\n"
        "🛒 <b>Ozon и Wildberries</b>\n"
        "Для каждого маркетплейса я отслеживаю одну общую корзину. Новая ссылка на корзину "
        "заменяет предыдущую ссылку этого же маркетплейса, а не добавляется к ней. Цены и "
        "уведомления обновляются только для товаров, которые находятся в текущей корзине.\n\n"
        "📦 <b>Avito</b>\n"
        "Объявления добавляются по одному — отдельной прямой ссылкой. Новое объявление не "
        "заменяет добавленные ранее.\n\n"
        "2️⃣ <b>Автоматическое обновление</b>\n"
        "Я проверяю цены 3 раза в день: в течение 2 часов после 7:00, 15:00 и 22:00 МСК, "
        "и сохраняю историю изменений.\n\n"
        "3️⃣ <b>Уведомления о скидках</b>\n"
        "Если цена опустится ниже минимальной за всё время — вы сразу получите уведомление! 🔔\n\n"
        "4️⃣ <b>История цен</b>\n"
        "Просматривайте динамику изменения цен и принимайте решение о покупке в нужный момент.\n\n"
        "⚠️ <b>Важно:</b> Пользоваться можно и без авторизации в Ozon и Wildberries, но без нее f"
        "цены могут отображаться некорректно. Для Avito авторизация не требуется.\n"
        "Авторизоваться можно через кнопку "
        "🔐 Авторизация.\n\n"
        "❓ Нажмите <b>Помощь</b> для получения подробной информации."
    )

    await message.answer(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# Обработчик команды /help и кнопки "❓ Помощь"
@common_router.message(Command("help"))
@common_router.message(F.text == "❓ Помощь")
async def cmd_help(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /help от пользователя {user.id}")
    await state.clear()

    help_text = (
        "📖 <b>Руководство по использованию бота</b>\n\n"

        "🛒 <b>Как работают корзины Ozon и Wildberries</b>\n\n"
        "• Для каждого маркетплейса сохраняется одна активная общая корзина: одна для Ozon и "
        "одна для Wildberries.\n"
        "• Новая ссылка Ozon заменяет предыдущую корзину Ozon, но не влияет на Wildberries. "
        "Для Wildberries действует то же правило.\n"
        "• Новая корзина не дополняет старый список. Перед отправкой ссылки соберите в корзине "
        "все товары, которые хотите продолжать отслеживать.\n"
        "• Бот обновляет цены и отправляет уведомления только по товарам из текущей активной "
        "корзины.\n"
        "• Если товара больше нет в активной корзине, он получает отметку ❌ и перестаёт "
        "отслеживаться. Последняя цена и история сохраняются, но новые цены и уведомления для "
        "него не поступают.\n"
        "• Если бот позднее распознает тот же товар в корзине, отслеживание возобновится, а "
        "прежняя история цен сохранится.\n"
        "• Товары сопоставляются по названию. Другой размер или вариант с отличающимся "
        "названием будет добавлен как новый товар с отдельной историей. Одинаковые названия "
        "разных вариантов могут быть распознаны как один товар.\n\n"

        "<b>Пример:</b> в старой корзине Ozon было пять товаров, а в новой ссылке осталось "
        "три. Бот будет отслеживать эти три товара. Два остальных останутся в списке с "
        "отметкой ❌ и сохранённой историей, но их цены больше не будут обновляться.\n\n"

        "📦 <b>Как работает Avito</b>\n\n"
        "• Каждое объявление добавляется отдельно по прямой ссылке.\n"
        "• Новая ссылка Avito не заменяет и не отключает объявления, добавленные ранее.\n"
        "• Цена каждого объявления обновляется независимо.\n"
        "• Для Avito не нужна авторизация.\n\n"

        "📊 <b>Просмотр товаров:</b>\n"
        "• Товар без отметки ❌ отслеживается: цена обновляется, уведомления включены.\n"
        "• ❌ перед названием означает, что товар не отслеживается. Отображаются только "
        "последняя сохранённая цена и прежняя история.\n"
        "• Нажмите на товар для просмотра детальной информации\n"
        "• Текущая/минимальная/максимальная цены\n"
        "• <b>История цен</b> — все изменения с датами\n"
        "• Кнопка <b>🗑 Удалить</b> полностью удаляет товар и всю историю цен.\n\n"

        "⏰ <b>Автоматическое обновление:</b>\n"
        "Цены обновляются 3 раза в день в случайное время внутри окон:\n"
        "• 7:00–9:00 МСК\n"
        "• 15:00–17:00 МСК\n"
        "• 22:00–00:00 МСК\n\n"

        "🔔 <b>Уведомления:</b>\n"
        "Вы получите сообщение, когда цена отслеживаемого товара упадёт ниже исторического "
        "минимума.\n\n"

        "🔐 <b>Авторизация:</b>\n"
        "⚠️ <b>Важно!</b> Без авторизации цены Ozon и Wildberries могут отображаться "
        "некорректно.\n\n"
        "Для корректной работы бот может использовать ваши профили Ozon и Wildberries. "
        "Авторизация выполняется отдельно для каждого маркетплейса и требует принятия "
        "пользовательского соглашения. Для Avito авторизация не требуется.\n\n"
        "<b>Что нужно знать:</b>\n"
        "• Это неофициальный сервис\n"
        "• Данные авторизации хранятся на сервере\n"
        "• Бот и администратор не получают доступ к платежным реквизитам\n"
        "• Администратор несёт ограниченную ответственность\n"
        "• Подробности — при авторизации\n\n"

        "⚠️ <b>Возможные проблемы:</b>\n"
        "Возможно временное приостановление обновления цен, которое требует ручного вмешательства.\n"
        "Администратор устранит проблему в ближайшее время.\n\n"

        "Если остались вопросы, пишите в ЛС - @Ivanches1"
    )

    await message.answer(help_text, parse_mode="HTML")


@common_router.message(Command("subscription"))
async def cmd_subscription(message: types.Message, state: FSMContext):
    """Показывает пользователю состояние подписки."""
    user = message.from_user
    logger.info(f"Команда /subscription от пользователя {user.id}")
    await state.clear()

    await register_user(user.id, user.username, user.first_name)
    subscription = await get_user_subscription_details(user.id)

    if not subscription:
        await message.answer("Не удалось получить данные подписки. Попробуйте позже.")
        return

    status = _format_subscription_status(subscription)
    paid_until = subscription.get("paid_until")
    paid_until_text = (
        _format_subscription_date(paid_until)
        if paid_until else "подписка отсутствует"
    )

    text = (
        "💳 Подписка\n\n"
        f"Статус: {status}\n"
        f"Дата окончания: {paid_until_text}\n\n"
        "Пока бот работает бесплатно, существующие функции доступны без подписки."
    )

    await message.answer(text)


# Эхо-обработчик (должен быть последним при регистрации)
@common_router.message()
async def echo(message: types.Message):
    await message.answer(f"Неизвестная команда. Ты написал: {message.text}")
