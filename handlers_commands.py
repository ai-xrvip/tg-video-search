"""handlers_commands.py — Command handlers for TG Video Search Bot"""
import asyncio
import html
import logging
import secrets
import string

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot_utils import (
    now_ts, is_vip, check_rate_limit,
    user_waiting_search, user_waiting_card, user_category,
    VIP_USERS, ALL_USERS, INVITES, ADMIN_IDS,
    START_TEXT, START_KEYBOARD, VIP_TEXT, PURCHASE_URL, _ONE_DAY, MENU_KEYBOARD,
    save_vip_db, save_invite_db, load_vip_db, build_search_keyboard,
    get_invite_lock, get_vip_lock,
)
from handlers_search import _do_search
from config import config
from database import (
    db_add_user, db_bump_stat, db_save_vip, db_card_count_used, db_card_count_total,
    db_vip_count, db_user_count, db_get_stats_last_days,
    db_delete_expired_vip,
    db_get_user_history,
)

logger = logging.getLogger(__name__)


async def cmd_start(update, context):
    user_id = update.effective_user.id
    user_waiting_search.discard(user_id)
    user_waiting_card.discard(user_id)

    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        asyncio.create_task(db_add_user(user_id))
        asyncio.create_task(db_bump_stat(datetime.now().strftime("%Y-%m-%d"), "new_users"))

        if context.args:
            code = context.args[0]
            async with get_invite_lock():
                inviter = INVITES.get(code)
                if inviter and int(inviter) != user_id:
                    inviter_id = int(inviter)
                    current_expiry = VIP_USERS.get(inviter_id)
                    if current_expiry is None:
                        pass
                    elif current_expiry > now_ts():
                        VIP_USERS[inviter_id] = current_expiry + _ONE_DAY
                    else:
                        VIP_USERS[inviter_id] = now_ts() + _ONE_DAY
                    await db_save_vip(inviter_id, VIP_USERS[inviter_id])
                    try:
                        await context.bot.send_message(
                            chat_id=int(inviter),
                            text="🎉 恭喜！你邀请的用户已加入~\nVIP 已延长 1 天！"
                        )
                    except Exception:
                        pass

                    invited_expiry = VIP_USERS.get(user_id)
                    if invited_expiry is None:
                        VIP_USERS[user_id] = now_ts() + _ONE_DAY
                    elif invited_expiry > now_ts():
                        VIP_USERS[user_id] = invited_expiry + _ONE_DAY
                    else:
                        VIP_USERS[user_id] = now_ts() + _ONE_DAY
                    await db_save_vip(user_id, VIP_USERS[user_id])

    await update.message.reply_text(START_TEXT, reply_markup=START_KEYBOARD, parse_mode="HTML")
    await update.message.reply_text("📄 使用下方快捷按钮操作～", reply_markup=MENU_KEYBOARD)


async def cmd_search(update, context):
    user_id = update.effective_user.id
    user_waiting_card.discard(user_id)

    if not context.args:
        user_waiting_search.add(user_id)
        keyboard = await build_search_keyboard(user_id, [
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
        ])
        await update.message.reply_text(
            "🔍 请直接输入搜索关键词～\n📨 点击上方分类按钮切换源：",
            parse_mode="HTML",
            reply_markup=keyboard)
        return

    keyword = " ".join(context.args)
    category = user_category.get(user_id, "all")
    await _do_search(update, keyword, category)


