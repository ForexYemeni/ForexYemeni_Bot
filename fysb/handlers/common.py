"""Common handlers — render(), start, /admin, error handler, /backup.

CRITICAL (button experience, v6.8.2 — the LIVING PANEL, fixed for reality):
  * every callback is answered IMMEDIATELY (await q.answer()) in the dispatcher
  * the chat keeps ONE living panel per chat:
      - tapping a button ON the current living panel mutates it in place
        (edit_text) — zero message spam, zero scroll-storm
      - tapping a button on an OLD/stale panel DELETES that panel (best
        effort) and sends a FRESH page at the bottom of the chat — exactly
        where the user is looking, so a page ALWAYS visibly appears
      - messages Telegram refuses to serve (>48h → InaccessibleMessage) go
        through the same fresh path using msg.chat (present on both kinds)
  * REGRESSION THIS REPLACES (owner report 2026-11-23 15:11, «زر يُضغط ولا
    يظهر أي شيء»): v6.8.1 edited the tapped message in place unconditionally.
    Telegram edits do NOT move a message — tapping a button on an old panel
    silently mutated a message high up in the chat while the user stared at
    the bottom: no visible change, no error, and the dead `q.message_chat`
    fallback raised RuntimeError for inaccessible messages.
"""
import html
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import ContextTypes

from fysb import config, db
from fysb.config import log
from fysb.services import users as users_service
from fysb.services import audit as audit_log
from fysb.ui import keyboards as kb

# Set to True when telegram.error.Conflict shut the bot down, so main()
# can exit with code 3 and the installer can explain the remedy.
conflict_detected = False

# ---------------------------------------------------------------------------
# Living-panel registry: chat_id -> message_id of the panel that is currently
# the single interactive surface of that chat. Process-local by design:
# after a restart the registry is empty, so the first tap takes the fresh
# path (delete stale + send at the bottom) — the safest possible default.
# ---------------------------------------------------------------------------
_last_panel: dict[int, int] = {}


def register_panel(chat_id, message_id) -> None:
    """Mark a message as the chat's living panel (start()/admin_command/render)."""
    try:
        if chat_id is not None and message_id:
            _last_panel[int(chat_id)] = int(message_id)
    except (TypeError, ValueError):
        pass


def safe(s) -> str:
    return html.escape(str(s if s is not None else ""))


