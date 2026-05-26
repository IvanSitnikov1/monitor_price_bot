import logging

from aiogram import F, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import config
from keyboards.reply import main_kb, admin_kb


logger = logging.getLogger(__name__)
common_router = Router()


# Обработчик команды /start
@common_router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    logger.info(f"Команда /start от пользователя {user.id} (@{user.username})")
    await state.clear()

    keyboard = admin_kb if user.id == config.ADMIN_USER_ID else main_kb

    await message.answer(
        "Привет! Я бот для мониторинга цен. Выбери действие:",
        reply_markup=keyboard,
    )


# Эхо-обработчик (должен быть последним при регистрации)
@common_router.message()
async def echo(message: types.Message):
    await message.answer(f"Неизвестная команда. Ты написал: {message.text}")
