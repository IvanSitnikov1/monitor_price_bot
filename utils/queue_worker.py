import asyncio
import json
import logging
import random
from pathlib import Path
from datetime import datetime
from typing import Literal

from utils.scraper import scrape_ozon_product
from core.database import (
    add_product,
    update_product_price,
    get_min_price,
    get_product_subscribers
)


logger = logging.getLogger(__name__)

QUEUE_FILE = Path("queue_state.json")


class TaskQueue:
    """
    Очередь задач для парсинга товаров с приоритетами и персистентностью.

    Приоритеты:
    - 1 (высокий): добавление товара пользователем
    - 2 (низкий): автоматическое обновление цен
    """

    def __init__(self, bot):
        self.bot = bot
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.running = False
        self.worker_task = None
        self.counter = 0  # Счетчик для разрешения конфликтов приоритетов

    async def start(self):
        """Запускает воркер очереди"""
        if self.running:
            logger.warning("Воркер очереди уже запущен")
            return

        self.running = True
        await self._load_queue()
        self.worker_task = asyncio.create_task(self._worker())
        logger.info("Воркер очереди запущен")

    async def stop(self):
        """Останавливает воркер и сохраняет очередь"""
        if not self.running:
            return

        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        await self._save_queue()
        logger.info("Воркер очереди остановлен")

    async def add_task(
        self,
        task_type: Literal["add_product", "update_price"],
        url: str,
        priority: int = 2,
        user_id: int | None = None,
        product_id: int | None = None,
        product_name: str | None = None,
        marketplace: str | None = None
    ):
        """
        Добавляет задачу в очередь.

        Args:
            task_type: Тип задачи
            url: URL товара
            priority: Приоритет (1 - высокий, 2 - низкий)
            user_id: ID пользователя (для add_product)
            product_id: ID товара в БД (для update_price)
            product_name: Название товара (для логов)
            marketplace: Маркетплейс товара (для add_product)
        """
        task = {
            "type": task_type,
            "url": url,
            "user_id": user_id,
            "product_id": product_id,
            "product_name": product_name,
            "marketplace": marketplace,
            "added_at": datetime.now().isoformat()
        }

        # Используем кортеж (priority, counter, task) для разрешения конфликтов
        self.counter += 1
        await self.queue.put((priority, self.counter, task))
        logger.info(
            f"Задача добавлена в очередь: {task_type}, "
            f"приоритет={priority}, очередь={self.queue.qsize()}"
        )

    async def _worker(self):
        """Воркер, обрабатывающий задачи из очереди"""
        logger.info("Воркер начал обработку задач")

        while self.running:
            try:
                # Проверяем режим техработ
                from utils.maintenance import MaintenanceMode
                if MaintenanceMode.is_active():
                    logger.warning("Режим техработ активен, воркер приостановлен")
                    await asyncio.sleep(10)
                    continue

                # Получаем задачу с наивысшим приоритетом
                priority, counter, task = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )

                logger.info(
                    f"Обработка задачи: {task['type']}, "
                    f"приоритет={priority}, осталось в очереди={self.queue.qsize()}"
                )

                await self._process_task(task)

                # Рандомная задержка между задачами
                # Для обновления цен (приоритет 2) - больше задержка
                if priority == 2:
                    delay = random.uniform(10, 15)
                else:
                    delay = random.uniform(3, 5)

                logger.info(f"Задержка перед следующей задачей: {delay:.2f} сек")
                await asyncio.sleep(delay)

            except asyncio.TimeoutError:
                # Очередь пуста, ждем новых задач
                continue
            except Exception as e:
                logger.error(f"Ошибка в воркере очереди: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_task(self, task: dict):
        """Обрабатывает одну задачу из очереди"""
        task_type = task["type"]
        url = task["url"]

        try:
            # Парсим товар (до 3 попыток)
            product_name = None
            price = None

            for attempt in range(1, 4):
                logger.info(f"Попытка парсинга {attempt}/3: {url}")
                product_name, price = await scrape_ozon_product(url, bot=self.bot)

                if product_name and price:
                    logger.info(f"Товар успешно спарсен: {product_name}, цена: {price}")
                    break
                else:
                    logger.warning(f"Попытка {attempt} не удалась")
                    if attempt < 3:
                        await asyncio.sleep(5)

            if task_type == "add_product":
                await self._handle_add_product(task, product_name, price)
            elif task_type == "update_price":
                await self._handle_update_price(task, product_name, price)

        except Exception as e:
            logger.error(f"Ошибка при обработке задачи {task_type}: {e}", exc_info=True)

            if task_type == "add_product" and task["user_id"]:
                try:
                    await self.bot.send_message(
                        task["user_id"],
                        f"❌ Ошибка при добавлении товара:\n{str(e)}"
                    )
                except Exception as send_error:
                    logger.error(f"Не удалось отправить сообщение пользователю: {send_error}")

    async def _handle_add_product(self, task: dict, product_name: str | None, price: str | None):
        """Обрабатывает задачу добавления товара"""
        user_id = task["user_id"]
        url = task["url"]
        marketplace = task.get("marketplace", "ozon")

        if not product_name or not price:
            logger.error(f"Не удалось спарсить товар после 3 попыток: {url}")
            await self.bot.send_message(
                user_id,
                "❌ Не удалось получить данные о товаре после 3 попыток.\n\n"
                "Возможные причины:\n"
                "- Обнаружена капча (требуется ручное решение)\n"
                "- Неверная ссылка\n"
                "- Товар недоступен"
            )
            return

        # Извлекаем число из строки цены
        import re
        price_match = re.search(r'(\d+)', price)
        if not price_match:
            logger.error(f"Ошибка при обработке цены: {price}")
            await self.bot.send_message(user_id, "❌ Ошибка при обработке цены.")
            return

        price_int = int(price_match.group(1))

        # Импортируем Marketplace для создания enum
        from core.models import Marketplace
        marketplace_enum = Marketplace(marketplace)

        # Сохраняем в БД
        logger.info(f"Сохранение товара в БД: {product_name}")
        product_id = await add_product(url, product_name, price_int, user_id, marketplace_enum)

        if product_id:
            logger.info(f"Товар успешно добавлен в БД с ID: {product_id}")
            await self.bot.send_message(
                user_id,
                f"✅ Товар добавлен!\n\n"
                f"Название: {product_name}\n"
                f"Цена: {price}"
            )
        else:
            logger.warning(f"Товар уже существует в БД для пользователя {user_id}: {url}")
            await self.bot.send_message(user_id, "⚠️ Этот товар уже добавлен в твой список.")

    async def _handle_update_price(self, task: dict, product_name: str | None, price: str | None):
        """Обрабатывает задачу обновления цены"""
        product_id = task["product_id"]
        product_display_name = task.get("product_name", f"ID {product_id}")

        if not product_name or not price:
            logger.error(f"Не удалось обновить цену товара {product_display_name}")
            return

        # Извлекаем число из строки цены
        import re
        price_match = re.search(r'(\d+)', price)
        if not price_match:
            logger.warning(f"Не удалось распарсить цену: {price}")
            return

        price_int = int(price_match.group(1))

        # Получаем минимальную цену до обновления
        min_price_before = await get_min_price(product_id)

        # Обновляем цену в БД
        await update_product_price(product_id, price_int)
        logger.info(f"✅ Цена обновлена для {product_display_name}: {price_int} ₽")

        # Проверяем, упала ли цена ниже минимальной
        if min_price_before and price_int < min_price_before:
            logger.info(
                f"🔔 Цена упала ниже минимальной! "
                f"Было: {min_price_before} ₽, стало: {price_int} ₽"
            )

            # Получаем всех подписчиков товара
            subscribers = await get_product_subscribers(product_id)
            logger.info(f"Отправка уведомлений {len(subscribers)} подписчикам")

            # Отправляем уведомления всем подписчикам
            for user_id in subscribers:
                try:
                    notification_text = (
                        f"🔔 Цена снизилась!\n\n"
                        f"📦 {product_display_name}\n\n"
                        f"💰 Новая цена: {price_int} ₽\n"
                        f"📉 Было минимум: {min_price_before} ₽\n"
                        f"💵 Экономия: {min_price_before - price_int} ₽"
                    )

                    await self.bot.send_message(user_id, notification_text)
                    logger.info(f"Уведомление отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(
                        f"Не удалось отправить уведомление пользователю {user_id}: {e}"
                    )

    async def _save_queue(self):
        """Сохраняет текущую очередь на диск"""
        try:
            tasks = []
            while not self.queue.empty():
                try:
                    priority, counter, task = self.queue.get_nowait()
                    tasks.append({"priority": priority, "counter": counter, "task": task})
                except asyncio.QueueEmpty:
                    break

            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)

            logger.info(f"Очередь сохранена на диск: {len(tasks)} задач")
        except Exception as e:
            logger.error(f"Ошибка при сохранении очереди: {e}", exc_info=True)

    async def _load_queue(self):
        """Загружает очередь с диска"""
        if not QUEUE_FILE.exists():
            logger.info("Файл очереди не найден, начинаем с пустой очереди")
            return

        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                tasks = json.load(f)

            for item in tasks:
                priority = item["priority"]
                counter = item.get("counter", 0)  # Для совместимости со старым форматом
                task = item["task"]
                await self.queue.put((priority, counter, task))
                # Обновляем счетчик
                if counter >= self.counter:
                    self.counter = counter + 1

            logger.info(f"Очередь загружена с диска: {len(tasks)} задач")

            # Удаляем файл после загрузки
            QUEUE_FILE.unlink()
        except Exception as e:
            logger.error(f"Ошибка при загрузке очереди: {e}", exc_info=True)
