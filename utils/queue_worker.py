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
    AVITO_CLOSED_MARKER,
    AVITO_DELETED_MARKER,
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
    update_product_cart_identity,
    get_price_snapshot,
    get_all_products,
    get_product_by_url,
    has_marketplace_authorization,
    save_user_cart,
)
from utils.maintenance import maintenance_mode
from loggers import logger
from config import config


def _normalize_identity_part(value: str | None) -> str:
    """Нормализует часть ключа позиции корзины."""
    if not value:
        return ""

    normalized = value.replace("ё", "е").replace("Ё", "Е").lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s+([/•.,])", r"\1", normalized)
    normalized = re.sub(r"([/•.,])\s+", r"\1 ", normalized)
    normalized = re.sub(r"\.(?=\s*(?:•|$))", "", normalized)
    return normalized.strip()


def _build_cart_item_key(product: dict, marketplace: str) -> str:
    """Строит стабильный ключ позиции корзины без учета цены."""
    name_part = _normalize_identity_part(product.get("name"))
    variant_part = _normalize_identity_part(product.get("variant_text"))

    if variant_part:
        return f"{marketplace}|name:{name_part}|variant:{variant_part}"

    if marketplace == Marketplace.OZON.value:
        return f"{marketplace}|name:{name_part}"

    for fallback_field in ("url", "image_url"):
        fallback_part = _normalize_identity_part(product.get(fallback_field))
        if fallback_part:
            return f"{marketplace}|name:{name_part}|{fallback_field}:{fallback_part}"

    return f"{marketplace}|name:{name_part}"


def _assign_cart_item_keys(products: list[dict], marketplace: str) -> dict[int, str]:
    """Назначает ключи товарам корзины и разруливает дубли внутри одного парсинга."""
    seen_keys: dict[str, int] = {}
    assigned_keys: dict[int, str] = {}

    for index, product in enumerate(products):
        base_key = _build_cart_item_key(product, marketplace)
        seen_keys[base_key] = seen_keys.get(base_key, 0) + 1

        if seen_keys[base_key] == 1:
            assigned_keys[index] = base_key
            continue

        assigned_keys[index] = f"{base_key}|occurrence:{seen_keys[base_key]}"
        logger.warning(
            f"Найден дубль ключа позиции корзины: {product.get('name')} "
            f"({seen_keys[base_key]}-е совпадение)"
        )

    return assigned_keys


def _get_stored_cart_item_key(product: dict, marketplace: str) -> str:
    """Возвращает сохраненный ключ позиции или строит совместимый fallback."""
    if marketplace == Marketplace.OZON.value and not product.get("variant_text"):
        stored_key = product.get("cart_item_key") or ""
        if "|occurrence:" in stored_key:
            return stored_key

        name_part = _normalize_identity_part(product.get("name"))
        return f"{marketplace}|name:{name_part}"

    return product.get("cart_item_key") or _build_cart_item_key(product, marketplace)


def _is_legacy_name_only_product(product: dict, marketplace: str) -> bool:
    """Проверяет старую запись Ozon без надежного ключа варианта."""
    if marketplace != Marketplace.OZON.value or product.get("variant_text"):
        return False

    stored_key = product.get("cart_item_key")
    name_key = _build_cart_item_key(product, marketplace)

    return not stored_key or stored_key == name_key


def _unpack_cart_parse_result(result) -> tuple[list[dict], bool, int]:
    """Возвращает товары и признак частичного парсинга корзины."""
    if isinstance(result, dict):
        products = result.get("products") or []
        partial = bool(result.get("partial"))
        skipped_count = int(result.get("skipped_count") or 0)
        return products, partial, skipped_count

    return result or [], False, 0


