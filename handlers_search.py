"""handlers_search.py — Search logic with video playback."""
import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit,
    ALL_USERS, RESULTS_PER_PAGE,
    CATEGORY_LABELS as UI_CATEGORIES, store_url,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from pre_cache import cache_get, cache_set, track_search
import scrapers
from scrapers import search_all, _ensure_built, get_scraper, CATEGORIES
from config import config

logger = logging.getLogger(__name__)

# Only show these 5 category buttons in UI
UI_CAT_IDS = ["all", "guochan", "jav", "oumei", "jav_id"]


async def _do_search(update_or_msg, keyword, category="all", page=1):
    """Handle a search request with caching."""
    user_id = None
    msg = None

    if hasattr(update_or_msg, "effective_user"):
        user_id = update_or_msg.effective_user.id
        try:
            msg = await update_or_msg.message.reply_text("\U0001f50d 正在搜索...")
        except Exception:
            return
    elif hasattr(update_or_msg, "message") and hasattr(update_or_msg, "from_user"):
        user_id = update_or_msg.from_user.id
        try:
            msg = await update_or_msg.message.reply_text("\U0001f50d 正在搜索...")
        except Exception:
            pass
    else:
        logger.error("Unknown update type: %s", type(update_or_msg))
        return

    if not user_id or not msg:
        return

    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

    if not is_vip(user_id) and not await check_rate_limit(user_id):
        try:
            await msg.edit_text("\u23f1\ufe0f 操作太频繁了，请稍后再试～\n开通VIP可无限搜索！")
        except Exception:
            pass
        return

    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))
    asyncio.create_task(track_search(keyword))

    # Try cache first
    cached_results = await cache_get(keyword)
    if cached_results is not None:
        try:
            await msg.edit_text(
                f"\U0001f50d <b>{html.escape(keyword)}</b>  \u2212 缓存命中 ({len(cached_results)} 个结果)",
                parse_mode="HTML",
            )
        except Exception:
            pass
        logger.info("Cache HIT for '%s' (%d results)", keyword, len(cached_results))
        entry = {"keyword": keyword, "category": category, "results": cached_results, "ts": now_ts()}
        await _show_results_page(msg, entry, 1)
        asyncio.create_task(_async_refresh(keyword, category))
        return

    # Live search
    try:
        await msg.edit_text(f"\U0001f50d 正在搜索 <b>{html.escape(keyword)}</b> ...", parse_mode="HTML")
    except Exception:
        pass

    results = await asyncio.wait_for(
        search_all(keyword, category, config.MAX_SEARCH_RESULTS),
        timeout=25.0,
    )

    if results:
        asyncio.create_task(cache_set(keyword, results))
        entry = {"keyword": keyword, "category": category, "results": results, "ts": now_ts()}
        await _show_results_page(msg, entry, 1)
    else:
        cat_label = UI_CATEGORIES.get(category, "\u5168\u90e8")
        try:
            await msg.edit_text(
                f"\u274c 没有找到 <b>{html.escape(keyword)}</b> 在 <b>{cat_label}</b> 中的结果",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("\U0001f504 重试", callback_data=f"resrch_{keyword}_{category}"),
                ]]),
            )
        except Exception:
            pass


async def _async_refresh(keyword, category="all"):
    try:
        results = await asyncio.wait_for(
            search_all(keyword, category, config.MAX_SEARCH_RESULTS),
            timeout=15.0,
        )
        if results:
            await cache_set(keyword, results)
            logger.info("Async refresh: cached '%s' (%d results)", keyword, len(results))
    except Exception as e:
        logger.debug("Async refresh error: %s", e)


async def _show_results_page(msg, search_entry, page=1):
    """Display results with clickable titles."""
    results = search_entry["results"]
    keyword = search_entry["keyword"]
    cur_cat = search_entry.get("category", "all")

    total = len(results)
    total_pages = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * RESULTS_PER_PAGE
    end_idx = min(start_idx + RESULTS_PER_PAGE, total)
    page_results = results[start_idx:end_idx]

    # Header text only (no category row needed - buttons speak for themselves)
    parts = [f"\U0001f50d <b>{html.escape(keyword)}</b>  (共{total}个  第{page}/{total_pages}页)"]

    btns = []

    # Category filter row — only 5 buttons: 全部/国产/日韩/欧美/番号
    _ensure_built()
    cat_row = []
    for cid in UI_CAT_IDS:
        cinfo = CATEGORIES.get(cid)
        if not cinfo:
            continue
        label = cinfo["label"]
        if cid == cur_cat:
            cat_row.append(InlineKeyboardButton(f"[{label}]", callback_data=f"catr_{keyword}_{cid}"))
        else:
            cat_row.append(InlineKeyboardButton(label, callback_data=f"catr_{keyword}_{cid}"))
    if cat_row:
        btns.append(cat_row)

    # Each result = one clickable button:  🎬[05:12] title
    for i, r in enumerate(page_results):
        idx = start_idx + i + 1
        title = r.get("title", "?")[:60]
        duration = r.get("duration", "")
        url = r.get("url", "")
        source = r.get("source", "")

        # Store URL
        url_key = await store_url(url, source=source, keyword=keyword)

        dur_str = f"[{duration}]" if duration else ""
        btn_text = f"{idx}. \U0001f3ac{dur_str} {title.replace('[', '【').replace(']', '】')}"

        btns.append([
            InlineKeyboardButton(btn_text[:50], callback_data=f"play_{source}_{url_key}")
        ])

    # Navigation row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("\u25c0", callback_data=f"pg_{keyword}_{page-1}_{cur_cat}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="page_info"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("\u25b6", callback_data=f"pg_{keyword}_{page+1}_{cur_cat}"))
    if nav_row:
        btns.append(nav_row)

    # Bottom actions
    btns.append([
        InlineKeyboardButton("\U0001f504 重搜", callback_data=f"resrch_{keyword}_{cur_cat}"),
        InlineKeyboardButton("\U0001f3e0 主页", callback_data="menu_home"),
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
