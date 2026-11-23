"""Common handlers — render(), start, /admin, error handler, /backup.

CRITICAL (v6.8.4 ROYAL RESTORE — owner demand 2026-11-23, «هذه النسخة كانت
تعمل فيها الأزرار بشكل حقيقي وفاخر جداً»):
  * the render contract is EXACTLY v6.8.0 again: every callback page is a
    FRESH message (reply_text) right under the tapped panel — Telegram moves
    nothing, hides nothing, edits nothing; the page ALWAYS appears where the
    owner is looking. This is the last build whose buttons worked flawlessly
    on the owner's hosting, and it is now the permanent contract again.
  * every callback is still answered IMMEDIATELY (dispatcher: q.answer())
  * fallback for >48h InaccessibleMessage panels uses msg.chat (v6.8.2
    knowledge) — still a fresh page, never an edit, never a dead tap.
"""
import html
import time

BOOT_TS = time.time()   # v6.8.3: /ping reports uptime

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
# Legacy panel bookkeeping (v6.8.2–v6.8.3): kept as inert bookkeeping so all
# call sites and tests stay stable — the v6.8.4 render NEVER consults it and
# NEVER edits in place. One fresh page per tap, like v6.8.0. Always.
# ---------------------------------------------------------------------------
_last_panel: dict[int, int] = {}


def register_panel(chat_id, message_id) -> None:
    """Inert since v6.8.4 (kept for call-site/test compatibility)."""
    try:
        if chat_id is not None and message_id:
            _last_panel[int(chat_id)] = int(message_id)
    except (TypeError, ValueError):
        pass


def safe(s) -> str:
    return html.escape(str(s if s is not None else ""))


async def render(q, text: str, markup=None, bot=None) -> None:
    """Deliver callback pages as FRESH messages — the proven v6.8.0 fix.

    Restored 100% by owner demand (v6.8.4 ROYAL RESTORE): reply_text under
    the tapped panel, every tap, no exceptions. Inaccessible >48h panels
    (no reply_text) deliver via bot.send_message to msg.chat — still a
    fresh page, never an edit, never silent.
    """
    t0 = time.monotonic()
    # v6.8.4 ROYAL RESTORE — the EXACT v6.8.0 button experience, restored 100%.
    # EVERY tap delivers a FRESH message right under the tapped panel
    # (q.message.reply_text). Nothing is ever edited in place, nothing is
    # ever deleted, nothing can silently mutate high up in the chat history —
    # the page ALWAYS visibly appears where the user is looking. That is
    # precisely why v6.8.0 «كانت تعمل بشكل حقيقي وفاخر جداً».
    if q.message is not None and hasattr(q.message, "reply_text"):
        await q.message.reply_text(
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        log.info("PAGE RENDERED | user=%s | method=new_message", q.from_user.id)
        return
    # Fallback (hardened, never an edit): panels older than 48h arrive as
    # InaccessibleMessage with no reply_text — deliver a fresh page through
    # bot.send_message to msg.chat. Still a fresh visible page, never silent.
    chat = getattr(q.message, "chat", None) if q.message is not None \
        else getattr(q, "message_chat", None)
    if chat is None or bot is None:
        raise RuntimeError("callback message is no longer available")
    await bot.send_message(chat.id, text, reply_markup=markup,
                           parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    log.info("PAGE RENDERED | user=%s | method=chat_fallback | %.0fms",
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
        await update.effective_message.reply_text(
            f"👑 <b>{config.BRAND}</b>\n"
            f"<b>SUPER ADMIN COMMAND CENTER</b>\n\n"
            f"مرحباً بك في مركز التحكم الرئيسي — جميع الوحدات متاحة عبر الأزرار.",
            parse_mode=ParseMode.HTML, reply_markup=kb.admin_menu())
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


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v6.8.3 — the 5-second remote diagnosis.

    Proves INSIDE Telegram, with zero log access:
      * which exact build the container runs (old build => no /ping at all,
        because the handler does not exist in it — absence is the answer)
      * which engine is live (SQLite vs PostgreSQL/Supabase) + round-trip ms
      * how long the process has been up (a tiny uptime => the bot just
        restart-looped; a huge uptime => the old process never restarted)
    No secrets: engine type, latency and version only.
    """
    uid = update.effective_user.id
    t0 = time.monotonic()
    ok, _integrity = db.healthy()
    db_ms = (time.monotonic() - t0) * 1000
    engine = "PostgreSQL (Supabase)" if config.USE_PG else "SQLite محلية"
    up = max(0, int(time.time() - BOOT_TS))
    up_txt = f"{up // 3600} ساعة {up % 3600 // 60} د" if up >= 3600 else f"{up // 60} دقيقة {up % 60} ث"
    db_state = f"✅ متصلة ({db_ms:.0f}ms)" if ok else f"⛔ لا تستجيب ({db_ms:.0f}ms)"
    await update.effective_message.reply_text(
        f"🩺 <b>فحص سريع — {config.BRAND}</b>\n"
        f"<blockquote>◆ الإصدار: <b>v{config.VERSION}</b>\n"
        f"◆ المحرك: {engine}\n"
        f"◆ قاعدة البيانات: {db_state}\n"
        f"◆ مدة التشغيل: {up_txt}</blockquote>\n"
        "✅ معالج الأزرار مفعّل في هذه النسخة.\n"
        "<i>إذا لم يرد هذا الأمر أصلاً، فالنسخة العاملة على الاستضافة قديمة — أعد رفع الملفات الجديدة وأعد التشغيل.</i>",
        parse_mode=ParseMode.HTML)
    audit_log.log(uid, "ping")


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
