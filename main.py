"""main.py — TG Video Search Bot entry point"""
import asyncio
import gc
import logging
import os
import signal
import sys
import time
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, filters,
)

from config import config
from database import (
    start_database, stop_database,
    db_load_vip, db_load_users, db_load_invites,
    db_delete_expired_vip, db_vip_count,
)
from bot_utils import (
    init_locks, VIP_USERS, ALL_USERS, INVITES,
    cleanup_all, is_vip, now_ts, _ONE_DAY,
)
from handlers_commands import (
    cmd_start, cmd_setvip, cmd_admin, cmd_stats,
    cmd_my, cmd_help, cmd_search,
)
from handlers_callbacks import handle_callback
from handlers_text import handle_text
from handlers_inline import inline_search
from pre_cache import start_pre_cache, stop_pre_cache

logger = logging.getLogger(__name__)


# ========== Health Server (for Railway) ==========

def _start_health_server():
    """Start a minimal health check HTTP server in a daemon thread."""
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            logger.debug("Health server: %s", format % args)

    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    t.start()
    logger.info("Health server started on port %d", port)


# ========== Logging ==========

def _setup_logging():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    # Quiet down noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ========== Periodic Cleanup ==========

async def _periodic_cleanup(application):
    last_reminder_day = 0
    while True:
        await asyncio.sleep(600)
        try:
            await cleanup_all()
            gc.collect()
            today = datetime.now().strftime("%Y%m%d")
            if today != last_reminder_day:
                last_reminder_day = today
                now = now_ts()
                for uid, expiry in list(VIP_USERS.items()):
                    if expiry is not None and 0 < expiry - now <= _ONE_DAY:
                        exp_str = datetime.fromtimestamp(expiry).strftime("%Y-%m-%d")
                        try:
                            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                            await application.bot.send_message(
                                chat_id=uid,
                                text=f"⏰ <b>VIP即将到期提醒</b>\n\n你的VIP会员将于 <b>{exp_str}</b> 到期，请及时续费哦～",
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("💰 购买卡密", url="https://t.me/xiuren88bot?start=buy_524")
                                ]]))
                        except Exception as e:
                            logger.debug("VIP reminder send failed for user %s: %s", uid, e)
        except Exception as e:
            logger.error("Periodic cleanup error: %s", e)


# ========== Startup ==========

async def _startup(application):
    """Run after application is initialized."""
    try:
        # Start database first
        await start_database()
        # Load data
        await _load_data()
        # Start background tasks
        asyncio.create_task(_periodic_cleanup(application))
        asyncio.create_task(start_pre_cache())
        # Set bot commands
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("start", "🏠 主菜单"),
            BotCommand("search", "🔍 搜索视频"),
            BotCommand("my", "🙋 我的VIP"),
            BotCommand("help", "📖 使用帮助"),
        ])
        logger.info("Bot started — all services running")
    except Exception as e:
        logger.error("Startup failed: %s", e)
        raise


async def shutdown(app, signal_str=None):
    if signal_str:
        logger.info("Received signal %s, shutting down...", signal_str)
    else:
        logger.info("Shutting down...")
    try:
        await stop_pre_cache()
        await stop_database()
        await app.stop()
        await app.shutdown()
    except Exception as e:
        logger.warning("Shutdown error: %s", e)
    logger.info("Bot stopped.")


# ========== Register Handlers ==========

_CMD_HANDLERS = [
    ("start", cmd_start),
    ("help", cmd_help),
    ("search", cmd_search),
    ("my", cmd_my),
    ("setvip", cmd_setvip),
    ("admin", cmd_admin),
    ("stats", cmd_stats),
]


async def _load_data():
    """Load persistent data from SQLite into module globals."""
    logger.info("Loading data from database...")
    try:
        VIP_USERS.clear()
        VIP_USERS.update(await db_load_vip())
        ALL_USERS.clear()
        ALL_USERS.update(await db_load_users())
        INVITES.clear()
        INVITES.update(await db_load_invites())

        # Ensure at least one admin VIP exists
        if not VIP_USERS and config.ADMIN_IDS:
            from database import db_save_vip
            for aid in config.ADMIN_IDS:
                VIP_USERS[aid] = None
                await db_save_vip(aid, None)

        logger.info("Loaded %d VIP users, %d total users, %d invites",
                     len(VIP_USERS), len(ALL_USERS), len(INVITES))
    except Exception as e:
        logger.error("Failed to load data: %s", e)
        raise


async def _post_shutdown(app):
    await shutdown(app)


def main():
    _setup_logging()

    # Start health check server for Railway
    _start_health_server()

    # Validate config
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        sys.exit(1)

    # Initialize async locks
    init_locks()

    # Build the Application
    app = Application.builder() \
        .token(config.BOT_TOKEN) \
        .post_init(_startup) \
        .concurrent_updates(True) \
        .build()

    # Register command handlers
    for cmd, handler in _CMD_HANDLERS:
        app.add_handler(CommandHandler(cmd, handler))

    # Register message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Register callback query handler
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Register inline query handler
    app.add_handler(InlineQueryHandler(inline_search))

    # Register error handler
    async def error_handler(update, context):
        logger.error("Update %s caused error: %s", update, context.error)

    app.add_error_handler(error_handler)

    # ========== Start ==========
    if config.WEBHOOK_URL:
        logger.info("Starting in webhook mode: %s", config.WEBHOOK_URL)
        try:
            app.run_webhook(
                listen="0.0.0.0",
                port=config.WEBHOOK_PORT,
                url_path="webhook",
                webhook_url=config.WEBHOOK_URL + "/webhook",
            )
        except KeyboardInterrupt:
            asyncio.run(shutdown(app, "SIGINT"))
    else:
        # Polling mode — with retry for Telegram conflict errors
        logger.info("Starting in polling mode")
        max_retries = 10
        base_delay = 15
        for attempt in range(1, max_retries + 1):
            try:
                app.run_polling(
                    allowed_updates=["message", "callback_query", "inline_query"],
                    drop_pending_updates=True,
                )
                break
            except KeyboardInterrupt:
                asyncio.run(shutdown(app, "SIGINT"))
                break
            except Exception as e:
                error_str = str(e)
                if "Conflict" in error_str or "409" in error_str:
                    delay = min(base_delay * (2 ** (attempt - 1)), 120)
                    logger.warning("Telegram conflict (attempt %d/%d), retrying in %ds...", attempt, max_retries, delay)
                    time.sleep(delay)
                else:
                    logger.error("Polling error: %s", e)
                    break


if __name__ == "__main__":
    main()
