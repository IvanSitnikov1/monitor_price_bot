import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from core import database
from core.models import Marketplace, QueueTask, QueueTaskStatus
from core.task_repository import LegacyQueueError, TaskRepository


class TaskRepositoryTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет транзакционную очередь на отдельной SQLite без рабочих данных."""

    async def asyncSetUp(self):
        """Создаёт временную БД с настоящей схемой приложения."""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db_path = self.directory / 'test.db'
        self.stack.enter_context(patch.object(database, 'DB_PATH', self.db_path))
        # Таймер позволяет потоку SQLite разбудить цикл в ограниченном тестовом окружении.
        self.pulse_task = asyncio.create_task(self.pulse())
        await database.setup_database()
        self.now = 1000.0
        self.repo = TaskRepository(self.db_path, clock=lambda: self.now)

    async def pulse(self):
        """Поддерживает таймер цикла событий в песочнице тестового запуска."""
        while True:
            await asyncio.sleep(0.001)

    async def asyncTearDown(self):
        """Завершает вспомогательный таймер до удаления временной БД."""
        self.pulse_task.cancel()
        try:
            await self.pulse_task
        except asyncio.CancelledError:
            pass

    def task(self, url='https://example.test/cart', priority=2, created=None):
        """Формирует корректное задание без обращения к реальным маркетплейсам."""
        return QueueTask(
            type='add_cart', user_id=1, marketplace=Marketplace.OZON, url=url,
            priority=priority, created_at=self.now if created is None else created,
            available_at=self.now, updated_at=self.now,
        )

    async def count(self):
        """Считает физические записи для проверки отсутствия повторного импорта."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('SELECT COUNT(*) FROM queue_tasks')
            return (await cursor.fetchone())[0]

    def legacy_file(self, items=None):
        """Создаёт старый формат JSON в изолированном каталоге."""
        path = self.directory / 'queue_state.json'
        if items is None:
            items = [{'priority': 2, 'counter': 1, 'task': {
                'type': 'add_cart', 'url': 'https://example.test/legacy',
                'user_id': 1, 'marketplace': 'ozon', 'product_id': None,
                'added_at': '2026-10-17T10:00:00',
            }}]
        path.write_text(json.dumps(items), encoding='utf-8')
        return path

    async def test_schema_is_repeatable(self):
        """Повторная инициализация не удаляет сохранённое задание."""
        task = await self.repo.enqueue(self.task())
        await database.setup_database()
        self.assertEqual((await self.repo.get(task.id)).status, QueueTaskStatus.PENDING)

    async def test_durable_enqueue_and_recovery(self):
        """Незавершённое задание восстанавливается без вызова штатной остановки."""
        saved = await self.repo.enqueue(self.task())
        other = TaskRepository(self.db_path, clock=lambda: self.now)
        self.assertEqual((await other.get(saved.id)).status, QueueTaskStatus.PENDING)
        claimed = await other.claim()
        await other.begin_attempt(claimed.id)
        restarted = TaskRepository(self.db_path, clock=lambda: self.now)
        await restarted.recover_running()
        recovered = await restarted.claim()
        self.assertEqual(recovered.id, saved.id)
        self.assertEqual(recovered.attempts, 1)
        await restarted.transition(saved.id, QueueTaskStatus.DONE)
        await restarted.recover_running()
        self.assertIsNone(await restarted.claim())

    async def test_priority_fifo_and_delayed_task(self):
        """Приоритет, время создания и ID определяют порядок доступных заданий."""
        first = await self.repo.enqueue(self.task('/first', created=900))
        second = await self.repo.enqueue(self.task('/second', created=900))
        urgent = await self.repo.enqueue(self.task('/urgent', priority=1))
        self.assertEqual((await self.repo.claim()).id, urgent.id)
        await self.repo.transition(urgent.id, QueueTaskStatus.PENDING, delay=60)
        self.assertEqual((await self.repo.claim()).id, first.id)
        await self.repo.transition(first.id, QueueTaskStatus.DONE)
        self.assertEqual((await self.repo.claim()).id, second.id)
        await self.repo.transition(second.id, QueueTaskStatus.DONE)
        self.assertIsNone(await self.repo.claim())
        self.now += 60
        self.assertEqual((await self.repo.claim()).id, urgent.id)

    async def test_concurrent_deduplication_and_priority_promotion(self):
        """Параллельные добавления одного задания дают одну запись даже во время работы."""
        saved = await asyncio.gather(*[self.repo.enqueue(self.task()) for _ in range(5)])
        self.assertEqual(len({task.id for task in saved}), 1)
        await self.repo.claim()
        promoted = await self.repo.enqueue(self.task(priority=1))
        self.assertEqual(promoted.id, saved[0].id)
        self.assertEqual(promoted.priority, 1)
        self.assertEqual(promoted.status, QueueTaskStatus.RUNNING)
        await self.repo.transition(promoted.id, QueueTaskStatus.DONE)
        new = await self.repo.enqueue(self.task())
        self.assertNotEqual(new.id, promoted.id)

    async def test_identity_separates_user_marketplace_url_and_type(self):
        """Разные корзины, пользователи и операции не объединяются."""
        original = self.task()
        variants = [original, original.model_copy(update={'user_id': 2}),
                    original.model_copy(update={'marketplace': Marketplace.WILDBERRIES}),
                    original.model_copy(update={'url': '/other'}),
                    original.model_copy(update={'type': 'update_cart'})]
        saved = [await self.repo.enqueue(task) for task in variants]
        self.assertEqual(len({task.id for task in saved}), 5)

    async def test_attempts_and_refund(self):
        """Техработы не расходуют попытку, обнаруженная блокировка возвращает её."""
        saved = await self.repo.enqueue(self.task())
        await self.repo.claim()
        await self.repo.transition(saved.id, QueueTaskStatus.PENDING, delay=60)
        self.assertEqual((await self.repo.get(saved.id)).attempts, 0)
        self.now += 60
        await self.repo.claim()
        attempt = await self.repo.begin_attempt(saved.id)
        self.assertEqual(attempt.attempts, 1)
        await self.repo.transition(saved.id, QueueTaskStatus.PENDING, delay=60,
                                   refund_attempt=True)
        self.assertEqual((await self.repo.get(saved.id)).attempts, 0)

    async def test_legacy_import_commits_and_archives_once(self):
        """Все записи импортируются одной транзакцией, повторный запуск безопасен."""
        path = self.legacy_file()
        await self.repo.import_legacy(path)
        self.assertFalse(path.exists())
        self.assertTrue(path.with_name(path.name + '.migrated').exists())
        await self.repo.import_legacy(path)
        self.assertEqual(await self.count(), 1)

    async def test_import_rename_failure_does_not_duplicate_committed_tasks(self):
        """Сбой после коммита не вызывает повторный импорт на следующем старте."""
        path = self.legacy_file()
        with patch.object(Path, 'rename', side_effect=OSError('Сбой диска')):
            with self.assertRaises(LegacyQueueError):
                await self.repo.import_legacy(path)
        task = await self.repo.claim()
        await self.repo.transition(task.id, QueueTaskStatus.DONE)
        self.assertTrue(path.exists())
        await self.repo.import_legacy(path)
        self.assertEqual(await self.count(), 1)
        self.assertIsNone(await self.repo.claim())

    async def test_corrupt_or_partially_invalid_import_leaves_source_and_database(self):
        """Невалидная запись запрещает импорт всей партии и сохраняет исходный файл."""
        path = self.legacy_file()
        valid = json.loads(path.read_text())[0]
        cases = ['{', '{}', json.dumps([valid, {'priority': 2, 'task': {'type': 'unknown'}}])]
        for content in cases:
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(LegacyQueueError):
                    await self.repo.import_legacy(path)
                self.assertEqual(path.read_text(), content)
                self.assertEqual(await self.count(), 0)
        self.legacy_file()
        await self.repo.import_legacy(path)
        self.assertEqual(await self.count(), 1)

    async def test_import_database_failure_rolls_back(self):
        """Ошибка записи не оставляет часть импортированных заданий или маркер импорта."""
        path = self.legacy_file()
        original = path.read_text()
        with patch.object(self.repo, '_enqueue', side_effect=RuntimeError('Сбой SQLite')):
            with self.assertRaises(RuntimeError):
                await self.repo.import_legacy(path)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(await self.count(), 0)
        await self.repo.import_legacy(path)
        self.assertEqual(await self.count(), 1)

    async def test_authorization_event_is_once_per_task(self):
        """Повторный запуск не обрабатывает событие авторизации второй раз."""
        saved = await self.repo.enqueue(self.task())
        self.assertTrue(await self.repo.mark_authorization_notified(saved.id))
        other = TaskRepository(self.db_path)
        self.assertFalse(await other.mark_authorization_notified(saved.id))
