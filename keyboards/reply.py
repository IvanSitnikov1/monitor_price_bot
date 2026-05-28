from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить корзину"),
            KeyboardButton(text="Список позиций"),
        ],
        [
            KeyboardButton(text="🔐 Авторизация"),
        ]
    ],
    resize_keyboard=True,
)


admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить корзину"),
            KeyboardButton(text="Список позиций"),
        ],
        [
            KeyboardButton(text="📢 Рассылка"),
            KeyboardButton(text="🔄 Обновить цены"),
            KeyboardButton(text="🔐 Авторизация"),
        ]
    ],
    resize_keyboard=True,
)
