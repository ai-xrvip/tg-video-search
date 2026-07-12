"""bot_utils.py — Shared state, constants, and helper functions for the TG Video Search Bot."""
import asyncio
import html
import logging
import re
import time as _time
from collections import defaultdict
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import config
from database import (
    db_load_vip, db_save_vip, db_delete_expired_vip,
    db_load_users, db_add_user,
    db_load_invites, db_save_invite,
    db_get_user_history,
)

logger = logging.getLogger(__name__)

# ---- Constants ----
_SEARCH_TIMEOUTS: dict[str, float] = {
    "xchina": 8.0,
    "guochan": config.SEARCH_TIMEOUT_GUOCHAN,
    "hanime": config.SEARCH_TIMEOUT_HANIME,
    "jav": config.SEARCH_TIMEOUT_JAV,
    "oumei": config.SEARCH_TIMEOUT_OUMEI,
    "jav_id": 8.0,
}
RESULTS_PER_PAGE: int = 5
URL_TTL: int = 3600
USER_STATE_TTL: int = 1800
RATE_LIMIT_WINDOW: int = 60
RATE_LIMIT_MAX: int = config.MAX_SEARCHES_PER_MINUTE
_ONE_DAY: int = 86400

# ---- Category info (from scrapers) ----
# Use fixed labels from scrapers/__init__
from scrapers.__init__ import CATEGORY_LABEL_MAP as _SRC_CAT_LABELS

CATEGORY_LABELS = dict(_SRC_CAT_LABELS)

CATEGORY_BUTTONS = [
    [InlineKeyboardButton("全部", callback_data="cat_all"),
     InlineKeyboardButton("国产", callback_data="cat_guochan"),
     InlineKeyboardButton("日韩", callback_data="cat_jav"),
     InlineKeyboardButton("欧美", callback_data="cat_oumei"),
     InlineKeyboardButton("番号", callback_data="cat_jav_id")],
]

PURCHASE_URL: str = "https://t.me/xiuren88bot?start=buy_524"

# ---- State ----
user_search_state: dict = {}
user_waiting_search: set[int] = set()
user_waiting_card: set[int] = set()
url_store: dict = {}
url_counter: int = 0
VIP_USERS: dict[int, float | None] = {}
ALL_USERS: set[int] = set()
INVITES: dict[str, str] = {}
_user_search_times: dict[int, list[float]] = defaultdict(list)

# Current category filter per user
user_category: dict[int, str] = {}
admin_setvip_state: dict[int, bool] = {}

# Async locks
_url_store_lock: asyncio.Lock | None = None
_url_counter_lock: asyncio.Lock | None = None
_user_search_lock: asyncio.Lock | None = None
_invite_lock: asyncio.Lock | None = None
_vip_lock: asyncio.Lock | None = None


def init_locks() -> None:
    global _url_store_lock, _url_counter_lock, _user_search_lock, _invite_lock, _vip_lock
    if _url_store_lock is None:
        _url_store_lock = asyncio.Lock()
        _url_counter_lock = asyncio.Lock()
        _user_search_lock = asyncio.Lock()
        _invite_lock = asyncio.Lock()
        _vip_lock = asyncio.Lock()


def get_url_store_lock() -> asyncio.Lock:
    assert _url_store_lock is not None
    return _url_store_lock


def get_url_counter_lock() -> asyncio.Lock:
    assert _url_counter_lock is not None
    return _url_counter_lock


def get_user_search_lock() -> asyncio.Lock:
    assert _user_search_lock is not None
    return _user_search_lock


def get_invite_lock() -> asyncio.Lock:
    assert _invite_lock is not None
    return _invite_lock


def get_vip_lock() -> asyncio.Lock:
    assert _vip_lock is not None
    return _vip_lock


ADMIN_IDS: set[int] = config.ADMIN_IDS

# ---- Keyboards ----
MENU_KEYBOARD = ReplyKeyboardMarkup([
    [KeyboardButton("🔍 搜索"), KeyboardButton("💎 VIP"), KeyboardButton("🙋 我的")],
    [KeyboardButton("📖 帮助")],
], resize_keyboard=True)

START_TEXT: str = """<b>🚀 TG视频搜索Bot 🚀</b>

💢 主人好呀～我是你的专属视频小助手！

📰 <b>我能做什么？</b>
• 🔍 海量视频随意搜（全部/国产/日韩/欧美/番号）
• 📫 点击结果直接播放视频

💞 点击下方按钮开始探索吧！"""

