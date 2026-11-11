from enum import StrEnum
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class Marketplace(StrEnum):
    """Поддерживаемые маркетплейсы"""
    OZON = "ozon"
    WILDBERRIES = "wildberries"
    AVITO = "avito"


class QueueTaskStatus(StrEnum):
    """Состояние сохранённого задания очереди."""

    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class QueueTask(BaseModel):
    """Задание очереди с состоянием и временем следующего запуска."""

    id: int | None = Field(None, description='ID задания в SQLite', examples=[1])
    type: Literal['add_cart', 'add_product', 'update_cart', 'update_product'] = Field(
        description='Операция над корзиной или объявлением', examples=['add_cart']
    )
    user_id: int = Field(gt=0, strict=True, description='ID пользователя', examples=[1])
    marketplace: Marketplace = Field(description='Маркетплейс', examples=['ozon'])
    url: str = Field(
        min_length=1, description='Ссылка для обработки', examples=['https://ozon.ru/']
    )
    product_id: int | None = Field(
        None, gt=0, strict=True, description='ID обновляемого объявления', examples=[7]
    )
    priority: int = Field(2, ge=1, strict=True, description='Приоритет задания', examples=[1])
    status: QueueTaskStatus = Field(
        QueueTaskStatus.PENDING, description='Состояние задания', examples=['pending']
    )
    attempts: int = Field(0, ge=0, description='Число начатых запусков', examples=[0])
    available_at: float = Field(description='Время доступности в секундах Unix', examples=[1.0])
    created_at: float = Field(description='Время создания в секундах Unix', examples=[1.0])
    updated_at: float = Field(description='Время изменения в секундах Unix', examples=[1.0])
    last_error: str | None = Field(
        None, description='Безопасная причина последнего переноса или ошибки', examples=['Сбой БД']
    )
    authorization_notified: bool = Field(
        False, description='Событие завершения авторизации уже обработано', examples=[False]
    )

    @model_validator(mode='after')
    def validate_target(self) -> Self:
        """Проверяет соответствие операции маркетплейсу и объявлению."""
        if self.type in ('add_product', 'update_product'):
            if self.marketplace != Marketplace.AVITO:
                raise ValueError('Отдельные объявления поддерживаются только для Avito')
        elif self.marketplace == Marketplace.AVITO:
            raise ValueError('Корзина Avito не поддерживается')
        if self.type == 'update_product' and self.product_id is None:
            raise ValueError('Для обновления объявления нужен ID товара')
        return self


class Product(BaseModel):
    """Модель товара"""
    id: int = Field(description="ID товара", examples=[1])
    name: str = Field(description="Название товара", examples=["Смартфон Apple iPhone 15"])
    url: str | None = Field(
        None,
        description="Ссылка на страницу товара",
        examples=["https://www.ozon.ru/product/example-123456789/"]
    )
    image_url: str | None = Field(
        None,
        description="Ссылка на изображение товара",
        examples=["https://cdn1.ozone.ru/s3/multimedia-example.jpg"]
    )
    image_path: str | None = Field(
        None,
        description="Локальный путь к изображению товара",
        examples=["product_images/1189033386/1.jpg"]
    )
    variant_text: str | None = Field(
        None,
        description="Характеристики варианта товара из блока корзины",
        examples=["50 RU / 34 • Хаки /1171-3/"]
    )
    cart_item_key: str | None = Field(
        None,
        description="Стабильный ключ позиции корзины",
        examples=["ozon|шорты dlvaxty карго лето 2026|50 ru / 34 • хаки /1171-3/"]
    )
    target_price: int | None = Field(
        None,
        description="Ожидаемая цена товара в рублях",
        examples=[3500]
    )
    marketplace: Marketplace = Field(
        description="Маркетплейс товара",
        examples=["ozon"]
    )
    date_added: str = Field(
        description="Дата добавления товара",
        examples=["2024-01-15T10:30:00"]
    )


class PriceHistory(BaseModel):
    """Модель истории цен"""
    id: int = Field(description="ID записи", examples=[1])
    product_id: int = Field(description="ID товара", examples=[1])
    price: int = Field(description="Цена в рублях", examples=[50000])
    date_checked: str = Field(
        description="Дата проверки цены",
        examples=["2024-01-15T10:30:00"]
    )


class AuthRequest(BaseModel):
    """Запрос на авторизацию от пользователя"""
    user_id: int = Field(..., description="ID пользователя Telegram")
    phone: str = Field(..., description="Номер телефона", examples=["+79991234567"])
    username: str | None = Field(None, description="Username пользователя")
    request_date: datetime = Field(default_factory=datetime.now, description="Дата запроса")
    status: str = Field("pending", description="Статус: pending | approved | rejected")


class Subscription(BaseModel):
    """Текущее состояние подписки пользователя"""
    user_id: int = Field(description="ID пользователя Telegram", examples=[1189033386])
    status: str = Field(description="Статус подписки", examples=["active"])
    paid_until: str | None = Field(
        None,
        description="Дата окончания оплаченного доступа",
        examples=["2026-07-25T12:00:00"]
    )
    created_at: str = Field(
        description="Дата создания записи",
        examples=["2026-06-25T12:00:00"]
    )
    updated_at: str = Field(
        description="Дата обновления записи",
        examples=["2026-06-25T12:00:00"]
    )
