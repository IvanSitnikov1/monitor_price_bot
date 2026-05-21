from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить позицию"),
            KeyboardButton(text="Список позиций"),
        ]
    ],
    resize_keyboard=True,
)


admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Добавить позицию"),
            KeyboardButton(text="Список позиций"),
        ],
        [
            KeyboardButton(text="🔄 Обновить цены"),
        ]
    ],
    resize_keyboard=True,
)
