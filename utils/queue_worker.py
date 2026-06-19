import asyncio
import aiohttp
import mimetypes
import json
import random
import re
from pathlib import Path
from datetime import datetime
from typing import Literal

from utils.scraper import (
    scrape_avito_product,
    scrape_ozon_cart,
    scrape_ozon_cart_with_urls,
    scrape_wildberries_cart,
)
from core.database import mark_product_not_in_cart, mark_product_in_cart
from utils.browser_manager import BrowserManager
from core.models import Marketplace
from core.database import (
    add_product,
    update_product_price,
    update_product_url,
    update_product_image_url,
    update_product_image_path,
    get_min_price,
    get_all_products,
    get_product_by_url,
    save_user_cart,
)
from utils.maintenance import maintenance_mode
from loggers import logger
from config import config


async def _download_product_image(image_url: str | None, user_id: int, product_id: int) -> str | None:
    """Скачивает изображение товара на сервер и возвращает локальный путь."""
    if not image_url:
        return None

    images_dir = Path("product_images") / str(user_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    logger.warning(
                        f"Не удалось скачать изображение товара {product_id}: "
                        f"HTTP {response.status}"
                    )
                    return None

                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                suffix = mimetypes.guess_extension(content_type) or ".jpg"
                image_path = images_dir / f"{product_id}{suffix}"
                image_path.write_bytes(await response.read())
                logger.info(f"Изображение товара {product_id} сохранено: {image_path}")
                return str(image_path)

    except Exception as e:
        logger.warning(f"Не удалось скачать изображение товара {product_id}: {e}")
        return None


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
        self.queue_file = Path(config.QUEUE_FILE)

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
        task_type: Literal[
            "add_cart",
            "add_product",
            "update_cart",
            "update_product",
        ],
        url: str,
        user_id: int,
        priority: int = 2,
        marketplace: str | None = None,
        product_id: int | None = None,
    ):
        """
        Добавляет задачу в очередь.

        Args:
            task_type: Тип задачи
            url: URL товара или корзины
            priority: Приоритет (1 - высокий, 2 - низкий)
            user_id: ID пользователя (для add_product/add_cart/update_cart)
            marketplace: Маркетплейс товара (для add_product/add_cart)
            product_id: ID товара для задачи обновления отдельного товара
        """
        task = {
            "type": task_type,
            "url": url,
            "user_id": user_id,
            "marketplace": marketplace,
            "product_id": product_id,
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
                # Получаем задачу с наивысшим приоритетом
                priority, counter, task = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )

                # Проверяем персональный режим техработ для пользователя
                user_id = task.get("user_id")
                task_marketplace = task.get("marketplace")
                if user_id and maintenance_mode.is_active(user_id, task_marketplace):
                    logger.warning(
                        f"Пользователь {user_id} в режиме техработ, задача пропущена. "
                        f"Тип: {task['type']}, маркетплейс={task_marketplace}"
                    )
                    await asyncio.sleep(1)
                    continue

                logger.info(
                    f"Обработка задачи: {task['type']}, "
                    f"user_id={user_id}, "
                    f"приоритет={priority}, осталось в очереди={self.queue.qsize()}"
                )

                await self._process_task(task)

                # Рандомная задержка между задачами
                delay = random.uniform(10, 20)
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

        try:
            # Для добавления корзины используем отдельную логику
            if task_type == "add_cart":
                await self._handle_add_cart(task)
                return

            # Для обновления корзины используем отдельную логику
            if task_type == "update_cart":
                await self._handle_update_cart(task)
                return

            if task_type == "add_product":
                await self._handle_add_avito_product(task)
                return

            if task_type == "update_product":
                await self._handle_update_avito_product(task)
                return

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

    async def _scrape_avito_with_retries(self, task: dict):
        """Парсит объявление Avito с повторными попытками."""
        for attempt in range(1, 4):
            result = await scrape_avito_product(
                task["url"],
                user_id=task["user_id"],
                browser_manager=self.browser_manager,
                maintenance_callback=lambda: self._activate_maintenance_mode(
                    task["user_id"],
                    Marketplace.AVITO.value,
                    reason="обнаружена блокировка при парсинге объявления"
                )
            )
            if result == "maintenance_required" or result:
                return result
            logger.warning(f"Avito: попытка парсинга {attempt}/3 не удалась")
            if attempt < 3:
                await asyncio.sleep(5)

        await self._activate_maintenance_mode(
            task["user_id"],
            Marketplace.AVITO.value,
            reason="три неудачные попытки парсинга объявления"
        )
        return "maintenance_required"

    async def _handle_add_avito_product(self, task: dict):
        """Добавляет одно объявление Avito."""
        user_id = task["user_id"]
        existing_product = await get_product_by_url(
            user_id,
            task["url"],
            Marketplace.AVITO
        )
        if existing_product:
            await self.bot.send_message(user_id, "ℹ️ Это объявление уже отслеживается.")
            return

        product = await self._scrape_avito_with_retries(task)
        if not product or product == "maintenance_required":
            if product != "maintenance_required":
                await self.bot.send_message(
                    user_id,
                    "❌ Не удалось получить данные объявления Avito. Попробуйте позже."
                )
            return

        product_id = await add_product(
            name=product["name"],
            price=product["price"],
            user_id=user_id,
            marketplace=Marketplace.AVITO,
            url=task["url"],
            image_url=product.get("image_url")
        )
        if not product_id:
            raise RuntimeError("Не удалось сохранить объявление Avito")

        image_path = await _download_product_image(
            product.get("image_url"),
            user_id,
            product_id
        )
        await update_product_image_path(product_id, image_path)
        await self.bot.send_message(
            user_id,
            f"✅ Объявление добавлено!\n\n📦 {product['name']}\n💰 {product['price']} ₽"
        )

    async def _handle_update_avito_product(self, task: dict):
        """Обновляет цену одного объявления Avito."""
        stored_product = await get_product_by_url(
            task["user_id"],
            task["url"],
            Marketplace.AVITO
        )
        if not stored_product or stored_product["id"] != task.get("product_id"):
            logger.warning("Товар Avito удалён или не принадлежит пользователю")
            return

        product = await self._scrape_avito_with_retries(task)
        if not product or product == "maintenance_required":
            return

        product_id = stored_product["id"]

        min_price_before = await get_min_price(product_id)
        await update_product_price(product_id, product["price"])

        if min_price_before is not None and product["price"] < min_price_before:
            await self.bot.send_message(
                task["user_id"],
                "🔔 Цена снизилась!\n\n"
                f"📦 {product['name']}\n\n"
                f"💰 Новая цена: {product['price']} ₽\n"
                f"📉 Было минимум: {min_price_before} ₽\n"
                f"💵 Экономия: {min_price_before - product['price']} ₽"
            )

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

            # Определяем, нужно ли обновлять страницу или открывать новую
            # Проверяем, есть ли уже активный браузер для этого пользователя
            # И совпадает ли URL корзины с текущим открытым
            is_refresh = False
            if (
                marketplace == Marketplace.OZON.value and
                self.browser_manager.has_cart_page(user_id, marketplace, cart_url)
            ):
                is_refresh = True
                logger.info(f"URL корзины {marketplace} совпадает, используем refresh")
            elif marketplace == Marketplace.WILDBERRIES.value:
                logger.info("Для Wildberries открываем сохранённый URL корзины заново")
            else:
                logger.info(
                    f"URL корзины {marketplace} изменился или вкладка не открыта, "
                    "открываем новую"
                )

            match marketplace:
                case Marketplace.OZON.value:
                    cart_products = await scrape_ozon_cart_with_urls(
                        cart_url,
                        user_id=user_id,
                        browser_manager=self.browser_manager,
                        is_refresh=is_refresh,
                        maintenance_callback=lambda: self._activate_maintenance_mode(
                            user_id,
                            marketplace,
                            reason="обнаружена блокировка при парсинге корзины"
                        )
                    )
                case Marketplace.WILDBERRIES.value:
                    cart_products = await scrape_wildberries_cart(
                        cart_url,
                        user_id=user_id,
                        browser_manager=self.browser_manager,
                        is_refresh=is_refresh,
                        maintenance_callback=lambda: self._activate_maintenance_mode(
                            user_id,
                            marketplace,
                            reason="обнаружена блокировка при парсинге корзины"
                        )
                    )
                case _:
                    logger.error(f"Неподдерживаемый маркетплейс корзины: {marketplace}")
                    return

            # Проверяем на специальный маркер maintenance_required
            if cart_products == "maintenance_required":
                logger.error("Получен маркер maintenance_required - прерываем попытки парсинга")
                return

            if cart_products:
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                break
            else:
                logger.warning(f"Попытка {attempt} не удалась")
                if attempt < 3:
                    await asyncio.sleep(5)

        # Если все 3 попытки провалились
        if not cart_products:
            logger.error(
                f"Все 3 попытки парсинга корзины провалились для {cart_url} "
                f"пользователя {user_id}"
            )
            await self._activate_maintenance_mode(
                user_id,
                marketplace,
                reason="три неудачные попытки парсинга корзины"
            )
            return

        # Получаем текущие товары пользователя (включая удалённые из корзины)
        current_products = await get_all_products(
            user_id,
            marketplace=marketplace,
            in_cart_only=False
        )

        # Создаём словари для быстрого поиска
        current_products_dict = {p["name"]: p for p in current_products}
        cart_products_dict = {p["name"]: p for p in cart_products}

        logger.info(f"Текущие товары пользователя (всего): {len(current_products)}")
        logger.info(f"Товары в корзине: {len(cart_products)}")

        marketplace_enum = Marketplace(marketplace)

        # Счётчики изменений
        added_count = 0
        updated_count = 0
        removed_count = 0
        restored_count = 0

        # Обрабатываем товары из корзины
        for cart_product in cart_products:
            product_name = cart_product["name"]

            # Извлекаем число из строки цены
            price_match = re.search(r'([\d\s\xa0]+)', cart_product["price"])
            if not price_match:
                logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                continue

            new_price = int(re.sub(r'[\s\xa0]', '', price_match.group(1)))
            product_url = cart_product.get("url")
            product_image_url = cart_product.get("image_url")

            # Если товар уже есть в БД
            if product_name in current_products_dict:
                existing_product = current_products_dict[product_name]
                product_id = existing_product["id"]

                # Если товар был удалён из корзины ранее — восстанавливаем
                if existing_product["in_cart"] == 0:
                    await mark_product_in_cart(product_id)
                    restored_count += 1
                    logger.info(f"Восстановлен товар в корзине: {product_name}")

                # Обновляем цену
                await update_product_price(product_id, new_price)
                await update_product_url(product_id, product_url)
                await update_product_image_url(product_id, product_image_url)

                image_path = await _download_product_image(product_image_url, user_id, product_id)
                await update_product_image_path(product_id, image_path)
                updated_count += 1
                logger.info(f"Цена обновлена для {product_name}: {new_price} ₽")

            # Если товар новый — добавляем
            else:
                product_id = await add_product(
                    name=product_name,
                    price=new_price,
                    user_id=user_id,
                    marketplace=marketplace_enum,
                    url=product_url,
                    image_url=product_image_url
                )

                if product_id:
                    image_path = await _download_product_image(product_image_url, user_id, product_id)
                    await update_product_image_path(product_id, image_path)
                    added_count += 1
                    logger.info(f"Добавлен товар: {product_name} - {new_price} ₽")

        # Помечаем товары, которых нет в новой корзине, как удалённые (но не удаляем из БД)
        for product in current_products:
            if product["in_cart"] == 1 and product["name"] not in cart_products_dict:
                await mark_product_not_in_cart(product["id"])
                removed_count += 1
                logger.info(f"Товар помечен как удалённый из корзины: {product['name']}")

        match marketplace:
            case Marketplace.OZON.value:
                marketplace_name = "Ozon"
            case Marketplace.WILDBERRIES.value:
                marketplace_name = "Wildberries"
            case _:
                marketplace_name = marketplace

        # Отправляем итоговое уведомление
        summary = f"✅ Корзина {marketplace_name} синхронизирована!\n\n"
        summary += (
            f"Эта ссылка теперь используется как единственная активная корзина "
            f"{marketplace_name}.\n\n"
        )
        summary += f"📦 Товаров в активной корзине: {len(cart_products)}\n"
        summary += f"➕ Добавлено новых: {added_count}\n"
        summary += f"🔄 Обновлено цен: {updated_count}\n"
        summary += f"↩️ Возобновлено отслеживание: {restored_count}\n"
        summary += f"❌ Перестали отслеживаться: {removed_count}\n"

        if added_count > 0:
            summary += f"\n🆕 Новые товары:\n"
            for cart_product in cart_products:
                if cart_product["name"] not in current_products_dict:
                    summary += f"• {cart_product['name']} - {cart_product['price']}\n"

        if restored_count > 0:
            summary += "\n↩️ Отслеживание возобновлено:\n"
            for product in current_products:
                if product["in_cart"] == 0 and product["name"] in cart_products_dict:
                    summary += f"• {product['name']}\n"

        if removed_count > 0:
            summary += (
                "\n❌ Перестали отслеживаться:\n"
                "Эти товары отсутствуют в активной корзине. Их последняя цена и история "
                "сохранены, но новые цены и уведомления поступать не будут.\n"
            )
            for product in current_products:
                if product["in_cart"] == 1 and product["name"] not in cart_products_dict:
                    summary += f"• {product['name']}\n"

        await self.bot.send_message(user_id, summary)
        logger.info(f"Корзина успешно обработана для пользователя {user_id}")

        # Сохраняем ссылку на корзину в БД
        await save_user_cart(user_id, cart_url, marketplace_enum)
        logger.info(f"Ссылка на корзину сохранена для пользователя {user_id}")

    async def _handle_update_cart(self, task: dict):
        """Обрабатывает задачу обновления цен через корзину"""
        user_id = task["user_id"]
        cart_url = task["url"]
        marketplace = task.get("marketplace", Marketplace.OZON.value)

        logger.info(
            f"Начало обновления цен через корзину для пользователя {user_id}, "
            f"маркетплейс={marketplace}"
        )

        # Парсим корзину (до 3 попыток) с использованием BrowserManager
        cart_products = None
        for attempt in range(1, 4):
            logger.info(f"Попытка парсинга корзины {attempt}/3: {cart_url}")

            # Определяем, нужно ли обновлять страницу или открывать новую
            # Проверяем, есть ли уже активный браузер для этого пользователя
            # И совпадает ли URL корзины с текущим открытым
            is_refresh = False
            if (
                marketplace == Marketplace.OZON.value and
                self.browser_manager.has_cart_page(user_id, marketplace, cart_url)
            ):
                is_refresh = True
                logger.info(f"URL корзины {marketplace} совпадает, используем refresh")
            elif marketplace == Marketplace.WILDBERRIES.value:
                logger.info("Для Wildberries открываем сохранённый URL корзины заново")
            else:
                logger.info(
                    f"URL корзины {marketplace} изменился или вкладка не открыта, "
                    "открываем новую"
                )

            match marketplace:
                case Marketplace.OZON.value:
                    cart_products = await scrape_ozon_cart(
                        cart_url,
                        user_id=user_id,
                        browser_manager=self.browser_manager,
                        is_refresh=is_refresh,
                        maintenance_callback=lambda: self._activate_maintenance_mode(
                            user_id,
                            marketplace,
                            reason="обнаружена блокировка при парсинге корзины"
                        )
                    )
                case Marketplace.WILDBERRIES.value:
                    cart_products = await scrape_wildberries_cart(
                        cart_url,
                        user_id=user_id,
                        browser_manager=self.browser_manager,
                        is_refresh=is_refresh,
                        maintenance_callback=lambda: self._activate_maintenance_mode(
                            user_id,
                            marketplace,
                            reason="обнаружена блокировка при парсинге корзины"
                        )
                    )
                case _:
                    logger.error(f"Неподдерживаемый маркетплейс корзины: {marketplace}")
                    return

            # Проверяем на специальный маркер maintenance_required
            if cart_products == "maintenance_required":
                logger.error("Получен маркер maintenance_required - прерываем попытки парсинга")
                return

            if cart_products:
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                break
            else:
                logger.warning(f"Попытка {attempt} не удалась")
                if attempt < 3:
                    await asyncio.sleep(5)

        # Если все 3 попытки провалились
        if not cart_products:
            logger.error(
                f"Все 3 попытки парсинга корзины провалились для {cart_url} "
                f"пользователя {user_id}"
            )
            await self._activate_maintenance_mode(
                user_id,
                marketplace,
                reason="три неудачные попытки парсинга корзины"
            )
            return

        # Получаем текущие товары пользователя из БД (включая удалённые из корзины)
        current_products = await get_all_products(
            user_id,
            marketplace=marketplace,
            in_cart_only=False
        )

        # Создаём словари для быстрого поиска
        current_products_dict = {p["name"]: p for p in current_products}
        cart_products_dict = {p["name"]: p for p in cart_products}

        # Счётчики изменений
        updated_count = 0
        restored_count = 0
        removed_count = 0
        price_drops = []  # Список товаров со снижением цены

        # Обрабатываем товары из корзины
        for cart_product in cart_products:
            product_name = cart_product["name"]

            # Если товар есть в БД
            if product_name in current_products_dict:
                existing_product = current_products_dict[product_name]
                product_id = existing_product["id"]

                # Если товар был удалён из корзины ранее — восстанавливаем
                if existing_product["in_cart"] == 0:
                    await mark_product_in_cart(product_id)
                    restored_count += 1
                    logger.info(f"Восстановлен товар в корзине: {product_name}")

                # Извлекаем число из строки цены
                price_match = re.search(r'([\d\s\xa0]+)', cart_product["price"])
                if not price_match:
                    logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                    continue

                new_price = int(re.sub(r'[\s\xa0]', '', price_match.group(1)))

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

        # Помечаем товары, которых нет в новой корзине, как удалённые (но не удаляем из БД)
        for product in current_products:
            if product["in_cart"] == 1 and product["name"] not in cart_products_dict:
                await mark_product_not_in_cart(product["id"])
                removed_count += 1
                logger.info(f"Товар помечен как удалённый из корзины: {product['name']}")

        logger.info(
            f"Обновлено цен: {updated_count}, восстановлено: {restored_count}, "
            f"удалено: {removed_count}"
        )

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

    async def _activate_maintenance_mode(
        self,
        user_id: int,
        marketplace: str | None = None,
        reason: str = "обнаружена блокировка"
    ):
        """
        Активирует режим техработ для конкретного пользователя.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс, для которого обнаружена блокировка
            reason: Причина включения режима техработ
        """
        logger.warning(
            f"Активация режима техработ для пользователя {user_id}, "
            f"маркетплейс={marketplace}, причина={reason}"
        )

        # Включаем режим техработ для пользователя
        maintenance_mode.enable(user_id, marketplace)

        # Отправляем уведомление администратору
        try:
            marketplace_suffix = f"_{marketplace}" if marketplace else ""
            await self.bot.send_message(
                config.ADMIN_USER_ID,
                f"🚧 Активирован maintenance mode для пользователя {user_id}\n"
                f"Маркетплейс: {marketplace or 'не определён'}\n"
                f"Причина: {reason}\n\n"
                f"Для разблокировки удалите файл: "
                f"maintenance_flags/maintenance_{user_id}{marketplace_suffix}.flag"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление администратору: {e}")

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

            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)

            logger.info(f"Очередь сохранена на диск: {len(tasks)} задач")
        except Exception as e:
            logger.error(f"Ошибка при сохранении очереди: {e}", exc_info=True)

    async def _load_queue(self):
        """Загружает очередь с диска"""
        if not self.queue_file.exists():
            logger.info("Файл очереди не найден, начинаем с пустой очереди")
            return

        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
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
            self.queue_file.unlink()
        except Exception as e:
            logger.error(f"Ошибка при загрузке очереди: {e}", exc_info=True)
