import asyncio
import time
from pathlib import Path
from io import BytesIO

from playwright.async_api import async_playwright, Page
from aiogram import Bot
from aiogram.types import BufferedInputFile

from core.database import update_auth_request_status
from config import config


async def wait_for_authorization(page: Page, initial_cookies: list, timeout: int = 120) -> bool:
    """
    Ждёт появления новых cookies авторизации Ozon.

    Args:
        page: Страница браузера
        initial_cookies: Список cookies до авторизации
        timeout: Таймаут ожидания в секундах

    Returns:
        True если авторизация успешна, False если таймаут
    """
    start_time = time.time()

    # Получаем имена начальных cookies
    initial_cookie_names = {c['name'] for c in initial_cookies}

    while time.time() - start_time < timeout:
        cookies = await page.context.cookies()
        current_cookie_names = {c['name'] for c in cookies}

        # Проверяем появление новых ключевых cookies Ozon
        new_cookies = current_cookie_names - initial_cookie_names

        # Если появились новые cookies авторизации
        if any(name in ['abt_data', '__Secure-access-token', 'ADDRESSBOOKBAR_NEW_ADDRESS_ADDED'] for name in new_cookies):
            return True

        # Также проверяем изменение существующих cookies (обновление токенов)
        for cookie in cookies:
            if cookie['name'] in ['__Secure-access-token', 'abt_data']:
                # Проверяем, что cookie свежий (создан недавно)
                for initial_cookie in initial_cookies:
                    if initial_cookie['name'] == cookie['name']:
                        if cookie.get('value') != initial_cookie.get('value'):
                            return True

        await asyncio.sleep(2)

    return False


async def authorize_user(user_id: int, phone: str, bot: Bot):
    """
    Запускает процесс авторизации пользователя через QR-код Ozon.

    Args:
        user_id: ID пользователя Telegram
        phone: Номер телефона пользователя
        bot: Экземпляр бота для отправки сообщений

    Raises:
        TimeoutError: Если пользователь не авторизовался в течение таймаута
        Exception: Другие ошибки в процессе авторизации
    """
    profile_path = Path(f"user_data/{user_id}").absolute()
    profile_path.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
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

            # Сохраняем начальные cookies для сравнения
            initial_cookies = await page.context.cookies()

            # Уведомляем админа о необходимости ручных действий
            await bot.send_message(
                config.ADMIN_USER_ID,
                f"🔐 Браузер открыт для пользователя {user_id}\n"
                f"Телефон: {phone}\n\n"
                f"Выполните следующие действия:\n"
                f"1. Нажмите 'Войти' на странице\n"
                f"2. Введите номер телефона: {phone}\n"
                f"3. Дождитесь появления QR-кода\n\n"
                f"Через 60 секунд бот автоматически сделает скриншот и отправит пользователю."
            )

            # Ждём 60 секунд, чтобы админ успел ввести номер и получить QR-код
            await asyncio.sleep(60)

            # Делаем скриншот средней трети экрана (где обычно находится QR-код)
            viewport = page.viewport_size
            if viewport:
                # Вычисляем координаты средней трети экрана
                height = viewport['height']
                width = viewport['width']

                # Скриншот средней трети по горизонтали и вертикали
                # По ширине: средняя треть
                clip_x = width // 3
                clip_width = width // 3

                # По высоте: от 25% до 95% (расширено вниз на 30%)
                clip_y = int(height * 0.25)
                clip_height = int(height * 0.7)

                screenshot = await page.screenshot(
                    clip={
                        'x': clip_x,
                        'y': clip_y,
                        'width': clip_width,
                        'height': clip_height
                    }
                )
            else:
                # Если viewport недоступен, делаем полный скриншот
                screenshot = await page.screenshot(full_page=False)

            # Отправляем пользователю
            await bot.send_photo(
                chat_id=user_id,
                photo=BufferedInputFile(screenshot, filename='qr.png'),
                caption="🔐 Отсканируйте QR-код в приложении Ozon для авторизации\n\n"
                        "У вас есть 2 минуты для завершения авторизации."
            )

            # Уведомляем админа
            await bot.send_message(
                config.ADMIN_USER_ID,
                f"✅ Скриншот отправлен пользователю {user_id}\n"
                f"Ожидаю 2 минуты для завершения авторизации..."
            )

            # Ждём 2 минуты для завершения авторизации
            await asyncio.sleep(120)

            # Закрываем браузер (профиль автоматически сохранится)
            await browser.close()

            # Обновляем статус в БД
            await update_auth_request_status(user_id, "approved")

            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                "✅ Профиль сохранён.\n\n"
                "Если вы не успели авторизоваться — напишите администратору."
            )

            # Уведомляем админа
            await bot.send_message(
                config.ADMIN_USER_ID,
                f"✅ Профиль пользователя {user_id} сохранён"
            )

        except Exception as e:
            await browser.close()
            raise e
