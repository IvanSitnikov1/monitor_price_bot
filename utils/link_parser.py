import re
from urllib.parse import urlparse

from core.models import Marketplace


def extract_url_from_text(text: str) -> str | None:
    """
    Извлекает URL из текста, который может содержать дополнительный мусор.

    Args:
        text: Текст, содержащий URL

    Returns:
        Извлеченный URL или None, если URL не найден
    """
    # Паттерн для поиска URL (http/https)
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'

    match = re.search(url_pattern, text)
    if match:
        url = match.group(0)
        # Убираем возможные trailing символы (точки, запятые и т.д.)
        url = re.sub(r'[.,;:!?)\]]+$', '', url)
        return url

    return None


def detect_marketplace(url: str) -> Marketplace | None:
    """
    Определяет маркетплейс по URL.

    Args:
        url: URL товара

    Returns:
        Маркетплейс или None, если не удалось определить
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Убираем www. если есть
        domain = domain.replace('www.', '')

        match domain:
            case d if 'ozon.ru' in d:
                return Marketplace.OZON
            case d if 'wildberries.ru' in d or 'wb.ru' in d:
                return Marketplace.WILDBERRIES
            case d if 'avito.ru' in d:
                return Marketplace.AVITO
            case _:
                return None

    except Exception:
        return None


def parse_product_link(text: str) -> tuple[str, Marketplace] | tuple[None, None]:
    """
    Извлекает URL из текста и определяет маркетплейс.

    Args:
        text: Текст, содержащий URL товара

    Returns:
        Кортеж (url, marketplace) или (None, None) если не удалось распарсить
    """
    url = extract_url_from_text(text)
    if not url:
        return None, None

    marketplace = detect_marketplace(url)
    if not marketplace:
        return None, None

    return url, marketplace
