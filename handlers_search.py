"""handlers_search.py — Search logic with proper update object handling."""
import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit, store_url, get_url,
    user_waiting_search, user_category,
    VIP_USERS, ALL_USERS, RESULTS_PER_PAGE,
    CATEGORY_LABELS, CATEGORY_BUTTONS,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from scrapers import search_all, CATEGORIES
from config import config

logger = logging.getLogger(__name__)

_search_cache = {}
_search_cache_lock = asyncio.Lock()
_search_counter = 0
_SEARCH_CACHE_TTL = 600


async def _clean_search_cache():
    now = now_ts()
    async with _search_cache_lock:
        expired = [sid for sid, e in list(_search_cache.items()) if now - e.get("ts", 0) > _SEARCH_CACHE_TTL]
        for sid in expired:
            del _search_cache[sid]


async def _do_search(update_or_msg, keyword, category="all", page=1):
    """Handle a search request.

    Accepts either a telegram.Update or a telegram.CallbackQuery as input.
    Returns (user_id, chat_id) for the caller.
    """
    # Extract user_id, message object, and chat_id from update/query
    user_id = None
    msg = None
    chat_id = None

    if hasattr(update_or_msg, "effective_user"):
        # Called from a command handler (telegram.Update)
        user_id = update_or_msg.effective_user.id
        chat_id = update_or_msg.effective_chat.id
        # Send a "Searching..." placeholder
        try:
            msg = await update_or_msg.message.reply_text("🔍 Searching...")
        except Exception:
            return
    elif hasattr(update_or_msg, "message") and hasattr(update_or_msg, "from_user"):
        # Called from a callback query handler (telegram.CallbackQuery)
        user_id = update_or_msg.from_user.id
        chat_id = update_or_msg.message.chat_id
        msg = update_or_msg.message  # Edit the callback message directly
    else:
        logger.error("Unknown update type: %s", type(update_or_msg))
        return

    if not user_id or not msg:
        return

    # Track new users
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

    # Rate limit for non-VIP
    if not is_vip(user_id) and not await check_rate_limit(user_id):
        try:
            await msg.edit_text("⏱️ 操作太频繁了，请稍后再试～\n开通VIP可无限搜索！")
        except Exception:
            pass
        return

    cat_label = CATEGORY_LABELS.get(category, "全部")
    status_text = f"🔍 正在搜索 <b>{html.escape(keyword)}</b> 在 <b>{cat_label}</b> 中..."
    try:
        await msg.edit_text(status_text, parse_mode="HTML")
    except Exception:
        pass

    # Log search
    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))

    # Perform the search
    try:
        results = await asyncio.wait_for(
            search_all(keyword, category, config.MAX_SEARCH_RESULTS),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        try:
            await msg.edit_text("⏱️ 搜索超时，请重试", parse_mode="HTML")
        except Exception:
            pass
        return
    except Exception as e:
        logger.error("Search error: %s", e)
        try:
            await msg.edit_text("❌ 搜索出错，请稍后重试", parse_mode="HTML")
        except Exception:
            pass
        return

    if not results:
        kb = InlineKeyboardMarkup([
            CATEGORY_BUTTONS[0],
            [InlineKeyboardButton("🔄 重试", callback_data=f"resrch_{keyword}_{category}")],
            [InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")],
        ])
        try:
            await msg.edit_text(
                f"❌ 没有找到 <b>{html.escape(keyword)}</b> 在 <b>{cat_label}</b> 中的结果",
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    # Cache results
    global _search_counter
    async with _search_cache_lock:
        _search_counter += 1
        search_id = str(_search_counter)
        _search_cache[search_id] = {
            "results": results,
            "keyword": keyword,
            "category": category,
            "chat_id": chat_id,
            "ts": now_ts(),
        }
        await _clean_search_cache()

    await _show_results_page(msg, search_id, 1)


async def _show_results_page(msg, search_id, page=1):
    """Display a page of search results."""
    async with _search_cache_lock:
        entry = _search_cache.get(search_id)

    if not entry:
        try:
            await msg.edit_text("⌛ 搜索结果已过期，请重新搜索",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔍 搜索", callback_data="menu_search")
                ]]))
        except Exception:
            pass
        return

    results = entry["results"]
    keyword = entry["keyword"]
    cur_cat = entry.get("category", "all")
    total = len(results)
    total_pages = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * RESULTS_PER_PAGE
    end_idx = min(start_idx + RESULTS_PER_PAGE, total)
    page_results = results[start_idx:end_idx]

    # Build category switch row
    cat_row = []
    for cid, cinfo in CATEGORIES.items():
        label = cinfo["label"]
        if cid == cur_cat:
            cat_row.append(InlineKeyboardButton(f"[{label}]", callback_data=f"catr_{keyword}_{cid}"))
        else:
            cat_row.append(InlineKeyboardButton(label, callback_data=f"catr_{keyword}_{cid}"))

    btns = [cat_row]

    # Build result text
    parts = [
        f"🔍 <b>{html.escape(keyword)}</b>  (共{total}个  第{page}/{total_pages}页)"
    ]

    for i, r in enumerate(page_results):
        idx = start_idx + i + 1
        title = r.get("title", "?")[:60]
        source_label = r.get("source_label", "")
        duration = r.get("duration", "")
        url = r.get("url", "")

        parts.append(f"\n{idx}. <a href=\"{html.escape(url)}\">{html.escape(title)}</a>")
        dur_str = f"  ⏱{duration}" if duration else ""
        parts.append(f"   {source_label}{dur_str}")

    # Navigation buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀ 上一页", callback_data=f"page_{search_id}_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="page_info"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("下一页 ▶", callback_data=f"page_{search_id}_{page + 1}"))
    if nav_row:
        btns.append(nav_row)

    # Bottom action buttons
    btns.append([
        InlineKeyboardButton("🔄 重新搜索", callback_data=f"resrch_{keyword}_{cur_cat}"),
        InlineKeyboardButton("🏠 主页", callback_data="menu_home"),
    ])

    try:
        await msg.edit_text(
            "\n".join(parts),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(btns),
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("Edit failed: %s", e)
