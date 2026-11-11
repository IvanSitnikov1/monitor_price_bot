from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from core.models import Marketplace


class ParseStatus(StrEnum):
    """Исход получения данных маркетплейса."""

    SUCCESS = 'success'
    PARTIAL = 'partial'
    BLOCKED = 'blocked'
    CLOSED = 'closed'
    DELETED = 'deleted'
    ERROR = 'error'


class ParseIssueCode(StrEnum):
    """Причина ошибки или пропуска позиции."""

    BROWSER_ERROR = 'browser_error'
    EXTRACTION_ERROR = 'extraction_error'
    NO_PRODUCTS = 'no_products'
    INVALID_PRODUCT = 'invalid_product'
    BLOCKED = 'blocked'
    UNAVAILABLE_PAGE = 'unavailable_page'
    UNEXPECTED_ERROR = 'unexpected_error'


class ParsedProduct(BaseModel):
    """Позиция маркетплейса с нормализованной ценой."""

    name: str = Field(min_length=1, description='Название товара', examples=['Шорты'])
    price: int | None = Field(
        None, ge=0, strict=True, description='Цена в рублях', examples=[1500]
    )
    url: str | None = Field(None, description='Ссылка на товар', examples=['https://ozon.ru/'])
    image_url: str | None = Field(
        None, description='Ссылка на изображение', examples=['https://cdn.example.ru/image.jpg']
    )
    variant_text: str | None = Field(None, description='Вариант товара', examples=['50 RU'])
    unavailable: bool = Field(False, description='Товар недоступен', examples=[False])

    @model_validator(mode='after')
    def validate_price(self) -> Self:
        """Проверяет согласованность цены и доступности."""
        if not self.name.strip():
            raise ValueError('Название товара не может быть пустым')
        if self.unavailable != (self.price is None):
            raise ValueError('Цена отсутствует только у недоступной позиции')
        return self


class ParseIssue(BaseModel):
    """Безопасное описание проблемы получения данных."""

    code: ParseIssueCode = Field(description='Код причины', examples=['no_products'])
    description: str = Field(
        min_length=1,
        description='Описание без персональных данных',
        examples=['Позиции не найдены'],
    )
    block_index: int | None = Field(
        None, ge=0, description='Индекс пропущенного блока с нуля', examples=[0]
    )


class ParseResult(BaseModel):
    """Единый результат парсинга корзины или объявления."""

    marketplace: Marketplace = Field(description='Маркетплейс', examples=['ozon'])
    status: ParseStatus = Field(description='Исход парсинга', examples=['success'])
    products: list[ParsedProduct] = Field(
        default_factory=list, description='Достоверные позиции', examples=[[]]
    )
    detected_count: int = Field(0, ge=0, description='Число обнаруженных блоков', examples=[1])
    skipped_count: int = Field(0, ge=0, description='Число пропущенных блоков', examples=[0])
    issues: list[ParseIssue] = Field(
        default_factory=list, description='Причины ошибок и пропусков', examples=[[]]
    )
    retryable: bool = Field(False, description='Допустим повтор ошибки', examples=[False])
    authorization_lost: bool = Field(
        False, description='Обнаружен завершённый сеанс', examples=[False]
    )

    @model_validator(mode='after')
    def validate_result(self) -> Self:
        """Исключает применение ошибочных и противоречивых результатов."""
        if self.detected_count != len(self.products) + self.skipped_count:
            raise ValueError('Счётчики блоков не соответствуют позициям и пропускам')
        match self.status:
            case ParseStatus.SUCCESS:
                if not self.products or self.skipped_count:
                    raise ValueError('Успешный результат должен содержать позиции без пропусков')
            case ParseStatus.PARTIAL:
                if not self.products or not self.skipped_count or not self.issues:
                    raise ValueError('Частичный результат требует позиций и описания пропусков')
            case ParseStatus.BLOCKED | ParseStatus.CLOSED | ParseStatus.DELETED | ParseStatus.ERROR:
                if self.products:
                    raise ValueError('Неуспешный результат не должен содержать позиции')
        if self.status == ParseStatus.ERROR and not self.issues:
            raise ValueError('Ошибка должна содержать причину')
        if self.status in (ParseStatus.CLOSED, ParseStatus.DELETED):
            if self.marketplace != Marketplace.AVITO:
                raise ValueError('Закрытие и удаление применимы только к Avito')
        if self.marketplace == Marketplace.AVITO:
            if self.status == ParseStatus.PARTIAL:
                raise ValueError('Частичный результат неприменим к одному объявлению')
            if self.status == ParseStatus.SUCCESS and len(self.products) != 1:
                raise ValueError('Результат Avito должен содержать одно объявление')
        if self.retryable and self.status != ParseStatus.ERROR:
            raise ValueError('Повтор допустим только для ошибки')
        return self

    @classmethod
    def failure(
        cls,
        marketplace: Marketplace,
        code: ParseIssueCode,
        description: str,
        authorization_lost: bool = False,
        detected_count: int = 0,
    ) -> Self:
        """Создаёт повторяемую ошибку без данных для применения."""
        return cls(
            marketplace=marketplace,
            status=ParseStatus.ERROR,
            issues=[ParseIssue(code=code, description=description)],
            retryable=True,
            authorization_lost=authorization_lost,
            detected_count=detected_count,
            skipped_count=detected_count,
        )