def _should_use_existing_cart_page(
    browser_manager,
    user_id: int,
    marketplace: str,
    cart_url: str,
    attempt: int,
) -> bool:
    """Определяет, можно ли не выполнять новый переход по URL корзины."""
    if not browser_manager.has_cart_page(user_id, marketplace, cart_url):
        return False

    match marketplace:
        case Marketplace.OZON.value:
            return True
        case Marketplace.WILDBERRIES.value:
            return attempt > 1
        case _:
            return False


def _index_current_products_by_key(products: list[dict], marketplace: str) -> dict[str, dict]:
    """Индексирует сохраненные товары только по однозначным ключам."""
    grouped_products: dict[str, list[dict]] = {}

    for product in products:
        if _is_legacy_name_only_product(product, marketplace):
            continue

        key = _get_stored_cart_item_key(product, marketplace)
        grouped_products.setdefault(key, []).append(product)

    unique_products = {}
    for key, items in grouped_products.items():
        if len(items) == 1:
            unique_products[key] = items[0]
            continue

        logger.warning(
            f"Неоднозначный сохраненный ключ позиции корзины: {key}, "
            f"совпадений: {len(items)}"
        )

    return unique_products


def _find_legacy_product_by_name_and_price(
    products_by_name: dict[str, list[dict]],
    product_name: str,
    processed_product_ids: set[int],
    marketplace: str,
    new_price: int | None
) -> dict | None:
    """Ищет старую запись по имени, цене или порядку для миграции старых дублей."""
    same_name_products = [
        product for product in products_by_name.get(product_name, [])
        if product["id"] not in processed_product_ids
        and _is_legacy_name_only_product(product, marketplace)
    ]

    if len(same_name_products) == 1:
        return same_name_products[0]

    if len(same_name_products) > 1 and new_price is not None:
        same_price_products = [
            product for product in same_name_products
            if product.get("current_price") == new_price
        ]
        if len(same_price_products) == 1:
            logger.info(
                f"Старая позиция сопоставлена по названию и цене: {product_name}"
            )
            return same_price_products[0]

    if len(same_name_products) > 1:
        logger.info(
            f"Старая позиция сопоставлена по названию и порядку: {product_name}"
        )
        return same_name_products[0]

    return None


def _format_product_title(product: dict) -> str:
    """Формирует название товара с вариантом для пользовательских сообщений."""
    variant_text = product.get("variant_text")
    if variant_text:
        return f"{product['name']} ({variant_text})"

    return product["name"]


