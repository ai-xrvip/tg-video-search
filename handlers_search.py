"""handlers_search.py — Search logic with proper update object handling."""
import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    safe_search_wrapper,

    now_ts, is_vip, check_rate_limit, store_url, get_url,
    user_waiting_search, user_category,
    VIP_USERS, ALL_USERS, RESULTS_PER_PAGE,
    CATEGORY_LABELS, CATEGORY_BUTTONS,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from pre_cache import search_with_cache, cache_get, track_search
import scrapers
from scrapers import search_all, _ensure_built, get_scraper
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


async def _async_refresh(keyword, category="all"):
    """Async refresh cache in background without blocking user."""
    try:
        results = await asyncio.wait_for(
            search_all(keyword, category, config.MAX_SEARCH_RESULTS),
            timeout=15.0,
        )
        if results:
            from pre_cache import cache_set
            await cache_set(keyword, results)
            logger.info("Async refresh: cached '%s' (%d)", keyword, len(results))
    except Exception as e:
        logger.debug("Async refresh error: %s", e)


async def _do_search(update_or_msg, keyword, category="all", page=1):
    """Handle a search request with progressive display.
    
    Shows results progressively: first batch at 3s, update at 6s, then final.
    """
    user_id = None
    msg = None
    chat_id = None

    if hasattr(update_or_msg, "effective_user"):
        user_id = update_or_msg.effective_user.id
        chat_id = update_or_msg.effective_chat.id
        try:
            msg = await update_or_msg.message.reply_text("\U0001f50d Searching...")
        except Exception:
            return
    elif hasattr(update_or_msg, "message") and hasattr(update_or_msg, "from_user"):
        user_id = update_or_msg.from_user.id
        chat_id = update_or_msg.message.chat_id
        msg = update_or_msg.message
        try:
            msg = await msg.reply_text("\U0001f50d Searching...")
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
            await msg.edit_text("\u23f1\ufe0f \u64cd\u4f5c\u592a\u9891\u7e41\u4e86\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\uff5e\n\u5f00\u901aVIP\u53ef\u65e0\u9650\u641c\u7d22\uff01")
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
            await msg.edit_text("\\U0001f50d <b>{}<\\b>  \\u2212 \\u7f13\\u5b58\\u547d\\u4e2d ({} \\u4e2a\\u7ed3\\u679c)".format(
                html.escape(keyword), len(cached_results)), parse_mode="HTML")
        except Exception:
            pass
        logger.info("Cache HIT for '%s' (%d results)", keyword, len(cached_results))
        entry = {"keyword": keyword, "category": category, "results": cached_results, "ts": now_ts()}
        await _show_results_page(msg, entry, 1)
        # Trigger async refresh without blocking
        asyncio.create_task(_async_refresh(keyword, category))
        return

    # 2. Cache miss — build per-source search tasks
    _ensure_built()
    cat_config = scrapers.CATEGORIES.get(category, scrapers.CATEGORIES.get("all", {}))
    source_names = cat_config.get("sources", [])
    results_per_source = max(config.MAX_SEARCH_RESULTS // max(len(source_names), 1), 5)

    tasks = []
    task_names = []
    for src_name in source_names:
        cls = get_scraper(src_name)
        if cls is None:
            continue
        scraper = cls()
        task = asyncio.create_task(scraper.search_with_retry(keyword, results_per_source))
        tasks.append(task)
        task_names.append(src_name)

    if not tasks:
        try:
            await msg.edit_text("\u6ca1\u6709\u53ef\u7528\u7684\u641c\u7d22\u6e90")
        except Exception:
            pass
        return

    # Progressive display: collect results at checkpoints
    all_results = []
    seen_urls = set()
    displayed_once = False
    pending_tasks = dict(zip(task_names, tasks))
    name_map = {t: n for n, t in zip(task_names, tasks)}

    checkpoints = [3.0, 5.0, None]  # 3s first batch, 5s update, then all remaining

    for checkpoint in checkpoints:
        remaining = {n: t for n, t in pending_tasks.items() if not t.done()}
        if not remaining:
            break

        if checkpoint is not None:
            done_set, _ = await asyncio.wait(
                list(remaining.values()),
                timeout=checkpoint,
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            done_set, _ = await asyncio.wait(
                list(remaining.values()),
                timeout=None,
            )

        # Collect completed tasks
        for task in done_set:
            src_name = name_map[task]
            try:
                results = task.result()
                if results:
                    new_count = 0
                    for r in results:
                        url = r.get("url", "") or getattr(r, "url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append(r.__dict__ if hasattr(r, "__dict__") else r)
                            new_count += 1
                    if new_count > 0:
                        logger.info("Progressive: +%d from %s", new_count, src_name)
            except Exception as e:
                logger.debug("%s search failed: %s", src_name, e)
            del pending_tasks[src_name]

        # Tasks not in done_set will be retried in the next checkpoint

        if not all_results:
            try:
                status = f"\U0001f50d \u641c\u7d22 <b>{html.escape(keyword)}</b> \u5728 <b>{cat_label}</b>...\n\u5df2\u5b8c\u6210: {len(task_names) - len(pending_tasks)}/{len(task_names)}"
                await msg.edit_text(status, parse_mode="HTML")
            except Exception:
                pass
            continue

        # First display or update
        entry = {"keyword": keyword, "category": category, "results": all_results, "ts": now_ts()}
        if not displayed_once:
            await _show_results_page(msg, entry, 1)
            displayed_once = True
        else:
            try:
                await _show_results_page(msg, entry, 1)
            except Exception:
                pass

    # Cancel anything still pending
    for _, task in pending_tasks.items():
        task.cancel()

    # Cache for callback navigation
    if all_results:
        entry_for_cache = {"keyword": keyword, "category": category, "results": all_results, "ts": now_ts()}
        global _search_counter
        async with _search_cache_lock:
            _search_counter += 1
            search_id = str(_search_counter)
            _search_cache[search_id] = entry_for_cache
            # Clean old entries
            now = now_ts()
            expired = [sid for sid, e in list(_search_cache.items()) if now - e.get("ts", 0) > _SEARCH_CACHE_TTL]
            for sid in expired:
                del _search_cache[sid]

    for _, task in pending_tasks.items():
        task.cancel()

    # Final display
    if all_results:
        entry = {"keyword": keyword, "category": category, "results": all_results, "ts": now_ts()}
        await _show_results_page(msg, entry, 1)
    else:
        cat_label = CATEGORY_LABELS.get(category, "\u5168\u90e8")
        try:
            await msg.edit_text(
                f"\u274c \u6ca1\u6709\u627e\u5230 <b>{html.escape(keyword)}</b> \u5728 <b>{cat_label}</b> \u4e2d\u7684\u7ed3\u679c",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("\U0001f504 \u91cd\u8bd5", callback_data=f"resrch_{keyword}_{category}"),
                ]]))
        except Exception:
            pass
async def _show_results_page(msg, search_or_entry, page=1):
    """Display a page of search results.
    
    Args:
        msg: Message object to edit
        search_or_entry: Either a search_id (str) or an entry dict with results/keyword/category
        page: Page number (1-indexed)
    """
    # Support both cached search_id and direct entry dict
    if isinstance(search_or_entry, str):
        async with _search_cache_lock:
            entry = _search_cache.get(search_or_entry)
        if not entry:
            try:
                await msg.edit_text("\u232b \u641c\u7d22\u7ed3\u679c\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u641c\u7d22",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("\U0001f50d \u641c\u7d22", callback_data="menu_search")
                    ]]))
            except Exception:
                pass
            return
        results = entry["results"]
        keyword = entry["keyword"]
        cur_cat = entry.get("category", "all")
    else:
        results = search_or_entry["results"]
        keyword = search_or_entry["keyword"]
        cur_cat = search_or_entry.get("category", "all")
        entry = search_or_entry
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
