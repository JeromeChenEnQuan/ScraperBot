"""
main.py – ScraperBot entry point.

Usage
-----
  python main.py           – start the bot (requires a saved session)
  python main.py --login   – open a browser for manual login, save session

The --login flow must be run at least once before the normal bot can operate.
Re-run it whenever Lazada logs you out (the bot will send a Telegram alert).
"""

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

import config
from scraper.lazada_scraper import LazadaScraper, ScrapeReport, SessionExpiredError
from bot.telegram_bot import build_app, notify, fmt_report

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


# ── Scrape logic ───────────────────────────────────────────────────────────────

def _run_scrape_sync() -> list[ScrapeReport]:
    """Blocking scrape that runs inside asyncio.to_thread."""
    reports: list[ScrapeReport] = []
    with LazadaScraper() as scraper:
        # Verify the saved session is still valid before doing anything
        scraper.verify_session()

        for query in config.SEARCH_QUERIES:
            logger.info("Processing query: %s", query)
            report = scraper.run_query(query)
            reports.append(report)

    return reports


async def run_scrape_and_notify(app_bot_data: dict | None = None) -> list[ScrapeReport]:
    """Run scrape in a thread, then push Telegram notifications."""
    logger.info("Scrape cycle starting")

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    async with bot:
        try:
            reports = await asyncio.to_thread(_run_scrape_sync)
        except SessionExpiredError as exc:
            logger.error("Session expired: %s", exc)
            await notify(
                bot,
                f"*Session expired*\n{str(exc)}\nRun `python main\\.py \\-\\-login` to refresh\\.",
            )
            return []

        total_added = 0
        for report in reports:
            await notify(bot, fmt_report(report))
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


# ── Main modes ─────────────────────────────────────────────────────────────────

def run_login() -> None:
    """Interactive login mode: open a visible browser and save the session."""
    LazadaScraper.interactive_login()


async def run_bot() -> None:
    """Normal bot mode: schedule scrapes and listen for Telegram commands."""
    logger.info("ScraperBot starting up")
    logger.info(
        "Country: %s | Queries: %s | Interval: %d min",
        config.LAZADA_COUNTRY.upper(),
        ", ".join(config.SEARCH_QUERIES),
        config.CHECK_INTERVAL_MINUTES,
    )

    async def _trigger_from_command() -> list[ScrapeReport]:
        return await run_scrape_and_notify(app.bot_data)

    app = build_app(_trigger_from_command)

    # Recurring scrape schedule
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

    # Immediate scrape on startup
    logger.info("Running initial scrape on startup")
    await run_scrape_and_notify(app.bot_data)

    # Block on Telegram polling
    logger.info("Telegram bot polling started – send /help in chat")
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    if "--login" in sys.argv:
        run_login()
    else:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            logger.info("ScraperBot stopped by user")