async def render(q, text: str, markup=None, bot=None) -> None:
    """Living-panel rendering — one surface per chat, a page ALWAYS appears.

    Contract (v6.8.2):
      1. tapped message IS the chat's living panel  -> edit in place (instant,
         no new message; identical content is a silent no-op per Telegram).
      2. anything else (stale panel, unknown message, edit failure,
         InaccessibleMessage >48h, media caption) -> the tapped panel is
         deleted (best effort) and a FRESH page is sent to the chat bottom —
         visible instantly, never lost up in the history.
      3. every render is logged with its method and latency for support.
    """
    t0 = time.monotonic()
    msg = q.message
    chat = getattr(msg, "chat", None)   # present on Message AND InaccessibleMessage
    if chat is None or bot is None:
        raise RuntimeError("callback message is no longer available")
    mid = getattr(msg, "message_id", None)
    is_current = mid is not None and _last_panel.get(chat.id) == mid

    # 1) Living panel -> mutate in place (the zero-spam luxury surface).
    if is_current and hasattr(msg, "edit_text") and len(text) <= 4096:
        try:
            await msg.edit_text(text=text, reply_markup=markup,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True)
            log.info("PAGE RENDERED | user=%s | method=edit_in_place | %.0fms",
                     q.from_user.id, (time.monotonic() - t0) * 1000)
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                log.info("PAGE RENDERED | user=%s | method=edit_noop | %.0fms",
                         q.from_user.id, (time.monotonic() - t0) * 1000)
                return
            # any other edit failure -> replace with a fresh page below

    # 2) Stale / uneditable / inaccessible panel -> remove it and deliver the
    #    page fresh. Preferred: bot.send_message — the TRUE bottom of the chat,
    #    exactly where the user is looking. Legacy call sites that omit `bot`
    #    degrade gracefully to reply_text (right after the tapped message).
    if msg is not None and hasattr(msg, "delete"):
        try:
            await msg.delete()
        except Exception:
            pass  # already gone / no permission / too old — never fatal
    if bot is not None:
        sent = await bot.send_message(chat.id, text, reply_markup=markup,
                                      parse_mode=ParseMode.HTML,
                                      disable_web_page_preview=True)
        register_panel(chat.id, getattr(sent, "message_id", None))
        log.info("PAGE RENDERED | user=%s | method=fresh | %.0fms",
                 q.from_user.id, (time.monotonic() - t0) * 1000)
        return
    await msg.reply_text(text=text, reply_markup=markup,
                         parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True)
    log.info("PAGE RENDERED | user=%s | method=fresh_reply | %.0fms",
             q.from_user.id, (time.monotonic() - t0) * 1000)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    uid = tg_user.id
    try:
        users_service.ensure_user(tg_user)
    except PermissionError:
        await update.effective_message.reply_text(
            "⛔ التسجيل مغلق مؤقتاً. تواصل مع الدعم إذا كان لديك حساب قائم.")
        return
    users_service.touch(uid)
    # --- Royal referral capture (first link wins, server-side only) ---------
    # /start ref<CODE> — e.g. https://t.me/<bot>?start=refROYAL
    payload = (context.args or [""])[0] if context.args else ""
    if isinstance(payload, str) and payload.lower().startswith("ref") and len(payload) > 3 \
            and uid != config.ADMIN_ID:
        try:
            from fysb.services import referrals
            cap = referrals.capture(payload[3:], uid)
            if cap.get("captured"):
                audit_log.log(uid, f"referral_captured:{payload[3:].upper()}")
        except Exception:
            log.exception("referral capture failed")
    if uid == config.ADMIN_ID:
        sent = await update.effective_message.reply_text(
            f"👑 <b>{config.BRAND}</b>\n"
            f"<b>SUPER ADMIN COMMAND CENTER</b>\n\n"
            f"مرحباً بك في مركز التحكم الرئيسي — جميع الوحدات متاحة عبر الأزرار.",
            parse_mode=ParseMode.HTML, reply_markup=kb.admin_menu())
        register_panel(sent.chat_id, sent.message_id)
        audit_log.log(uid, "admin_panel_open")
        return
    from fysb.theme import header
    sent = await update.effective_message.reply_text(
        header("منصة التداول", "اختر وجهتك من لوحة الأزرار التفاعلية") +
        "\n\n<b>الوحدات المتاحة:</b>\n"
        "📊 لوحة الأداء · 📡 الإشارات المباشرة\n"
        "💎 الباقات · 🧪 التداول التجريبي\n"
        "🎧 الدعم الفني · ⚙️ التفضيلات",
        parse_mode=ParseMode.HTML, reply_markup=kb.user_menu())
    register_panel(sent.chat_id, sent.message_id)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        audit_log.log(uid, "security:admin_command_denied")
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة المخولة فقط.")
        return
    sent = await update.effective_message.reply_text(
        f"👑 <b>{config.BRAND} | SUPER ADMIN</b>\nاختر وحدة الإدارة من الأزرار:",
        parse_mode=ParseMode.HTML, reply_markup=kb.admin_menu())
    register_panel(sent.chat_id, sent.message_id)


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    from fysb.services import backup as backup_service
    try:
        dest = backup_service.quick_db_snapshot("admin")
        if dest is None:
            await update.effective_message.reply_text(
                "⏳ قاعدة البيانات مشغولة حالياً — تعذر أخذ نسخة لحظية الآن. حاول بعد قليل.")
            return
        with open(dest, "rb") as f:
            await update.effective_message.reply_document(f, filename=dest.name)
        audit_log.log(uid, f"database_backup:{dest.name}")
        await update.effective_message.reply_text(
            f"✅ تم إنشاء النسخة الاحتياطية ({dest.stat().st_size // 1024} KB) وإرسالها.")
    except Exception:
        log.exception("backup failed")
        await update.effective_message.reply_text("❌ فشل إنشاء النسخة الاحتياطية. راجع السجل.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    global conflict_detected
    err = context.error
    # --- Double-instance conflict (Telegram enforces one getUpdates per token).
    #     Instead of spamming tracebacks forever, announce it ONCE with the exact
    #     remedy and shut down gracefully (post_shutdown still runs).
    if isinstance(err, Conflict):
        if not conflict_detected:
            conflict_detected = True
            log.critical("Telegram Conflict: another bot instance is polling with this token")
            print()
            print("⛔ " + "=" * 58)
            print("⛔ يوجد نسخة أخرى من البوت تعمل الآن بنفس BOT_TOKEN.")
            print("   تلغرام يسمح بنسخة واحدة فقط تستقبل التحديثات (getUpdates).")
            print("   ➜ أوقف النسخة الأخرى أولاً (على هذا الجهاز أو على الجهاز/السيرفر الآخر):")
            print("      • ابحث عن العملية:  ps aux | grep main.py")
            print("      • أوقفها:           kill <PID>")
            print("   ➜ ثم أعد التشغيل:    ./INSTALL_AND_RUN.sh")
            print("⛔ " + "=" * 58)
            try:
                context.application.stop_running()  # documented graceful stop
            except Exception:
                pass
        return
    log.error("Unhandled application error", exc_info=err)
    try:
        if hasattr(update, "effective_message") and update.effective_message:
            await update.effective_message.reply_text("⚠️ حدث خطأ غير متوقع. أعد المحاولة.")
    except Exception:
        pass
