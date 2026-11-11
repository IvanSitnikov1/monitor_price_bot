import asyncio
import inspect
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import Marketplace
from core.parsing import ParsedProduct, ParseIssue, ParseIssueCode, ParseResult, ParseStatus

# Импорт настроек не требует доступа к реальному токену бота.
with patch.dict(os.environ, {'BOT_TOKEN': '123456:тестовый_токен'}):
    from utils import queue_worker, scraper


class ParserContractTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет реальные парсеры и эффекты воркера без сети и браузера."""

    def setUp(self):
        """Изолирует задержки, скриншоты и журнал каждого сценария."""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(scraper, 'Path', return_value=Path(self.directory)))
        self.stack.enter_context(patch.object(scraper, 'logger'))
        self.stack.enter_context(patch.object(queue_worker, 'logger'))
        self.stack.enter_context(patch.object(
            queue_worker, 'get_notification_settings', new=AsyncMock(return_value={})
        ))
        self.stack.enter_context(patch.object(
            queue_worker, 'claim_target_price_notification', new=AsyncMock(return_value=None)
        ))
        for name in ('_human_delay', '_human_like_behavior'):
            self.stack.enter_context(patch.object(scraper, name, new_callable=AsyncMock))
        self.page = MagicMock()
        self.page.wait_for_load_state = AsyncMock()
        self.page.screenshot = AsyncMock()
        self.page.evaluate = AsyncMock()
        self.manager = MagicMock()
        self.manager.get_cart_page = AsyncMock(return_value=self.page)
        self.manager.open_cart_page = AsyncMock(return_value=self.page)
        self.manager.refresh_cart_page = AsyncMock(return_value=self.page)
        self.manager.get_product_page = AsyncMock(return_value=self.page)
        self.manager.has_cart_page.return_value = False
        for name in ('_check_ozon_block', '_check_wildberries_block', '_check_avito_block',
                     '_check_avito_deleted', '_check_ozon_logged_out',
                     '_check_wildberries_logged_out'):
            self.stack.enter_context(patch.object(scraper, name, new=AsyncMock(return_value=False)))
        self.queue = queue_worker.TaskQueue(MagicMock(send_message=AsyncMock()))
        self.queue.browser_manager = self.manager
        self.queue._activate_maintenance_mode = AsyncMock()
        self.queue._notify_authorization_lost = AsyncMock()
        self.task = {'type': 'update_cart', 'marketplace': 'ozon', 'user_id': 1,
                     'url': 'https://example.test/cart', 'product_id': 7}

    def ozon_blocks(self, names):
        """Создаёт блоки корзины и пустой локатор проверки соединения."""
        blocks = MagicMock()
        blocks.count = AsyncMock(return_value=len(names))
        items = []
        for name in names:
            block = MagicMock()
            block.locator.return_value.first.text_content = AsyncMock(return_value=name)
            items.append(block)
        blocks.nth.side_effect = lambda index: items[index]
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        empty.first = empty
        self.page.locator.side_effect = lambda selector: (
            empty if 'нет соединения' in selector else blocks
        )
        self.stack.enter_context(patch.object(
            scraper, '_extract_ozon_price_from_block', new=AsyncMock(return_value='1\xa0500 ₽')
        ))
        self.stack.enter_context(patch.object(
            scraper, '_extract_product_variant_from_block', new=AsyncMock(return_value='50 RU')
        ))
        self.stack.enter_context(patch.object(
            scraper, '_is_ozon_product_unavailable', new=AsyncMock(return_value=False)
        ))

    async def test_ozon_success_partial_and_error(self):
        """Полнота и отсутствие данных не смешиваются с успешным результатом."""
        for names, status in [(['Товар'], ParseStatus.SUCCESS),
                              (['Товар', None], ParseStatus.PARTIAL),
                              ([], ParseStatus.ERROR), ([None], ParseStatus.ERROR)]:
            with self.subTest(names=names):
                self.ozon_blocks(names)
                result = await scraper.scrape_ozon_cart(self.task['url'], 1, self.manager)
                self.assertIsInstance(result, ParseResult)
                self.assertEqual(result.status, status)
                self.assertEqual(result.detected_count, len(names))
                if status in (ParseStatus.SUCCESS, ParseStatus.PARTIAL):
                    self.assertEqual(result.products[0].price, 1500)
                else:
                    self.assertEqual(result.products, [])

    async def test_ozon_details_and_unavailable(self):
        """Добавление сохраняет ссылки, а недоступная позиция не получает цену."""
        self.ozon_blocks(['Товар'])
        with patch.object(scraper, '_extract_product_details_from_block',
                          new=AsyncMock(return_value={'url': 'https://example.test/product'})):
            result = await scraper.scrape_ozon_cart_with_urls(self.task['url'], 1, self.manager)
        self.assertEqual(result.products[0].url, 'https://example.test/product')
        scraper._extract_ozon_price_from_block.reset_mock()
        scraper._extract_ozon_price_from_block.return_value = '1000 ₽'
        scraper._is_ozon_product_unavailable.return_value = True
        result = await scraper.scrape_ozon_cart(self.task['url'], 1, self.manager)
        self.assertTrue(result.products[0].unavailable)
        self.assertIsNone(result.products[0].price)
        self.assertEqual(result.products[0].variant_text, '50 RU')
        scraper._extract_ozon_price_from_block.assert_not_awaited()

    async def test_wildberries_price_and_no_products(self):
        """Цена Wildberries преобразуется без изменения выбранного значения."""
        self.page.evaluate.return_value = [{'name': 'Товар', 'price': '2 499 ₽'}]
        result = await scraper.scrape_wildberries_cart(self.task['url'], 1, self.manager)
        self.assertEqual(result.status, ParseStatus.SUCCESS)
        self.assertEqual(result.products[0].price, 2499)
        self.page.evaluate.return_value = []
        result = await scraper.scrape_wildberries_cart(self.task['url'], 1, self.manager)
        self.assertEqual(result.status, ParseStatus.ERROR)
        self.assertTrue(result.retryable)

    async def test_avito_states(self):
        """Avito возвращает типизированные успех, закрытие, удаление и ошибку."""
        for data, deleted, status in [
            ({'title': 'Товар', 'price': '3 000 ₽'}, False, ParseStatus.SUCCESS),
            ({'closed': True}, False, ParseStatus.CLOSED),
            ({}, True, ParseStatus.DELETED),
            ({'unavailable': True}, False, ParseStatus.ERROR),
            ({}, False, ParseStatus.ERROR),
        ]:
            with self.subTest(status=status):
                self.page.evaluate.return_value = data
                scraper._check_avito_deleted.return_value = deleted
                result = await scraper.scrape_avito_product(self.task['url'], 1, self.manager)
                self.assertEqual(result.status, status)
                if status == ParseStatus.SUCCESS:
                    self.assertEqual(result.products[0].price, 3000)

    async def test_blocked_parsers_have_no_external_effects(self):
        """Парсеры только сообщают блокировку и не включают техработы сами."""
        for parser, check in [(scraper.scrape_ozon_cart, '_check_ozon_block'),
                              (scraper.scrape_wildberries_cart, '_check_wildberries_block'),
                              (scraper.scrape_avito_product, '_check_avito_block')]:
            with self.subTest(parser=parser.__name__):
                getattr(scraper, check).return_value = True
                result = await parser(self.task['url'], 1, self.manager)
                self.assertEqual(result.status, ParseStatus.BLOCKED)
                self.assertEqual(result.products, [])
                getattr(scraper, check).return_value = False
        self.queue._activate_maintenance_mode.assert_not_awaited()
        self.queue.bot.send_message.assert_not_awaited()

    async def test_authorization_loss_survives_success_and_error(self):
        """Флаг завершения сессии сохраняется даже после последующей ошибки."""
        for parser, check in [(scraper.scrape_ozon_cart, '_check_ozon_logged_out'),
                              (scraper.scrape_wildberries_cart,
                               '_check_wildberries_logged_out')]:
            with self.subTest(parser=parser.__name__):
                self.ozon_blocks(['Товар'])
                self.page.evaluate.return_value = [{'name': 'Товар', 'price': '1500 ₽'}]
                getattr(scraper, check).return_value = True
                result = await parser(self.task['url'], 1, self.manager)
                self.assertEqual(result.status, ParseStatus.SUCCESS)
                self.assertTrue(result.authorization_lost)
                with patch.object(self.page, 'locator', side_effect=RuntimeError('Сбой')):
                    self.page.evaluate.side_effect = RuntimeError('Сбой')
                    result = await parser(self.task['url'], 1, self.manager)
                self.page.evaluate.side_effect = None
                self.assertEqual(result.status, ParseStatus.ERROR)
                self.assertTrue(result.authorization_lost)

    async def test_unexpected_errors_and_cancellation(self):
        """Сбой браузера даёт безопасную ошибку, отмена распространяется наружу."""
        for parser in (scraper.scrape_ozon_cart, scraper.scrape_ozon_cart_with_urls,
                       scraper.scrape_wildberries_cart, scraper.scrape_avito_product):
            with self.subTest(parser=parser.__name__):
                with patch.object(scraper, '_human_delay',
                                  side_effect=RuntimeError('Секретные данные исключения')):
                    result = await parser(self.task['url'], 1, self.manager)
                self.assertEqual(result.status, ParseStatus.ERROR)
                self.assertNotIn('Секретные', result.model_dump_json())
                with patch.object(scraper, '_human_delay', side_effect=asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await parser(self.task['url'], 1, self.manager)

    async def test_failed_cart_never_changes_database(self):
        """Ошибочный результат не применяется как пустая корзина."""
        result = ParseResult.failure(Marketplace.OZON, ParseIssueCode.NO_PRODUCTS,
                                     'Позиции не найдены')
        for handler in (self.queue._handle_add_cart, self.queue._handle_update_cart):
            with patch.object(self.queue, '_scrape_cart',
                              new=AsyncMock(return_value=result)):
                with patch.object(queue_worker, 'get_all_products', new_callable=AsyncMock) as db:
                    await handler(self.task, result)
                    db.assert_not_awaited()

    async def test_worker_cancellation_has_no_maintenance_effect(self):
        """Отмена задания не становится блокировкой или ошибкой парсинга."""
        for parser_name, method in [
            ('scrape_ozon_cart', self.queue._scrape_cart),
            ('scrape_avito_product', self.queue._scrape_avito),
        ]:
            with patch.object(queue_worker, parser_name, side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await method(self.task)
        self.queue._activate_maintenance_mode.assert_not_awaited()

    async def test_avito_add_success_saves_integer_price(self):
        """Объявление сохраняется с той же числовой ценой и изображением."""
        result = ParseResult(
            marketplace=Marketplace.AVITO, status=ParseStatus.SUCCESS,
            products=[ParsedProduct(name='Товар', price=1500)], detected_count=1,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.queue, '_scrape_avito',
                                             new=AsyncMock(return_value=result)))
            stack.enter_context(patch.object(queue_worker, 'get_product_by_url',
                                             new=AsyncMock(return_value=None)))
            add = stack.enter_context(patch.object(queue_worker, 'add_product',
                                                   new=AsyncMock(return_value=7)))
            stack.enter_context(patch.object(queue_worker, '_download_product_image',
                                             new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(queue_worker, 'update_product_image_path',
                                             new_callable=AsyncMock))
            await self.queue._handle_add_avito_product(self.task, result)
        self.assertEqual(add.call_args.kwargs['price'], 1500)
        self.assertEqual(add.call_args.kwargs['marketplace'], Marketplace.AVITO)
        self.queue.bot.send_message.assert_awaited_once()

    async def test_partial_cart_updates_known_product_without_removing_others(self):
        """Частичный результат обновляет цену, сохраняя отсутствующие позиции."""
        result = ParseResult(
            marketplace=Marketplace.OZON, status=ParseStatus.PARTIAL,
            products=[ParsedProduct(name='Товар', price=1500)], detected_count=2,
            skipped_count=1, issues=[ParseIssue(code=ParseIssueCode.INVALID_PRODUCT,
                                                description='Пропущен блок')],
        )
        existing = [{'id': 1, 'name': 'Товар', 'current_price': 1500, 'in_cart': 1},
                    {'id': 2, 'name': 'Другой товар', 'current_price': 100, 'in_cart': 1}]
        with patch.object(self.queue, '_scrape_cart',
                          new=AsyncMock(return_value=result)):
            with patch.object(queue_worker, 'get_all_products',
                              new=AsyncMock(return_value=existing)):
                with patch.object(queue_worker, 'get_price_snapshot', new=AsyncMock(
                    return_value={'previous_price': 1500, 'min_price': 1500, 'max_price': 1500}
                )):
                    with ExitStack() as stack:
                        update = stack.enter_context(patch.object(
                            queue_worker, 'update_product_price', new_callable=AsyncMock))
                        remove = stack.enter_context(patch.object(
                            queue_worker, 'mark_product_not_in_cart', new_callable=AsyncMock))
                        stack.enter_context(patch.object(queue_worker,
                                                         'update_product_cart_identity',
                                                         new_callable=AsyncMock))
                        await self.queue._handle_update_cart(self.task, result)
                    update.assert_awaited_once_with(1, 1500)
                    remove.assert_not_awaited()

    async def test_cart_add_preserves_price_and_active_link(self):
        """Добавление каждого маркетплейса сохраняет цену и активную ссылку."""
        for marketplace in (Marketplace.OZON, Marketplace.WILDBERRIES):
            with self.subTest(marketplace=marketplace), ExitStack() as stack:
                self.task.update(type='add_cart', marketplace=marketplace.value)
                result = ParseResult(
                    marketplace=marketplace, status=ParseStatus.SUCCESS,
                    products=[ParsedProduct(name='Товар', price=1500)], detected_count=1,
                )
                stack.enter_context(patch.object(self.queue, '_scrape_cart',
                                                 new=AsyncMock(return_value=result)))
                stack.enter_context(patch.object(queue_worker, 'get_all_products',
                                                 new=AsyncMock(return_value=[])))
                add = stack.enter_context(patch.object(queue_worker, 'add_product',
                                                       new=AsyncMock(return_value=7)))
                stack.enter_context(patch.object(queue_worker, '_download_product_image',
                                                 new=AsyncMock(return_value=None)))
                stack.enter_context(patch.object(queue_worker, 'update_product_image_path',
                                                 new_callable=AsyncMock))
                save = stack.enter_context(patch.object(queue_worker, 'save_user_cart',
                                                        new_callable=AsyncMock))
                await self.queue._handle_add_cart(self.task, result)
                self.assertEqual(add.call_args.kwargs['price'], 1500)
                save.assert_awaited_once_with(1, self.task['url'], marketplace)
                self.assertIn('1500 ₽', self.queue.bot.send_message.call_args.args[1])

    async def test_closed_avito_preserves_history_and_stops_tracking(self):
        """Подтверждённое закрытие отключает мониторинг без записи новой цены."""
        self.task.update(type='update_product', marketplace='avito')
        for status in (ParseStatus.CLOSED, ParseStatus.DELETED):
            with self.subTest(status=status):
                result = ParseResult(marketplace=Marketplace.AVITO, status=status)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(self.queue, '_scrape_avito',
                                                     new=AsyncMock(return_value=result)))
                    stack.enter_context(patch.object(queue_worker, 'get_product_by_url',
                                                     new=AsyncMock(return_value={
                                                         'id': 7, 'in_cart': 1, 'name': 'Товар'})))
                    remove = stack.enter_context(patch.object(
                        queue_worker, 'mark_product_not_in_cart', new_callable=AsyncMock))
                    update = stack.enter_context(patch.object(
                        queue_worker, 'update_product_price', new_callable=AsyncMock))
                    await self.queue._handle_update_avito_product(self.task, result)
                    remove.assert_awaited_once_with(7)
                    update.assert_not_awaited()

    def test_public_signatures(self):
        """Все публичные функции имеют единый контракт без callback-параметров."""
        for parser in (scraper.scrape_ozon_cart, scraper.scrape_ozon_cart_with_urls,
                       scraper.scrape_wildberries_cart, scraper.scrape_avito_product):
            signature = inspect.signature(parser)
            self.assertIs(signature.return_annotation, ParseResult)
            self.assertNotIn('maintenance_callback', signature.parameters)
            self.assertNotIn('authorization_lost_callback', signature.parameters)
