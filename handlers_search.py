"""handlers_search.py — Search logic. Titles as links, cats re-search on click."""
import asyncio, html, logging
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
        expired = [sid for sid, e in _search_cache.items() if now - e.get("ts", 0) > _SEARCH_CACHE_TTL]
        for sid in expired:
            del _search_cache[sid]

async def _do_search(update_or_msg, keyword, category="all", page=1):
    user_id, msg, chat_id = None, None, None
    if hasattr(update_or_msg, "effective_user"):
        user_id = update_or_msg.effective_user.id
        msg = await update_or_msg.message.reply_text("Searching...")
        chat_id = update_or_msg.effective_chat.id
    elif hasattr(update_or_msg, "reply_text"):
        user_id = getattr(update_or_msg, "from_user", None) and update_or_msg.from_user.id
        chat_id = getattr(update_or_msg, "chat_id", None)
        msg = update_or_msg
    else:
        user_id = update_or_msg.from_user.id
        msg = update_or_msg.message
        chat_id = update_or_msg.message.chat_id
    if not user_id:
        return
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))
    if not is_vip(user_id) and not await check_rate_limit(user_id):
        await msg.edit_text("Too frequent.")
        return
    cat_label = CATEGORY_LABELS.get(category, "All")
    try:
        await msg.edit_text("Searching %s in %s..." % (keyword, cat_label))
    except Exception:
        pass
    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))
    try:
        results = await asyncio.wait_for(search_all(keyword, category, config.MAX_SEARCH_RESULTS), timeout=25.0)
    except asyncio.TimeoutError:
        await msg.edit_text("Timeout.")
        return
    except Exception as e:
        logger.error("Search error: %s", e)
        await msg.edit_text("Error.")
        return
    if not results:
        kb = InlineKeyboardMarkup([CATEGORY_BUTTONS[0], [InlineKeyboardButton("Retry", callback_data="resrch_%s_%s" % (keyword, category))]])
        await msg.edit_text("No results for %s in %s." % (keyword, cat_label), reply_markup=kb)
        return
    global _search_counter
    async with _search_cache_lock:
        _search_counter += 1
        search_id = str(_search_counter)
        _search_cache[search_id] = {"results": results, "keyword": keyword, "category": category, "chat_id": chat_id, "ts": now_ts()}
        await _clean_search_cache()
    await _show_results_page(msg, search_id, 1)

async def _show_results_page(msg, search_id, page=1):
    async with _search_cache_lock:
        entry = _search_cache.get(search_id)
    if not entry:
        await msg.edit_text("Expired.")
        return
    results, keyword = entry["results"], entry["keyword"]
    cur_cat = entry.get("category", "all")
    total = len(results)
    tp = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    page = max(1, min(page, tp))
    s, e = (page - 1) * RESULTS_PER_PAGE, min((page - 1) * RESULTS_PER_PAGE + RESULTS_PER_PAGE, total)
    pr = results[s:e]

    cat_row = []
    for cid, cinfo in CATEGORIES.items():
        label = cinfo["label"]
        if cid == cur_cat:
            cat_row.append(InlineKeyboardButton("[" + label + "]", callback_data="catr_%s_%s" % (keyword, cid)))
        else:
            cat_row.append(InlineKeyboardButton(label, callback_data="catr_%s_%s" % (keyword, cid)))
    btns = [cat_row]

    parts = ["\U0001f50d <b>%s</b>  (共%d个, 第%d/%d页)" % (html.escape(keyword), total, page, tp)]

    for i, r in enumerate(pr):
        idx = s + i + 1
        t = r.get("title", "?")[:60]
        sl = r.get("source_label", "")
        d = r.get("duration", "")
        url = r.get("url", "")
        parts.append("\n%d. <a href=\"%s\">%s</a>" % (idx, html.escape(url), html.escape(t)))
        dur_str = (" ⏱" + d) if d else ""
        parts.append("   %s%s" % (sl, dur_str))

    nr = []
    if page > 1:
        nr.append(InlineKeyboardButton("◀ Prev", callback_data="page_%s_%d" % (search_id, page - 1)))
    nr.append(InlineKeyboardButton("%d/%d" % (page, tp), callback_data="page_info"))
    if page < tp:
        nr.append(InlineKeyboardButton("Next ▶", callback_data="page_%s_%d" % (search_id, page + 1)))
    if nr:
        btns.append(nr)
    btns.append([InlineKeyboardButton("New Search", callback_data="resrch_%s_%s" % (keyword, cur_cat)), InlineKeyboardButton("Home", callback_data="menu_home")])
    try:
        await msg.edit_text("\n".join(parts), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns), disable_web_page_preview=True)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("Edit failed: %s", e)
