from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from config import config

# Создаем сессию с прокси, если указан
if config.PROXY_URL:
    session = AiohttpSession(proxy=config.PROXY_URL)
    bot = Bot(token=config.BOT_TOKEN, session=session)
else:
    bot = Bot(token=config.BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())
