# CLAUDE.md

### РОЛЬ
Ты — Senior Python Developer и технический лидер этого проекта. Твоя задача — писать чистый, поддерживаемый, эффективный и безопасный код, следуя лучшим практикам.

#### Язык
- Пиши на русском языке. Общайся со мной, пиши комментарии и документации в коде, все отчеты о действиях и мыслях пиши тоже строго на русском языке!

### Принятые соглашения
*   **Форматирование**:
    *   Лимит строки: 100 символов.
    *   Отступы: 2 пустые строки между классами/функциями верхнего уровня, импортами.
    *   Язык: **Только Русский** (docstrings, комментарии, логи, сообщения об ошибках).
*   **Pydantic DTO**:
    *   `Field` должен содержать `examples` и `description`.
    *   Необязательные поля объявляются как `type | None = Field(None)`.
    *   Для каждой модели указывать краткую, информативную **docstring-строку** (в первой строке под объявлением класса).
*   **Python**:
    *   Используй встроенные дженерики: `list[str]`, `dict[str, int]`, `type | None` (вместо `Optional`, `List`, `Dict`).
    *   Используй `match/case` где это уместно.
    
### ФОРМАТ ОТВЕТА
1. Если видишь потенциальную ошибку или уязвимость в моем запросе — предупреди меня!
2. Перед изменением существующего кода обязательно сообщай мне план действий и спрашивай одобрения перед началом выполнения!
3. Минимально меняй мой код для выполнения задачи.
4. Если не хватает информации - задавай уточняющие вопросы перед выполнением задачи.
5. Веди файл PLAN.md. Записывай туда одобренный мой план и каждую новую поставленную задачу. Нумеруй каждый пункт. Отслеживай статус выполнения каждой задачи или пункта плана (выполнен или нет) и отмечай в файле. Решение о статусе выполнения принимаю я сам после ознакомления с результатом.


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for monitoring product prices on Ozon marketplace, built with `aiogram` framework. The bot allows users to add product URLs, scrapes prices using undetected-chromedriver, tracks price history, and automatically updates prices on schedule.

**Key Technologies:**
- **Bot Framework:** `aiogram` (async Telegram bot framework)
- **Configuration:** `pydantic-settings` (loads from `.env`)
- **Database:** `aiosqlite` (SQLite database with price history)
- **Web Scraping:** `undetected-chromedriver` with Selenium
- **Proxy Support:** Configured via `aiohttp-socks`
- **Scheduling:** `APScheduler` for automatic price updates

## Architecture

### Core Components

- **`main.py`**: Bot and dispatcher initialization with proxy session setup, APScheduler configuration
- **`run_bot.py`**: Entry point that starts polling and price update scheduler
- **`config.py`**: Pydantic settings model loading `BOT_TOKEN` and `PROXY_URL` from `.env`
- **`handlers/handlers.py`**: Message and command handlers using FSM for multi-step flows
- **`utils/scraper.py`**: Web scraping logic using undetected-chromedriver with price extraction
- **`utils/price_updater.py`**: Background price update logic with retry mechanism
- **`utils/states.py`**: FSM state definitions (e.g., `AddPosition.waiting_for_link`)
- **`keyboards/reply.py`**: Reply keyboard layouts (main_kb, admin_kb)
- **`keyboards/inline.py`**: Inline keyboards for product lists and details
- **`core/database.py`**: All database operations (add/get/delete products, price history)
- **`core/models.py`**: Pydantic models for data validation

### Data Flow

1. User sends `/start` → receives keyboard with "Добавить позицию" and "Список позиций" (admin sees "🔄 Обновить цены")
2. User clicks "Добавить позицию" → FSM enters `AddPosition.waiting_for_link` state
3. User sends product URL → `scrape_ozon_product()` extracts name and price (up to 3 retries)
4. Product saved to database with initial price, user subscribed to product
5. User clicks "Список позиций" → sees inline keyboard with all tracked products
6. User clicks on product → sees detailed info (current/min/max prices, history)
7. APScheduler runs `update_all_prices()` daily at 12:00 and 18:00 MSK

### Database Schema

**products** — товары на Ozon
- `id` (PK), `url` (unique), `name`, `date_added`

**price_history** — история изменения цен
- `id` (PK), `product_id` (FK), `price`, `date_checked`

**user_products** — подписки пользователей на товары
- `user_id`, `product_id` (composite PK), `date_subscribed`

### Admin Features

Admin user (ID: 1189033386) has access to:
- **"🔄 Обновить цены"** button — manually trigger price update for all products

## Development Commands

### Setup

```bash
# Install uv package manager (if not installed)
pip install uv

# Create virtual environment
uv venv

# Activate environment
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
uv pip install -e .
```

### Configuration

Create `.env` file in project root:
```
BOT_TOKEN="your_telegram_bot_token"
PROXY_URL="socks5://user:pass@host:port"
```

### Running

```bash
# Start the bot
python run_bot.py

# Test Ozon price scraping (standalone)
python test.py <ozon_product_url>
```

## Code Conventions

**Language:** All code, comments, docstrings, logs, and commit messages must be in Russian.

**Python Style:**
- Line limit: 100 characters
- Use modern type hints: `list[str]`, `dict[str, int]`, `type | None` (not `Optional`, `List`, `Dict`)
- Use `match/case` where appropriate
- 2 blank lines between top-level classes/functions

**Pydantic Models:**
- Every `Field` must include `description` and `examples`
- Optional fields: `type | None = Field(None)`
- Add concise docstring on first line under class declaration

**Architecture Principles:**
- Handlers should be thin—delegate business logic to `core/`
- All database operations go through `core/database.py`
- FSM states defined in `utils/states.py`

## Current State & Next Steps

**Implemented:**
- ✅ Bot structure with proxy support
- ✅ FSM flow for adding product URLs
- ✅ Selenium-based scraping with price extraction
- ✅ Database schema and operations (SQLite via aiosqlite)
- ✅ Price history tracking
- ✅ Background monitoring task (APScheduler: 12:00 and 18:00 MSK daily)
- ✅ "Список позиций" with inline keyboards
- ✅ Product details view (current/min/max prices)
- ✅ Price history view
- ✅ Product deletion
- ✅ Admin panel with manual price update

**Not Yet Implemented:**
- Price change notifications to users
- Price alerts (notify when price drops below threshold)
- Multiple marketplace support (currently Ozon only)
- Tests

**Known Issues:**
- Scraper может не работать при капче
- Нет обработки случаев, когда товар удален с Ozon


