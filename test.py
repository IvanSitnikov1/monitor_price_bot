import asyncio
import re
import sys
from playwright.async_api import async_playwright


async def get_ozon_price(product_url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(product_url, wait_until="domcontentloaded", timeout=30000)

        selectors = [
            '[data-widget="webPrice"]',
            '[data-widget="webSale"]',
            '[data-widget="price"]',
            'meta[itemprop="price"]',
            'script[type="application/ld+json"]',
            'span:has-text("₽")',
        ]

        price_text = None

        for selector in selectors:
            try:
                if selector.startswith("meta[") or selector.startswith("script["):
                    el = await page.query_selector(selector)
                    if not el:
                        continue
                    if selector.startswith("meta["):
                        price_text = await el.get_attribute("content")
                    else:
                        txt = await el.text_content()
                        if txt:
                            m = re.search(r'"price"\s*:\s*"?(\\d+[\\d\\s]*)"?', txt)
                            if m:
                                price_text = m.group(1)
                else:
                    el = await page.query_selector(selector)
                    if el:
                        txt = await el.text_content()
                        if txt:
                            m = re.search(r'(\d[\d\s\u00A0]*)\s*₽', txt)
                            if m:
                                price_text = m.group(1)
                            else:
                                price_text = txt
                if price_text:
                    break
            except Exception:
                continue

        await browser.close()

    if not price_text:
        return "Цена не найдена"

    m = re.search(r'(\d[\d\s\u00A0]*)', str(price_text))
    if not m:
        return "Цена не найдена"

    price = m.group(1).replace(" ", "").replace("\u00A0", "")
    return f"{price} ₽"


def main():
    if len(sys.argv) < 2:
        print("Использование: python ozon_price.py <url>")
        sys.exit(1)

    url = sys.argv[1]
    price = asyncio.run(get_ozon_price(url))
    print(price)


if __name__ == "__main__":
    main()