from enum import Enum
from pydantic import BaseModel, Field


class Marketplace(str, Enum):
    """Поддерживаемые маркетплейсы"""
    OZON = "ozon"
    WILDBERRIES = "wildberries"
    AVITO = "avito"


class Product(BaseModel):
    """Модель товара"""
    id: int = Field(description="ID товара", examples=[1])
    url: str = Field(description="URL товара", examples=["https://www.ozon.ru/product/..."])
    name: str = Field(description="Название товара", examples=["Смартфон Apple iPhone 15"])
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
