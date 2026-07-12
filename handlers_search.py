"""handlers_search.py — Progressive search with category buttons at bottom, 10/page."""
import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit,
    ALL_USERS, store_url,
    CATEGORY_LABELS,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from pre_cache import cache_get, cache_set, track_search
import scrapers
from scrapers import search_all, _ensure_built
from scrapers.__init__ import CATEGORY_SOURCES, CATEGORY_LABEL_MAP
from config import config

logger = logging.getLogger(__name__)

UI_PAGE_SIZE = 10
# Category IDs shown in search results (ORDER matters)
UI_CAT_IDS = ["all", "guochan", "jav", "oumei", "jav_id"]


async def _do_search(update_or_msg, keyword, category="all", page=1):
    """Run search and display results progressively."""
    user_id = None
    msg = None

    if hasattr(update_or_msg, "effective_user"):
        user_id = update_or_msg.effective_user.id
        try:
            msg = await update_or_msg.message.reply_text("🔍 正在搜索...")
        except Exception:
            return
    elif hasattr(update_or_msg, "message") and hasattr(update_or_msg, "from_user"):
        user_id = update_or_msg.from_user.id
        try:
            msg = await update_or_msg.message.reply_text("🔍 正在搜索...")
        except Exception:
            pass
    else:
        logger.error("Unknown type: %s", type(update_or_msg))
        return

    if not user_id or not msg:
        return

    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

    if not is_vip(user_id) and not await check_rate_limit(user_id):
        try:
            await msg.edit_text("⏱️ 操作太频繁了，请稍后再试～\n开通VIP可无限搜索！")
        except Exception:
            pass
        return

    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))
    asyncio.create_task(track_search(keyword))

    # Check cache first
    cached_results = await cache_get(keyword)
    if cached_results is not None:
        try:
            await msg.edit_text(
                f"🔍 <b>{html.escape(keyword)}</b>  — 缓存 ({len(cached_results)} 个结果)",
                parse_mode="HTML",
            )
        except Exception:
            pass
        entry = {"keyword": keyword, "category": category, "results": cached_results, "ts": now_ts()}
        await _show_results(msg, entry, page)
        return

    # Progressive search: show results as sources complete
    _ensure_built()
    source_names = CATEGORY_SOURCES.get(category, CATEGORY_SOURCES["all"])
    all_results = []
    seen_urls = set()

    # Create search tasks
    from scrapers.base import get_scraper
    tasks = {}
    for src_name in source_names:
        cls = get_scraper(src_name)
        if cls is None:
            continue
        scraper = cls()
        tasks[src_name] = asyncio.create_task(
            scraper.search_with_retry(keyword, max(config.MAX_SEARCH_RESULTS // max(len(source_names), 1), 5))
        )

    if not tasks:
        try:
            await msg.edit_text("❌ 没有可用的搜索源")
        except Exception:
            pass
        return

    # Progressive display: update as results come in
    displayed_once = False
    remaining = set(tasks.values())
    first_batch = True

    while remaining:
        done, pending = await asyncio.wait(
            remaining,
            timeout=3.0 if first_batch else 5.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        first_batch = False

        if done:
            for task in done:
                src_name = next(n for n, t in tasks.items() if t is task)
                try:
                    results = task.result()
                    if results:
                        for r in results:
                            url = r.url if hasattr(r, "url") else r.get("url", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                all_results.append(r.__dict__ if hasattr(r, "__dict__") else r)
                except Exception as e:
                    logger.debug("%s error: %s", src_name, e)

        remaining = pending

        if all_results and not displayed_once:
            # First display
            try:
                await msg.edit_text(
                    f"🔍 <b>{html.escape(keyword)}</b>  — 找到 {len(all_results)} 个结果",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            entry = {"keyword": keyword, "category": category, "results": all_results, "ts": now_ts()}
            await _show_results(msg, entry, page)
            displayed_once = True
            asyncio.create_task(cache_set(keyword, all_results))
        elif all_results and displayed_once and not remaining:
            # Final update when all done
            asyncio.create_task(cache_set(keyword, all_results))
            entry = {"keyword": keyword, "category": category, "results": all_results, "ts": now_ts()}
            await _show_results(msg, entry, page)

    # No results at all
    if not all_results:
        cat_label = CATEGORY_LABEL_MAP.get(category, "全部")
        try:
            await msg.edit_text(
                f"❌ 没有找到 <b>{html.escape(keyword)}</b> 在 <b>{cat_label}</b> 中的结果",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 重试", callback_data=f"resrch_{keyword}_{category}"),
                ]]),
            )
        except Exception:
            pass


async def _show_results(msg, search_entry, page=1):
    """Display results page with clickable titles and category buttons at bottom."""
    results = search_entry["results"]
    keyword = search_entry["keyword"]
    cur_cat = search_entry.get("category", "all")

    total = len(results)
    total_pages = max(1, (total + UI_PAGE_SIZE - 1) // UI_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * UI_PAGE_SIZE
    end_idx = min(start_idx + UI_PAGE_SIZE, total)
    page_results = results[start_idx:end_idx]

    btns = []

    # 1. Each result = full-width clickable button (title plays video)
    for i, r in enumerate(page_results):
        idx = start_idx + i + 1
        title = r.get("title", "?")[:55]
        duration = r.get("duration", "")
        url = r.get("url", "")
        source = r.get("source", "xchina")
        url_key = await store_url(url, source=source, keyword=keyword, title=title)

        dur_str = f"[{duration}]" if duration else ""
        btn_text = f"{idx}. 🎬{dur_str} {title}"
        btns.append([
            InlineKeyboardButton(btn_text[:64], callback_data=f"play_{source}_{url_key}")
        ])

    # 2. Category buttons at BOTTOM (no emoji flags, just text)
    cat_row = []
    for cid in UI_CAT_IDS:
        label = CATEGORY_LABEL_MAP.get(cid, cid)
        if cid == cur_cat:
            cat_row.append(InlineKeyboardButton(f"[{label}]", callback_data=f"catr_{keyword}_{cid}"))
        else:
            cat_row.append(InlineKeyboardButton(label, callback_data=f"catr_{keyword}_{cid}"))
    btns.append(cat_row)

    # 3. Navigation + actions
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀", callback_data=f"pg_{keyword}_{page-1}_{cur_cat}"))
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="page_info"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("▶", callback_data=f"pg_{keyword}_{page+1}_{cur_cat}"))
    btns.append(nav_row)
    btns.append([
        InlineKeyboardButton("🔄 重搜", callback_data=f"resrch_{keyword}_{cur_cat}"),
        InlineKeyboardButton("🏠 主页", callback_data="menu_home"),
    ])

    # Header: keyword + page info
    header = f"🔍 <b>{html.escape(keyword)}</b>  (共{total}个  第{page}/{total_pages}页)"

    try:
        await msg.edit_text(
            header,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(btns),
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("Edit failed: %s", e)
