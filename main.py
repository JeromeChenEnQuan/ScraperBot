"""
main.py – ScraperBot entry point.

Starts the Telegram bot listener and an APScheduler job that
runs the Lazada scraper on the interval defined in .env.
"""

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

import config
from scraper.lazada_scraper import LazadaScraper, ScrapeReport
from bot.telegram_bot import build_app, notify, _fmt_report

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ── Scrape logic ───────────────────────────────────────────────────────────────

def _run_scrape_sync() -> list[ScrapeReport]:
    """Blocking scrape. Runs in a thread via asyncio.to_thread."""
    reports: list[ScrapeReport] = []
    with LazadaScraper() as scraper:
        logged_in = scraper.login()
        if not logged_in:
            logger.error("Login failed – skipping scrape cycle")
            return [ScrapeReport(query="(all)", error="Login failed")]

        for query in config.SEARCH_QUERIES:
            logger.info("Processing query: %s", query)
            report = scraper.run_query(query)
            reports.append(report)

    return reports


async def run_scrape_and_notify(app_bot_data: dict | None = None) -> list[ScrapeReport]:
    """
    Run scrape in a thread, then push Telegram notifications.
    app_bot_data: bot_data dict from the Application, used to update the
                  session cart (optional – omitted during scheduled runs
                  before the bot is initialised).
    """
    logger.info("Scrape cycle starting")
    reports = await asyncio.to_thread(_run_scrape_sync)

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    async with bot:
        total_added = 0
        for report in reports:
            msg = _fmt_report(report)
            await notify(bot, msg)
            for item in report.listings:
                if item.added_to_cart:
                    total_added += 1
                    if app_bot_data is not None:
                        app_bot_data.setdefault("cart", []).append(
                            {"name": item.name, "price": item.price, "url": item.url}
                        )

        summary = (
            f"*Scrape complete* – {len(reports)} quer{'y' if len(reports)==1 else 'ies'}, "
            f"{total_added} item\\(s\\) added to cart\\."
        )
        await notify(bot, summary)

    logger.info("Scrape cycle done: %d items added to cart", total_added)
    return reports


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("ScraperBot starting up")
    logger.info("Country: %s | Queries: %s | Interval: %d min",
                config.LAZADA_COUNTRY.upper(),
                ", ".join(config.SEARCH_QUERIES),
                config.CHECK_INTERVAL_MINUTES)

    # Build the Telegram application, injecting the scrape function
    async def _trigger_from_command() -> list[ScrapeReport]:
        return await run_scrape_and_notify(app.bot_data)

    app = build_app(_trigger_from_command)

    # APScheduler for recurring scrapes
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scrape_and_notify,
        "interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        id="lazada_scrape",
        kwargs={"app_bot_data": app.bot_data},
    )
    scheduler.start()
    logger.info("Scheduler started – next run in %d minutes", config.CHECK_INTERVAL_MINUTES)

    # Run an immediate scrape on first startup
    logger.info("Running initial scrape on startup")
    await run_scrape_and_notify(app.bot_data)

    # Start polling for Telegram commands (blocking)
    logger.info("Telegram bot polling started – send /help in chat")
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("ScraperBot stopped by user")
