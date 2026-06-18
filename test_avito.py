import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.products import _normalize_avito_product_url, process_cart_link
from core.models import Marketplace
from utils.scraper import scrape_avito_product
from utils.price_updater import update_all_prices


class AvitoUrlTestCase(unittest.TestCase):
    """Проверяет нормализацию прямых ссылок Avito."""

    def test_normalizes_product_url(self):
        url = (
            "https://www.avito.ru/yaroslavl/predlozheniya_uslug/"
            "platnaya_rybalka_8211280976?context=test#fragment"
        )

        self.assertEqual(
            _normalize_avito_product_url(url),
            "https://www.avito.ru/yaroslavl/predlozheniya_uslug/"
            "platnaya_rybalka_8211280976"
        )

    def test_rejects_non_product_urls(self):
        invalid_urls = [
            "http://www.avito.ru/yaroslavl/item_8211280976",
            "https://www.avito.ru/",
            "https://www.avito.ru/yaroslavl?q=рыбалка",
            "https://example.org/item_8211280976",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertIsNone(_normalize_avito_product_url(url))


class AvitoHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    """Проверяет маршрутизацию ссылки Avito в отдельную задачу."""

    async def test_routes_avito_url_as_product(self):
        task_queue = SimpleNamespace(add_task=AsyncMock())
        user = SimpleNamespace(id=10, username="user", first_name="Иван")
        message = SimpleNamespace(
            text=(
                "https://www.avito.ru/yaroslavl/predlozheniya_uslug/"
                "platnaya_rybalka_8211280976?context=test"
            ),
            from_user=user,
            bot=SimpleNamespace(task_queue=task_queue),
            answer=AsyncMock()
        )
        state = SimpleNamespace(clear=AsyncMock())

        with patch("handlers.products.register_user", new=AsyncMock()):
            await process_cart_link(message, state)

        task_queue.add_task.assert_awaited_once_with(
            task_type="add_product",
            url=(
                "https://www.avito.ru/yaroslavl/predlozheniya_uslug/"
                "platnaya_rybalka_8211280976"
            ),
            marketplace=Marketplace.AVITO.value,
            priority=1,
            user_id=user.id
        )


class AvitoScraperTestCase(unittest.IsolatedAsyncioTestCase):
    """Проверяет извлечение данных объявления Avito через Playwright."""

    async def test_extracts_product_data(self):
        page = SimpleNamespace(
            wait_for_load_state=AsyncMock(),
            evaluate=AsyncMock(return_value={
                "blocked": False,
                "unavailable": False,
                "title": "Платная рыбалка",
                "price": "от 500 ₽",
                "imageUrl": "https://80.img.avito.st/image/example",
            })
        )
        browser_manager = SimpleNamespace(get_product_page=AsyncMock(return_value=page))

        with (
            patch("utils.scraper._human_delay", new=AsyncMock()),
            patch("utils.scraper._human_like_behavior", new=AsyncMock()),
        ):
            product = await scrape_avito_product(
                "https://www.avito.ru/yaroslavl/item_8211280976",
                user_id=1,
                browser_manager=browser_manager
            )

        self.assertEqual(product["name"], "Платная рыбалка")
        self.assertEqual(product["price"], 500)
        self.assertEqual(
            product["image_url"],
            "https://80.img.avito.st/image/example"
        )

    async def test_reports_blocking(self):
        page = SimpleNamespace(
            wait_for_load_state=AsyncMock(),
            evaluate=AsyncMock(return_value={
                "blocked": True,
                "unavailable": False,
                "title": None,
                "price": None,
                "imageUrl": None,
            })
        )
        browser_manager = SimpleNamespace(get_product_page=AsyncMock(return_value=page))
        maintenance_callback = AsyncMock()

        with (
            patch("utils.scraper._human_delay", new=AsyncMock()),
            patch("utils.scraper._human_like_behavior", new=AsyncMock()),
        ):
            result = await scrape_avito_product(
                "https://www.avito.ru/yaroslavl/item_8211280976",
                user_id=1,
                browser_manager=browser_manager,
                maintenance_callback=maintenance_callback
            )

        self.assertEqual(result, "maintenance_required")
        maintenance_callback.assert_awaited_once()


class AvitoPriceUpdaterTestCase(unittest.IsolatedAsyncioTestCase):
    """Проверяет постановку объявлений Avito на плановое обновление."""

    async def test_adds_individual_update_task(self):
        task_queue = SimpleNamespace(add_task=AsyncMock())
        product = {
            "id": 5,
            "user_id": 10,
            "url": "https://www.avito.ru/yaroslavl/item_8211280976",
        }

        with (
            patch("utils.price_updater.get_all_user_carts", new=AsyncMock(return_value=[])),
            patch(
                "utils.price_updater.get_all_products_for_update",
                new=AsyncMock(return_value=[product])
            ),
        ):
            await update_all_prices(task_queue)

        task_queue.add_task.assert_awaited_once_with(
            task_type="update_product",
            url=product["url"],
            priority=2,
            user_id=product["user_id"],
            marketplace=Marketplace.AVITO.value,
            product_id=product["id"]
        )


if __name__ == "__main__":
    unittest.main()
