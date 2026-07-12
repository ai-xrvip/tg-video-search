"""handlers_callbacks.py — Full callback handler with video playback"""
import asyncio
import html
import logging
import secrets
import string

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, store_url, get_url, url_store,
    user_waiting_search, user_waiting_card, user_category,
    VIP_USERS, ALL_USERS, INVITES, ADMIN_IDS, admin_setvip_state,
    _ONE_DAY, CATEGORY_LABELS, PURCHASE_URL,
    save_vip_db, save_invite_db,
    get_invite_lock, get_vip_lock,
)
from handlers_search import _do_search, _show_results
from scrapers import CATEGORIES, _ensure_built
from scrapers.__init__ import CATEGORY_LABEL_MAP
from config import config
from database import (
    db_add_user, db_save_vip, db_save_card,
    db_card_count_used, db_card_count_total,
    db_list_unused_cards, db_vip_count,
    db_delete_expired_vip, db_get_user_history, db_bump_stat, db_activate_card,
)

logger = logging.getLogger(__name__)


async def handle_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    try:
        await query.answer()
    except Exception:
        pass

    try:
        # ── PLAY VIDEO ──
        if data.startswith("play_"):
            await _handle_play(query, context, data)
            return

        # ── Category switching in search results ──
        if data.startswith("catr_"):
            rest = data[5:]  # remove "catr_"
            # keyword could contain underscores, so split from the end
            # Format: catr_{keyword}_{category}
            # Find last underscore for category
            last_underscore = rest.rfind("_")
            if last_underscore > 0:
                keyword = rest[:last_underscore]
                cat = rest[last_underscore + 1:]
                await _do_search(query, keyword, cat)
            return

        # ── Page navigation ──
        if data.startswith("pg_"):
            parts = data.split("_", 3)
            if len(parts) >= 4:
                try:
                    keyword = parts[1]
                    page = int(parts[2])
                    cat = parts[3]
                    await _do_search(query, keyword, cat, page)
                except (ValueError, IndexError):
                    pass
            return

        # ── Category switching (main menu) ──
        if data.startswith("cat_"):
            cat = data[4:]
            if cat in CATEGORY_LABEL_MAP:
                user_category[user_id] = cat
                label = CATEGORY_LABEL_MAP[cat]
                keyboard = await _build_keyboard_with_category(user_id, cat)
                try:
                    await query.edit_message_text(
                        f"📨 已切换到 <b>{label}</b>\n🔍 请输入搜索关键词～",
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception:
                    pass
            return

        # ── Hot keyword search ──
        if data.startswith("hot_"):
            keyword = data[4:]
            keyword = html.unescape(keyword)
            if not keyword:
                return
            category = user_category.get(user_id, "all")
            await _do_search(query, keyword, category)
            return

        # ── Re-search ──
        if data.startswith("resrch_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                keyword = parts[1]
                category = parts[2] if len(parts) > 2 else "all"
                await _do_search(query, keyword, category)
            return

        # ── Menu navigation ──
        if data == "menu_home":
            user_waiting_search.discard(user_id)
            user_waiting_card.discard(user_id)
            from bot_utils import START_TEXT, START_KEYBOARD
            await query.edit_message_text(START_TEXT, parse_mode="HTML", reply_markup=START_KEYBOARD)
            return

        if data == "menu_search":
            user_waiting_search.add(user_id)
            user_waiting_card.discard(user_id)
            keyboard = await _build_keyboard_with_category(user_id, user_category.get(user_id, "all"))
            await query.edit_message_text(
                "🔍 请直接输入搜索关键词～\n📨 点击上方分类按钮切换源：",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return

        if data == "menu_vip":
            user_waiting_search.discard(user_id)
            user_waiting_card.discard(user_id)
            if is_vip(user_id):
                await query.edit_message_text(
                    "<b>💎 你已是VIP会员</b>\n\n🎀 享受所有特权～",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                    ]]]))
            else:
                from bot_utils import VIP_TEXT
                await query.edit_message_text(VIP_TEXT, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔽 输入卡密激活", callback_data="vip_activate")],
                        [InlineKeyboardButton("💰 购买卡密", url=PURCHASE_URL)],
                        [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")]
                    ]))
            return

        if data == "menu_help":
            user_waiting_search.discard(user_id)
            user_waiting_card.discard(user_id)
            await query.edit_message_text(
                "<b>📖 使用帮助</b>\n\n"
                "🔍 直接输入关键词搜索视频\n"
                "📂 点击分类按钮切换搜索源\n"
                "▶️ 点击结果自动播放视频\n"
                "💎 开通VIP可无限搜索\n\n"
                "分类说明：\n"
                "• 全部 — 同时搜索所有源\n"
                "• 国产 — 国内视频\n"
                "• 日韩 — 日韩AV\n"
                "• 欧美 — 欧美视频\n"
                "• 番号 — 输入番号搜索（如ABW-123）",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        if data == "page_info":
            return

        # ── VIP ──
        if data == "vip_activate":
            user_waiting_card.add(user_id)
            user_waiting_search.discard(user_id)
            await query.edit_message_text(
                "💳 请直接输入卡密（卡密格式如：Y-XXXXXX）：",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        if data == "invite_info":
            await query.edit_message_text(
                "<b>🎁 邀请好友得VIP</b>\n\n"
                "每邀请一位好友注册，你和好友各获得1天VIP！\n\n"
                "在 /my 页面点击「生成邀请码」获取你的邀请链接～",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        if data == "invite_gen":
            code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            async with get_invite_lock():
                INVITES[code] = str(user_id)
                await save_invite_db(code, user_id)
            await query.edit_message_text(
                f"🎉 你的邀请码已生成！\n\n<code>{code}</code>\n\n"
                f"发送给好友，让他们在 /start 时输入即可~\n"
                "每邀请一位好友，你和好友各获得1天VIP！",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        # ── Admin ──
        if data == "admin_setvip_prompt":
            if user_id not in ADMIN_IDS:
                return
            admin_setvip_state[user_id] = True
            await query.edit_message_text(
                "请输入用户ID和天数（如: 123456 30），天数为0表示永久VIP：",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("取消", callback_data="menu_home")
                ]]))
            return

        if data == "admin_gencode":
            if user_id not in ADMIN_IDS:
                return
            prefix_map = {"1": ("Y", 30), "2": ("J", 90), "3": ("N", 360), "4": ("S", None)}
            keyboard = [
                [InlineKeyboardButton("月卡(30天)", callback_data="gencard_month")],
                [InlineKeyboardButton("季卡(90天)", callback_data="gencard_quarter")],
                [InlineKeyboardButton("年卡(360天)", callback_data="gencard_year")],
                [InlineKeyboardButton("永久卡", callback_data="gencard_forever")],
                [InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")],
            ]
            await query.edit_message_text(
                "选择要生成的卡密类型：",
                reply_markup=InlineKeyboardMarkup(keyboard))
            return

        if data.startswith("gencard_"):
            if user_id not in ADMIN_IDS:
                return
            type_map = {"month": ("Y", 30), "quarter": ("J", 90), "year": ("N", 360), "forever": ("S", None)}
            ctype = data.split("_", 1)[1]
            if ctype not in type_map:
                return
            prefix, days = type_map[ctype]
            code = f"{prefix}-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))}"
            await db_save_card(code, ctype, days, user_id)
            days_text = f"永久" if days is None else f"{days}天"
            await query.edit_message_text(
                f"✅ 已生成 {days_text} 卡密：\n\n<code>{code}</code>\n\n"
                "请复制上面的卡密发给用户。",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("继续生成", callback_data="admin_gencode"),
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home"),
                ]]))
            return

        if data == "admin_exportcards":
            if user_id not in ADMIN_IDS:
                return
            cards = await db_list_unused_cards()
            if not cards:
                await query.edit_message_text("没有未使用的卡密。",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                    ]]))
                return
            type_names = {"month": "月卡", "quarter": "季卡", "year": "年卡", "forever": "永久"}
            lines = []
            for c in cards:
                tname = type_names.get(c["card_type"], c["card_type"])
                lines.append(f"{c['code']} ({tname})")
            await query.edit_message_text(
                f"共 {len(cards)} 张未使用卡密：\n\n" + "\n".join(list(lines[:50])),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                ]]))
            return

    except Exception as e:
        logger.error("Callback error data=%s: %s", data, e)
        try:
            await query.edit_message_text("❌ 操作出错，请重试",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                ]]))
        except Exception:
            pass


