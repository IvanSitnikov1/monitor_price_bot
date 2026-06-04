from aiogram.fsm.state import StatesGroup, State


class AddPosition(StatesGroup):
    waiting_for_link = State()


class AddCart(StatesGroup):
    waiting_for_link = State()


class LoginState(StatesGroup):
    waiting_for_phone = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()


class AuthorizationInProgress(StatesGroup):
    waiting_for_qr_link = State()
    waiting_for_confirmation = State()
