"""handlers_inline.py — Inline query handler for @botname search"""
import asyncio
import html
import logging
import re
from datetime import datetime
from uuid import uuid4

from telegram import InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit, user_category,
    VIP_USERS, ALL_USERS, RESULTS_PER_PAGE,
    CATEGORY_LABELS,
)
from database import db_add_user, db_bump_stat, db_add_search_history
from scrapers import search_all, CATEGORIES
from pre_cache import search_with_cache, cache_get, track_search
from config import config

logger = logging.getLogger(__name__)


async def inline_search(update, context):
    """Handle inline query (@botname keyword)."""
    query = update.inline_query
    user_id = query.from_user.id
    keyword = query.query.strip()

    if not keyword:
        results = [
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="输入关键词开始搜索",
                description="例如: 国产 新片 或番号 ABW-123",
                input_message_content=InputTextMessageContent(
                    "💡 在任意聊天输入 @机器人 关键词 即可搜索视频\n"
                    "例: @bot 国产 或 @bot ABW-123",
                    parse_mode="HTML"
                ),
                thumbnail_url="https://img.icons8.com/color/96/000000/search.png",
            )
        ]
        await query.answer(results, cache_time=10, is_personal=True)
        return

    # Only track first-time users
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

    # Rate limit for non-VIP
    if not is_vip(user_id) and not await check_rate_limit(user_id):
        results = [
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="⏱ 操作太频繁",
                description="请稍后再试，或开通VIP获取无限搜索",
                input_message_content=InputTextMessageContent(
                    "⏱ 搜索太频繁了，请稍后再试～\n\n开通VIP可无限搜索！",
                    parse_mode="HTML"
                ),
            )
        ]
        await query.answer(results, cache_time=5, is_personal=True)
        return

    # Determine category from keyword prefix / default
    cat_match = re.match(r'^(国产|日韩|里番|欧美|番号)\s+(.+)', keyword)
    if cat_match:
        cat_map = {"国产": "guochan", "日韩": "jav", "里番": "hanime", "欧美": "oumei", "番号": "jav_id"}
        category = cat_map.get(cat_match.group(1), user_category.get(user_id, "all"))
        keyword = cat_match.group(2)
    else:
        category = user_category.get(user_id, "all")

    asyncio.create_task(db_add_search_history(user_id, keyword))
    asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "searches"))
    asyncio.create_task(track_search(keyword))

    # Try cache first
    cached_results = await cache_get(keyword)
    if cached_results is not None:
        results = cached_results
    else:

        # Show searching indicator
        try:
            results = await asyncio.wait_for(
                search_all(keyword, category, config.MAX_SEARCH_RESULTS),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            results = []
        except Exception as e:
            logger.error("Inline search error: %s", e)
            results = []

    if not results:
        result_item = InlineQueryResultArticle(
            id=str(uuid4()),
            title="没有找到结果: %s" % keyword,
            description="换个关键词试试，或切换到番号分类搜索番号",
            input_message_content=InputTextMessageContent(
                "❌ 没有找到 <b>%s</b> 的相关视频" % html.escape(keyword),
                parse_mode="HTML"
            ),
        )
        await query.answer([result_item], cache_time=30, is_personal=True)
        return

    # Build inline results (first 10)
    items = []
    for i, r in enumerate(results[:10]):
        idx = i + 1
        title = r.get("title", "?")[:80]
        url = r.get("url", "")
        sl = r.get("source_label", "")
        dur = r.get("duration", "")
        desc = "%s %s" % (sl, ("⏱ " + dur) if dur else "")

        msg_text = "<b>%s</b>\n\n%s\n\n<a href=\"%s\">\U0001f517 打开视频</a>" % (
            html.escape(title), desc, html.escape(url)
        )

        item = InlineQueryResultArticle(
            id=str(uuid4()),
            title="%d. %s" % (idx, title),
            description=desc.strip(),
            input_message_content=InputTextMessageContent(msg_text, parse_mode="HTML", disable_web_page_preview=False),
            thumbnail_url=r.get("cover", "") or None,
        )
        items.append(item)

    await query.answer(items, cache_time=30, is_personal=True)