# ── Video Playback ──


async def _handle_play(query, context, data):
    """Handle video playback request.
    
    data format: play_{source}_{url_key}
    """
    parts = data.split("_", 2)
    if len(parts) < 3:
        await query.answer("❌ 无效的播放请求", show_alert=True)
        return

    source = parts[1]
    url_key = parts[2]

    # Get the original URL + title from url_store
    entry = url_store.get(url_key)
    if not entry:
        await query.answer("❌ 视频链接已过期，请重新搜索", show_alert=True)
        return
    original_url = str(entry.get("url", ""))
    video_title = str(entry.get("title", ""))
    if not original_url:
        await query.answer("❌ 视频链接已过期", show_alert=True)
        return
    logger.info("Play request: source=%s title=%s", source, video_title[:40])

    # Get video detail from the appropriate scraper
    try:
        video_url = None
        detail = None

        if source == "xchina":
            # XChina returns gallery pages; send the URL as-is
            video_url = original_url
        elif source == "guochan":
            from scrapers.guochan import get_video_detail as gd
            video_url = await gd(original_url)
        elif source == "hanime":
            from scrapers.hanime import get_video_detail as gd
            detail = await gd(original_url)
            if detail:
                for q in ["720p", "480p", "360p", "mp4", "hls"]:
                    if q in detail:
                        video_url = detail[q]
                        break
        elif source == "jav":
            from scrapers.jav import get_video_detail as gd
            detail = await gd(original_url)
            if detail and "mp4" in detail:
                video_url = detail["mp4"]
        elif source == "jav_id":
            from scrapers.jav_id import get_video_detail as gd
            detail = await gd(original_url)
            if detail and "magnets" in detail:
                await query.message.reply_text(
                    f"🔗 磁力链接:\n<code>{detail['magnets'][0]}</code>",
                    parse_mode="HTML",
                )
                await query.answer("✅ 已发送磁力链接", show_alert=False)
                return
        elif source == "oumei":
            from scrapers.oumei import get_video_detail as gd
            detail = await gd(original_url)
            if detail:
                for q in ["720p", "480p", "mp4"]:
                    if q in detail:
                        video_url = detail[q]
                        break
        else:
            video_url = original_url

        if video_url:
            await query.answer("✅ 正在加载视频...", show_alert=False)
            try:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=video_url,
                    caption=f"🎬 {video_title}",
                    supports_streaming=True,
                )
            except Exception as ve:
                logger.warning("send_video failed: %s, trying send_message", ve)
                # Fallback: send as message with URL link
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"▶️ <a href=\"{html.escape(video_url)}\">{html.escape(video_title)}</a>",
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
        else:
            # No video URL found, send original page as fallback
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"🔗 <a href=\"{html.escape(original_url)}\">{html.escape(video_title)}</a>",
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            await query.answer("⚠️ 无法提取视频直链，已发送原始链接", show_alert=True)

    except Exception as e:
        logger.error("Play error: %s", e)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"🔗 <a href=\"{html.escape(original_url)}\">{html.escape(video_title)}</a>",
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
        await query.answer("❌ 视频加载失败", show_alert=True)


async def _build_keyboard_with_category(user_id: int, category: str):
    """Build search keyboard with category buttons."""
    from bot_utils import build_search_keyboard
    return await build_search_keyboard(user_id, [
        [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
    ])
