"""
lazada_scraper.py – Playwright-based Lazada scraper.

Authentication model
--------------------
The bot never handles credentials. Instead:

  1. Run `python main.py --login` once.
     A visible browser opens on the Lazada homepage. Log in normally
     (password, OTP, CAPTCHA — whatever Lazada presents). The bot
     detects when you're signed in and saves the session to
     AUTH_STATE_FILE (default: auth_state.json).

  2. On every subsequent run the scraper loads that saved state into
     a fresh browser context, inheriting your cookies and localStorage.
     If the session has expired the bot will send a Telegram alert
     asking you to re-run --login.

Responsibilities (normal run)
------------------------------
  1. Load saved auth state.
  2. Verify the session is still valid.
  3. Search for each query from config.
  4. Apply price filters and sort order.
  5. Collect up to MAX_ITEMS_PER_QUERY listings.
  6. Add each listing to the cart.
  7. Return structured results for the Telegram bot to report.
"""

import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
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

# Selector that is only present when a user is logged in (account name/avatar)
_LOGGED_IN_SELECTOR = (
    "[data-spm='account'], "
    ".account-name, "
    "span.name, "
    "//span[contains(@class,'Username')]"
)


class SessionExpiredError(Exception):
    """Raised when the saved auth state is no longer valid."""


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
        # Load saved auth state so the session is already authenticated
        state_path = config.AUTH_STATE_FILE
        ctx_kwargs = dict(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        if Path(state_path).exists():
            ctx_kwargs["storage_state"] = state_path
            logger.info("Loaded auth state from %s", state_path)
        else:
            logger.warning(
                "No auth state found at '%s'. Run `python main.py --login` first.",
                state_path,
            )
        self._context = self._browser.new_context(**ctx_kwargs)
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

    def verify_session(self) -> bool:
        """
        Navigate to the Lazada homepage and confirm the account widget
        is visible (i.e. we are logged in).

        Raises SessionExpiredError if the session is invalid so the
        caller can send an alert rather than scraping as a guest.
        """
        page = self._page
        try:
            page.goto(config.LAZADA_BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            self._delay()
            logged_in = page.locator(_LOGGED_IN_SELECTOR).first.is_visible(timeout=6_000)
            if not logged_in:
                raise SessionExpiredError(
                    f"Session in '{config.AUTH_STATE_FILE}' has expired. "
                    "Run `python main.py --login` to refresh it."
                )
            logger.info("Session verified — logged in")
            return True
        except SessionExpiredError:
            raise
        except PWTimeout:
            raise SessionExpiredError("Timed out checking login status")

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

    # ── interactive login (run once via --login flag) ──────────────────────────

    @classmethod
    def interactive_login(cls) -> None:
        """
        Open a visible browser, navigate to Lazada, and wait for the user
        to log in manually. Once the account widget appears the session is
        saved to AUTH_STATE_FILE automatically.

        This method blocks until login is detected or the user presses
        Ctrl-C to abort.
        """
        save_path = config.AUTH_STATE_FILE
        print(
            "\n──────────────────────────────────────────────\n"
            " ScraperBot – Manual Login\n"
            "──────────────────────────────────────────────\n"
            f" 1. A browser window will open on {config.LAZADA_BASE_URL}\n"
            " 2. Log in as you normally would (OTP / CAPTCHA / etc.).\n"
            " 3. Once you see your account name in the top bar,\n"
            "    the bot will save your session automatically.\n"
            "──────────────────────────────────────────────\n"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,  # always visible for manual login
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(config.LAZADA_BASE_URL, wait_until="domcontentloaded")

            print("Waiting for you to log in (timeout: 5 minutes)…")
            try:
                # Poll every 2 s for up to 5 minutes
                page.wait_for_selector(
                    _LOGGED_IN_SELECTOR,
                    timeout=300_000,  # 5 minutes
                    state="visible",
                )
                print("Login detected! Saving session…")
                context.storage_state(path=save_path)
                print(f"Session saved to '{save_path}'. You can close the browser.\n")
            except PWTimeout:
                print("Login not detected within 5 minutes — aborting.")
            finally:
                browser.close()

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

            # Strip currency symbols and commas to get a plain float
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
