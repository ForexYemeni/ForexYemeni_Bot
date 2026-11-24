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
import asyncio
import html
import time

BOOT_TS = time.time()   # v6.8.3: /ping reports uptime

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict
from telegram.ext import ContextTypes

from fysb import afn, config, db
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


async def _send_guarded(send_fn, text: str, markup=None):
    """Bulletproof Telegram sender: tries full HTML + styled buttons first,
    then automatically degrades (strip Bot API 9.4 styles -> repaired HTML ->
    plain text) on BadRequest so NO button press can EVER die silently."""
    from fysb.ui import htmlguard
    try:
        return await send_fn(
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as first_err:
        log.warning("render BadRequest (%s) -> retrying with style-stripped fallback", first_err)
        plain_mk = kb.strip_button_styles(markup)
        for body, pmode in (
            (text, ParseMode.HTML),
            (htmlguard.truncate_safe(text, 3900), ParseMode.HTML),
            (htmlguard.plain_fallback(text)[:3900], None),
        ):
            try:
                kw = {"text": body, "reply_markup": plain_mk, "disable_web_page_preview": True}
                if pmode is not None:
                    kw["parse_mode"] = pmode
                return await send_fn(**kw)
            except BadRequest:
                continue
        raise first_err


async def render(q, text: str, markup=None, bot=None, force_new: bool = False):
    """Deliver callback pages reliably (v6.14.1 — Proven Fresh Page by default,
    optional In-Place Edit toggle in Admin Settings, + automatic BadRequest
    fallback stripping button styles / repairing HTML so buttons NEVER fail).

    Returns the delivered/edited Message object so callers (like u:signalview)
    can pin message_id/chat_id for live tracker edits.
    """
    t0 = time.monotonic()
    edit_mode = (not force_new) and (await afn.run(db.get_setting, "ui_edit_in_place", "0") == "1")

    if edit_mode and q.message is not None and hasattr(q.message, "edit_text"):
        try:
            msg = await q.message.edit_text(
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            log.info("PAGE RENDERED | user=%s | method=edit_in_place | %.0fms",
                     q.from_user.id, (time.monotonic() - t0) * 1000)
            return msg if msg is not None else q.message
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                log.info("PAGE RENDERED | user=%s | method=not_modified | %.0fms",
                         q.from_user.id, (time.monotonic() - t0) * 1000)
                return q.message
            log.debug("edit_text fallback (%s) -> sending fresh page", e)
        except Exception as e:
            log.debug("edit_text fallback (%s) -> sending fresh page", type(e).__name__)

    if q.message is not None and hasattr(q.message, "reply_text"):
        msg = await _send_guarded(q.message.reply_text, text, markup)
        log.info("PAGE RENDERED | user=%s | method=new_message | %.0fms",
                 q.from_user.id, (time.monotonic() - t0) * 1000)
        return msg

    chat = getattr(q.message, "chat", None) if q.message is not None \
        else getattr(q, "message_chat", None)
    if chat is None or bot is None:
        raise RuntimeError("callback message is no longer available")

    async def _chat_send(**kw):
        t = kw.pop("text")
        return await bot.send_message(chat.id, t, **kw)

    msg = await _send_guarded(_chat_send, text, markup)
    log.info("PAGE RENDERED | user=%s | method=chat_fallback | %.0fms",
             q.from_user.id, (time.monotonic() - t0) * 1000)
    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    uid = tg_user.id
    try:
        await afn.run(users_service.ensure_user, tg_user)
    except PermissionError:
        await update.effective_message.reply_text(
            "⛔ التسجيل مغلق مؤقتاً. تواصل مع الدعم إذا كان لديك حساب قائم.")
        return
    # ensure_user already updated last_seen — no redundant touch() round trip.
    # --- Royal referral capture (first link wins, server-side only) ---------
    # /start ref<CODE> — e.g. https://t.me/<bot>?start=refROYAL
    payload = (context.args or [""])[0] if context.args else ""
    if isinstance(payload, str) and payload.lower().startswith("ref") and len(payload) > 3 \
            and uid != config.ADMIN_ID:
        try:
            from fysb.services import referrals
            cap = await afn.run(referrals.capture, payload[3:], uid)
            if cap.get("captured"):
                await afn.run(audit_log.log, uid, f"referral_captured:{payload[3:].upper()}")
        except Exception:
            log.exception("referral capture failed")
    if uid == config.ADMIN_ID:
        await _send_guarded(
            update.effective_message.reply_text,
            f"👑 <b>{config.BRAND}</b>\n"
            f"<b>SUPER ADMIN COMMAND CENTER</b>\n\n"
            f"مرحباً بك في مركز التحكم الرئيسي — جميع الوحدات متاحة عبر الأزرار.",
            kb.admin_menu(),
        )
        await afn.run(audit_log.log, uid, "admin_panel_open")
        return
    from fysb.services import partners as partners_svc
    active_p = await afn.run(partners_svc.get_partner_by_user, uid)
    if active_p and active_p.get("enabled"):
        # v6.21.2 Strict Partner-Only Mode: while registered & active as a
        # Channel Partner, the regular User account interface is closed and
        # /start opens ONLY the Partner Command Panel until Admin disables or
        # removes the partner.
        from fysb.handlers import admin_partners
        await admin_partners.render_partner_start(update, active_p)
        return
    from fysb.handlers.user_pages import build_user_home_card
    home_text = await build_user_home_card(uid, tg_user.full_name or tg_user.username or "")
    await _send_guarded(
        update.effective_message.reply_text,
        home_text,
        kb.user_menu(),
    )


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
    ok, _integrity = await afn.run(db.healthy)
    db_ms = (time.monotonic() - t0) * 1000
    engine = "PostgreSQL (Supabase)" if config.USE_PG else "SQLite محلية"
    up = max(0, int(time.time() - BOOT_TS))
    up_txt = f"{up // 3600} ساعة {up % 3600 // 60} د" if up >= 3600 else f"{up // 60} دقيقة {up % 60} ث"
    db_state = f"✅ متصلة ({db_ms:.0f}ms)" if ok else f"⛔ لا تستجيب ({db_ms:.0f}ms)"
    import telegram as _tg
    ui_mode = "تعديل مكاني" if (await afn.run(db.get_setting, "ui_edit_in_place", "0")) == "1" else "رسالة جديدة (فورية)"
    color_state = "✅ مفعّلة بالكامل" if kb._HAS_STYLE else "⚠️ تحتاج تحديث python-telegram-bot>=22.7"
    await update.effective_message.reply_text(
        f"🩺 <b>فحص سريع — {config.BRAND}</b>\n"
        f"<blockquote>◆ الإصدار: <b>v{config.VERSION}</b>\n"
        f"◆ المحرك: {engine}\n"
        f"◆ قاعدة البيانات: {db_state}\n"
        f"◆ وضع الأزرار: {ui_mode} · الألوان الفاخرة: {color_state}\n"
        f"◆ مكتبة تلغرام: PTB v{getattr(_tg, '__version__', '?')} · مدة التشغيل: {up_txt}</blockquote>\n"
        "✅ معالج الأزرار مفعّل في هذه النسخة.\n"
        "<i>إذا لم يرد هذا الأمر أصلاً، فالنسخة العاملة على الاستضافة قديمة — أعد رفع الملفات الجديدة وأعد التشغيل.</i>",
        parse_mode=ParseMode.HTML)
    await afn.run(audit_log.log, uid, "ping")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v6.16.0 — التحديث الذاتي الفاخر من داخل تلغرام (الإدارة فقط).

    الأوضاع المدعومة:
      * /update              ← التحديث لأحدث إصدار منشور على GitHub
      * /update v6.14.1      ← تثبيت إصدار محدد (ترقية أو رجوع لإصدار سابق أو إعادة تثبيت)
      * /update list         ← عرض أحدث الإصدارات بأزرار تثبيت وتراجع فوري
      * /update rollback     ← تراجع فوري لآخر نسخة محفوظة محلياً (حتى بلا إنترنت)
    """
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        await afn.run(audit_log.log, uid, "security:update_command_denied")
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة المخولة فقط.")
        return
    import updater
    args = list(getattr(context, "args", None) or [])
    arg = (args[0].strip() if args else "")
    arg_l = arg.lower()

    # --- 1) List available versions with 1-tap buttons -----------------------
    if arg_l in ("list", "versions", "ls", "الإصدارات", "اصدارات"):
        msg = await update.effective_message.reply_text(
            "🔎 <b>جارٍ جلب قائمة الإصدارات من GitHub...</b>",
            parse_mode=ParseMode.HTML)
        releases = await asyncio.to_thread(updater.list_releases, 8)
        snaps = await asyncio.to_thread(updater.list_rollback_snapshots)
        lines = [
            "🔄 <b><u>إدارة إصدارات البوت</u></b>",
            f"<blockquote>◆ الإصدار الحالي: <b>v{safe(config.VERSION)}</b>",
        ]
        if snaps:
            lines.append(f"◆ نسخة تراجع محلية جاهزة: <b>v{safe(updater.snapshot_version(snaps[0]))}</b>")
        lines.append("</blockquote>")
        rows_kb = []
        if releases:
            lines.append("<b>الإصدارات المتاحة على GitHub (اضغط للتثبيت أو الرجوع):</b>")
            for rel in releases[:8]:
                tag = rel["tag"]
                ver = updater.display_tag(tag)
                is_cur = updater.parse_version(tag) == updater.parse_version(config.VERSION)
                mark = "✅ الحالي" if is_cur else ("⬆️ أحدث" if updater.is_newer(tag, config.VERSION) else "⬇️ سابق")
                lines.append(f"  • <code>/update {safe(tag)}</code> — {mark}")
                rows_kb.append([kb.btn(
                    f"{'🔄 إعادة تثبيت' if is_cur else ('⬆️ ترقية' if updater.is_newer(tag, config.VERSION) else '⬇️ رجوع')} v{ver}",
                    f"a:updver:{tag}",
                    style="success" if updater.is_newer(tag, config.VERSION) else ("primary" if not is_cur else ""),
                )])
        else:
            lines.append("⚠️ تعذّر جلب قائمة GitHub حالياً — يمكنك كتابة <code>/update v6.14.1</code> مباشرة أو استخدام التراجع المحلي.")
        if snaps:
            rows_kb.append([kb.btn(
                f"↩️ تراجع فوري للنسخة السابقة (v{updater.snapshot_version(snaps[0])})",
                "a:updrollback",
                style="danger",
            )])
        lines.append("\n<i>مثال سريع للرجوع لأي إصدار: <code>/update v6.14.1</code></i>")
        await _send_guarded(msg.edit_text, "\n".join(lines), kb.InlineKeyboardMarkup(rows_kb) if rows_kb else None)
        return

    # --- 2) Instant offline rollback to previous local snapshot --------------
    if arg_l in ("rollback", "back", "prev", "تراجع", "رجوع"):
        msg = await update.effective_message.reply_text(
            "↩️ <b>جارٍ استعادة النسخة السابقة المحفوظة محلياً...</b>\n"
            f"◆ الإصدار الحالي: v{config.VERSION}",
            parse_mode=ParseMode.HTML)
        res = await asyncio.to_thread(updater.rollback_update)
        if res.get("status") == "rolled_back":
            await afn.run(audit_log.log, uid,
                          f"self_update_rollback:{res.get('local')}->{res.get('restored_version')}")
            await msg.edit_text(
                f"✅ <b>تم التراجع بنجاح إلى الإصدار v{safe(res.get('restored_version'))}.</b>\n"
                f"◆ الملفات المستعادة: {res.get('files', 0)}\n"
                "♻️ إعادة تشغيل البوت الآن — جاهز خلال لحظات.\n"
                "<i>بعد الإقلاع أرسل /ping للتحقق من الإصدار.</i>",
                parse_mode=ParseMode.HTML)
            await asyncio.sleep(1.5)
            updater.restart()
            return
        await afn.run(audit_log.log, uid, f"self_update_rollback:error:{str(res.get('detail', ''))[:80]}")
        await msg.edit_text(
            "⚠️ <b>تعذّر التراجع المحلي.</b>\n"
            f"◆ السبب: {safe(res.get('detail') or 'غير معروف')}\n"
            "<i>يمكنك الرجوع لأي إصدار من GitHub مباشرة: <code>/update v6.14.1</code></i>",
            parse_mode=ParseMode.HTML)
        return

    # --- 3) Pin / install a specific version (upgrade, downgrade, reinstall) -
    if arg:
        norm = updater.normalize_tag(arg)
        if not norm:
            await update.effective_message.reply_text(
                "⚠️ <b>صيغة أمر التحديث غير معروفة.</b>\n"
                "<blockquote>◆ أحدث إصدار: <code>/update</code>\n"
                "◆ إصدار محدد: <code>/update v6.14.1</code>\n"
                "◆ قائمة الإصدارات: <code>/update list</code>\n"
                "◆ تراجع فوري: <code>/update rollback</code></blockquote>",
                parse_mode=ParseMode.HTML)
            return
        msg = await update.effective_message.reply_text(
            f"🔎 <b>جارٍ جلب وتثبيت الإصدار {safe(norm)}...</b>\n"
            f"◆ الإصدار الحالي: v{config.VERSION}\n"
            "<i>سيتم حفظ نسخة تراجع تلقائية قبل التثبيت دون المساس بـ .env أو قاعدة البيانات.</i>",
            parse_mode=ParseMode.HTML)
        res = await asyncio.to_thread(updater.self_update, None, norm, True)
    else:
        # --- 4) Default: upgrade to latest GitHub release --------------------
        msg = await update.effective_message.reply_text(
            "🔎 <b>جارٍ فحص الإصدار الأحدث على GitHub...</b>\n"
            f"◆ الإصدار الحالي: v{config.VERSION}",
            parse_mode=ParseMode.HTML)
        res = await asyncio.to_thread(updater.self_update)

    status = res.get("status")
    if status == "updated":
        direction = res.get("direction") or "upgrade"
        dir_label = {
            "downgrade": "⬇️ تم الرجوع وتثبيت الإصدار",
            "reinstall": "🔄 تمت إعادة تثبيت الإصدار",
        }.get(direction, "✅ تم تنزيل وتثبيت الإصدار")
        sha_line = "◆ بصمة SHA-256: ✅ موثّقة\n" if res.get("sha256_verified") else ""
        bak_line = (f"◆ نسخة تراجع محفوظة: <code>{safe(res.get('rollback_backup'))}</code>\n"
                    if res.get("rollback_backup") else "")
        await afn.run(audit_log.log, uid,
                      f"self_update:{direction}:{res.get('local')}->{res.get('remote')}")
        await msg.edit_text(
            f"<b>{dir_label} v{safe(updater.display_tag(res.get('remote')))} بنجاح.</b>\n"
            f"◆ الملفات المثبتة: {res.get('files', 0)}\n"
            f"{sha_line}{bak_line}"
            "♻️ إعادة تشغيل البوت الآن — جاهز خلال لحظات.\n"
            "<i>بعد الإقلاع أرسل /ping للتحقق من الإصدار، وللتراجع أرسل /update rollback.</i>",
            parse_mode=ParseMode.HTML)
        await asyncio.sleep(1.5)     # الوقت اللازم لتوصيل الرد قبل استبدال العملية
        updater.restart()            # استبدال فوري للعملية — لا يعود
        return
    if status == "no_update":
        await afn.run(audit_log.log, uid, "self_update:no_update")
        await msg.edit_text(
            "🟢 <b>أنت على أحدث إصدار بالفعل.</b>\n"
            f"◆ المحلي: v{safe(res.get('local'))}\n"
            f"◆ الأحدث على GitHub: v{safe(updater.display_tag(res.get('remote')))}\n"
            "<i>لعرض كل الإصدارات أو الرجوع لإصدار سابق: <code>/update list</code> أو <code>/update v6.14.1</code></i>",
            parse_mode=ParseMode.HTML)
        return
    await afn.run(audit_log.log, uid, f"self_update:error:{str(res.get('detail', ''))[:80]}")
    await msg.edit_text(
        "⚠️ <b>تعذّر التحديث الآن — الإصدار الحالي ما زال يعمل بشكل طبيعي.</b>\n"
        f"◆ الإصدار الحالي: v{safe(res.get('local'))}\n"
        f"◆ السبب: {safe(res.get('detail') or 'غير معروف')}\n"
        "<i>جرّب <code>/update list</code> لعرض الإصدارات أو <code>/update rollback</code> للتراجع المحلي.</i>",
        parse_mode=ParseMode.HTML)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        await afn.run(audit_log.log, uid, "security:admin_command_denied")
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة المخولة فقط.")
        return
    sent = await _send_guarded(
        update.effective_message.reply_text,
        f"👑 <b>{config.BRAND} | SUPER ADMIN</b>\nاختر القسم من لوحة التحكم الرئيسية:",
        kb.admin_menu(),
    )
    if sent is not None:
        register_panel(getattr(sent, "chat_id", None), getattr(sent, "message_id", None))


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != config.ADMIN_ID:
        await update.effective_message.reply_text("⛔ هذا الأمر للإدارة فقط.")
        return
    from fysb.services import backup as backup_service
    try:
        dest = await afn.run(backup_service.quick_db_snapshot, "admin")
        if dest is None:
            await update.effective_message.reply_text(
                "⏳ قاعدة البيانات مشغولة حالياً — تعذر أخذ نسخة لحظية الآن. حاول بعد قليل.")
            return
        with open(dest, "rb") as f:
            await update.effective_message.reply_document(f, filename=dest.name)
        await afn.run(audit_log.log, uid, f"database_backup:{dest.name}")
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
