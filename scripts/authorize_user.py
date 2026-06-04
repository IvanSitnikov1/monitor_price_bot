import asyncio
from pathlib import Path

from playwright.async_api import async_playwright
from aiogram import Bot

from core.database import update_auth_request_status
from config import config


async def authorize_user(user_id: int, phone: str, bot: Bot):
    """
    Запускает процесс авторизации пользователя через QR-код Ozon.
    Открывает браузер и оставляет его для ручной авторизации админом.

    Args:
        user_id: ID пользователя Telegram
        phone: Номер телефона пользователя
        bot: Экземпляр бота для отправки сообщений

    Returns:
        Контекст браузера для дальнейшей работы

    Raises:
        Exception: Ошибки в процессе открытия браузера
    """
    profile_path = Path(f"user_data/{user_id}").absolute()
    profile_path.mkdir(parents=True, exist_ok=True)

    # Создаём playwright без async with, чтобы браузер оставался открытым
    p = await async_playwright().start()

    # Запускаем браузер с UI для админа
    browser = await p.chromium.launch_persistent_context(
        user_data_dir=str(profile_path),
        headless=False,
        args=[
            '--start-maximized',
            '--disable-blink-features=AutomationControlled'
        ]
    )

    page = await browser.new_page()

    try:
        # Переходим на Ozon
        await page.goto('https://www.ozon.ru/', wait_until='domcontentloaded')
        await asyncio.sleep(3)

    except Exception as e:
        # Не закрываем браузер при ошибке — админ сможет работать с ним вручную
        pass

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
        'browser': browser,
        'page': page,
        'playwright': p,
        'user_id': user_id,
        'phone': phone
    }
