# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for monitoring product prices on Ozon marketplace, built with `aiogram` framework. The bot allows users to add product URLs, scrapes prices using Playwright/Selenium, and will eventually track price changes over time. Currently in early development with basic URL handling and scraping functionality implemented.

**Key Technologies:**
- **Bot Framework:** `aiogram` (async Telegram bot framework)
- **Configuration:** `pydantic-settings` (loads from `.env`)
- **Database:** `aiosqlite` (planned, not yet implemented)
- **Web Scraping:** `playwright`, `selenium`, `undetected-chromedriver`
- **Proxy Support:** Configured via `aiohttp-socks`

## Architecture

### Core Components

- **`main.py`**: Bot and dispatcher initialization with proxy session setup
- **`run_bot.py`**: Entry point that starts polling
- **`config.py`**: Pydantic settings model loading `BOT_TOKEN` and `PROXY_URL` from `.env`
- **`handlers/handlers.py`**: Message and command handlers using FSM for multi-step flows
- **`utils/scraper.py`**: Web scraping logic using undetected-chromedriver (currently saves HTML for debugging)
- **`utils/states.py`**: FSM state definitions (e.g., `AddPosition.waiting_for_link`)
- **`keyboards/reply.py`**: Reply keyboard layouts
- **`core/`**: Intended for business logic and database operations (currently empty/minimal)

### Data Flow

1. User sends `/start` → receives main keyboard with "Добавить позицию" and "Список позиций"
2. User clicks "Добавить позицию" → FSM enters `AddPosition.waiting_for_link` state
3. User sends product URL → `scrape_ozon_product()` runs in executor, saves HTML to `ozon_page.html`
4. Bot responds with placeholder data (actual parsing not yet implemented)

### Scraping Strategy

Two scraping implementations exist:
- **`utils/scraper.py`**: Uses `undetected-chromedriver` with headless Chrome, currently saves HTML for debugging
- **`test.py`**: Standalone Playwright script with multiple selector fallbacks for price extraction

The Playwright approach in `test.py` is more complete and should be integrated into the main bot flow.

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
- Basic bot structure with proxy support
- FSM flow for adding product URLs
- Selenium-based scraping (saves HTML, parsing incomplete)
- Standalone Playwright scraper with working price extraction

**Not Yet Implemented:**
- Database schema and operations (`core/database.py` is empty)
- Price history tracking
- Background monitoring task (periodic price checks)
- "Список позиций" functionality
- Integration of working Playwright scraper into main bot flow

**Known Issues:**
- `utils/scraper.py` only saves HTML to file, doesn't extract price
- `core/models.py` and `core/database.py` are empty
- No tests