async def _download_product_image(
    image_url: str | None,
    user_id: int,
    product_id: int
) -> str | None:
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

                content_type = (
                    response.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
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

        await self.browser_manager.close_all()
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

                # Проверяем персональный флаг и общую паузу маркетплейса
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
                    reason="обнаружена блокировка при парсинге объявления",
                    host_level=True,
                ),
                reuse_page=attempt > 1,
            )
            if result in (
                "maintenance_required",
                AVITO_CLOSED_MARKER,
                AVITO_DELETED_MARKER,
            ) or result:
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
        if product == AVITO_CLOSED_MARKER:
            await self.bot.send_message(
                user_id,
                "❌ Объявление Avito снято с публикации и не может быть добавлено."
            )
            return

        if product == AVITO_DELETED_MARKER:
            await self.bot.send_message(
                user_id,
                "❌ Объявление Avito больше нельзя посмотреть: пользователь его удалил."
            )
            return

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
        if product in (AVITO_CLOSED_MARKER, AVITO_DELETED_MARKER):
            product_id = stored_product["id"]

            if stored_product["in_cart"] == 1:
                await mark_product_not_in_cart(product_id)
                if product == AVITO_DELETED_MARKER:
                    status_text = (
                        "❌ Объявление Avito больше нельзя посмотреть: "
                        "пользователь его удалил."
                    )
                    log_reason = "удалено пользователем"
                else:
                    status_text = "❌ Объявление Avito снято с публикации."
                    log_reason = "снято с публикации"

                await self.bot.send_message(
                    task["user_id"],
                    f"{status_text}\n\n"
                    f"📦 {stored_product['name']}\n\n"
                    "Мониторинг этого объявления остановлен. Последняя цена и история "
                    "сохранены в списке позиций."
                )
                logger.info(
                    f"Мониторинг объявления Avito остановлен: product_id={product_id}, "
                    f"причина={log_reason}"
                )
            else:
                logger.info(
                    f"Объявление Avito уже неактивно: product_id={product_id}"
                )

            return

        if not product or product == "maintenance_required":
            return

        product_id = stored_product["id"]

        price_snapshot = await get_price_snapshot(product_id)
        previous_price = price_snapshot["previous_price"]
        await update_product_price(product_id, product["price"])
        logger.info(
            f"✅ Цена Avito обновлена для {product['name']}: {product['price']} ₽"
        )

        if previous_price is not None and product["price"] < previous_price:
            min_price = min(
                price_snapshot["min_price"] or product["price"],
                product["price"]
            )
            max_price = max(
                price_snapshot["max_price"] or product["price"],
                product["price"]
            )
            await self.bot.send_message(
                task["user_id"],
                "🔔 Цена снизилась!\n\n"
                f"📦 {product['name']}\n\n"
                f"💰 Новая цена: {product['price']} ₽\n"
                f"📉 Минимальная цена: {min_price} ₽\n"
                f"📈 Максимальная цена: {max_price} ₽\n"
                f"💵 Экономия: {max_price - product['price']} ₽"
            )

    async def _notify_product_unavailable(
        self,
        user_id: int,
        product: dict,
        marketplace: str
    ):
        """Уведомляет пользователя, что товар закончился и больше не отслеживается."""
        match marketplace:
            case Marketplace.OZON.value:
                marketplace_name = "Ozon"
            case Marketplace.WILDBERRIES.value:
                marketplace_name = "Wildberries"
            case _:
                marketplace_name = marketplace

        try:
            await self.bot.send_message(
                user_id,
                "⚠️ Товар закончился и больше не отслеживается.\n\n"
                f"📦 {_format_product_title(product)}\n"
                f"🛒 Маркетплейс: {marketplace_name}\n\n"
                "Последняя цена и история сохранены в списке позиций."
            )
        except Exception as e:
            logger.error(
                f"Не удалось отправить уведомление о закончившемся товаре "
                f"пользователю {user_id}: {e}"
            )

    async def _notify_product_available(
        self,
        user_id: int,
        product: dict,
        marketplace: str,
        price: int
    ):
        """Уведомляет пользователя, что товар снова доступен и отслеживается."""
        match marketplace:
            case Marketplace.OZON.value:
                marketplace_name = "Ozon"
            case Marketplace.WILDBERRIES.value:
                marketplace_name = "Wildberries"
            case _:
                marketplace_name = marketplace

        try:
            await self.bot.send_message(
                user_id,
                "✅ Товар снова в наличии и отслеживается.\n\n"
                f"📦 {_format_product_title(product)}\n"
                f"🛒 Маркетплейс: {marketplace_name}\n"
                f"💰 Текущая цена: {price} ₽\n\n"
                "История цен сохранена, обновления и уведомления снова включены."
            )
        except Exception as e:
            logger.error(
                f"Не удалось отправить уведомление о восстановлении товара "
                f"пользователю {user_id}: {e}"
            )

    async def _notify_authorization_lost(
        self,
        user_id: int,
        marketplace: str,
    ) -> None:
        """Уведомляет ранее авторизованного пользователя о завершении сессии."""
        try:
            was_authorized = await has_marketplace_authorization(user_id, marketplace)
        except Exception as e:
            logger.error(
                f"Не удалось проверить историю авторизации пользователя {user_id} "
                f"в {marketplace}: {e}",
                exc_info=True,
            )
            return

        if not was_authorized:
            logger.info(
                f"Кнопка входа {marketplace} обнаружена у пользователя {user_id}, "
                "но подтвержденной авторизации нет — уведомление не отправляется"
            )
            return

        match marketplace:
            case Marketplace.OZON.value:
                marketplace_name = "Ozon"
            case Marketplace.WILDBERRIES.value:
                marketplace_name = "Wildberries"
            case _:
                logger.warning(
                    f"Неизвестный маркетплейс для уведомления об авторизации: {marketplace}"
                )
                return

        try:
            await self.bot.send_message(
                user_id,
                f"⚠️ Авторизация в {marketplace_name} больше не активна.\n\n"
                "Выполните повторную авторизацию через раздел «🔐 Авторизация», чтобы бот "
                "снова использовал ваши персональные цены.\n\n"
                "Если вы больше не хотите получать такие сообщения, откройте раздел "
                "«🔐 Авторизация» и нажмите «🗑 Удалить мои данные»."
            )
            logger.info(
                f"Пользователю {user_id} отправлено уведомление о завершении "
                f"авторизации в {marketplace_name}"
            )
        except Exception as e:
            logger.error(
                f"Не удалось уведомить пользователя {user_id} о завершении "
                f"авторизации в {marketplace_name}: {e}"
            )

    def _create_authorization_lost_callback(self, user_id: int, marketplace: str):
        """Создает callback с одним уведомлением на задачу очереди."""
        notification_processed = False

        async def authorization_lost_callback() -> None:
            nonlocal notification_processed

            if notification_processed:
                return

            notification_processed = True
            await self._notify_authorization_lost(user_id, marketplace)

        return authorization_lost_callback

    async def _handle_add_cart(self, task: dict):
        """Обрабатывает задачу добавления корзины"""
        user_id = task["user_id"]
        cart_url = task["url"]
        marketplace = task.get("marketplace", "ozon")

        logger.info(f"Начало обработки корзины для пользователя {user_id}")

        # Парсим корзину (до 3 попыток) с использованием BrowserManager
        cart_products = None
        cart_parse_partial = False
        skipped_products_count = 0
        authorization_lost_callback = self._create_authorization_lost_callback(
            user_id,
            marketplace,
        )
        for attempt in range(1, 4):
            logger.info(f"Попытка парсинга корзины {attempt}/3: {cart_url}")

            # Определяем, нужно ли обновлять страницу или открывать новую
            # Проверяем, есть ли уже активный браузер для этого пользователя
            # И совпадает ли URL корзины с текущим открытым
            is_refresh = _should_use_existing_cart_page(
                self.browser_manager,
                user_id,
                marketplace,
                cart_url,
                attempt,
            )
            if marketplace == Marketplace.OZON.value and is_refresh:
                logger.info(f"URL корзины {marketplace} совпадает, используем refresh")
            elif marketplace == Marketplace.WILDBERRIES.value:
                if is_refresh:
                    logger.info("Повторно используем загруженную страницу Wildberries")
                else:
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
                            reason="обнаружена блокировка при парсинге корзины",
                            host_level=True,
                        ),
                        authorization_lost_callback=authorization_lost_callback,
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
                            reason="обнаружена блокировка при парсинге корзины",
                            host_level=True,
                        ),
                        authorization_lost_callback=authorization_lost_callback,
                    )
                case _:
                    logger.error(f"Неподдерживаемый маркетплейс корзины: {marketplace}")
                    return

            # Проверяем на специальный маркер maintenance_required
            if cart_products == "maintenance_required":
                logger.error("Получен маркер maintenance_required - прерываем попытки парсинга")
                return

            parsed_products, partial, skipped_count = _unpack_cart_parse_result(cart_products)
            if parsed_products:
                cart_products = parsed_products
                cart_parse_partial = partial
                skipped_products_count = skipped_count
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                if cart_parse_partial:
                    logger.warning(
                        f"Корзина распарсена частично, пропущено блоков: "
                        f"{skipped_products_count}"
                    )
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

        # Создаём индексы для поиска по стабильному ключу позиции корзины
        cart_item_keys = _assign_cart_item_keys(cart_products, marketplace)
        current_products_by_key = _index_current_products_by_key(current_products, marketplace)
        current_products_by_name: dict[str, list[dict]] = {}
        for product in current_products:
            current_products_by_name.setdefault(product["name"], []).append(product)

        cart_keys = set(cart_item_keys.values())
        processed_product_ids: set[int] = set()

        logger.info(f"Текущие товары пользователя (всего): {len(current_products)}")
        logger.info(f"Товары в корзине: {len(cart_products)}")

        marketplace_enum = Marketplace(marketplace)

        # Счётчики изменений
        added_count = 0
        updated_count = 0
        removed_count = 0
        restored_count = 0
        added_item_keys: set[str] = set()
        restored_product_ids: set[int] = set()
        removed_product_ids: set[int] = set()

        # Обрабатываем товары из корзины
        for index, cart_product in enumerate(cart_products):
            product_name = cart_product["name"]
            cart_item_key = cart_item_keys[index]
            variant_text = cart_product.get("variant_text")
            product_unavailable = bool(cart_product.get("unavailable"))

            # Извлекаем число из строки цены
            new_price = None
            if not product_unavailable:
                price_match = re.search(r'([\d\s\xa0]+)', cart_product["price"])
                if not price_match:
                    logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                    continue

                new_price = int(re.sub(r'[\s\xa0]', '', price_match.group(1)))

            product_url = cart_product.get("url")
            product_image_url = cart_product.get("image_url")

            # Если товар уже есть в БД
            existing_product = current_products_by_key.get(cart_item_key)

            # Совместимость со старыми записями без ключа: восстанавливаем только
            # однозначное совпадение по названию, чтобы не сливать разные варианты.
            if not existing_product:
                existing_product = _find_legacy_product_by_name_and_price(
                    current_products_by_name,
                    product_name,
                    processed_product_ids,
                    marketplace,
                    new_price
                )

            if existing_product:
                product_id = existing_product["id"]
                processed_product_ids.add(product_id)

                if product_unavailable:
                    if existing_product["in_cart"] == 1:
                        await mark_product_not_in_cart(product_id)
                        removed_count += 1
                        removed_product_ids.add(product_id)
                        await self._notify_product_unavailable(
                            user_id,
                            cart_product,
                            marketplace
                        )
                        logger.info(f"Товар закончился и отключен: {product_name}")
                    await update_product_cart_identity(product_id, variant_text, cart_item_key)
                    continue

                # Если товар был удалён из корзины ранее — восстанавливаем
                if existing_product["in_cart"] == 0:
                    await mark_product_in_cart(product_id)
                    restored_count += 1
                    restored_product_ids.add(product_id)
                    await self._notify_product_available(
                        user_id,
                        cart_product,
                        marketplace,
                        new_price
                    )
                    logger.info(f"Восстановлен товар в корзине: {product_name}")

                # Обновляем цену
                await update_product_price(product_id, new_price)
                if product_url:
                    await update_product_url(product_id, product_url)
                if product_image_url:
                    await update_product_image_url(product_id, product_image_url)
                await update_product_cart_identity(product_id, variant_text, cart_item_key)

                image_path = await _download_product_image(product_image_url, user_id, product_id)
                if image_path:
                    await update_product_image_path(product_id, image_path)
                updated_count += 1
                logger.info(f"Цена обновлена для {product_name}: {new_price} ₽")

            # Если товар новый — добавляем
            else:
                if product_unavailable:
                    logger.info(f"Закончившийся новый товар не добавлен: {product_name}")
                    continue

                product_id = await add_product(
                    name=product_name,
                    price=new_price,
                    user_id=user_id,
                    marketplace=marketplace_enum,
                    url=product_url,
                    image_url=product_image_url,
                    variant_text=variant_text,
                    cart_item_key=cart_item_key
                )

                if product_id:
                    image_path = await _download_product_image(
                        product_image_url,
                        user_id,
                        product_id
                    )
                    await update_product_image_path(product_id, image_path)
                    added_count += 1
                    added_item_keys.add(cart_item_key)
                    logger.info(f"Добавлен товар: {product_name} - {new_price} ₽")

        if cart_parse_partial:
            logger.warning(
                "Пропускаем пометку отсутствующих товаров из-за частичного парсинга корзины"
            )
        else:
            # Помечаем товары, которых нет в новой корзине, как удалённые (но не удаляем из БД)
            for product in current_products:
                stored_key = _get_stored_cart_item_key(product, marketplace)
                if product["in_cart"] == 1 and stored_key not in cart_keys:
                    await mark_product_not_in_cart(product["id"])
                    removed_count += 1
                    removed_product_ids.add(product["id"])
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
        if cart_parse_partial:
            summary += (
                f"\n⚠️ Корзина обработана частично: не удалось прочитать "
                f"{skipped_products_count} товар(ов). Отсутствующие позиции не отключались.\n"
            )

        if added_count > 0:
            summary += f"\n🆕 Новые товары:\n"
            for index, cart_product in enumerate(cart_products):
                if cart_item_keys[index] in added_item_keys:
                    summary += (
                        f"• {_format_product_title(cart_product)} - "
                        f"{cart_product['price']}\n"
                    )

        if restored_count > 0:
            summary += "\n↩️ Отслеживание возобновлено:\n"
            for product in current_products:
                if product["id"] in restored_product_ids:
                    summary += f"• {_format_product_title(product)}\n"

        if removed_count > 0:
            summary += (
                "\n❌ Перестали отслеживаться:\n"
                "Эти товары отсутствуют в активной корзине. Их последняя цена и история "
                "сохранены, но новые цены и уведомления поступать не будут.\n"
            )
            for product in current_products:
                if product["id"] in removed_product_ids:
                    summary += f"• {_format_product_title(product)}\n"

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
        cart_parse_partial = False
        skipped_products_count = 0
        authorization_lost_callback = self._create_authorization_lost_callback(
            user_id,
            marketplace,
        )
        for attempt in range(1, 4):
            logger.info(f"Попытка парсинга корзины {attempt}/3: {cart_url}")

            # Определяем, нужно ли обновлять страницу или открывать новую
            # Проверяем, есть ли уже активный браузер для этого пользователя
            # И совпадает ли URL корзины с текущим открытым
            is_refresh = _should_use_existing_cart_page(
                self.browser_manager,
                user_id,
                marketplace,
                cart_url,
                attempt,
            )
            if marketplace == Marketplace.OZON.value and is_refresh:
                logger.info(f"URL корзины {marketplace} совпадает, используем refresh")
            elif marketplace == Marketplace.WILDBERRIES.value:
                if is_refresh:
                    logger.info("Повторно используем загруженную страницу Wildberries")
                else:
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
                            reason="обнаружена блокировка при парсинге корзины",
                            host_level=True,
                        ),
                        authorization_lost_callback=authorization_lost_callback,
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
                            reason="обнаружена блокировка при парсинге корзины",
                            host_level=True,
                        ),
                        authorization_lost_callback=authorization_lost_callback,
                    )
                case _:
                    logger.error(f"Неподдерживаемый маркетплейс корзины: {marketplace}")
                    return

            # Проверяем на специальный маркер maintenance_required
            if cart_products == "maintenance_required":
                logger.error("Получен маркер maintenance_required - прерываем попытки парсинга")
                return

            parsed_products, partial, skipped_count = _unpack_cart_parse_result(cart_products)
            if parsed_products:
                cart_products = parsed_products
                cart_parse_partial = partial
                skipped_products_count = skipped_count
                logger.info(f"Корзина успешно спарсена: {len(cart_products)} товаров")
                if cart_parse_partial:
                    logger.warning(
                        f"Корзина распарсена частично, пропущено блоков: "
                        f"{skipped_products_count}"
                    )
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

        # Создаём индексы для поиска по стабильному ключу позиции корзины
        cart_item_keys = _assign_cart_item_keys(cart_products, marketplace)
        current_products_by_key = _index_current_products_by_key(current_products, marketplace)
        current_products_by_name: dict[str, list[dict]] = {}
        for product in current_products:
            current_products_by_name.setdefault(product["name"], []).append(product)

        cart_keys = set(cart_item_keys.values())
        processed_product_ids: set[int] = set()

        # Счётчики изменений
        updated_count = 0
        restored_count = 0
        removed_count = 0
        restored_product_ids: set[int] = set()
        removed_product_ids: set[int] = set()
        price_drops = []  # Список товаров со снижением цены

        # Обрабатываем товары из корзины
        for index, cart_product in enumerate(cart_products):
            product_name = cart_product["name"]
            cart_item_key = cart_item_keys[index]
            variant_text = cart_product.get("variant_text")
            product_unavailable = bool(cart_product.get("unavailable"))

            new_price = None
            if not product_unavailable:
                price_match = re.search(r'([\d\s\xa0]+)', cart_product["price"])
                if not price_match:
                    logger.warning(f"Не удалось распарсить цену: {cart_product['price']}")
                    continue

                new_price = int(re.sub(r'[\s\xa0]', '', price_match.group(1)))

            # Если товар есть в БД
            existing_product = current_products_by_key.get(cart_item_key)

            # Совместимость со старыми записями без ключа: восстанавливаем только
            # однозначное совпадение по названию, чтобы не сливать разные варианты.
            if not existing_product:
                existing_product = _find_legacy_product_by_name_and_price(
                    current_products_by_name,
                    product_name,
                    processed_product_ids,
                    marketplace,
                    new_price
                )

            if existing_product:
                product_id = existing_product["id"]
                processed_product_ids.add(product_id)

                if product_unavailable:
                    if existing_product["in_cart"] == 1:
                        await mark_product_not_in_cart(product_id)
                        removed_count += 1
                        removed_product_ids.add(product_id)
                        await self._notify_product_unavailable(
                            user_id,
                            cart_product,
                            marketplace
                        )
                        logger.info(f"Товар закончился и отключен: {product_name}")
                    await update_product_cart_identity(product_id, variant_text, cart_item_key)
                    continue

                # Если товар был удалён из корзины ранее — восстанавливаем
                if existing_product["in_cart"] == 0:
                    await mark_product_in_cart(product_id)
                    restored_count += 1
                    restored_product_ids.add(product_id)
                    await self._notify_product_available(
                        user_id,
                        cart_product,
                        marketplace,
                        new_price
                    )
                    logger.info(f"Восстановлен товар в корзине: {product_name}")

                price_snapshot = await get_price_snapshot(product_id)
                previous_price = price_snapshot["previous_price"]

                # Обновляем цену в БД
                await update_product_price(product_id, new_price)
                if cart_product.get("url"):
                    await update_product_url(product_id, cart_product.get("url"))
                if cart_product.get("image_url"):
                    await update_product_image_url(product_id, cart_product.get("image_url"))
                await update_product_cart_identity(product_id, variant_text, cart_item_key)
                updated_count += 1
                logger.info(f"✅ Цена обновлена для {product_name}: {new_price} ₽")

                # Проверяем, упала ли цена ниже предыдущего замера
                if previous_price is not None and new_price < previous_price:
                    min_price = min(
                        price_snapshot["min_price"] or new_price,
                        new_price
                    )
                    max_price = max(
                        price_snapshot["max_price"] or new_price,
                        new_price
                    )
                    price_drops.append({
                        "name": _format_product_title(cart_product),
                        "new_price": new_price,
                        "previous_price": previous_price,
                        "min_price": min_price,
                        "max_price": max_price,
                        "savings": max_price - new_price
                    })
                    logger.info(
                        f"🔔 Цена снизилась относительно предыдущего замера! "
                        f"Было: {previous_price} ₽, стало: {new_price} ₽"
                    )

        if cart_parse_partial:
            logger.warning(
                "Пропускаем пометку отсутствующих товаров из-за частичного парсинга корзины"
            )
        else:
            # Помечаем товары, которых нет в новой корзине, как удалённые (но не удаляем из БД)
            for product in current_products:
                stored_key = _get_stored_cart_item_key(product, marketplace)
                if product["in_cart"] == 1 and stored_key not in cart_keys:
                    await mark_product_not_in_cart(product["id"])
                    removed_count += 1
                    removed_product_ids.add(product["id"])
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
                    f"📉 Минимальная цена: {drop['min_price']} ₽\n"
                    f"📈 Максимальная цена: {drop['max_price']} ₽\n"
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
        reason: str = "обнаружена блокировка",
        host_level: bool = False,
    ):
        """
        Активирует персональный режим техработ или общую паузу маркетплейса.

        Args:
            user_id: ID пользователя
            marketplace: Маркетплейс, для которого обнаружена блокировка
            reason: Причина включения режима техработ
            host_level: Включить временную общую паузу вместо персонального флага
        """
        logger.warning(
            f"Активация режима техработ для пользователя {user_id}, "
            f"маркетплейс={marketplace}, уровень={'хост' if host_level else 'пользователь'}, "
            f"причина={reason}"
        )

        is_host_mode = bool(host_level and marketplace)
        if is_host_mode:
            maintenance_mode.enable_host(marketplace)
        else:
            maintenance_mode.enable(user_id, marketplace)

        # Уведомляем администратора до очистки вкладок, чтобы ее ошибка не скрыла событие.
        try:
            marketplace_suffix = f"_{marketplace}" if marketplace else ""
            maintenance_level = "общий для хоста" if is_host_mode else "пользовательский"
            if is_host_mode:
                flag_instructions = (
                    "Общий флаг хоста: "
                    f"maintenance_flags/maintenance_host_{marketplace}.flag\n"
                    f"Автоматическое снятие через: "
                    f"{config.HOST_MAINTENANCE_TTL_SECONDS} сек."
                )
            else:
                flag_instructions = (
                    "Для разблокировки удалите файл: "
                    f"maintenance_flags/maintenance_{user_id}{marketplace_suffix}.flag"
                )

            logger.info(
                "Отправляем уведомление о maintenance mode администратору: "
                f"admin_id={config.ADMIN_USER_ID}, user_id={user_id}, "
                f"маркетплейс={marketplace}, причина={reason}"
            )
            await self.bot.send_message(
                config.ADMIN_USER_ID,
                f"🚧 Активирован maintenance mode для пользователя {user_id}\n"
                f"Маркетплейс: {marketplace or 'не определён'}\n"
                f"Уровень: {maintenance_level}\n"
                f"Причина: {reason}\n\n{flag_instructions}"
            )
            logger.info(
                "Уведомление о maintenance mode успешно отправлено администратору: "
                f"admin_id={config.ADMIN_USER_ID}"
            )
        except Exception as e:
            logger.error(
                "Не удалось отправить уведомление администратору: "
                f"admin_id={config.ADMIN_USER_ID}, user_id={user_id}, "
                f"маркетплейс={marketplace}, ошибка={e}",
                exc_info=True,
            )

        try:
            if is_host_mode:
                await self.browser_manager.close_marketplace_pages(marketplace)
            elif marketplace:
                await self.browser_manager.close_user_marketplace_page(
                    user_id,
                    marketplace,
                )
        except Exception as e:
            logger.error(
                "Не удалось закрыть вкладки после включения maintenance mode: "
                f"user_id={user_id}, маркетплейс={marketplace}, ошибка={e}",
                exc_info=True,
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
