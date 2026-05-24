import logging
import re
from datetime import datetime

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import config
from keyboards.reply import main_kb, admin_kb
from keyboards.inline import get_products_keyboard, get_product_detail_keyboard
from utils.states import AddPosition, LoginState
from utils.maintenance import MaintenanceMode
from core.database import (
    add_product,
    get_all_products,
    get_product_details,
    delete_product,
    get_price_history,
    check_user_has_product,
    save_auth_request,
    get_auth_request,
    update_auth_request_status
)
from utils.price_updater import update_all_prices
from utils.link_parser import parse_product_link
from core.models import Marketplace


logger = logging.getLogger(__name__)
main_router = Router()


# Обработчик команды /start
@main_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /start от пользователя {user.id} (@{user.username})")
    await state.clear()

    keyboard = admin_kb if user.id == config.ADMIN_USER_ID else main_kb

    await message.answer(
        "Привет! Я бот для мониторинга цен. Выбери действие:",
        reply_markup=keyboard,
    )


# Обработчик кнопки "Добавить позицию"
@main_router.message(F.text == "Добавить позицию")
async def add_position(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} начал добавление товара")
    await message.answer("Отправь мне ссылку на товар, цену которого ты хочешь отслеживать.")
    await state.set_state(AddPosition.waiting_for_link)


# Обработчик кнопки "Список позиций"
@main_router.message(F.text == "Список позиций")
async def list_positions(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} запросил список товаров")

    # Очищаем состояние FSM, если пользователь был в процессе добавления товара
    await state.clear()

    products = await get_all_products(user.id)
    logger.info(f"Найдено товаров в БД для пользователя {user.id}: {len(products)}")

    if not products:
        await message.answer("Список позиций пуст. Добавьте товары для отслеживания.")
        return

    await message.answer(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products, marketplace_filter=None)
    )


# Обработчик кнопки "🔄 Обновить цены" (только для админа)
@main_router.message(F.text == "🔄 Обновить цены")
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


# Обработчик кнопки "🔐 Авторизация"
@main_router.message(F.text == "🔐 Авторизация")
async def authorization_button(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Пользователь {user.id} нажал кнопку Авторизация")
    await state.clear()

    await message.answer("📱 Отправьте ваш номер телефона в формате +79991234567")
    await state.set_state(LoginState.waiting_for_phone)


# Обработчик команды /resolve_captcha (только для админа)
@main_router.message(Command("resolve_captcha"))
async def resolve_captcha(message: types.Message, state: FSMContext):
    user = message.from_user

    if user.id != config.ADMIN_USER_ID:
        logger.warning(f"Попытка доступа к админ-команде от пользователя {user.id}")
        return

    await state.clear()

    # Проверяем, активен ли режим техработ
    if not MaintenanceMode.is_active():
        await message.answer("ℹ️ Режим техработ не активен. Всё работает нормально.")
        return

    logger.info(f"Админ {user.id} запустил команду /resolve_captcha")

    await message.answer(
        "🔧 Запускаю процесс решения капчи...\n\n"
        "Откроется браузер в режиме с интерфейсом. "
        "Решите капчу вручную, после чего бот автоматически продолжит работу.\n\n"
        "⏳ Ожидайте..."
    )

    # Временно включаем DEBUG_CAPTCHA
    original_debug = config.DEBUG_CAPTCHA
    config.DEBUG_CAPTCHA = True

    try:
        # Импортируем scraper
        from utils.scraper import scrape_ozon_product

        # Берём любой товар из БД для теста
        from core.database import get_all_products_for_update
        products = await get_all_products_for_update()

        if not products:
            await message.answer("❌ Нет товаров в БД для проверки капчи.")
            config.DEBUG_CAPTCHA = original_debug
            return

        test_url = products[0]['url']
        logger.info(f"Тестовый парсинг для решения капчи: {test_url}")

        # Пробуем спарсить товар (с ожиданием капчи)
        product_name, price = await scrape_ozon_product(test_url, bot=message.bot)

        if product_name and price:
            # Капча решена успешно
            logger.info("Капча решена успешно!")

            # Выключаем режим техработ
            MaintenanceMode.disable()

            # Уведомляем всех пользователей
            await message.answer("✅ Капча решена! Отправляю уведомления пользователям...")

            success, fail = await MaintenanceMode.notify_all_users(
                message.bot,
                "✅ Технические работы завершены!\n\n"
                "Бот снова работает в штатном режиме. "
                "Обновление цен возобновлено."
            )

            await message.answer(
                f"✅ Режим техработ отключён.\n\n"
                f"Уведомления отправлены: {success} успешно, {fail} ошибок."
            )
        else:
            await message.answer(
                "❌ Не удалось решить капчу или произошла ошибка.\n"
                "Попробуйте ещё раз."
            )

    except Exception as e:
        logger.error(f"Ошибка при решении капчи: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        # Возвращаем исходное значение DEBUG_CAPTCHA
        config.DEBUG_CAPTCHA = original_debug


# Обработчик команды /login
@main_router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /login от пользователя {user.id} (@{user.username})")
    await state.clear()

    await message.answer("📱 Отправьте ваш номер телефона в формате +79991234567")
    await state.set_state(LoginState.waiting_for_phone)


# Обработчик приёма номера телефона
@main_router.message(LoginState.waiting_for_phone, F.text)
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
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    text = (
        f"🔐 <b>Запрос на авторизацию</b>\n\n"
        f"Пользователь: @{username or 'без username'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Телефон: <code>{phone}</code>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Авторизовать", callback_data=f"auth_approve:{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"auth_reject:{user_id}")
        ]
    ])

    await bot.send_message(config.ADMIN_USER_ID, text, reply_markup=keyboard, parse_mode="HTML")