async def cmd_my(update, context):
    user_id = update.effective_user.id
    if is_vip(user_id):
        expiry = VIP_USERS.get(user_id)
        is_permanent = expiry is None
        if is_permanent:
            info = "永久会员 ⭐️"
        else:
            exp_str = datetime.fromtimestamp(expiry).strftime("%Y年%m月%d日")
            remaining = max(0, int((expiry - now_ts()) / 86400))
            info = f"到期：{exp_str}  (剩{remaining}天)"
        my_invites = [code for code, inviter in INVITES.items() if inviter == str(user_id)]
        inv_text = f"\n\n🎆 你的邀请码: <code>{my_invites[0]}</code>\n发送 /start {my_invites[0]} 给好友" if my_invites else ""
        reply_buttons = [
            [InlineKeyboardButton("🎆 生成邀请码", callback_data="invite_gen")],
        ]
        if not is_permanent:
            reply_buttons.append([InlineKeyboardButton("💰 续费VIP", url=PURCHASE_URL)])
        reply_buttons.append([InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")])
        await update.message.reply_text(
            f"<b>💎 VIP信息</b>\n\n{info}{inv_text}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(reply_buttons))
    else:
        await update.message.reply_text(
            "🙋 <b>VIP会员</b>\n\n你还不是VIP会员喔～\n开通后可以无限搜索、查看完整结果！",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔽 输入卡密激活", callback_data="vip_activate")],
                [InlineKeyboardButton("💰 购买卡密", url=PURCHASE_URL)],
                [InlineKeyboardButton("🎆 邀请好友得VIP", callback_data="invite_info")],
                [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
            ]))

async def cmd_help(update, context):
    await update.message.reply_text(
        "<b>📖 使用帮助</b>\n\n"
        "🔍 TG视频搜索器使用指南：\n\n"
        "🔍 /search 关键词 — 搜索视频\n"
        "🙋 /my — 查看VIP信息\n"
        "🏠 /start — 回到主菜单\n"
        "💡 在任意聊天输入 @机器人 关键词 即可快速搜索\n\n"
        "分类说明：\n"
        "• 全部 — 同时搜索所有源\n"
        "• 国产 — 国内视频\n"
        "• 日韩 — 日韩AV\n"
        "• 欧美 — 欧美视频\n"
        "• 番号 — 输入番号搜索（如ABW-123）\n\n"
        "▶️ 点击搜索结果即可自动播放视频！",
        parse_mode="HTML"
    )


async def cmd_setvip(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("用法: /setvip <用户ID> [天数]\n例如: /setvip 123456 30")
        return
    try:
        target = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 0
        if days > 0:
            VIP_USERS[target] = now_ts() + days * 86400
            await update.message.reply_text(f"✅ 已将用户 {target} 设为VIP（{days}天）")
        else:
            VIP_USERS[target] = None
            await update.message.reply_text(f"✅ 已将用户 {target} 设为永久VIP")
        await save_vip_db(target, VIP_USERS[target])
        logger.info(f"VIP added: {target}")
    except ValueError:
        await update.message.reply_text("用户ID必须是数字")


async def cmd_admin(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    fresh_vip = await load_vip_db()
    now = now_ts()
    expired = [uid for uid, exp in list(fresh_vip.items()) if exp is not None and now > exp]
    for uid in expired:
        del fresh_vip[uid]
    if expired:
        asyncio.create_task(db_delete_expired_vip())
    async with get_vip_lock():
        VIP_USERS.clear()
        VIP_USERS.update(fresh_vip)

    total_vip = len(VIP_USERS)
    permanent = sum(1 for v in VIP_USERS.values() if v is None)
    timed = total_vip - permanent
    total_cards = await db_card_count_total()
    used_cards = await db_card_count_used()

    regular_users = [uid for uid in ALL_USERS if uid not in VIP_USERS]

    stats_text = (
        "📱 <b>管理员面板</b>\n\n"
        f"👃 总用户: {len(ALL_USERS)}\n"
        f"   普通用户: {len(regular_users)}\n"
        f"   VIP用户: {total_vip} ({permanent}永久 + {timed}限时)\n\n"
        f"🔽 卡密: 已用{used_cards}/总计{total_cards}\n"
        f"🎆 邀请码: {len(INVITES)}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 设置VIP用户", callback_data="admin_setvip_prompt")],
        [InlineKeyboardButton("🎨 生成卡密", callback_data="admin_gencode")],
        [InlineKeyboardButton("📜 导出卡密TXT", callback_data="admin_exportcards")],
    ])
    await update.message.reply_text(stats_text, parse_mode="HTML", reply_markup=keyboard)


async def cmd_stats(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    total_vip = len(VIP_USERS)
    permanent = sum(1 for v in VIP_USERS.values() if v is None)
    timed = total_vip - permanent
    total_cards = await db_card_count_total()
    used_cards = await db_card_count_used()
    stats = (
        "📱 <b>统计数据</b>\n\n"
        f"👃 用户: {len(ALL_USERS)}\n"
        f"💎 VIP: {total_vip} ({permanent}永久 + {timed}限时)\n"
    )
    await update.message.reply_text(stats, parse_mode="HTML")
