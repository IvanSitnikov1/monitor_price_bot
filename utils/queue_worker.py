import asyncio
import json
import logging
import random
import re
from pathlib import Path
from datetime import datetime
from typing import Literal

from utils.scraper import scrape_ozon_product, scrape_ozon_cart
from utils.browser_manager import BrowserManager
from core.database import (
    add_product,
    update_product_price,
    get_min_price,
    get_product_owner,
    get_all_products,
    delete_product,
    save_user_cart,
)
from utils.maintenance import MaintenanceMode


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
        self.browser_manager = BrowserManager()  # Менеджер браузеров для переиспользования

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
        task_type: Literal["add_product", "add_cart", "update_price", "update_cart"],
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
            url: URL товара или корзины
            priority: Приоритет (1 - высокий, 2 - низкий)
            user_id: ID пользователя (для add_product/add_cart/update_cart)
            product_id: ID товара в БД (для update_price)
            product_name: Название товара (для логов)
            marketplace: Маркетплейс товара (для add_product/add_cart)
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
                if MaintenanceMode.is_active():
                    logger.warning("Режим техработ активен, воркер приостановлен")
                    await asyncio.sleep(30)
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
                delay = random.uniform(5, 15)
                logger.info(f"Задержка перед следующей задачей: {delay:.2f} сек")
                await asyncio.sleep(delay)

            except asyncio.TimeoutError:
                # Очередь пуста, ждем новых задач
                await asyncio.sleep(1)
                continue
            except Exception as e:
                logger.error(f"Ошибка в воркере очереди: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_task(self, task: dict):
        """Обрабатывает одну задачу из очереди"""
        task_type = task["type"]
        url = task["url"]

        try:
            # Для корзины используем отдельную логику
            if task_type == "add_cart":
                await self._handle_add_cart(task)
                return

            # Для обновления корзины используем отдельную логику
            if task_type == "update_cart":
                await self._handle_update_cart(task)
                return

            # Для товаров парсим через scrape_ozon_product (до 3 попыток)
            product_name = None
            price = None

            for attempt in range(1, 4):
                logger.info(f"Попытка парсинга {attempt}/3: {url}")

                # Передаём user_id в scraper
                user_id = task.get("user_id")
                product_name, price = await scrape_ozon_product(url, user_id=user_id)

                if product_name and price:
                    logger.info(f"Товар успешно спарсен: {product_name}, цена: {price}")
                    break
                elif product_name == 'no_connection':
                    logger.info("Cтраница 'нет соединения'")
                    await self.bot.send_message(
                        task["user_id"],
                        f"❌ Нет соединения с Ozon!"
                    )
                    return
                elif product_name == 'end':
                    logger.info('Товар закончился')
                    await self.bot.send_message(
                        task["user_id"],
                        f"❌ Товар закончился, рекомендуется удалить из списка: {url}"
                    )
                    return
                else:
                    logger.warning(f"Попытка {attempt} не удалась")
                    if attempt < 3:
                        await asyncio.sleep(5)

            # Если все 3 попытки провалились — включаем режим техработ
            if not product_name or not price:
                logger.error(f"Все 3 попытки парсинга провалились для {url}")
                await self._activate_maintenance_mode()

            if task_type == "add_product":
                await self._handle_add_product(task, product_name, price)
            elif task_type == "update_price":
                await self._handle_update_price(task, product_name, price)

        except Exception as e:
            logger.error(f"Ошибка при обработке задачи {task_type}: {e}", exc_info=True)

            if task_type in ["add_product", "add_cart"] and task["user_id"]:
                try:
                    await self.bot.send_message(
                        task["user_id"],
                        f"❌ Ошибка при добавлении: {str(e)}"
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

    async def _handle_add_cart(self, task: dict):
        """Обрабатывает задачу добавления корзины"""
        user_id = task["user_id"]
        cart_url = task["url"]
        marketplace = task.get("marketplace", "ozon")

        logger.info(f"Начало обработки корзины для пользователя {user_id}")

        # Парсим корзину (до 3 попыток) с использованием BrowserManager
        cart_products = None
        for attempt in range(1, 4):
            logger.info(f"Попытка парсинга корзины {attempt}/3: {cart_url}")

            # Определяем, нужно ли обновлять страницу
            # Проверяем, есть ли уже активный браузер для этого пользователя
            is_refresh = user_id in self.browser_manager._instances and \
                         self.browser_manager._instances[user_id]._cart_page is not None

            cart_products = await scrape_ozon_cart(
                cart_url,
                user_id=user_id,
                browser_manager=self.browser_manager,
                is_refresh=is_refresh
            )

            if cart_products:
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                break
            else:
                logger.warning(f"Попытка {attempt} не удалась")
                if attempt < 3:
                    await asyncio.sleep(5)

        # Если все 3 попытки провалились
        if not cart_products:
            logger.error(f"Все 3 попытки парсинга корзины провалились для {cart_url}")
            await self.bot.send_message(
                user_id,
                "❌ Не удалось получить данные корзины после 3 попыток.\n\n"
                "Возможные причины:\n"
                "- Обнаружена капча (требуется ручное решение)\n"
                "- Неверная ссылка\n"
                "- Корзина пуста или недоступна"
            )
            await self._activate_maintenance_mode()
            return

        # Получаем текущие товары пользователя
        current_products = await get_all_products(user_id)
        current_names = {p["name"] for p in current_products}
        cart_names = {p["name"] for p in cart_products}

        logger.info(f"Текущие товары пользователя: {len(current_names)}")
        logger.info(f"Товары в корзине: {len(cart_names)}")

        # Определяем, что нужно добавить и удалить
        to_add = cart_names - current_names
        to_remove = current_names - cart_names

        logger.info(f"Товаров к добавлению: {len(to_add)}")
        logger.info(f"Товаров к удалению: {len(to_remove)}")

        # Импортируем Marketplace для создания enum
        from core.models import Marketplace
        marketplace_enum = Marketplace(marketplace)

        # Удаляем товары, которых нет в новой корзине
        removed_count = 0
        for product in current_products:
            if product["name"] in to_remove:
                await delete_product(product["id"])
                removed_count += 1
                logger.info(f"Удалён товар: {product['name']}")

        # Добавляем новые товары
        added_count = 0
        for cart_product in cart_products:
            if cart_product["name"] in to_add:
                # Извлекаем число из строки цены
                price_match = re.search(r'(\d+)', cart_product["price"])
                if not price_match:
                    logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                    continue

                price_int = int(price_match.group(1))

                # Добавляем товар в БД (URL = название, т.к. у нас нет прямых ссылок)
                product_id = await add_product(
                    url=f"cart_item_{cart_product['name']}",  # Временный URL
                    name=cart_product["name"],
                    price=price_int,
                    user_id=user_id,
                    marketplace=marketplace_enum
                )

                if product_id:
                    added_count += 1
                    logger.info(f"Добавлен товар: {cart_product['name']} - {cart_product['price']}")

        # Отправляем итоговое уведомление
        summary = f"✅ Корзина обработана!\n\n"
        summary += f"📦 Всего товаров в корзине: {len(cart_products)}\n"
        summary += f"➕ Добавлено: {added_count}\n"
        summary += f"➖ Удалено: {removed_count}\n"

        if added_count > 0:
            summary += f"\n🆕 Новые товары:\n"
            for cart_product in cart_products:
                if cart_product["name"] in to_add:
                    summary += f"• {cart_product['name']} - {cart_product['price']}\n"

        if removed_count > 0:
            summary += f"\n🗑 Удалённые товары:\n"
            for product in current_products:
                if product["name"] in to_remove:
                    summary += f"• {product['name']}\n"

        await self.bot.send_message(user_id, summary)
        logger.info(f"Корзина успешно обработана для пользователя {user_id}")

        # Сохраняем ссылку на корзину в БД
        await save_user_cart(user_id, cart_url)
        logger.info(f"Ссылка на корзину сохранена для пользователя {user_id}")

    async def _handle_update_cart(self, task: dict):
        """Обрабатывает задачу обновления цен через корзину"""
        user_id = task["user_id"]
        cart_url = task["url"]

        logger.info(f"Начало обновления цен через корзину для пользователя {user_id}")

        # Парсим корзину (до 3 попыток) с использованием BrowserManager
        cart_products = None
        for attempt in range(1, 4):
            logger.info(f"Попытка парсинга корзины {attempt}/3: {cart_url}")

            # Определяем, нужно ли обновлять страницу
            # Проверяем, есть ли уже активный браузер для этого пользователя
            is_refresh = user_id in self.browser_manager._instances and \
                         self.browser_manager._instances[user_id]._cart_page is not None

            cart_products = await scrape_ozon_cart(
                cart_url,
                user_id=user_id,
                browser_manager=self.browser_manager,
                is_refresh=is_refresh
            )

            if cart_products:
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                break
            else:
                logger.warning(f"Попытка {attempt} не удалась")
                if attempt < 3:
                    await asyncio.sleep(5)

        # Если все 3 попытки провалились
        if not cart_products:
            logger.error(f"Все 3 попытки парсинга корзины провалились для пользователя {user_id}")
            await self._activate_maintenance_mode()
            return

        # Получаем текущие товары пользователя из БД
        current_products = await get_all_products(user_id)

        # Создаём словарь: название товара -> данные из корзины
        cart_products_dict = {p["name"]: p for p in cart_products}

        # Обновляем цены для существующих товаров
        updated_count = 0
        price_drops = []  # Список товаров со снижением цены

        for product in current_products:
            product_name = product["name"]

            # Если товар есть в корзине
            if product_name in cart_products_dict:
                cart_product = cart_products_dict[product_name]

                # Извлекаем число из строки цены
                price_match = re.search(r'(\d+)', cart_product["price"])
                if not price_match:
                    logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                    continue

                new_price = int(price_match.group(1))
                product_id = product["id"]

                # Получаем минимальную цену до обновления
                min_price_before = await get_min_price(product_id)

                # Обновляем цену в БД
                await update_product_price(product_id, new_price)
                updated_count += 1
                logger.info(f"✅ Цена обновлена для {product_name}: {new_price} ₽")

                # Проверяем, упала ли цена ниже минимальной
                if min_price_before and new_price < min_price_before:
                    price_drops.append({
                        "name": product_name,
                        "new_price": new_price,
                        "old_min": min_price_before,
                        "savings": min_price_before - new_price
                    })
                    logger.info(
                        f"🔔 Цена упала ниже минимальной! "
                        f"Было: {min_price_before} ₽, стало: {new_price} ₽"
                    )

        logger.info(f"Обновлено цен: {updated_count}")

        # Отправляем уведомления о снижении цен
        if price_drops:
            for drop in price_drops:
                notification_text = (
                    f"🔔 Цена снизилась!\n\n"
                    f"📦 {drop['name']}\n\n"
                    f"💰 Новая цена: {drop['new_price']} ₽\n"
                    f"📉 Было минимум: {drop['old_min']} ₽\n"
                    f"💵 Экономия: {drop['savings']} ₽"
                )

                try:
                    await self.bot.send_message(user_id, notification_text)
                    logger.info(f"Уведомление о снижении цены отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        logger.info(f"Обновление цен завершено для пользователя {user_id}")

    async def _handle_update_price(self, task: dict, product_name: str | None, price: str | None):
        """Обрабатывает задачу обновления цены"""
        product_id = task["product_id"]
        product_display_name = task.get("product_name", f"ID {product_id}")

        if not product_name or not price:
            logger.error(f"Не удалось обновить цену товара {product_display_name}")
            return

        # Извлекаем число из строки цены
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

            # Получаем владельца товара
            owner_id = await get_product_owner(product_id)
            if owner_id:
                logger.info(f"Отправка уведомления владельцу {owner_id}")

                try:
                    notification_text = (
                        f"🔔 Цена снизилась!\n\n"
                        f"📦 {product_display_name}\n\n"
                        f"💰 Новая цена: {price_int} ₽\n"
                        f"📉 Было минимум: {min_price_before} ₽\n"
                        f"💵 Экономия: {min_price_before - price_int} ₽"
                    )

                    await self.bot.send_message(owner_id, notification_text)
                    logger.info(f"Уведомление отправлено пользователю {owner_id}")
                except Exception as e:
                    logger.error(
                        f"Не удалось отправить уведомление пользователю {owner_id}: {e}"
                    )

    async def _activate_maintenance_mode(self):
        """Активирует режим техработ и уведомляет всех пользователей"""
        logger.warning("Активация режима техработ после 3 неудачных попыток парсинга")

        # Включаем режим техработ
        MaintenanceMode.enable()

        # Отправляем уведомление администратору
        await MaintenanceMode.notify_admin(
            self.bot,
            "🚧 Ошибка при парсинге товара! Активирован MaintenanceMode!",
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
