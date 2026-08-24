from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


REPLY_KEYBOARD_VERSION = "1"


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить товары"),
            KeyboardButton(text="Список позиций"),
        ],
        [
            KeyboardButton(text="Настройки"),
        ],
        [
            KeyboardButton(text="❓ Помощь"),
            KeyboardButton(text="🔐 Авторизация"),
        ]
    ],
    resize_keyboard=True,
)


admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить товары"),
            KeyboardButton(text="Список позиций"),
        ],
        [
            KeyboardButton(text="📢 Рассылка"),
            KeyboardButton(text="🔄 Обновить цены"),
        ],
        [
            KeyboardButton(text="💳 Подписки"),
        ],
        [
            KeyboardButton(text="Настройки"),
        ],
        [
            KeyboardButton(text="❓ Помощь"),
            KeyboardButton(text="🔐 Авторизация"),
        ]
    ],
    resize_keyboard=True,
)


def get_reply_keyboard_version(is_admin: bool) -> str:
    """Возвращает версию клавиатуры с учетом роли пользователя."""
    role = "admin" if is_admin else "user"
    return f"{REPLY_KEYBOARD_VERSION}:{role}"


def get_reply_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    """Возвращает пользовательскую или административную клавиатуру."""
    return admin_kb if is_admin else main_kb