# Обработчик одобрения авторизации (админ)
@main_router.callback_query(F.data.startswith("auth_approve:"))
async def approve_authorization(callback: types.CallbackQuery):
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} одобрил авторизацию для пользователя {user_id}")

    # Получаем данные запроса
    request = await get_auth_request(user_id)

    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ Запускаю процесс авторизации для пользователя {user_id}...\n"
        f"Телефон: {request['phone']}"
    )

    try:
        # Запускаем процесс авторизации
        from scripts.authorize_user import authorize_user
        await authorize_user(user_id, request['phone'], callback.bot)

        await callback.message.edit_text(
            f"✅ Пользователь {user_id} успешно авторизован"
        )

    except TimeoutError as e:
        logger.error(f"Таймаут авторизации для пользователя {user_id}: {e}")
        await callback.message.edit_text(
            f"❌ Таймаут: пользователь {user_id} не завершил авторизацию вовремя"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Время авторизации истекло. Попробуйте позже или обратитесь к администратору."
        )

    except Exception as e:
        logger.error(f"Ошибка авторизации для пользователя {user_id}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка авторизации: {str(e)}"
        )
        await callback.bot.send_message(
            user_id,
            "❌ Не удалось завершить авторизацию. Попробуйте позже или обратитесь к администратору."
        )


# Обработчик отклонения авторизации (админ)
@main_router.callback_query(F.data.startswith("auth_reject:"))
async def reject_authorization(callback: types.CallbackQuery):
    user = callback.from_user

    if user.id != config.ADMIN_USER_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    logger.info(f"Админ {user.id} отклонил авторизацию для пользователя {user_id}")

    await callback.message.edit_text(f"❌ Запрос от пользователя {user_id} отклонён")

    await callback.bot.send_message(
        user_id,
        "❌ Ваш запрос на авторизацию отклонён администратором."
    )

    await update_auth_request_status(user_id, "rejected")
    await callback.answer()


