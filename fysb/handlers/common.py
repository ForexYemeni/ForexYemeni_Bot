"""Common handlers — render(), start, /admin, error handler, /backup.

CRITICAL (button-fix preserved from the working V5 build):
  * every callback is answered IMMEDIATELY (await q.answer()) in the dispatcher
  * pages render as FRESH messages (reply_text) — never edit_message_text
  * every action is logged and wrapped; failures reply with a visible message
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


def safe(s) -> str:
    return html.escape(str(s if s is not None else ""))


async def render(q, text: str, markup=None, bot=None) -> None:
    """Send callback pages as fresh Telegram messages (the proven fix)."""
    if q.message is not None:
        await q.message.reply_text(
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        log.info("PAGE RENDERED | user=%s | method=new_message", q.from_user.id)
        return
    # Fallback: original message deleted/too old — deliver to the chat directly.
    chat = getattr(q, "message_chat", None)
    if chat is None or bot is None:
        raise RuntimeError("callback message is no longer available")
    await bot.send_message(chat.id, text, reply_markup=markup,
                           parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    log.info("PAGE RENDERED | user=%s | method=chat_fallback", q.from_user.id)


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
        await update.effective_message.reply_text(
            f"👑 <b>{config.BRAND}</b>\n"
            f"<b>SUPER ADMIN COMMAND CENTER</b>\n\n"
            f"مرحباً بك في مركز التحكم الرئيسي — جميع الوحدات متاحة عبر الأزرار.",
            parse_mode=ParseMode.HTML, reply_markup=kb.admin_menu())
        audit_log.log(uid, "admin_panel_open")
        return
    from fysb.theme import header
    await update.effective_message.reply_text(
        header("منصة التداول", "اختر وجهتك من لوحة الأزرار التفاعلية") +
        "\n\n<b>الوحدات المتاحة:</b>\n"
        "📊 لوحة الأداء · 📡 الإشارات المباشرة\n"
        "💎 الباقات · 🧪 التداول التجريبي\n"
        "🎧 الدعم الفني · ⚙️ التفضيلات",
        parse_mode=ParseMode.HTML, reply_markup=kb.user_menu())


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        audit_log.log(uid, "security:admin_command_denied")
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة المخولة فقط.")
        return
    await update.effective_message.reply_text(
        f"👑 <b>{config.BRAND} | SUPER ADMIN</b>\nاختر وحدة الإدارة من الأزرار:",
        parse_mode=ParseMode.HTML, reply_markup=kb.admin_menu())


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
