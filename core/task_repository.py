import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiosqlite

from core.models import Marketplace, QueueTask, QueueTaskStatus


logger = logging.getLogger(__name__)


class LegacyQueueError(Exception):
    """Ошибка безопасного переноса старого файла очереди."""


async def create_queue_tables(db: aiosqlite.Connection) -> None:
    """Создаёт таблицы и индексы очереди внутри инициализации БД."""
    await db.execute('''
        CREATE TABLE IF NOT EXISTS queue_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            url TEXT NOT NULL,
            product_id INTEGER,
            priority INTEGER NOT NULL,
            identity_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            available_at REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_error TEXT,
            authorization_notified INTEGER NOT NULL DEFAULT 0
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS queue_ready
        ON queue_tasks (status, available_at, priority, created_at, id)
    ''')
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS queue_active_identity
        ON queue_tasks (identity_key) WHERE status IN ('pending', 'running')
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS queue_imports (
            source TEXT PRIMARY KEY,
            imported_at REAL NOT NULL
        )
    ''')


class TaskRepository:
    """Хранит очередь и атомарно изменяет состояние заданий."""

    def __init__(self, db_path: Path, clock: Callable[[], float] = time.time):
        """Сохраняет путь к БД и источник времени для повторяемых проверок."""
        self.db_path = db_path
        self.clock = clock

    async def _enqueue(self, db: aiosqlite.Connection, task: QueueTask) -> QueueTask:
        """Добавляет задание или повышает приоритет активного дубля в транзакции."""
        identity = json.dumps(
            [task.type, task.user_id, task.marketplace.value, task.url, task.product_id],
            ensure_ascii=False,
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        cursor = await db.execute('''
            SELECT * FROM queue_tasks
            WHERE identity_key = ? AND status IN ('pending', 'running')
        ''', (key,))
        row = await cursor.fetchone()
        if row:
            task_id = row['id']
            await db.execute('''
                UPDATE queue_tasks SET priority = MIN(priority, ?), updated_at = ? WHERE id = ?
            ''', (task.priority, self.clock(), task_id))
        else:
            cursor = await db.execute('''
                INSERT INTO queue_tasks (
                    type, user_id, marketplace, url, product_id, priority, identity_key,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (task.type, task.user_id, task.marketplace.value, task.url, task.product_id,
                  task.priority, key, task.available_at, task.created_at, task.updated_at))
            task_id = cursor.lastrowid
        cursor = await db.execute('SELECT * FROM queue_tasks WHERE id = ?', (task_id,))
        return QueueTask.model_validate(dict(await cursor.fetchone()))

    async def enqueue(self, task: QueueTask) -> QueueTask:
        """Подтверждает добавление только после успешной фиксации в SQLite."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            saved = await self._enqueue(db, task)
            await db.commit()
            return saved

    async def claim(self) -> QueueTask | None:
        """Атомарно выбирает доступное задание по приоритету и времени создания."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            cursor = await db.execute('''
                SELECT * FROM queue_tasks WHERE status = 'pending' AND available_at <= ?
                ORDER BY priority, created_at, id LIMIT 1
            ''', (self.clock(),))
            row = await cursor.fetchone()
            if row is None:
                await db.commit()
                return None
            await db.execute('''
                UPDATE queue_tasks SET status = 'running', updated_at = ? WHERE id = ?
            ''', (self.clock(), row['id']))
            cursor = await db.execute('SELECT * FROM queue_tasks WHERE id = ?', (row['id'],))
            task = QueueTask.model_validate(dict(await cursor.fetchone()))
            await db.commit()
            return task

    async def begin_attempt(self, task_id: int) -> QueueTask:
        """Учитывает запуск после проверок актуальности и техработ."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                UPDATE queue_tasks SET attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = 'running' RETURNING *
            ''', (self.clock(), task_id))
            task = QueueTask.model_validate(dict(await cursor.fetchone()))
            await db.commit()
            return task

    async def transition(
        self,
        task_id: int,
        status: QueueTaskStatus,
        reason: str | None = None,
        delay: float = 0,
        refund_attempt: bool = False,
    ) -> None:
        """Завершает или откладывает выполняющееся задание с безопасной причиной."""
        now = self.clock()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE queue_tasks
                SET status = ?, last_error = ?, available_at = ?, updated_at = ?,
                    attempts = MAX(0, attempts - ?)
                WHERE id = ? AND status = 'running'
            ''', (status.value, reason, now + delay, now, int(refund_attempt), task_id))
            await db.commit()

    async def recover_running(self) -> None:
        """Возвращает незавершённые задания в ожидание после остановки одного воркера."""
        now = self.clock()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                UPDATE queue_tasks SET status = 'pending', available_at = ?, updated_at = ?
                WHERE status = 'running' RETURNING id, attempts
            ''', (now, now))
            recovered = await cursor.fetchall()
            await db.commit()
        for task_id, attempts in recovered:
            logger.info('Задание %s: running → pending, попытка=%s', task_id, attempts)

    async def get(self, task_id: int) -> QueueTask | None:
        """Возвращает сохранённое состояние задания для диагностики."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT * FROM queue_tasks WHERE id = ?', (task_id,))
            row = await cursor.fetchone()
            return QueueTask.model_validate(dict(row)) if row else None

    async def mark_authorization_notified(self, task_id: int) -> bool:
        """Фиксирует обработку события авторизации один раз на сохранённое задание."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                UPDATE queue_tasks SET authorization_notified = 1, updated_at = ?
                WHERE id = ? AND authorization_notified = 0 RETURNING id
            ''', (self.clock(), task_id))
            changed = await cursor.fetchone() is not None
            await db.commit()
            return changed

    async def import_legacy(self, path: Path) -> None:
        """Импортирует весь старый JSON один раз, сохраняя файл при любой ошибке."""
        source = str(path.resolve())
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            cursor = await db.execute('SELECT 1 FROM queue_imports WHERE source = ?', (source,))
            imported = await cursor.fetchone() is not None
            if not imported:
                if not path.exists():
                    return
                try:
                    items = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(items, list):
                        raise ValueError('Неверный формат списка заданий')
                    tasks = []
                    now = self.clock()
                    for item in sorted(items, key=lambda value: value.get('counter', 0)):
                        payload = item['task']
                        created = (
                            datetime.fromisoformat(payload['added_at']).timestamp()
                            if payload.get('added_at') else now
                        )
                        marketplace = payload.get('marketplace') or (
                            Marketplace.AVITO
                            if payload['type'] in ('add_product', 'update_product')
                            else Marketplace.OZON
                        )
                        tasks.append(QueueTask(
                            type=payload['type'], user_id=payload['user_id'],
                            marketplace=marketplace, url=payload['url'],
                            product_id=payload.get('product_id'), priority=item['priority'],
                            created_at=created, updated_at=now, available_at=now,
                        ))
                except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    raise LegacyQueueError(
                        'Старый файл очереди не прочитан или повреждён'
                    ) from None
                for task in tasks:
                    await self._enqueue(db, task)
                await db.execute('INSERT INTO queue_imports (source, imported_at) VALUES (?, ?)',
                                 (source, now))
                await db.commit()
        if path.exists():
            try:
                archive = path.with_name(path.name + '.migrated')
                if archive.exists():
                    raise OSError('Архив очереди уже существует')
                path.rename(archive)
            except OSError:
                raise LegacyQueueError('Очередь импортирована, но исходный файл не архивирован') \
                    from None
