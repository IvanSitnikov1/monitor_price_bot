from aiogram.fsm.state import StatesGroup, State


class AddPosition(StatesGroup):
    waiting_for_link = State()


class AddCart(StatesGroup):
    waiting_for_link = State()


class NotificationSettings(StatesGroup):
    waiting_for_drop_percent = State()
    waiting_for_drop_amount = State()
    waiting_for_target_price = State()


class LoginState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_wb_code = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()


class AuthorizationInProgress(StatesGroup):
    waiting_for_qr_link = State()
    waiting_for_wb_code = State()
    waiting_for_confirmation = State()
