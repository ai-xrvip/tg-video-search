"""handlers_search.py — Search logic with progressive display and cache support."""
import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit,
    user_waiting_search, user_category,
    ALL_USERS, RESULTS_PER_PAGE,
    CATEGORY_LABELS, build_search_keyboard,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from pre_cache import cache_get, cache_set, track_search
import scrapers
from scrapers import search_all, _ensure_built, get_scraper, CATEGORIES, CATEGORY_LABELS as SCRAPER_LABELS
from config import config

logger = logging.getLogger(__name__)


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

    # Track new users
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

    # Rate limit
    if not is_vip(user_id) and not await check_rate_limit(user_id):
        try:
            await msg.edit_text(
                "\u23f1\ufe0f 操作太频繁了，请稍后再试～\n开通VIP可无限搜索！"
            )
        except Exception:
            pass
        return

    cat_label = CATEGORY_LABELS.get(category, "\u5168\u90e8")

    # Log search + track popularity
    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))
    asyncio.create_task(track_search(keyword))

    # 1. Try cache first (instant)
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
        # Trigger async refresh
        asyncio.create_task(_async_refresh(keyword, category))
        return

    # 2. Cache miss — live search all sources in parallel
    try:
        await msg.edit_text(f"\U0001f50d 正在搜索 <b>{html.escape(keyword)}</b> ...", parse_mode="HTML")
    except Exception:
        pass

    results = await asyncio.wait_for(
        search_all(keyword, category, config.MAX_SEARCH_RESULTS),
        timeout=20.0,
    )

    if results:
        # Cache results
        asyncio.create_task(cache_set(keyword, results))
        entry = {"keyword": keyword, "category": category, "results": results, "ts": now_ts()}
        await _show_results_page(msg, entry, 1)
    else:
        # No results — show empty with retry button
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
    """Async refresh cache in background."""
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
    """Display a page of search results.

    Args:
        msg: Message object to edit
        search_entry: dict with results/keyword/category
        page: Page number (1-indexed)
    """
    results = search_entry["results"]
    keyword = search_entry["keyword"]
    cur_cat = search_entry.get("category", "all")

    total = len(results)
    total_pages = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * RESULTS_PER_PAGE
    end_idx = min(start_idx + RESULTS_PER_PAGE, total)
    page_results = results[start_idx:end_idx]

    # Build category switch row
    cat_row = []
    _ensure_built()
    for cid, cinfo in scrapers.CATEGORIES.items():
        label = cinfo["label"]
        if cid == cur_cat:
            cat_row.append(InlineKeyboardButton(f"[{label}]", callback_data=f"catr_{keyword}_{cid}"))
        else:
            cat_row.append(InlineKeyboardButton(label, callback_data=f"catr_{keyword}_{cid}"))

    btns = [cat_row]

    # Build result text
    parts = [
        f"\U0001f50d <b>{html.escape(keyword)}</b>  (共{total}个  第{page}/{total_pages}页)"
    ]

    for i, r in enumerate(page_results):
        idx = start_idx + i + 1
        title = r.get("title", "?")[:60]
        source_label = r.get("source_label", "")
        duration = r.get("duration", "")
        url = r.get("url", "")

        parts.append(f"\n{idx}. <a href=\"{html.escape(url)}\">{html.escape(title)}</a>")
        dur_str = f"  \u23f1{duration}" if duration else ""
        parts.append(f"   {source_label}{dur_str}")

    # Navigation buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("\u25c0 上一页", callback_data=f"page_{keyword}_{page - 1}_{cur_cat}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="page_info"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("下一页 \u25b6", callback_data=f"page_{keyword}_{page + 1}_{cur_cat}"))
    if nav_row:
        btns.append(nav_row)

    # Bottom action buttons
    btns.append([
        InlineKeyboardButton("\U0001f504 重新搜索", callback_data=f"resrch_{keyword}_{cur_cat}"),
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
