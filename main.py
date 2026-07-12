"""main.py 鈥?TG Video Search Bot entry point"""
import asyncio
import gc
import logging
import sys
import os
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

logger = logging.getLogger(__name__)


# ========== Logging ==========

def _setup_logging():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


# ========== Periodic Cleanup ==========

async def _periodic_cleanup(application):
    last_reminder_day = 0
    while True:
        await asyncio.sleep(600)
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
                            text=f"鈴?<b>VIP鍗冲皢鍒版湡鎻愰啋</b>\n\n浣犵殑VIP浼氬憳灏嗕簬 <b>{exp_str}</b> 鍒版湡锛岃鍙婃椂缁垂鍝︼綖",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("馃挸 璐拱鍗″瘑", url="https://t.me/xiuren88bot?start=buy_524")
                            ]]))
                    except Exception as e:
                        logger.debug("VIP reminder send failed for user %s: %s", uid, e)


# ========== Startup ==========

async def _startup(application):
    """Run after database is ready."""
    # Start database first (must be in same event loop as run_polling)
    await start_database()
    # Load data
    await _load_data()

    # Start background tasks
    asyncio.create_task(_periodic_cleanup(application))

    # Set bot commands
    from telegram import BotCommand
    await application.bot.set_my_commands([
                BotCommand("start", "🏠 主菜单"),
        BotCommand("search", "🔍 搜索视频"),
        BotCommand("my", "🙁 我的VIP"),
        BotCommand("help", "📉 使用帮助"),
    ])
    logger.info("Bot started 鈥?all services running")


async def shutdown(app, signal_str=None):
    if signal_str:
        logger.info(f"Received signal {signal_str}, shutting down...")
    else:
        logger.info("Shutting down...")
    try:
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

    logger.info(f"Loaded {len(VIP_USERS)} VIP users, {len(ALL_USERS)} total users, {len(INVITES)} invites")


def main():
    _setup_logging()

    # Validate config
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error("Config error: " + str(e))
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

    # ========== Start ==========
    if config.WEBHOOK_URL:
        logger.info("Starting in webhook mode: " + config.WEBHOOK_URL)

        # run_webhook() calls initialize()/start()/start_database() internally
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
        # Polling mode ? run_polling() handles everything
        logger.info("Starting in polling mode")
        try:
            app.run_polling(
                allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"],
                drop_pending_updates=True,
            )
        except KeyboardInterrupt:
            asyncio.run(shutdown(app, "SIGINT"))