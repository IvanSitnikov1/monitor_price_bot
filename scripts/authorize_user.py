from aiogram import Bot

from config import config


async def authorize_user(user_id: int, phone: str, bot: Bot, browser_manager):
    """
    Запускает процесс авторизации пользователя через QR-код Ozon.
    Открывает новую вкладку в браузере пользователя.

    Args:
        user_id: ID пользователя Telegram
        phone: Номер телефона пользователя
        bot: Экземпляр бота для отправки сообщений
        browser_manager: Менеджер браузеров

    Returns:
        Контекст авторизации для дальнейшей работы

    Raises:
        Exception: Ошибки в процессе открытия браузера
    """
    page = await browser_manager.open_authorization_page(
        user_id=user_id,
        marketplace="ozon",
        url="https://www.ozon.ru/"
    )

    # Уведомляем админа о необходимости ручных действий
    await bot.send_message(
        config.ADMIN_USER_ID,
        f"🔐 Браузер открыт для пользователя {user_id}\n"
        f"Телефон: {phone}\n\n"
        f"<b>Выполните следующие действия:</b>\n"
        f"1. Нажмите 'Войти' на странице\n"
        f"2. Введите номер телефона: <code>{phone}</code>\n"
        f"3. Дождитесь появления QR-кода\n"
        f"4. Отсканируйте QR-код и извлеките из него ссылку\n"
        f"5. Отправьте ссылку в этот чат\n"
        f"6. После успешной авторизации нажмите кнопку 'Подтвердить'",
        parse_mode="HTML"
    )

    # Возвращаем управление — браузер остаётся открытым
    return {
        'page': page,
        'browser_manager': browser_manager,
        'user_id': user_id,
        'phone': phone
    }
