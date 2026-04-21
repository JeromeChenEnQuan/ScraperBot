"""
lazada_scraper.py – Playwright-based Lazada scraper.

Responsibilities:
  1. Log in to Lazada.
  2. Search for each query from config.
  3. Apply price filters and sort order.
  4. Collect up to MAX_ITEMS_PER_QUERY listings.
  5. Add each listing to the cart.
  6. Return structured results for the Telegram bot to report.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout

import config

logger = logging.getLogger(__name__)

# Lazada sort-by URL parameter values
_SORT_PARAM = {
    "price_asc": "price_asc",
    "price_desc": "price_desc",
    "popularity": "popularity",
    "newest": "new",
    "rating": "rating",
}


@dataclass
class ListingResult:
    name: str
    price: float
    url: str
    added_to_cart: bool = False
    error: Optional[str] = None


@dataclass
class ScrapeReport:
    query: str
    listings: list[ListingResult] = field(default_factory=list)
    login_ok: bool = True
    error: Optional[str] = None


class LazadaScraper:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        logger.info("Browser started (headless=%s)", config.HEADLESS)

    def stop(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info("Browser closed")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ── public API ─────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """Navigate to Lazada and log in. Returns True on success."""
        page = self._page
        try:
            logger.info("Navigating to %s", config.LAZADA_BASE_URL)
            page.goto(config.LAZADA_BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            self._delay()

            # Click the account / login button
            login_trigger = page.locator(
                "//span[contains(@class,'Account') or contains(text(),'Sign in') "
                "or contains(text(),'Log in')]"
            ).first
            if login_trigger.is_visible(timeout=5_000):
                login_trigger.click()
                self._delay()
            else:
                # Some regions show login page directly
                page.goto(f"{config.LAZADA_BASE_URL}/customer/account/login/", timeout=30_000)
                self._delay()

            # Fill credentials
            page.fill("input[name='loginName'], input[type='email'], #email", config.LAZADA_EMAIL)
            self._delay(0.5)
            page.fill("input[name='password'], input[type='password'], #password", config.LAZADA_PASSWORD)
            self._delay(0.5)
            page.click("button[type='submit'], .login-button, #login-btn")
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            self._delay()

            # Verify login by checking for account-related element
            logged_in = page.locator(
                "//span[contains(@class,'Username') or contains(@class,'account-name')]"
            ).is_visible(timeout=8_000)
            if logged_in:
                logger.info("Login successful")
            else:
                logger.warning("Login status uncertain — proceeding anyway")
            return True

        except PWTimeout:
            logger.error("Login timed out")
            return False
        except Exception as exc:
            logger.error("Login error: %s", exc)
            return False

    def run_query(self, query: str) -> ScrapeReport:
        """Search for *query*, collect listings, add to cart, return report."""
        report = ScrapeReport(query=query)
        page = self._page

        try:
            search_url = self._build_search_url(query)
            logger.info("Searching: %s", search_url)
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            self._delay()

            # Collect product cards
            cards = page.locator("[data-qa-locator='product-item'], .Bm3ON, ._95X4G").all()
            logger.info("Found %d product cards for '%s'", len(cards), query)

            count = 0
            for card in cards:
                if count >= config.MAX_ITEMS_PER_QUERY:
                    break

                listing = self._extract_listing(card)
                if listing is None:
                    continue

                # Apply price filters
                if config.MIN_PRICE is not None and listing.price < config.MIN_PRICE:
                    continue
                if config.MAX_PRICE is not None and listing.price > config.MAX_PRICE:
                    continue

                # Open product page and add to cart
                self._add_to_cart(listing)
                report.listings.append(listing)
                count += 1
                self._delay()

        except PWTimeout:
            report.error = f"Timeout while processing query '{query}'"
            logger.error(report.error)
        except Exception as exc:
            report.error = str(exc)
            logger.error("Error on query '%s': %s", query, exc)

        return report

    # ── helpers ────────────────────────────────────────────────────────────────

    def _build_search_url(self, query: str) -> str:
        sort = _SORT_PARAM.get(config.SORT_BY, "popularity")
        url = f"{config.LAZADA_BASE_URL}/catalog/?q={query}&sort={sort}"
        if config.MIN_PRICE is not None:
            url += f"&price={int(config.MIN_PRICE)}-"
            if config.MAX_PRICE is not None:
                url += str(int(config.MAX_PRICE))
        elif config.MAX_PRICE is not None:
            url += f"&price=0-{int(config.MAX_PRICE)}"
        return url

    def _extract_listing(self, card) -> Optional[ListingResult]:
        try:
            name_el = card.locator("[data-qa-locator='product-name'], .RfADt, .line-clamp").first
            price_el = card.locator("[data-qa-locator='product-price'], .ooOxS, .price-box").first
            link_el = card.locator("a[href*='/products/'], a[href*='.html']").first

            name = name_el.inner_text(timeout=3_000).strip()
            price_text = price_el.inner_text(timeout=3_000).strip()
            url = link_el.get_attribute("href", timeout=3_000)

            # Parse price — strip currency symbols and commas
            price_clean = "".join(c for c in price_text if c.isdigit() or c == ".")
            price = float(price_clean) if price_clean else 0.0

            if not url.startswith("http"):
                url = config.LAZADA_BASE_URL + url

            return ListingResult(name=name, price=price, url=url)
        except Exception as exc:
            logger.debug("Could not extract listing: %s", exc)
            return None

    def _add_to_cart(self, listing: ListingResult) -> None:
        page = self._page
        try:
            logger.info("Opening product: %s", listing.name)
            page.goto(listing.url, wait_until="domcontentloaded", timeout=30_000)
            self._delay()

            # Handle size/variant selectors if present
            variant_btns = page.locator(".sku-variable-img-wrap button:not([disabled])").all()
            if variant_btns:
                variant_btns[0].click()
                self._delay(0.5)

            # Click Add to Cart
            add_btn = page.locator(
                "//button[contains(text(),'Add to Cart') or contains(text(),'Add to Bag') "
                "or @data-qa-locator='add-to-cart']"
            ).first
            add_btn.click(timeout=8_000)
            self._delay()

            listing.added_to_cart = True
            logger.info("Added to cart: %s", listing.name)
        except PWTimeout:
            listing.error = "Add-to-cart timed out"
            logger.warning("Add-to-cart timed out for: %s", listing.name)
        except Exception as exc:
            listing.error = str(exc)
            logger.warning("Could not add '%s' to cart: %s", listing.name, exc)

        # Navigate back to search results
        try:
            page.go_back(wait_until="domcontentloaded", timeout=15_000)
            self._delay()
        except Exception:
            pass

    def _delay(self, multiplier: float = 1.0) -> None:
        time.sleep(config.ACTION_DELAY_SECONDS * multiplier)
