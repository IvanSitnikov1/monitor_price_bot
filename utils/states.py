from aiogram.fsm.state import StatesGroup, State


class AddPosition(StatesGroup):
    waiting_for_link = State()


class LoginState(StatesGroup):
    waiting_for_phone = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()
