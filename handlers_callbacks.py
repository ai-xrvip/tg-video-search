"""handlers_callbacks.py — Callback query handler for inline keyboard interactions"""
import asyncio
import html
import logging
import secrets
import string

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, store_url, get_url,
    user_waiting_search, user_waiting_card, user_category,
    VIP_USERS, ALL_USERS, INVITES, ADMIN_IDS, admin_setvip_state,
    _ONE_DAY, CATEGORY_LABELS, PURCHASE_URL,
    save_vip_db, save_invite_db,
    get_invite_lock, get_vip_lock,
)
from handlers_search import _do_search, _show_results_page
from scrapers import CATEGORIES, _ensure_built
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
        # ── Category switching in search results ──
        if data.startswith("catr_"):
            rest = data[5:]
            parts = rest.rsplit("_", 1)
            if len(parts) == 2:
                keyword = parts[0]
                cat = parts[1]
                await _do_search(query, keyword, cat)
            return

        # ── Category switching (standalone) ──
        if data.startswith("cat_"):
            cat = data[4:]
            if cat in CATEGORY_LABELS:
                user_category[user_id] = cat
                label = CATEGORY_LABELS[cat]
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

        # ── Page navigation ──
        if data.startswith("page_"):
            parts = data.split("_", 3)
            if len(parts) >= 4:
                try:
                    keyword = parts[1]
                    page_str = parts[2]
                    cat = parts[3]
                    page = int(page_str)
                    from handlers_search import _show_results_page
                    # For page nav we need to re-search and show page
                    await _do_search(query, keyword, cat)
                except (ValueError, IndexError):
                    pass
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
                    ]]))
            else:
                from bot_utils import VIP_TEXT
                await query.edit_message_text(VIP_TEXT, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔽 输入卡密激活", callback_data="vip_activate")],
                        [InlineKeyboardButton("💰 购买卡密", url=PURCHASE_URL)],
                        [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
                    ]))
            return

        if data == "menu_help":
            user_waiting_search.discard(user_id)
            user_waiting_card.discard(user_id)
            from handlers_commands import cmd_help
            await cmd_help(query, context)
            return

        # ── VIP card activation ──
        if data == "vip_activate":
            user_waiting_search.discard(user_id)
            user_waiting_card.add(user_id)
            await query.edit_message_text(
                "🔽 请输入卡密（卡密格式：X-xxxxxxxxxxxx）：\n\n"
                "💡 卡密请联系管理员购买",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ 取消", callback_data="menu_home"),
                ]]))
            return

        # ── Card activation result ──
        if data.startswith("card_"):
            card_code = data[5:]
            if not card_code:
                return
            if is_vip(user_id):
                await query.edit_message_text(
                    "❌ 你已经是VIP会员了。如需续费请使用新卡密。",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home"),
                    ]]))
                return
            activated = await db_activate_card(card_code, user_id)
            if not activated:
                await query.edit_message_text(
                    "❌ 卡密无效或已被使用。",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔽 重新输入", callback_data="vip_activate")],
                        [InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")],
                    ]))
                return
            prefix = card_code.split("-")[0] if "-" in card_code else ""
            prefix_type = {"Y": "month", "J": "quarter", "N": "year", "S": "forever"}
            card_type = prefix_type.get(prefix, "")
            if not card_type:
                await query.edit_message_text(
                    "⚠️ 卡密格式无效，请确认卡密正确。",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 返回主页", callback_data="menu_home"),
                    ]]))
                return
            days_map = {"month": 30, "quarter": 90, "year": 360, "forever": None}
            day_names = {"month": "月卡(30天)", "quarter": "季卡(90天)", "year": "年卡(360天)", "forever": "永久"}
            days = days_map.get(card_type)
            expiry = None if days is None else now_ts() + days * 86400
            asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "card_activations"))
            VIP_USERS[user_id] = expiry
            await save_vip_db(user_id, expiry)
            name = day_names.get(card_type, card_type)
            if days:
                exp_str = datetime.fromtimestamp(expiry).strftime("%Y-%m-%d")
                msg = "✅ 卡密激活成功！\n\n类型：%s\n到期：%s\n\n返回主页即可享受VIP特权！" % (name, exp_str)
            else:
                msg = "✅ 卡密激活成功！\n\n类型：%s\n\n返回主页即可享受VIP特权！" % name
            await query.edit_message_text(msg,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home"),
                ]]))
            return

        # ── Invite code generation ──
        if data == "invite_gen":
            code = "INV-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            await save_invite_db(code, user_id)
            INVITES[code] = str(user_id)
            await query.edit_message_text(
                "✅ 邀请码生成成功！\n\n"
                f"你的邀请码: <code>{code}</code>\n\n"
                f"发送给好友: <code>/start {code}</code>\n\n"
                "每邀请一人双方各得1天VIP！",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                ]]))
            return

        # ── Page info ──
        if data == "page_info":
            await query.answer("使用下方按钮翻页", show_alert=False)
            return

        # ── Admin-only callbacks ──
        if user_id not in ADMIN_IDS:
            logger.warning("Non-admin user %s attempted admin callback: %s", user_id, data)
            return

        if data == "admin_setvip_prompt":
            admin_setvip_state[user_id] = True
            await query.edit_message_text(
                "请输入用户ID和天数（用空格分隔）:\n"
                "例如: 123456789 30 （30天VIP）\n"
                "不带天数则为永久VIP",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ 取消", callback_data="menu_home")
                ]]))
            return

        if data == "admin_gencode":
            count_map = {"Y": ("month", 30), "J": ("quarter", 90), "N": ("year", 360), "S": ("forever", None)}
            codes = []
            for prefix, (ctype, days) in count_map.items():
                for _ in range(10):
                    code = f"{prefix}-{''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))}"
                    await db_save_card(code, ctype, days, user_id)
                    day_str = f"{days}天" if days else "永久"
                    codes.append(f"{code} [{day_str}]")
            logger.info(f"Admin {user_id} generated 40 cards")
            await query.edit_message_text(
                f"✅ 已生成40张卡密！\n\n" + "\n".join(codes[:10]) + "\n\n更多卡密请导出查看。",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📜 导出卡密", callback_data="admin_exportcards"),
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home"),
                ]]))
            return

        if data == "admin_exportcards":
            unused = await db_list_unused_cards()
            if not unused:
                await query.edit_message_text("❌ 没有未使用的卡密。",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎴 生成卡密", callback_data="admin_gencode"),
                        InlineKeyboardButton("🏠 返回主页", callback_data="menu_home"),
                    ]]))
                return

            lines = []
            for c in unused:
                ct = c.get("card_type", "?")
                ct_map = {"month": "月卡(30天)", "quarter": "季卡(90天)", "year": "年卡(360天)", "forever": "永久"}
                label = ct_map.get(ct, ct)
                lines.append(f"{c['code']} — {label}")
            text = "\n".join(lines)
            if len(text) > 4000:
                parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
                for part in parts:
                    await context.bot.send_message(chat_id=user_id, text=f"<code>{html.escape(part)}</code>", parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"<code>{html.escape(text)}</code>", parse_mode="HTML")

            await query.edit_message_text("📜 卡密已发送到聊天中。",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                ]]))
            return

    except Exception as e:
        logger.error("Callback handler error for data=%s: %s", data, e)
        try:
            await query.edit_message_text("❌ 操作出错，请重试",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")
                ]]))
        except Exception:
            pass


async def _build_keyboard_with_category(user_id: int, category: str):
    """Build search keyboard with current category."""
    from bot_utils import build_search_keyboard
    return await build_search_keyboard(user_id, [
        [InlineKeyboardButton("🏠 返回主页", callback_data="menu_home")],
    ])
