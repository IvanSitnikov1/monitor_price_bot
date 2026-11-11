import asyncio
import sys
import importlib
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core import database
from core.models import Marketplace, QueueTaskStatus
from core.parsing import ParsedProduct, ParseIssue, ParseIssueCode, ParseResult, ParseStatus
from core.task_repository import TaskRepository
with patch.dict(os.environ, {'BOT_TOKEN': '123456:TEST_TOKEN_FOR_QUEUE_TESTS'}):
    from utils import queue_worker


class QueueWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет жизненный цикл одного воркера с настоящим хранилищем SQLite."""

    async def asyncSetUp(self):
        """Изолирует БД, время, браузер и Telegram для каждого сценария."""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db_path = self.directory / 'test.db'
        self.stack.enter_context(patch.object(database, 'DB_PATH', self.db_path))
        self.stack.enter_context(patch.object(queue_worker, 'logger'))
        self.maintenance = self.stack.enter_context(patch.object(queue_worker, 'maintenance_mode'))
        self.maintenance.is_active.return_value = False
        self.pulse_task = asyncio.create_task(self.pulse())
        await database.setup_database()
        await database.register_user(1, None, 'Тест')
        self.now = 1000.0
        self.repo = TaskRepository(self.db_path, clock=lambda: self.now)
        self.bot = MagicMock(send_message=AsyncMock())
        self.queue = queue_worker.TaskQueue(self.bot, self.repo)
        self.queue.accepting = True
        self.queue.queue_file = self.directory / 'queue.json'
        self.queue.browser_manager = MagicMock(close_all=AsyncMock())
        self.queue._activate_maintenance_mode = AsyncMock()
        self.success = ParseResult(
            marketplace=Marketplace.OZON, status=ParseStatus.SUCCESS,
            products=[ParsedProduct(name='Товар', price=1500)], detected_count=1,
        )
        self.error = ParseResult.failure(Marketplace.OZON, ParseIssueCode.NO_PRODUCTS,
                                         'Позиции не найдены')
        self.parser = self.stack.enter_context(patch.object(
            queue_worker, 'scrape_ozon_cart_with_urls', new=AsyncMock(return_value=self.error)
        ))

    async def pulse(self):
        """Даёт межпоточному SQLite завершать операции в тестовой песочнице."""
        while True:
            await asyncio.sleep(0.001)

    async def asyncTearDown(self):
        """Останавливает воркер и вспомогательный таймер до удаления БД."""
        await self.queue.stop()
        self.pulse_task.cancel()
        try:
            await self.pulse_task
        except asyncio.CancelledError:
            pass

    async def add(self, url='https://example.test/cart', **kwargs):
        """Сохраняет пользовательское задание через прежний внешний API."""
        return await self.queue.add_task('add_cart', url, 1, marketplace='ozon', **kwargs)

    async def test_acceptance_is_durable_and_database_failure_propagates(self):
        """Подтверждённое задание видно новому репозиторию, ошибка записи не скрывается."""
        task_id = await self.add()
        self.assertEqual((await TaskRepository(self.db_path).get(task_id)).status,
                         QueueTaskStatus.PENDING)
        with patch.object(self.repo, 'enqueue', side_effect=RuntimeError('Сбой записи')):
            with self.assertRaises(RuntimeError):
                await self.add('/other')
        self.bot.send_message.assert_not_awaited()
        self.queue.accepting = False
        with self.assertRaises(RuntimeError):
            await self.add('/stopped')

    async def test_three_attempts_have_exact_delays_and_no_nested_retries(self):
        """Три запуска парсера разделены паузами 60 и 300 секунд в SQLite."""
        task_id = await self.add()
        for attempt, delay in [(1, 60), (2, 300), (3, 0)]:
            await self.queue._run_next_task()
            saved = await self.repo.get(task_id)
            self.assertEqual(saved.attempts, attempt)
            self.assertEqual(self.parser.await_count, attempt)
            self.assertEqual(saved.available_at, self.now + delay)
            if attempt < 3:
                self.assertEqual(saved.status, QueueTaskStatus.PENDING)
                self.assertFalse(await self.queue._run_next_task())
                self.now += delay
            else:
                self.assertEqual(saved.status, QueueTaskStatus.FAILED)
        self.queue._activate_maintenance_mode.assert_awaited_once()
        self.assertFalse(await self.queue._run_next_task())

    async def test_authorization_defers_without_refunding_a_previous_attempt(self):
        """Авторизация откладывает задание без изменения числа предыдущих запусков."""
        task_id = await self.add()
        await self.queue._run_next_task()
        self.now += self.queue.RETRY_DELAYS[0]
        operation = self.queue.browser_manager.user_browser_operation.return_value
        operation.__aenter__.side_effect = queue_worker.BrowserAuthorizationInProgress

        await self.queue._run_next_task()

        saved = await self.repo.get(task_id)
        self.assertEqual(saved.status, QueueTaskStatus.PENDING)
        self.assertEqual(saved.attempts, 1)
        self.assertEqual(saved.available_at, self.now + self.queue.MAINTENANCE_DELAY)
        self.parser.assert_awaited_once()

    async def test_terminal_parse_error_has_one_attempt(self):
        """Неисправимая ошибка сразу завершает задание без персональных техработ."""
        self.parser.return_value = ParseResult(
            marketplace=Marketplace.OZON, status=ParseStatus.ERROR,
            issues=[ParseIssue(code=ParseIssueCode.EXTRACTION_ERROR, description='Ошибка данных')],
        )
        task_id = await self.add()
        await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.FAILED)
        self.parser.assert_awaited_once()
        self.queue._activate_maintenance_mode.assert_not_awaited()

    async def test_maintenance_defers_without_attempt_and_allows_another_user(self):
        """Заблокированное задание сохраняется и не останавливает другой маркетплейс."""
        first = await self.add(priority=1)
        second = await self.queue.add_task('add_cart', '/wb', 2, marketplace='wildberries')
        self.maintenance.is_active.side_effect = lambda user, market: market == 'ozon'
        self.queue._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        await self.queue._run_next_task()
        saved = await self.repo.get(first)
        self.assertEqual(saved.status, QueueTaskStatus.PENDING)
        self.assertEqual(saved.attempts, 0)
        self.assertEqual(saved.available_at, self.now + 60)
        await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(second)).status, QueueTaskStatus.DONE)
        self.maintenance.is_active.side_effect = None
        self.maintenance.is_active.return_value = False
        self.now += 60
        await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(first)).status, QueueTaskStatus.DONE)

    async def test_explicit_blocking_refunds_attempt(self):
        """Обнаруженная блокировка включает общую паузу и не исчерпывает бюджет."""
        self.parser.return_value = ParseResult(marketplace=Marketplace.OZON,
                                               status=ParseStatus.BLOCKED)
        task_id = await self.add()
        await self.queue._run_next_task()
        saved = await self.repo.get(task_id)
        self.assertEqual(saved.status, QueueTaskStatus.PENDING)
        self.assertEqual(saved.attempts, 0)
        self.queue._activate_maintenance_mode.assert_awaited_once()
        self.assertTrue(self.queue._activate_maintenance_mode.call_args.kwargs['host_level'])

    async def test_stale_cart_and_inactive_or_foreign_avito_are_cancelled(self):
        """Устаревшие обновления не запускают парсер и не расходуют попытки."""
        await database.save_user_cart(1, '/new', Marketplace.OZON)
        old = await self.queue.add_task('update_cart', '/old', 1, marketplace='ozon')
        await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(old)).status, QueueTaskStatus.CANCELLED)
        self.assertEqual((await self.repo.get(old)).attempts, 0)
        product_id = await database.add_product('Объявление', 100, 1, Marketplace.AVITO,
                                                url='/avito')
        await database.mark_product_not_in_cart(product_id)
        for user_id, target_id in [(1, product_id), (2, product_id), (1, product_id + 1)]:
            with self.subTest(user_id=user_id, target_id=target_id):
                task_id = await self.queue.add_task('update_product', '/avito', user_id,
                                                    marketplace='avito', product_id=target_id)
                await self.queue._run_next_task()
                self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.CANCELLED)
        self.parser.assert_not_awaited()

    async def test_price_updater_keeps_public_api_and_merges_repeated_updates(self):
        """Плановое и ручное обновление сохраняют задания через прежний API очереди."""
        from utils.price_updater import update_all_prices

        await database.save_user_cart(1, '/current', Marketplace.OZON)
        await database.add_product('Объявление', 100, 1, Marketplace.AVITO, url='/avito')
        await update_all_prices(self.queue)
        await update_all_prices(self.queue)
        self.queue._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        await self.queue._run_next_task()
        await self.queue._run_next_task()
        self.assertFalse(await self.queue._run_next_task())
        kinds = [call.args[0]['type'] for call in self.queue._process_task.call_args_list]
        self.assertEqual(kinds, ['update_cart', 'update_product'])

    async def test_host_maintenance_resumes_after_real_flag_ttl(self):
        """Истечение общего файлового флага снова разрешает сохранённое задание."""
        from utils.maintenance import MaintenanceMode
        from utils import maintenance

        mode = MaintenanceMode.__new__(MaintenanceMode)
        mode.maintenance_dir = self.directory / 'flags'
        mode.maintenance_dir.mkdir()
        flag = mode.maintenance_dir / 'maintenance_host_ozon.flag'
        flag.touch()
        os.utime(flag, (self.now, self.now))
        task_id = await self.add()
        self.queue._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        with patch.object(queue_worker, 'maintenance_mode', mode):
            with patch.object(maintenance.time, 'time', side_effect=lambda: self.now):
                with patch.object(maintenance.config, 'HOST_MAINTENANCE_TTL_SECONDS', 30):
                    await self.queue._run_next_task()
                    self.assertEqual((await self.repo.get(task_id)).attempts, 0)
                    self.now += 60
                    await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.DONE)
        self.assertFalse(flag.exists())

    async def test_repeated_failure_before_parser_is_bounded(self):
        """Ошибка проверки актуальности тоже заканчивается после трёх запусков."""
        task_id = await self.add()
        with patch.object(self.queue, '_is_current_task', side_effect=RuntimeError('Сбой SQLite')):
            for delay in (60, 300, 0):
                await self.queue._run_next_task()
                self.now += delay
        saved = await self.repo.get(task_id)
        self.assertEqual(saved.status, QueueTaskStatus.FAILED)
        self.assertEqual(saved.attempts, 3)

    async def test_running_cancel_returns_task_and_restart_finishes_it(self):
        """Штатная отмена после извлечения возвращает задание для следующего запуска."""
        task_id = await self.add()
        entered = asyncio.Event()

        async def wait_for_cancel(task):
            """Сигнализирует о начале обработки и ожидает остановки воркера."""
            entered.set()
            await asyncio.Event().wait()

        self.queue._process_task = AsyncMock(side_effect=wait_for_cancel)
        self.queue.running = True
        self.queue.worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.wait_for(entered.wait(), 2)
        await self.queue.stop()
        saved = await self.repo.get(task_id)
        self.assertEqual(saved.status, QueueTaskStatus.PENDING)
        self.assertEqual(saved.attempts, 1)
        self.assertFalse(self.queue.accepting)
        other = queue_worker.TaskQueue(self.bot, self.repo)
        other._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        await other._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.DONE)

    async def test_finalization_failure_is_recoverable(self):
        """Сбой фиксации исхода оставляет running для восстановления после перезапуска."""
        task_id = await self.add()
        self.queue._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        with patch.object(self.repo, 'transition', side_effect=RuntimeError('Сбой коммита')):
            with self.assertRaises(RuntimeError):
                await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.RUNNING)
        await self.repo.recover_running()
        await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.DONE)

    async def test_process_crash_recovers_without_stop(self):
        """Принудительно завершённый процесс оставляет восстанавливаемое задание."""
        task_id = await self.add()
        script = '''
import asyncio
import sys
from pathlib import Path
from core.task_repository import TaskRepository

async def pulse():
    """Поддерживает таймер цикла в песочнице проверки."""
    while True:
        await asyncio.sleep(0.001)

async def main():
    """Начинает обработку и ожидает принудительного завершения."""
    asyncio.create_task(pulse())
    repository = TaskRepository(Path(sys.argv[1]), clock=lambda: 1000.0)
    task = await repository.claim()
    await repository.begin_attempt(task.id)
    print('running', flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', script, str(self.db_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            self.assertEqual(await asyncio.wait_for(process.stdout.readline(), 5), b'running\n')
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.RUNNING)
        await self.repo.recover_running()
        self.queue._process_task = AsyncMock(
            return_value=queue_worker.TaskExecution(queue_worker.TaskOutcome.DONE)
        )
        await self.queue._run_next_task()
        saved = await self.repo.get(task_id)
        self.assertEqual(saved.status, QueueTaskStatus.DONE)
        self.assertEqual(saved.attempts, 2)

    async def test_failed_notification_does_not_repeat_saved_business_result(self):
        """Недоступный Telegram не запускает парсер повторно после сохранения товара."""
        self.parser.return_value = self.success
        self.bot.send_message.side_effect = RuntimeError('Telegram недоступен')
        task_id = await self.add()
        with patch.object(queue_worker, '_download_product_image',
                          new=AsyncMock(return_value=None)):
            await self.queue._run_next_task()
        self.assertEqual((await self.repo.get(task_id)).status, QueueTaskStatus.DONE)
        products = await database.get_all_products(1)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]['current_price'], 1500)
        self.assertEqual(await database.get_user_cart(1), 'https://example.test/cart')
        self.assertFalse(await self.queue._run_next_task())
        self.parser.assert_awaited_once()

    async def test_authorization_notification_is_not_repeated_across_attempts(self):
        """Событие авторизации не дублируется после переноса задания на повтор."""
        self.parser.return_value = self.error.model_copy(update={'authorization_lost': True})
        self.queue._notify_authorization_lost = AsyncMock()
        await self.add()
        await self.queue._run_next_task()
        self.now += 60
        await self.queue._run_next_task()
        self.queue._notify_authorization_lost.assert_awaited_once_with(1, 'ozon')

    async def test_corrupt_import_notifies_admin_without_file_contents(self):
        """Ошибка JSON сохраняет исходный файл и не мешает запуску SQLite-очереди."""
        self.queue.queue_file.write_text('секретное содержимое повреждённого JSON')
        with patch.object(self.queue, '_worker', new_callable=AsyncMock):
            await self.queue.start()
            await asyncio.sleep(0.01)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.call_args.args[0], queue_worker.config.ADMIN_USER_ID)
        self.assertNotIn('секретное', self.bot.send_message.call_args.args[1])
        self.assertTrue(self.queue.queue_file.exists())

    async def test_enqueue_failure_has_no_success_message_in_handler(self):
        """Пользователь не получает подтверждение, если SQLite не принял ссылку."""
        from handlers.products import process_cart_link

        message = MagicMock()
        message.from_user.id = 1
        message.from_user.username = None
        message.from_user.first_name = 'Тест'
        message.text = 'https://www.ozon.ru/cart?share=test'
        message.bot.task_queue = self.queue
        message.answer = AsyncMock()
        state = MagicMock(clear=AsyncMock())
        with patch.object(self.repo, 'enqueue', side_effect=RuntimeError('Сбой SQLite')):
            await process_cart_link(message, state)
        message.answer.assert_awaited_once()
        self.assertIn('Не удалось сохранить', message.answer.call_args.args[0])
        state.clear.assert_not_awaited()

    async def test_startup_failures_always_close_queue_and_session(self):
        """Ошибка любого этапа запуска соблюдает порядок остановки и очистки."""
        fake_main = MagicMock(bot=self.bot, dp=MagicMock())
        with patch.dict(sys.modules, {'main': fake_main}):
            run_bot = importlib.import_module('run_bot')
        for stage in ('database', 'queue', 'webhook', 'polling'):
            with self.subTest(stage=stage), ExitStack() as stack:
                events = []
                queue = MagicMock(start=AsyncMock(), stop=AsyncMock())
                scheduler = MagicMock(running=False)
                bot = MagicMock(delete_webhook=AsyncMock())
                bot.session.close = AsyncMock(side_effect=lambda: events.append('session'))
                dispatcher = MagicMock(start_polling=AsyncMock())
                setup = AsyncMock()

                def start_scheduler():
                    """Отмечает запуск планировщика без фоновых заданий."""
                    scheduler.running = True

                def shutdown_scheduler(**kwargs):
                    """Проверяет закрытие приёма перед остановкой планировщика."""
                    self.assertFalse(queue.accepting)
                    events.append('scheduler')

                async def stop_queue():
                    """Запоминает порядок очистки ресурсов приложения."""
                    events.append('queue')

                scheduler.start.side_effect = start_scheduler
                scheduler.shutdown.side_effect = shutdown_scheduler
                queue.stop.side_effect = stop_queue
                targets = {'database': setup, 'queue': queue.start,
                           'webhook': bot.delete_webhook, 'polling': dispatcher.start_polling}
                targets[stage].side_effect = RuntimeError('Тестовый сбой запуска')
                for name, value in [('bot', bot), ('dp', dispatcher), ('setup_database', setup),
                                    ('ProcessLock', MagicMock()),
                                    ('sync_reply_keyboards', AsyncMock()),
                                    ('TaskQueue', MagicMock(return_value=queue)),
                                    ('AsyncIOScheduler', MagicMock(return_value=scheduler))]:
                    stack.enter_context(patch.object(run_bot, name, value))
                with self.assertRaises(RuntimeError):
                    await run_bot.start_bot()
                expected = (['scheduler'] if stage in ('webhook', 'polling') else [])
                self.assertEqual(events, expected + ['queue', 'session'])
