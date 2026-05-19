import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from config import config


# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Создаем сессию с указанием прокси
session = AiohttpSession(proxy=config.PROXY_URL)

# Инициализируем бота с этой сессией
bot = Bot(token=config.BOT_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())
