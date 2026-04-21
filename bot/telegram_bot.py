"""
telegram_bot.py – Telegram notification and command layer.

Sends structured scrape reports to the configured chat and exposes
a small set of commands so the user can interact with the bot:

  /status   – show current config summary
  /run      – trigger an immediate scrape (outside the schedule)
  /cart     – show what's been added to cart this session
  /help     – list available commands
"""

import logging
from typing import Callable

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

import config
from scraper.lazada_scraper import LazadaScraper, ScrapeReport

logger = logging.getLogger(__name__)


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _fmt_report(report: ScrapeReport) -> str:
    lines = [f"*Search: {_esc(report.query)}*"]
    if report.error:
        lines.append(f"Error: {_esc(report.error)}")
        return "\n".join(lines)
    if not report.listings:
        lines.append("No listings matched your filters\\.")
        return "\n".join(lines)
    for i, item in enumerate(report.listings, 1):
        status = "Added to cart" if item.added_to_cart else f"Failed \\({_esc(item.error or 'unknown')}\\)"
        lines.append(
            f"{i}\\. [{_esc(item.name)}]({item.url})\n"
            f"   Price: {item.price:.2f} | {status}"
        )
    return "\n".join(lines)


def _esc(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def _fmt_config() -> str:
    queries = ", ".join(config.SEARCH_QUERIES)
    price_range = (
        f"{config.MIN_PRICE or '–'} – {config.MAX_PRICE or '–'}"
    )
    return (
        f"*Current Configuration*\n"
        f"Country: {_esc(config.LAZADA_COUNTRY.upper())}\n"
        f"Queries: {_esc(queries)}\n"
        f"Price range: {_esc(price_range)}\n"
        f"Sort by: {_esc(config.SORT_BY)}\n"
        f"Max items/query: {config.MAX_ITEMS_PER_QUERY}\n"
        f"Interval: every {config.CHECK_INTERVAL_MINUTES} min\n"
        f"Headless: {config.HEADLESS}"
    )


# ── Telegram command handlers ──────────────────────────────────────────────────

async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*ScraperBot Commands*\n"
        "/status – show current config\n"
        "/run    – trigger an immediate scrape\n"
        "/cart   – show items added to cart this session\n"
        "/help   – this message",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_fmt_config(), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Starting scrape now\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    reports = await ctx.application.bot_data["run_scrape"]()
    for report in reports:
        await update.message.reply_text(
            _fmt_report(report),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    session_cart: list = ctx.application.bot_data.get("cart", [])
    if not session_cart:
        await update.message.reply_text("Cart is empty this session\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    lines = ["*Items added to cart this session:*"]
    for i, item in enumerate(session_cart, 1):
        lines.append(f"{i}\\. [{_esc(item['name'])}]({item['url']}) — {item['price']:.2f}")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


# ── Core notification ──────────────────────────────────────────────────────────

async def notify(bot: Bot, message: str) -> None:
    """Send a plain notification message to the configured chat."""
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Failed to send Telegram notification: %s", exc)


# ── Bot builder ────────────────────────────────────────────────────────────────

def build_app(run_scrape_fn: Callable) -> Application:
    """
    Construct and wire up the telegram Application.

    run_scrape_fn: async callable that triggers a full scrape and returns
                   a list[ScrapeReport].  Injected by main.py so this module
                   stays decoupled from the scraper.
    """
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.bot_data["run_scrape"] = run_scrape_fn
    app.bot_data["cart"] = []

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("cart", cmd_cart))

    return app