@main_router.message(AddPosition.waiting_for_link, F.text)
async def process_link(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text

    logger.info(f"Пользователь {user.id} отправил текст: {text}")

    # Извлекаем URL и определяем маркетплейс
    url, marketplace = parse_product_link(text)

    if not url or not marketplace:
        logger.warning(f"Не удалось распарсить ссылку от пользователя {user.id}: {text}")
        await message.answer(
            "❌ Не удалось распознать ссылку на товар.\n"
            "Поддерживаются маркетплейсы: Ozon, Wildberries, Avito.\n"
            "Попробуй еще раз."
        )
        return

    logger.info(f"Распознан маркетплейс: {marketplace.value}, URL: {url}")

    # Проверяем, поддерживается ли маркетплейс (пока только Ozon)
    if marketplace != Marketplace.OZON:
        logger.info(f"Маркетплейс {marketplace.value} пока не поддерживается")
        await message.answer(
            f"🚧 Функционал для {marketplace.value.capitalize()} находится в разработке.\n"
            f"Пока доступен только Ozon."
        )
        await state.clear()
        return

    # Проверяем, не подписан ли пользователь уже на этот товар
    if await check_user_has_product(user.id, url):
        logger.warning(f"Пользователь {user.id} уже подписан на товар: {url}")
        await message.answer("⚠️ Этот товар уже добавлен в твой список.")
        await state.clear()
        return

    # Добавляем задачу в очередь с высоким приоритетом
    task_queue = message.bot.task_queue
    await task_queue.add_task(
        task_type="add_product",
        url=url,
        marketplace=marketplace.value,
        priority=1,  # Высокий приоритет
        user_id=user.id
    )

    await message.answer(
        "⏳ Товар добавлен в очередь обработки.\n"
        "Это может занять до 5 минут, ожидайте уведомления."
    )

    logger.info(f"Задача добавления товара добавлена в очередь для пользователя {user.id}")
    await state.clear()


# Эхо-обработчик (теперь он должен быть последним)
@main_router.message()
async def echo(message: types.Message):
    await message.answer(f"Неизвестная команда. Ты написал: {message.text}")


# Обработчик нажатия на товар в списке
@main_router.callback_query(F.data.startswith("product:"))
async def show_product_details(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} запросил детали товара ID: {product_id}")

    product = await get_product_details(product_id, user.id)

    if not product:
        logger.warning(f"Товар с ID {product_id} не найден в БД для пользователя {user.id}")
        await callback.answer("Товар не найден", show_alert=True)
        return

    # Форматируем даты
    date_added = datetime.fromisoformat(product['date_added']).strftime('%d.%m.%Y')
    min_price_date = datetime.fromisoformat(product['min_price_date']).strftime('%d.%m.%Y %H:%M')
    max_price_date = datetime.fromisoformat(product['max_price_date']).strftime('%d.%m.%Y %H:%M')

    text = (
        f"📦 {product['name']}\n\n"
        f"📅 Начало отслеживания: {date_added}\n\n"
        f"💰 Текущая цена: {product['current_price']} ₽\n"
        f"📉 Минимальная цена: {product['min_price']} ₽ ({min_price_date})\n"
        f"📈 Максимальная цена: {product['max_price']} ₽ ({max_price_date})\n\n"
        f"🔗 {product['url']}"
    )

    await callback.message.answer(
        text,
        reply_markup=get_product_detail_keyboard(product_id)
    )
    await callback.answer()


# Обработчик фильтров маркетплейсов
@main_router.callback_query(F.data.startswith("filter:"))
async def filter_products(callback: types.CallbackQuery):
    user = callback.from_user
    filter_value = callback.data.split(":")[1]
    logger.info(f"Пользователь {user.id} выбрал фильтр: {filter_value}")

    # Определяем маркетплейс для фильтрации
    marketplace = None if filter_value == "all" else filter_value

    products = await get_all_products(user.id, marketplace=marketplace)
    logger.info(f"Найдено товаров после фильтрации: {len(products)}")

    if not products:
        await callback.answer(
            "Нет товаров для выбранного маркетплейса",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products, marketplace_filter=marketplace)
    )
    await callback.answer()


# Обработчик кнопки "Назад"
@main_router.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery):
    products = await get_all_products()

    if not products:
        await callback.message.edit_text("Список позиций пуст.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 Список отслеживаемых товаров:",
        reply_markup=get_products_keyboard(products)
    )
    await callback.answer()


# Обработчик удаления товара
@main_router.callback_query(F.data.startswith("delete:"))
async def delete_product_handler(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} удаляет товар ID: {product_id}")

    success = await delete_product(product_id, user.id)

    if success:
        logger.info(f"Товар ID {product_id} успешно удален для пользователя {user.id}")

        # Загружаем обновленный список товаров
        products = await get_all_products(user.id)

        if not products:
            # Если список пуст, показываем соответствующее сообщение
            await callback.message.edit_text("Список позиций пуст. Добавьте товары для отслеживания.")
        else:
            # Показываем обновленный список товаров
            await callback.message.edit_text(
                "📋 Список отслеживаемых товаров:",
                reply_markup=get_products_keyboard(products, marketplace_filter=None)
            )

        await callback.answer("✅ Товар удален", show_alert=False)
    else:
        logger.warning(f"Товар ID {product_id} не найден для пользователя {user.id}")
        await callback.answer("❌ Товар не найден", show_alert=True)


# Обработчик просмотра истории цен
@main_router.callback_query(F.data.startswith("history:"))
async def show_price_history(callback: types.CallbackQuery):
    user = callback.from_user
    product_id = int(callback.data.split(":")[1])
    logger.info(f"Пользователь {user.id} запросил историю цен товара ID: {product_id}")

    history = await get_price_history(product_id)

    if not history:
        await callback.answer("История цен пуста", show_alert=True)
        return

    text = "📊 История цен:\n\n"
    for entry in history:
        date_time = datetime.fromisoformat(entry['date_checked']).strftime('%d.%m.%Y %H:%M')
        text += f"• {date_time} — {entry['price']} ₽\n"

    await callback.message.answer(text)
    await callback.answer()
