import logging

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import config
from utils.states import BroadcastState
from utils.maintenance import MaintenanceMode
from core.database import broadcast_to_users
from utils.price_updater import update_all_prices


logger = logging.getLogger(__name__)
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


# Обработчик команды /resolve_captcha (только для админа)
@admin_router.message(Command("resolve_captcha"))
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
        product_name, price = await scrape_ozon_product(test_url)

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
