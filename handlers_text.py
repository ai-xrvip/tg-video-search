"""handlers_text.py — Free-form text message handler for TG Video Search Bot"""
import asyncio
import logging

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit, user_waiting_search, user_waiting_card,
    user_category, ADMIN_IDS, VIP_USERS, ALL_USERS, INVITES, admin_setvip_state,
    PURCHASE_URL, _ONE_DAY, VIP_TEXT, build_search_keyboard,
    save_vip_db,
)
from handlers_commands import cmd_my, cmd_help
from handlers_search import _do_search
from config import config
from database import (
    db_add_user, db_activate_card, db_save_vip,
    db_bump_stat,
)

logger = logging.getLogger(__name__)


async def handle_text(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id in admin_setvip_state and user_id in ADMIN_IDS:
        del admin_setvip_state[user_id]
        try:
            parts = text.split()
            target_id = int(parts[0])
            days = int(parts[1]) if len(parts) > 1 else 0
            if days > 0:
                VIP_USERS[target_id] = now_ts() + days * 86400
                label = "%s天" % days
            else:
                VIP_USERS[target_id] = None
                label = "永久"
            await save_vip_db(target_id, VIP_USERS[target_id])
            if target_id not in ALL_USERS:
                ALL_USERS.add(target_id)
                asyncio.create_task(db_add_user(target_id))
            await update.message.reply_text(
                "✅ 已将用户 <code>%s</code> 设置为VIP（%s）" % (target_id, label),
                parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ 请输入有效的用户ID（数字）")
        return

    if text == "🔍 搜索":
        user_waiting_search.add(user_id)
        user_waiting_card.discard(user_id)
        keyboard = await build_search_keyboard(user_id, [
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
        ])
        await update.message.reply_text(
            "🔍 请直接输入搜索关键词～\n\n📂 点击上方分类按钮切换源：",
            parse_mode="HTML",
            reply_markup=keyboard)
        return
    elif text == "👑 VIP":
        user_waiting_search.discard(user_id)
        user_waiting_card.discard(user_id)
        if is_vip(user_id):
            await update.message.reply_text(
                "<b>👑 你已是VIP会员</b>\n\n🎉 享受所有特权～",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
        else:
            await update.message.reply_text(VIP_TEXT, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 输入卡密激活", callback_data="vip_activate")],
                    [InlineKeyboardButton("💳 购买卡密", url=PURCHASE_URL)],
                    [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")]
                ]))
        return
    elif text == "👤 我的":
        user_waiting_search.discard(user_id)
        user_waiting_card.discard(user_id)
        await cmd_my(update, context)
        return
    elif text == "📖 帮助":
        user_waiting_search.discard(user_id)
        user_waiting_card.discard(user_id)
        await cmd_help(update, context)
        return

    if user_id in user_waiting_card:
        user_waiting_card.discard(user_id)
        if is_vip(user_id):
            await update.message.reply_text(
                "❗ 你已经是VIP会员了。如需续费请使用新卡密。",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        card_code = text.strip()
        activated = await db_activate_card(card_code, user_id)
        if not activated:
            await update.message.reply_text(
                "❌ 卡密无效或已被使用。",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔑 重新输入", callback_data="vip_activate"),
                    InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
                ]]))
            return

        prefix = card_code.split("-")[0] if "-" in card_code else ""
        prefix_type = {"Y": "month", "J": "quarter", "N": "year", "S": "forever"}
        card_type = prefix_type.get(prefix, "")
        if not card_type:
            await update.message.reply_text(
                "⚠️ 卡密格式无效，请确认卡密正确。",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🌔 返回主菜单", callback_data="menu_home")
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
            msg = "✅ 卡密激活成功！\n\n类型：%s\n到期：%s\n\n返回主菜单即可享受VIP特权！" % (name, exp_str)
        else:
            msg = "✅ 卡密激活成功！\n\n类型：%s\n\n返回主菜单即可享受VIP特权！" % name
        await update.message.reply_text(msg,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")
            ]]))
        return

    if user_id in user_waiting_search:
        user_waiting_search.discard(user_id)
        keyword = text
        if not keyword:
            return
        category = user_category.get(user_id, "all")
        await _do_search(update, keyword, category)
        return

    # Default: any text = search
    if text and len(text) >= 1:
        user_waiting_search.discard(user_id)
        user_waiting_card.discard(user_id)
        category = user_category.get(user_id, "all")
        await _do_search(update, text, category)
        return

    return
