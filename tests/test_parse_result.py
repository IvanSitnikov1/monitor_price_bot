import unittest

from pydantic import ValidationError

from core.models import Marketplace
from core.parsing import ParsedProduct, ParseIssue, ParseIssueCode, ParseResult, ParseStatus


class ParseResultTests(unittest.TestCase):
    """Проверяет ограничения контракта, защищающие синхронизацию товаров."""

    def test_available_product_requires_integer_price(self):
        """Доступная позиция не принимает пропущенную или нечисловую цену."""
        for price in (None, -1, '1500 ₽', 1.5, True):
            with self.subTest(price=price), self.assertRaises(ValidationError):
                ParsedProduct(name='Товар', price=price)
        self.assertEqual(ParsedProduct(name='Товар', price=0).price, 0)

    def test_unavailable_product_has_no_price(self):
        """Недоступность не подменяет ошибку извлечения цены."""
        product = ParsedProduct(name='Товар', unavailable=True)
        self.assertIsNone(product.price)
        with self.assertRaises(ValidationError):
            ParsedProduct(name='Товар', price=10, unavailable=True)
        with self.assertRaises(ValidationError):
            ParsedProduct(name='  ', price=10)

    def test_invalid_result_combinations(self):
        """Противоречивые статусы и счётчики не доходят до воркера."""
        product = ParsedProduct(name='Товар', price=1500)
        issue = ParseIssue(code=ParseIssueCode.INVALID_PRODUCT, description='Пропущена позиция')
        cases = [
            {'status': ParseStatus.SUCCESS},
            {'status': ParseStatus.SUCCESS, 'products': [product]},
            {'status': ParseStatus.PARTIAL, 'products': [product], 'detected_count': 1},
            {'status': ParseStatus.PARTIAL, 'products': [product], 'detected_count': 2,
             'skipped_count': 1},
            {'status': ParseStatus.ERROR},
            {'status': ParseStatus.ERROR, 'products': [product], 'detected_count': 1,
             'issues': [issue]},
            {'status': ParseStatus.BLOCKED, 'products': [product], 'detected_count': 1},
            {'status': ParseStatus.CLOSED},
            {'status': ParseStatus.DELETED},
            {'status': ParseStatus.SUCCESS, 'products': [product], 'detected_count': 1,
             'retryable': True},
            {'status': ParseStatus.ERROR, 'detected_count': -1, 'issues': [issue]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                ParseResult(marketplace=Marketplace.OZON, **case)

    def test_valid_partial_and_authorization_loss(self):
        """Публичная корзина может быть разобрана после завершения авторизации."""
        result = ParseResult(
            marketplace=Marketplace.OZON, status=ParseStatus.PARTIAL,
            products=[ParsedProduct(name='Товар', price=1500)],
            detected_count=2, skipped_count=1, authorization_lost=True,
            issues=[ParseIssue(code=ParseIssueCode.INVALID_PRODUCT,
                               description='Пропущена позиция', block_index=1)],
        )
        self.assertTrue(result.authorization_lost)
        self.assertEqual(result.products[0].price, 1500)

    def test_failure_retains_counts_and_safe_reason(self):
        """Ошибка сохраняет число найденных блоков без позиций для применения."""
        result = ParseResult.failure(
            Marketplace.OZON, ParseIssueCode.NO_PRODUCTS, 'Позиции не найдены',
            authorization_lost=True, detected_count=2,
        )
        self.assertEqual(result.status, ParseStatus.ERROR)
        self.assertEqual(result.skipped_count, 2)
        self.assertEqual(result.products, [])
        self.assertTrue(result.retryable)

    def test_avito_requires_single_product(self):
        """Результат объявления не допускает несколько позиций или частичность."""
        product = ParsedProduct(name='Товар', price=100)
        for status in (ParseStatus.CLOSED, ParseStatus.DELETED):
            self.assertEqual(ParseResult(marketplace=Marketplace.AVITO, status=status).status,
                             status)
        with self.assertRaises(ValidationError):
            ParseResult(marketplace=Marketplace.AVITO, status=ParseStatus.SUCCESS,
                        products=[product, product], detected_count=2)
        with self.assertRaises(ValidationError):
            ParseResult(
                marketplace=Marketplace.AVITO, status=ParseStatus.PARTIAL,
                products=[product], detected_count=2, skipped_count=1,
                issues=[ParseIssue(code=ParseIssueCode.INVALID_PRODUCT,
                                   description='Пропущена позиция')],
            )
