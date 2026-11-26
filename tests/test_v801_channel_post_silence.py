"""v8.0.1 Regression Suite — Channel Posts & Non-Private Chats Silence Guard.

Verifies the exact production bug fix:
  When the bot is added as an admin to ANY Telegram channel (or group) and
  the channel admin posts text, photos, media, or commands, the bot MUST
  stay 100% silent — NEVER throwing AttributeError on `effective_user=None`
  and NEVER replying `⚠️ حدث خطأ غير متوقع. أعد المحاولة.` inside the channel.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb import config
from fysb.handlers import admin_partners, common, inputs


def _channel_post_update(text: str = "تحليل الذهب اليوم", *, has_photo: bool = False):
    msg = SimpleNamespace(
        text=text,
        caption=text if has_photo else None,
        photo=[SimpleNamespace(file_id="AgAC_test_photo")] if has_photo else [],
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        channel_post=msg,
        edited_channel_post=None,
        effective_message=msg,
        effective_user=None,  # Telegram channel posts ALWAYS have effective_user=None!
        effective_chat=SimpleNamespace(id=-1001999888777, type="channel"),
    )


def _group_update(text: str = "مرحباً في المجموعة", chat_type: str = "supergroup"):
    msg = SimpleNamespace(
        text=text,
        caption=None,
        photo=[],
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        channel_post=None,
        edited_channel_post=None,
        effective_message=msg,
        effective_user=SimpleNamespace(id=777001, username="member", full_name="Member"),
        effective_chat=SimpleNamespace(id=-1001222333444, type=chat_type),
    )


def _private_update(text: str = "اختبار"):
    msg = SimpleNamespace(
        text=text,
        caption=None,
        photo=[],
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        channel_post=None,
        edited_channel_post=None,
        effective_message=msg,
        effective_user=SimpleNamespace(id=777002, username="priv", full_name="Private User"),
        effective_chat=SimpleNamespace(id=777002, type="private"),
    )


def test_01_channel_post_text_photo_media_and_commands_stay_completely_silent():
    ctx = SimpleNamespace(user_data={}, args=[], bot=SimpleNamespace())

    # 1) Channel text post
    u_txt = _channel_post_update("تحديث السوق اليوم 🚀")
    asyncio.run(inputs.on_text(u_txt, ctx))
    u_txt.effective_message.reply_text.assert_not_called()

    # 2) Channel photo post
    u_pho = _channel_post_update("شارت الذهب", has_photo=True)
    asyncio.run(inputs.on_photo(u_pho, ctx))
    u_pho.effective_message.reply_text.assert_not_called()

    # 3) Channel video/document media post
    u_med = _channel_post_update("فيديو تعليمي")
    asyncio.run(inputs.on_media(u_med, ctx))
    u_med.effective_message.reply_text.assert_not_called()

    # 4) All slash commands posted in a channel
    for cmd_fn in (
        common.start,
        common.menu_command,
        common.ping_command,
        common.update_command,
        common.admin_command,
        common.backup_command,
        admin_partners.partner_command,
    ):
        u_cmd = _channel_post_update("/start")
        asyncio.run(cmd_fn(u_cmd, ctx))
        u_cmd.effective_message.reply_text.assert_not_called()


def test_02_group_and_supergroup_messages_are_ignored_silently():
    ctx = SimpleNamespace(user_data={}, args=[], bot=SimpleNamespace())
    for ctype in ("group", "supergroup"):
        u_grp = _group_update("السلام عليكم", chat_type=ctype)
        asyncio.run(inputs.on_text(u_grp, ctx))
        asyncio.run(common.start(u_grp, ctx))
        asyncio.run(common.ping_command(u_grp, ctx))
        u_grp.effective_message.reply_text.assert_not_called()


def test_03_error_handler_never_replies_inside_channel_or_group_but_still_replies_in_private():
    err_ctx = SimpleNamespace(error=RuntimeError("simulated handler failure"))

    # Channel post -> error_handler MUST NOT reply inside the channel!
    u_chan = _channel_post_update("رسالة إدارة القناة")
    asyncio.run(common.error_handler(u_chan, err_ctx))
    u_chan.effective_message.reply_text.assert_not_called()

    # Supergroup message -> error_handler MUST NOT spam the group!
    u_grp = _group_update("رسالة مجموعة")
    asyncio.run(common.error_handler(u_grp, err_ctx))
    u_grp.effective_message.reply_text.assert_not_called()

    # Private user chat -> error_handler SHOULD still inform the user politely
    u_priv = _private_update("رسالة خاصة")
    asyncio.run(common.error_handler(u_priv, err_ctx))
    u_priv.effective_message.reply_text.assert_awaited_once()
    assert "حدث خطأ غير متوقع" in u_priv.effective_message.reply_text.call_args.args[0]


def test_04_main_py_restricts_commands_and_messages_to_private_chats_and_version_801():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_PRIV = filters.ChatType.PRIVATE" in src
    assert 'CommandHandler("start", start, filters=_PRIV)' in src
    assert "MessageHandler(_PRIV & filters.PHOTO, inputs.on_photo)" in src
    assert "MessageHandler(_PRIV & filters.TEXT & ~filters.COMMAND, inputs.on_text)" in src
    assert config.VERSION.startswith("8.")
