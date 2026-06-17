from aiogram import Bot

from config import config


async def start_wildberries_authorization(user_id: int, phone: str, bot: Bot, browser_manager):
    """
    Открывает Wildberries в новой вкладке браузера пользователя.

    Args:
        user_id: ID пользователя Telegram
        phone: Номер телефона пользователя
        bot: Экземпляр бота
        browser_manager: Менеджер браузеров

    Returns:
        Контекст авторизации.
    """
    page = await browser_manager.open_authorization_page(
        user_id=user_id,
        marketplace="wildberries",
        url="https://www.wildberries.ru/"
    )

    await bot.send_message(
        config.ADMIN_USER_ID,
        f"🔐 Запущена авторизация Wildberries для пользователя {user_id}\n"
        f"Телефон: <code>{phone}</code>\n\n"
        "Выполните вход вручную: нажмите вход, введите телефон и код "
        "из уведомления приложения Wildberries.",
        parse_mode="HTML"
    )

    return {
        "page": page,
        "browser_manager": browser_manager,
        "user_id": user_id,
        "phone": phone,
    }
