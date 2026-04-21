"""
config.py – Loads and validates all environment variables.
All tuneable parameters live in .env; this module is the single
source of truth for the rest of the application.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID: str = os.environ["TELEGRAM_CHAT_ID"]

# ── Lazada account ────────────────────────────────────────────────────────────
LAZADA_EMAIL: str = os.environ["LAZADA_EMAIL"]
LAZADA_PASSWORD: str = os.environ["LAZADA_PASSWORD"]

LAZADA_COUNTRY: str = os.getenv("LAZADA_COUNTRY", "sg").lower()
_COUNTRY_DOMAINS = {
    "sg": "lazada.sg",
    "my": "lazada.com.my",
    "ph": "lazada.com.ph",
    "id": "lazada.co.id",
    "th": "lazada.co.th",
    "vn": "lazada.vn",
}
if LAZADA_COUNTRY not in _COUNTRY_DOMAINS:
    raise ValueError(
        f"LAZADA_COUNTRY '{LAZADA_COUNTRY}' is invalid. "
        f"Choose from: {', '.join(_COUNTRY_DOMAINS)}"
    )
LAZADA_BASE_URL: str = f"https://www.{_COUNTRY_DOMAINS[LAZADA_COUNTRY]}"

# ── Search configuration ──────────────────────────────────────────────────────
_raw_queries = os.getenv("SEARCH_QUERIES", "")
SEARCH_QUERIES: list[str] = [q.strip() for q in _raw_queries.split(",") if q.strip()]
if not SEARCH_QUERIES:
    raise ValueError("SEARCH_QUERIES must contain at least one search term.")

_min = os.getenv("MIN_PRICE", "").strip()
_max = os.getenv("MAX_PRICE", "").strip()
MIN_PRICE: float | None = float(_min) if _min else None
MAX_PRICE: float | None = float(_max) if _max else None

VALID_SORT_OPTIONS = {"price_asc", "price_desc", "popularity", "newest", "rating"}
SORT_BY: str = os.getenv("SORT_BY", "price_asc").lower()
if SORT_BY not in VALID_SORT_OPTIONS:
    raise ValueError(
        f"SORT_BY '{SORT_BY}' is invalid. Choose from: {', '.join(VALID_SORT_OPTIONS)}"
    )

MAX_ITEMS_PER_QUERY: int = int(os.getenv("MAX_ITEMS_PER_QUERY", "3"))

# ── Scheduler ─────────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
ACTION_DELAY_SECONDS: float = float(os.getenv("ACTION_DELAY_SECONDS", "2"))