START_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔍 搜索视频", callback_data="menu_search")],
    [InlineKeyboardButton("💎 开通VIP", callback_data="menu_vip")],
    [InlineKeyboardButton("📖 使用帮助", callback_data="menu_help")],
])

VIP_TEXT: str = """<b>💎 VIP 会员说明</b>

🛆 <b>VIP 特权：</b>
• 无限次搜索
• 查看完整搜索结果
• 翻页浏览所有结果
• 优先体验新功能

💵 联系管理员购买卡密～"""


# ========== Helper functions ==========

def now_ts() -> float:
    return datetime.now().timestamp()


async def safe_search_wrapper(name: str, coro):
    """Run a search coroutine with its configured timeout."""
    timeout = _SEARCH_TIMEOUTS.get(name, 8.0)
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("%s search timed out after %ss", name, timeout)
        return []
    except Exception as e:
        logger.error("%s search error: %s", name, e)
        return []


async def cleanup_url_store() -> None:
    global url_store
    now = now_ts()
    async with get_url_store_lock():
        url_store = {k: v for k, v in url_store.items() if now - v.get("ts", 0) < URL_TTL}


async def cleanup_all() -> None:
    now = now_ts()
    stale_users = [uid for uid, s in user_search_state.items() if now - s.get("ts", 0) > USER_STATE_TTL]
    for uid in stale_users:
        del user_search_state[uid]
        user_waiting_search.discard(uid)
        user_waiting_card.discard(uid)
    await cleanup_url_store()
    await _clean_expired_vip()
    cutoff = now - RATE_LIMIT_WINDOW * 2
    for uid in list(_user_search_times.keys()):
        _user_search_times[uid] = [t for t in _user_search_times[uid] if t > cutoff]
        if not _user_search_times[uid]:
            del _user_search_times[uid]


async def save_vip_db(user_id: int, expiry: float | None) -> None:
    await db_save_vip(user_id, expiry)


async def load_vip_db() -> dict[int, float | None]:
    return await db_load_vip()


async def load_users_db() -> set[int]:
    return await db_load_users()


async def load_invites_db() -> dict[str, str]:
    return await db_load_invites()


async def save_invite_db(code: str, inviter_id: int) -> None:
    await db_save_invite(code, inviter_id)


async def store_url(url: str, **kwargs: object) -> str:
    global url_counter
    async with get_url_counter_lock():
        url_counter += 1
        key = str(url_counter)
    async with get_url_store_lock():
        entry: dict[str, object] = {"url": url, "ts": now_ts()}
        entry.update(kwargs)
        url_store[key] = entry
        if url_counter % 1000 == 0:
            await cleanup_url_store()
    return key


def get_url(key: str) -> str:
    entry = url_store.get(key)
    if not entry:
        return ""
    if now_ts() - entry.get("ts", 0) > URL_TTL:
        return ""
    return str(entry["url"])


async def check_rate_limit(user_id: int) -> bool:
    now = now_ts()
    cutoff = now - RATE_LIMIT_WINDOW
    async with get_user_search_lock():
        times = list(_user_search_times.get(user_id, []))
        times = [t for t in times if t > cutoff]
        if len(times) >= RATE_LIMIT_MAX:
            return False
        times.append(now)
        _user_search_times[user_id] = times
        return True


def is_vip(user_id: int) -> bool:
    if user_id not in VIP_USERS:
        return False
    expiry = VIP_USERS[user_id]
    if expiry is None:
        return True
    if now_ts() > expiry:
        return False
    return True


async def _clean_expired_vip() -> None:
    async with get_vip_lock():
        await _async_clean_expired_vip()


async def _async_clean_expired_vip() -> None:
    now = now_ts()
    expired = [uid for uid, exp in list(VIP_USERS.items()) if exp is not None and now > exp]
    if expired:
        for uid in expired:
            del VIP_USERS[uid]
        await db_delete_expired_vip()
        logger.info("Cleaned %d expired VIP users", len(expired))


def format_duration(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


async def build_search_keyboard(user_id: int, extra_buttons=None):
    """Build inline keyboard with category buttons and hot keywords."""
    buttons = []
    buttons.append(CATEGORY_BUTTONS[0])

    history = await db_get_user_history(user_id, limit=6)
    if history:
        hist_row = []
        for kw in history[:3]:
            hist_row.append(InlineKeyboardButton("🔄 %s" % kw, callback_data="hot_%s" % html.escape(kw)))
        if hist_row:
            buttons.append(hist_row)

    if extra_buttons:
        buttons.extend(extra_buttons)
    return InlineKeyboardMarkup(buttons)
