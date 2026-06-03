from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить товары"),
            KeyboardButton(text="Список позиций"),
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
            KeyboardButton(text="❓ Помощь"),
            KeyboardButton(text="🔐 Авторизация"),
        ]
    ],
    resize_keyboard=True,
)
