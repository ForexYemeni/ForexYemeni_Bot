"""Admin system sections: Reports, Notifications, Broadcast, Support, Security,
Backup & Restore, Settings, Audit logs, System health."""
import asyncio
import json

from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.engines import market_data
from fysb.handlers.common import render, safe
from fysb.handlers.user_pages import _safe_int
from fysb.services import analytics as analytics_service
from fysb.services import audit as audit_log
from fysb.services import backup as backup_service
from fysb.services import broadcast as broadcast_service
from fysb.services import support as support_service
from fysb.theme import ICON, esc, human_dt
from fysb.ui import keyboards as kb

BROADCAST_AUDIENCES = [("all", "👥 كل المستخدمين"), ("free", "🆓 المشتركين المجانيين"),
                       ("paid", "💎 المشتركين المدفوعين"), ("notified", "🔔 المتلقون فقط")]


# --- Reports ------------------------------------------------------------------------
async def admin_reports(q, uid, context, bot):
    rows_kb = [
        [kb.btn("👥 المستخدمون", "a:repusers"), kb.btn("📡 الإشارات", "a:repsignals")],
        [kb.btn("💳 المدفوعات", "a:reppayments"), kb.btn("💎 الاشتراكات", "a:repsubs")],
        [kb.btn("🧠 دورات المحرك", "a:repengine")],
    ]
    body = "📈 <b>مركز التقارير</b>\nكل الأرقام مستخرجة فعلياً من قاعدة البيانات — اختر تقريراً:"
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def _report(q, uid, context, bot, fn, title):
    body = await afn.run(fn)
    await render(q, body, kb.admin_nav(), bot)


async def admin_rep_users(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.users_report, "users")


async def admin_rep_signals(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.signals_report, "signals")


async def admin_rep_payments(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.payments_report, "payments")


async def admin_rep_subs(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.subscriptions_report, "subs")


async def admin_rep_engine(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.engine_report, "engine")


# --- Analytics governance (per-plan daily quota) --------------------------------------
async def admin_analytics(q, uid, context, bot):
    enabled = await afn.run(analytics_service.analytics_enabled)
    plans, today, total = await afn.run(_analytics_page_rows)
    body = [f"🔬 <b><u>إدارة التحليلات</u></b>",
            f"▎القسم للمستخدمين: <b>{'🟢 مفعّل' if enabled else '⚪ معطل'}</b>",
            f"▎تحليلات اليوم: <b>{today}</b> · الإجمالي: <b>{total}</b>", "",
            "▎اضبط <b>حصة التحليلات اليومية لكل باقة</b> (0 = معطلة للباقة):"]
    rows_kb = []
    for p in plans:
        quota = p["max_analyses_per_day"] or 0
        body.append(f"▎{ICON['diamond']} <b>{esc(p['title'])} ({esc(p['code'])})</b> · حصة التحليلات: <b>{quota}/يوم</b>")
        rows_kb.append([kb.btn("➖", f"a:anladj:{p['code']}:-1"),
                        kb.btn(f"🔬 {quota}/يوم", "a:plannoop"),
                        kb.btn("➕", f"a:anladj:{p['code']}:1")])
    rows_kb.append([kb.btn(f"{'⏹ تعطيل القسم عالمياً' if enabled else '▶️ تفعيل القسم عالمياً'}", "a:anlglobal")])
    rows_kb.append([kb.btn("📈 تقرير التحليلات", "a:repanal")])
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_anl_adjust(q, uid, context, bot, code: str, delta: float):
    r = await afn.run(_anal_quota_row, code)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    val = max(0, min(500, int((r["max_analyses_per_day"] or 0) + delta)))
    await afn.run(_anal_quota_set, code, val)
    await afn.run(audit_log.log, uid, f"analytics_quota:{code}:{val}")
    await admin_analytics(q, uid, context, bot)


async def admin_anl_global(q, uid, context, bot):
    val = "0" if await afn.run(analytics_service.analytics_enabled) else "1"
    await afn.run(db.set_setting, "analytics_enabled", val)
    await afn.run(audit_log.log, uid, f"analytics_global:{val}")
    await admin_analytics(q, uid, context, bot)


async def admin_rep_anal(q, uid, context, bot):
    from fysb.services import reports
    await _report(q, uid, context, bot, reports.analyses_report, "analyses")


# --- Notifications -------------------------------------------------------------------
async def admin_notifications(q, uid, context, bot):
    push = await afn.run(db.get_setting, "signal_push_enabled", "1") == "1"
    remind_days = await afn.run(db.get_setting_int, "expiry_remind_days", 3)
    unread, recent = await afn.run(_notification_rows)
    lines = ["🔔 <b>مركز الإشعارات</b>",
             f"📡 دفع الإشارات الجديدة: <b>{'مفعل' if push else 'متوقف'}</b>",
             f"⏳ تذكير انتهاء الاشتراك قبل: <b>{remind_days}</b> يوم",
             f"📬 أحداث مسجلة غير مقروءة: <b>{unread}</b>", "", "<b>أحدث الأحداث:</b>"]
    for r in recent:
        lines.append(f"  {esc(r['kind'])} → user {r['user_id']} · {human_dt(r['created_at'])}")
    if not recent:
        lines.append("  لا أحداث بعد.")
    rows_kb = [
        [kb.btn(f"📡 دفع الإشارات: {'إيقاف' if push else 'تفعيل'}", "a:togglepush")],
        [kb.btn("➖ تذكير", f"a:reminddays:{remind_days - 1}"), kb.btn(f"⏳ {remind_days} يوم", "a:plannoop"), kb.btn("➕ تذكير", f"a:reminddays:{remind_days + 1}")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_toggle_push(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "signal_push_enabled", "1") == "1" else "1"
    await afn.run(db.set_setting, "signal_push_enabled", val)
    await afn.run(audit_log.log, uid, f"signal_push:{val}")
    await admin_notifications(q, uid, context, bot)


async def admin_remind_days(q, uid, context, bot, days: int):
    days = max(1, min(14, days))
    await afn.run(db.set_setting, "expiry_remind_days", days)
    await admin_notifications(q, uid, context, bot)


# --- Broadcast ------------------------------------------------------------------------
async def admin_broadcast(q, uid, context, bot):
    recent, active = await afn.run(_broadcast_page_rows)
    lines = ["📣 <b>مركز الإعلانات</b>",
             f"🚦 حملات نشطة/معلقة: <b>{active}</b>", "", "<b>الأحدث:</b>"]
    rows_kb = []
    for r in recent:
        icon = {"DONE": "✅", "RUNNING": "🏃", "ABORTED": "🚫", "PENDING": "🟡", "FAILED": "❌"}.get(r["status"], "🔵")
        lines.append(f"  {icon} #{r['id']} {esc(r['audience'])} · أُرسل {r['sent']}/{r['total']} · فشل {r['failed']} · {esc(r['status'])}")
        if r["status"] in ("PENDING", "RUNNING"):
            rows_kb.append([kb.btn(f"⏹ إيقاف #{r['id']}", f"a:bcabort:{r['id']}")])
    if not recent:
        lines.append("  لا توجد حملات بعد.")
    rows_kb += [[kb.btn(t, f"a:bcaud:{code}")] for code, t in BROADCAST_AUDIENCES[:2]]
    rows_kb += [[kb.btn(t, f"a:bcaud:{code}")] for code, t in BROADCAST_AUDIENCES[2:]]
    rows_kb.append([kb.btn("💎 جمهور باقة محددة", "a:bcplan")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_bc_audience(q, uid, context, bot, audience: str):
    context.user_data["broadcast_audience"] = audience
    context.user_data["broadcast_plan"] = ""
    context.user_data["awaiting_broadcast_text"] = True
    await render(q, "✍️ <b>أرسل نص الإعلان الآن</b> — نص عادي فاخر (بدون وسوم HTML — سيُغلف تلقائياً بالإطار الملكي).\n"
                    "سيُعرض للمعاينة بشكله النهائي مع عدد المستلمين قبل الإرسال.",
                 kb.admin_nav(), bot)


async def admin_bc_plan(q, uid, context, bot):
    plans = await afn.run(_plan_code_title_rows)
    rows_kb = [[kb.btn(f"💎 {esc(p['title'])}", f"a:bcpick:{p['code']}")] for p in plans]
    await render(q, "💎 اختر الباقة المستهدفة للإعلان:", kb.admin_nav(rows_kb), bot)


async def admin_bc_pick(q, uid, context, bot, code: str):
    context.user_data["broadcast_audience"] = "plan"
    context.user_data["broadcast_plan"] = code
    context.user_data["awaiting_broadcast_text"] = True
    await render(q, f"✍️ أرسل نص الإعلان لباقة <b>{esc(code)}</b> — نص عادي (يُغلف بالإطار الملكي تلقائياً):", kb.admin_nav(), bot)


async def admin_bc_go(q, uid, context, bot, bid: int):
    b = await afn.run(broadcast_service.get, bid)
    if not b:
        await render(q, "الحملة غير موجودة.", kb.admin_nav(), bot)
        return
    if b["status"] not in ("PENDING",):
        await render(q, f"⛔ الحملة بحالة {esc(b['status'])} — لا تكرار للإرسال.", kb.admin_nav(), bot)
        return
    targets = await afn.run(broadcast_service.audience_ids, b["audience"], b["plan_code"])
    if not targets:
        await render(q, "لا يوجد مستلمون مطابقون للجمهور المحدد.", kb.admin_nav(), bot)
        return
    await afn.run(audit_log.log, uid, f"broadcast_start:{bid}:{len(targets)}")
    rate = await afn.run(db.get_setting_int, "broadcast_rate_per_sec", 20)
    asyncio.create_task(_run_broadcast(bid, bot, targets))
    await render(q, f"🚀 <b>بدأ الإرسال #{bid}</b>\nالمستلمون: <b>{len(targets)}</b>\nمعدل الإرسال: {rate} رسالة/ثانية\nتابع العدّاد من قسم الإعلانات.",
                 kb.admin_nav(), bot)


async def _run_broadcast(bid: int, bot, targets: list[int]):
    try:
        await broadcast_service.run_broadcast(bid, bot, targets)
    except Exception:
        from fysb.config import log
        log.exception("broadcast %s failed", bid)
        await afn.run(broadcast_service.mark_status, bid, "FAILED")


async def admin_bc_abort(q, uid, context, bot, bid: int):
    await afn.run(broadcast_service.abort, bid)
    await afn.run(audit_log.log, uid, f"broadcast_abort:{bid}")
    await admin_broadcast(q, uid, context, bot)


async def admin_bc_cancel(q, uid, context, bot, bid: int):
    b = await afn.run(broadcast_service.get, bid)
    if b and b["status"] == "PENDING":
        await afn.run(broadcast_service.mark_status, bid, "ABORTED")
    await admin_broadcast(q, uid, context, bot)


# --- Support center ---------------------------------------------------------------------
async def admin_support(q, uid, context, bot):
    tickets = await afn.run(support_service.list_tickets, "OPEN")
    answered = await afn.run(support_service.list_tickets, "ANSWERED")
    cfg = await afn.run(support_service.config_row)
    lines = ["🎧 <b>مركز الدعم الفني</b>",
             f"🎫 مفتوحة: <b>{len(tickets)}</b> · بانتظار رد المستخدم: <b>{len(answered)}</b>", "",
             "<b>التذاكر المفتوحة:</b>"]
    rows_kb = []
    for t in tickets[:8]:
        lines.append(f"  #{t['id']} · user {t['user_id']} · {esc(t['subject'][:40])} · {human_dt(t['updated_at'] or t['created_at'])}")
        rows_kb.append([kb.btn(f"🎫 #{t['id']} {esc(t['subject'][:24])}", f"a:ticketview:{t['id']}")])
    if not tickets:
        lines.append("  لا توجد تذاكر مفتوحة.")
    rows_kb.append([kb.btn("✏️ إعداد وسائل التواصل", "a:supportconfig")])
    body = "\n".join(lines)
    if any(cfg.get(x) for x in ("username", "phone", "channel", "group_chat")):
        body += f"\n\n📞 القنوات الحالية: @{esc(cfg['username'] or '—')} · {esc(cfg['phone'] or '—')}"
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_support_config(q, uid, context, bot):
    cfg = await afn.run(support_service.config_row) or {}
    fields = [("username", "✉️ معرّف الدعم", f"@{cfg.get('username') or '—'}"),
              ("phone", "📞 رقم الهاتف/واتساب", cfg.get('phone') or '—'),
              ("channel", "📢 قناة البوت", cfg.get('channel') or '—'),
              ("group_chat", "👥 مجموعة المجتمع", cfg.get('group_chat') or '—')]
    rows_kb = [[kb.btn(f"{title}: {val}", f"a:supfield:{name}")] for name, title, val in fields]
    body = ["🎧 <b><u>إعداد وسائل التواصل — حقلاً حقلاً</u></b>",
            "اضغط الحقل لإرسال قيمته الجديدة. أرسل <code>-</code> لمسح قيمة حقل."]
    for _n, t, v in fields:
        body.append(f"▎{t}: <b>{esc(v.lstrip('@') if v.startswith('@') else v)}</b>")
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_support_field(q, uid, context, bot, field: str):
    if field not in ("username", "phone", "channel", "group_chat"):
        await render(q, "حقل غير معروف.", kb.admin_nav(), bot)
        return
    context.user_data["awaiting_support_field"] = field
    await render(q, f"✏️ أرسل القيمة الجديدة للحقل <b>{esc(field)}</b> — أو <code>-</code> لمسحه:", kb.admin_nav(), bot)


async def admin_ticket_view(q, uid, context, bot, tid: int):
    t = await afn.run(support_service.get_ticket, tid)
    if not t:
        await render(q, "التذكرة غير موجودة.", kb.admin_nav(), bot)
        return
    msgs = await afn.run(support_service.ticket_messages, tid)
    lines = [f"🎫 <b>تذكرة #{tid}</b> · {esc(t['subject'])} · <b>{esc(t['status'])}</b>",
             f"👤 المستخدم: <code>{t['user_id']}</code>", ""]
    for m in msgs[-8:]:
        who = "🧑 المستخدم" if m["sender_role"] == "user" else "🎧 الدعم"
        lines.append(f"{who} ({human_dt(m['created_at'])}):")
        lines.append(f"{esc(m['body'][:300])}\n")
    rows_kb = []
    if t["status"] in ("OPEN", "ANSWERED"):
        rows_kb.append([kb.btn("💬 رد على المستخدم", f"a:ticketreply:{tid}")])
    if t["status"] == "OPEN":
        rows_kb.append([kb.btn("✅ إغلاق التذكرة", f"a:ticketclose:{tid}")])
    elif t["status"] == "CLOSED":
        rows_kb.append([kb.btn("🔓 إعادة فتح", f"a:ticketreopen_admin:{tid}")])
    rows_kb.append([kb.btn("👤 ملف المستخدم", f"a:userdetail:{t['user_id']}"), kb.btn("↩️ الدعم", "a:support")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_ticket_reply(q, uid, context, bot, tid: int):
    context.user_data["awaiting_admin_reply"] = tid
    await render(q, f"💬 أرسل ردك على التذكرة #{tid} — سيصل للمستخدم مباشرة:", kb.admin_nav(), bot)


async def admin_ticket_close(q, uid, context, bot, tid: int):
    t = await afn.run(support_service.get_ticket, tid)
    await afn.run(support_service.set_status, tid, "CLOSED")
    await afn.run(audit_log.log, uid, f"ticket_closed:{tid}")
    if t:
        try:
            await bot.send_message(t["user_id"], f"✅ أُغلقت تذكرتك #{tid}. يمكنك إعادة فتحها من سجل الدعم.")
        except Exception:
            pass
    await admin_support(q, uid, context, bot)


async def admin_ticket_reopen(q, uid, context, bot, tid: int):
    await afn.run(support_service.set_status, tid, "OPEN")
    await afn.run(audit_log.log, uid, f"ticket_reopened:{tid}")
    await admin_ticket_view(q, uid, context, bot, tid)


# --- Security center ----------------------------------------------------------------------
async def admin_security(q, uid, context, bot):
    events = await afn.run(audit_log.security_events, 10)
    ok, integrity = await afn.run(db.healthy)
    maint = await afn.run(db.maintenance_on)
    bn = await afn.run(db.get_setting, "block_new_users", "0")
    lines = ["🔐 <b>مركز الأمان</b>",
             f"👑 Super Admin: <code>{masked_admin()}</code> (من ADMIN_ID في .env)",
             f"🛡 التحقق من الصلاحيات: <b>على السيرفر</b> في كل callback",
             f"🗄 سلامة قاعدة البيانات: <b>{'✅ ' + esc(integrity) if ok else '❌ ' + esc(integrity)}</b>",
             f"🚫 تسريب الأسرار: التوكن والمفاتيح لا تُطبع في السجلات ولا تُعرض في الواجهة",
             f"🔧 صيانة: <b>{'مفعلة' if maint else 'معطلة'}</b>",
             f"🆕 منع تسجيل مستخدمين جدد: <b>{'مفعل' if bn == '1' else 'معطل'}</b>",
             "", "<b>أحدث الأحداث الأمنية:</b>"]
    for e in events:
        lines.append(f"  {human_dt(e['created_at'])} · actor {e['actor']} · {esc(e['action'])[:60]}")
    if not events:
        lines.append("  لا أحداث أمنية مسجلة.")
    rows_kb = [[kb.btn("🔧 وضع الصيانة", "a:togglemaintenance"),
                kb.btn(f"🆕 منع الجدد: {'إيقاف' if bn == '1' else 'تفعيل'}", "a:toggleblocknew")]]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


def masked_admin() -> str:
    return "*" * (len(str(config.ADMIN_ID)) - 3) + str(config.ADMIN_ID)[-3:] if config.ADMIN_ID else "غير مضبوط"


async def admin_toggle_maintenance(q, uid, context, bot):
    new = "0" if await afn.run(db.maintenance_on) else "1"
    await afn.run(db.set_setting, "maintenance", new)
    await afn.run(audit_log.log, uid, f"maintenance:{new}")
    await admin_security(q, uid, context, bot)


async def admin_toggle_blocknew(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "block_new_users", "0") == "1" else "1"
    await afn.run(db.set_setting, "block_new_users", val)
    await afn.run(audit_log.log, uid, f"block_new_users:{val}")
    await admin_security(q, uid, context, bot)


# --- Backup & restore -----------------------------------------------------------------------
async def admin_backup(q, uid, context, bot):
    backups = await afn.run(backup_service.list_backups)
    ok, integrity = await afn.run(db.healthy)
    lines = ["💾 <b>النسخ الاحتياطي والاستعادة</b>",
             f"🗄 سلامة القاعدة الحالية: <b>{'✅' if ok else '❌ ' + esc(integrity)}</b>",
             f"📁 النسخ المحفوظة: <b>{len(backups)}</b>", "", "<b>أحدث النسخ:</b>"]
    rows_kb = []
    for b in backups[:6]:
        lines.append(f"  📄 {esc(b['name'])} · {b['size_kb']} KB · {b['modified']}")
        if b["name"].endswith(".db"):
            rows_kb.append([kb.btn(f"📤 إرسال {esc(b['name'][:28])}", f"a:bksend:{b['name']}")])
    rows_kb.append([kb.btn("🆕 نسخة قاعدة بيانات الآن", "a:bknow"),
                    kb.btn("📦 نسخة مشروع كاملة", "a:bkfull")])
    rows_kb.append([kb.btn("⚙️ تصدير الإعدادات (JSON)", "a:bksettings")])
    rows_kb.append([kb.btn("♻️ استعادة نسخة (أرسل ملف .db)", "a:bkrestore")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_backup_now(q, uid, context, bot):
    try:
        dest = await afn.run(backup_service.quick_db_snapshot, "admin")
        if dest is None:
            await render(q, "⏳ تعذر أخذ نسخة الآن — قاعدة البيانات مشغولة (حاول بعد قليل).",
                         kb.admin_nav(), bot)
            return
        await afn.run(audit_log.log, uid, f"backup_created:{dest.name}")
        with open(dest, "rb") as f:
            await bot.send_document(uid, f, filename=dest.name, caption=f"💾 نسخة قاعدة بيانات ({dest.stat().st_size // 1024} KB)")
        await admin_backup(q, uid, context, bot)
    except Exception as e:
        await render(q, "❌ فشل إنشاء النسخة: " + safe(e), kb.admin_nav(), bot)


async def admin_backup_full(q, uid, context, bot):
    await render(q, "📦 جارٍ تجهيز نسخة المشروع الكاملة (قد تستغرق لحظات)...", None)
    try:
        dest = await asyncio.to_thread(backup_service.full_project_archive)
        ok, detail = await afn.run(backup_service.verify_archive, dest)
        await afn.run(audit_log.log, uid, f"project_backup:{dest.name}:{ok}")
        if ok:
            with open(dest, "rb") as f:
                await bot.send_document(uid, f, filename=dest.name,
                                        caption=f"📦 نسخة المشروع الكاملة ({dest.stat().st_size // 1024} KB) — التحقق: ✅ {esc(detail)}\n"
                                                f"🔒 تشمل: الكود + قاعدة البيانات (تم استثناء .env لحماية التوكن والأسرار).")
            await admin_backup(q, uid, context, bot)
        else:
            await render(q, "❌ فشل التحقق من الأرشيف: " + safe(detail), kb.admin_nav(), bot)
    except Exception as e:
        await render(q, "❌ فشل إنشاء نسخة المشروع: " + safe(e), kb.admin_nav(), bot)


async def admin_backup_settings(q, uid, context, bot):
    dest = await afn.run(backup_service.settings_export)
    with open(dest, "rb") as f:
        await bot.send_document(uid, f, filename=dest.name, caption="⚙️ تصدير الإعدادات والفهارس (بدون أسرار)")
    await admin_backup(q, uid, context, bot)


async def admin_backup_send(q, uid, context, bot, name: str):
    from pathlib import Path
    path = config.BACKUP_DIR / name
    if not path.is_file() or ".." in name or "/" in name:
        await render(q, "الملف غير موجود.", kb.admin_nav(), bot)
        return
    with open(path, "rb") as f:
        await bot.send_document(uid, f, filename=path.name, caption=f"💾 {path.name} ({path.stat().st_size // 1024} KB)")


async def admin_backup_restore(q, uid, context, bot):
    context.user_data["awaiting_restore"] = True
    await render(q, "♻️ <b>الاستعادة</b>\nأرسل ملف <code>.db</code> هنا.\n\n"
                    "⚠️ سأأخذ نسخة أمان تلقائية أولاً، أتحقق من سلامة الملف، ثم أستبدل قاعدة البيانات.\n"
                    "لا تُحذف أي بيانات — النسخة الحالية تُحفظ دائماً قبل الاستبدال.",
                 kb.admin_nav(), bot)


async def admin_do_restore(q, uid, context, bot, file_path):
    context.user_data["awaiting_restore"] = False
    await render(q, "♻️ جارٍ التحقق من الملف والاستعادة...", None)
    ok, detail = await afn.run(backup_service.restore_from, file_path)
    await afn.run(audit_log.log, uid, f"restore:{ok}:{detail}")
    if ok:
        await render(q, f"✅ <b>تمت الاستعادة بنجاح</b>\nالتحقق: {esc(detail)}\nنسخة الأمان محفوظة في مجلد backups.",
                     kb.admin_nav(), bot)
    else:
        await render(q, f"❌ فشلت الاستعادة: {esc(detail)}\nلم يُستبدل أي ملف.", kb.admin_nav(), bot)


# --- Settings ------------------------------------------------------------------------------
async def admin_settings(q, uid, context, bot):
    pairs = [
        ("maintenance", "🔧 وضع الصيانة"),
        ("trading_mode", "⚙️ وضع التشغيل"),
        ("engine_enabled", "🧠 المحرك"),
        ("global_kill_switch", "⛔ Kill Switch"),
        ("live_enabled", "🔒 LIVE"),
        ("default_market", "🌐 السوق الافتراضي"),
        ("free_daily_signals", "🆓 حد الإشارات المجاني/يوم"),
        ("demo_start_balance", "🧪 رصيد التجريبي الابتدائي"),
        ("broadcast_rate_per_sec", "📣 معدل الإعلانات/ثانية"),
        ("signal_expiry_hours", "⏳ صلاحية الإشارة (ساعة)"),
    ]
    ui_edit = (await afn.run(db.get_setting, "ui_edit_in_place", "0")) == "1"
    lines = ["⚙️ <b>الإعدادات العامة</b>", ""]
    rows_kb = []
    for key, title in pairs:
        val = await afn.run(db.get_setting, key, "—")
        lines.append(f"{title}: <b>{esc(val)}</b>")
    lines.append(f"🖼 وضع التنقل بالواجهة: <b>{'تعديل مكاني سلس (edit_text)' if ui_edit else 'رسالة جديدة مع كل ضغطة'}</b>")
    rows_kb.append([kb.btn("🔧 تبديل الصيانة", "a:togglemaintenance", style="danger"),
                    kb.btn("🆓 تعديل الحد المجاني", "a:freeset")])
    rows_kb.append([kb.btn("🧪 تعديل رصيد التجريبي", "a:demobalset"),
                    kb.btn("⏳ تعديل صلاحية الإشارة", "a:expiryset")])
    rows_kb.append([kb.btn(
        f"🖼 التنقل: {'تبديل إلى رسالة جديدة' if ui_edit else 'تبديل إلى التعديل المكاني'}",
        "a:toggleuimode",
        style="primary",
    )])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_toggle_ui_mode(q, uid, context, bot):
    cur = (await afn.run(db.get_setting, "ui_edit_in_place", "0")) == "1"
    new_val = "0" if cur else "1"
    await afn.run(db.set_setting, "ui_edit_in_place", new_val)
    await afn.run(audit_log.log, uid, f"ui_edit_in_place:{new_val}")
    await admin_settings(q, uid, context, bot)


async def admin_free_set(q, uid, context, bot):
    context.user_data["awaiting_free_quota"] = True
    await render(q, "🆓 أرسل الرقم الجديد للحد اليومي للباقة المجانية (1-10):", kb.admin_nav(), bot)


async def admin_demo_bal_set(q, uid, context, bot):
    context.user_data["awaiting_demo_balance"] = True
    await render(q, "🧪 أرسل الرصيد الابتدائي الجديد للحسابات التجريبية (مثال: 10000):", kb.admin_nav(), bot)


async def admin_expiry_set(q, uid, context, bot):
    context.user_data["awaiting_expiry_hours"] = True
    await render(q, "⏳ أرسل صلاحية الإشارة بالساعات (مثال: 72):", kb.admin_nav(), bot)


# --- Audit log ------------------------------------------------------------------------------
async def admin_audit(q, uid, context, bot, page: int = 0):
    size = 12
    rows = await afn.run(audit_log.recent, size, page * size)
    total = await afn.run(_audit_total)
    lines = [f"🧾 <b>سجل الإدارة</b> · صفحة {page + 1} · الإجمالي {total}", ""]
    for r in rows:
        lines.append(f"  {r['created_at'][5:16]} · {r['actor']} · {esc(r['action'])[:70]}")
    rows_kb = []
    nav = []
    if page > 0:
        nav.append(kb.btn("◀️", f"a:auditpage:{page - 1}"))
    if (page + 1) * size < total:
        nav.append(kb.btn("▶️", f"a:auditpage:{page + 1}"))
    if nav:
        rows_kb.append(nav)
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


# --- System health ----------------------------------------------------------------------------
async def admin_health(q, uid, context, bot):
    import os
    import platform
    import resource
    import time as _time
    ok, integrity = await afn.run(db.healthy)
    eng = await afn.run(db.get_setting, "engine_enabled", "0")
    live = await afn.run(db.get_setting, "live_enabled", "0")
    try:
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        mem_mb = 0
    db_size = 0
    try:
        from pathlib import Path as _Path
        _dbf = _Path(config.DB_FILE)
        db_size = _dbf.stat().st_size // 1024
        wal = _dbf.with_suffix(".db-wal")
        wal_size = (wal.stat().st_size // 1024) if wal.exists() else 0
    except OSError:
        wal_size = 0
    provider = market_data.provider_status()
    from fysb.engines import crypto_ws, provider_health
    cb_snap = provider_health.health_snapshot()
    ws_info = crypto_ws.ws_stats()
    uptime = int(_time.time() - _BOOT_EPOCH)
    ws_badge = (
        f"🟢 متصل ({esc(ws_info['venue'])} · {ws_info['fresh_symbols']} عملة)"
        if ws_info["connected"]
        else ("⚪ غير متصل بعد" if ws_info["enabled"] else "⏸ معطّل")
    )
    lines = [
        "🖥 <b>حالة النظام</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🐍 Python: {platform.python_version()} · {platform.system()}",
        f"⏱ مدة التشغيل: {uptime // 3600}س {uptime % 3600 // 60}د",
        f"🧮 ذاكرة العملية: <b>{mem_mb:.1f} MB</b>",
        f"🗄 قاعدة البيانات: <b>{db_size} KB</b> (WAL: {wal_size} KB) · <b>{'✅ سليمة' if ok else '❌ ' + esc(integrity)}</b>",
        f"🌐 مزود البيانات: <b>{esc(provider.get('state', '?'))}</b> · مفاتيح مضبوطة: {'نعم' if provider.get('configured') else 'لا'}",
        f"⚡ بث الكريبتو WebSocket: <b>{ws_badge}</b>",
        f"🤖 البوت: <b>متصل</b> (polling نشط)",
        f"🧠 المحرك: <b>{'يعمل' if eng == '1' else 'متوقف'}</b>",
        f"🔒 LIVE: <b>{'مفعل ⚠️' if live == '1' else 'مغلق'}</b>",
    ]
    if cb_snap:
        lines.append("")
        lines.append("🛡 <b>قاطع الدائرة لمزودي الأسعار (Circuit Breaker):</b>")
        for pname, st in cb_snap.items():
            ico = "🟢" if st["state"] == "CLOSED" else ("🔴" if st["state"] == "OPEN" else "🟡")
            lat = f" · {st['avg_latency_ms']:.0f}ms" if st["avg_latency_ms"] > 0 else ""
            cd = f" (تبريد {st['cooldown_remaining_s']}s)" if st["cooldown_remaining_s"] > 0 else ""
            lines.append(f"  {ico} <code>{esc(pname)}</code>: {st['state']}{cd} · ✓{st['successes']} ✗{st['failures']}{lat}")
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("🔄 إدارة التحديثات والإصدارات", "a:updates", style="primary")]]), bot)


# --- Updates & Version Rollback Manager (v6.16.0) ---------------------------------------------
async def admin_updates(q, uid, context, bot):
    import updater
    releases = await asyncio.to_thread(updater.list_releases, 6)
    snaps = await asyncio.to_thread(updater.list_rollback_snapshots)
    lines = [
        "🔄 <b><u>إدارة التحديثات والإصدارات</u></b>",
        f"<blockquote>◆ الإصدار العامل حالياً: <b>v{esc(config.VERSION)}</b>",
    ]
    if snaps:
        lines.append(f"◆ آخر نسخة تراجع محلية: <b>v{esc(updater.snapshot_version(snaps[0]))}</b> ({len(snaps)} محفوظة)")
    else:
        lines.append("◆ نسخ التراجع المحلية: تُنشأ تلقائياً قبل كل تحديث")
    lines.append("</blockquote>")
    rows_kb = [[kb.btn("⬆️ فحص وتثبيت أحدث إصدار", "a:updlatest", style="success")]]
    if releases:
        lines.append("<b>اختر أي إصدار للتثبيت أو الرجوع الفوري:</b>")
        for rel in releases[:6]:
            tag = rel["tag"]
            ver = updater.display_tag(tag)
            is_cur = updater.parse_version(tag) == updater.parse_version(config.VERSION)
            is_new = updater.is_newer(tag, config.VERSION)
            badge = "✅ الحالي" if is_cur else ("⬆️ أحدث" if is_new else "⬇️ سابق")
            lines.append(f"  • <code>{esc(tag)}</code> — {badge}")
            rows_kb.append([kb.btn(
                f"{'🔄 إعادة تثبيت' if is_cur else ('⬆️ ترقية إلى' if is_new else '⬇️ رجوع إلى')} v{ver}",
                f"a:updver:{tag}",
                style="success" if is_new else ("primary" if not is_cur else ""),
            )])
    else:
        lines.append("⚠️ تعذّر جلب قائمة GitHub حالياً — يمكنك إرسال <code>/update v6.14.1</code> مباشرة.")
    if snaps:
        rows_kb.append([kb.btn(
            f"↩️ تراجع فوري للنسخة السابقة (v{updater.snapshot_version(snaps[0])})",
            "a:updrollback",
            style="danger",
        )])
    lines.append("\n<i>الأوامر المباشرة: <code>/update</code> · <code>/update v6.14.1</code> · <code>/update list</code> · <code>/update rollback</code></i>")
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_update_latest(q, uid, context, bot):
    import updater
    await render(q, f"🔎 <b>جارٍ فحص وتثبيت أحدث إصدار...</b>\n◆ الحالي: v{esc(config.VERSION)}", None, bot)
    res = await asyncio.to_thread(updater.self_update)
    if res.get("status") == "updated":
        await afn.run(audit_log.log, uid, f"self_update:{res.get('local')}->{res.get('remote')}")
        await render(
            q,
            f"✅ <b>تم تثبيت v{esc(updater.display_tag(res.get('remote')))} بنجاح</b> ({res.get('files', 0)} ملف).\n"
            "♻️ جارٍ إعادة تشغيل البوت الآن — أرسل /ping بعد لحظات.",
            None, bot,
        )
        await asyncio.sleep(1.5)
        updater.restart()
        return
    if res.get("status") == "no_update":
        await render(
            q,
            f"🟢 <b>أنت على أحدث إصدار بالفعل (v{esc(res.get('local'))}).</b>",
            kb.admin_nav([[kb.btn("🔄 قائمة الإصدارات", "a:updates", style="primary")]]), bot,
        )
        return
    await render(
        q,
        f"⚠️ <b>تعذّر التحديث:</b> {esc(res.get('detail') or 'غير معروف')}",
        kb.admin_nav([[kb.btn("🔄 إدارة التحديثات", "a:updates", style="primary")]]), bot,
    )


async def admin_update_ver_confirm(q, uid, context, bot, tag: str):
    import updater
    norm = updater.normalize_tag(tag)
    if not norm:
        await render(q, "⚠️ رقم الإصدار غير صالح.", kb.admin_nav([[kb.btn("🔄 إدارة التحديثات", "a:updates")]]), bot)
        return
    is_down = updater.parse_version(norm) < updater.parse_version(config.VERSION)
    action_txt = "الرجوع إلى الإصدار السابق" if is_down else "تثبيت الإصدار"
    body = (
        f"🔄 <b>تأكيد {action_txt}</b>\n"
        f"<blockquote>◆ الحالي: <b>v{esc(config.VERSION)}</b>\n"
        f"◆ المطلوب: <b>{esc(norm)}</b>\n"
        "◆ حماية البيانات: <code>.env</code> وقاعدة البيانات والسجلات لن تُمس\n"
        "◆ نسخة تراجع تلقائية ستُحفظ قبل التبديل</blockquote>"
    )
    rows_kb = [
        [kb.btn(f"✅ نعم، ثبّت {norm} الآن", f"a:updvergo:{norm}", style="success" if not is_down else "danger")],
        [kb.btn("❌ إلغاء والعودة", "a:updates")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_update_ver_go(q, uid, context, bot, tag: str):
    import updater
    norm = updater.normalize_tag(tag)
    if not norm:
        await render(q, "⚠️ رقم الإصدار غير صالح.", kb.admin_nav(), bot)
        return
    await render(q, f"⏳ <b>جارٍ تنزيل وتثبيت {esc(norm)}...</b>", None, bot)
    res = await asyncio.to_thread(updater.self_update, None, norm, True)
    if res.get("status") == "updated":
        await afn.run(audit_log.log, uid,
                      f"self_update:{res.get('direction')}:{res.get('local')}->{res.get('remote')}")
        await render(
            q,
            f"✅ <b>تم تثبيت {esc(norm)} بنجاح</b> ({res.get('files', 0)} ملف).\n"
            "♻️ جارٍ إعادة تشغيل البوت الآن — أرسل /ping بعد لحظات.",
            None, bot,
        )
        await asyncio.sleep(1.5)
        updater.restart()
        return
    await render(
        q,
        f"⚠️ <b>تعذّر تثبيت {esc(norm)}:</b> {esc(res.get('detail') or 'غير معروف')}",
        kb.admin_nav([[kb.btn("🔄 إدارة التحديثات", "a:updates", style="primary")]]), bot,
    )


async def admin_update_rollback_confirm(q, uid, context, bot):
    import updater
    snaps = await asyncio.to_thread(updater.list_rollback_snapshots)
    if not snaps:
        await render(
            q,
            "⚠️ لا توجد نسخة تراجع محلية محفوظة بعد.\n"
            "<i>يمكنك الرجوع لأي إصدار من GitHub عبر <code>/update v6.14.1</code> أو من قائمة الإصدارات.</i>",
            kb.admin_nav([[kb.btn("🔄 إدارة التحديثات", "a:updates", style="primary")]]), bot,
        )
        return
    ver = updater.snapshot_version(snaps[0])
    body = (
        "↩️ <b>تأكيد التراجع المحلي الفوري</b>\n"
        f"<blockquote>◆ الإصدار الحالي: <b>v{esc(config.VERSION)}</b>\n"
        f"◆ النسخة المستعادة: <b>v{esc(ver)}</b>\n"
        f"◆ الملف: <code>{esc(snaps[0].name)}</code></blockquote>"
    )
    rows_kb = [
        [kb.btn(f"↩️ نعم، تراجع إلى v{ver} الآن", "a:updrollbackgo", style="danger")],
        [kb.btn("❌ إلغاء", "a:updates")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_update_rollback_go(q, uid, context, bot):
    import updater
    await render(q, "↩️ <b>جارٍ استعادة النسخة السابقة المحفوظة محلياً...</b>", None, bot)
    res = await asyncio.to_thread(updater.rollback_update)
    if res.get("status") == "rolled_back":
        await afn.run(audit_log.log, uid,
                      f"self_update_rollback:{res.get('local')}->{res.get('restored_version')}")
        await render(
            q,
            f"✅ <b>تم التراجع إلى v{esc(res.get('restored_version'))} بنجاح</b> ({res.get('files', 0)} ملف).\n"
            "♻️ جارٍ إعادة تشغيل البوت الآن — أرسل /ping بعد لحظات.",
            None, bot,
        )
        await asyncio.sleep(1.5)
        updater.restart()
        return
    await render(
        q,
        f"⚠️ <b>تعذّر التراجع:</b> {esc(res.get('detail') or 'غير معروف')}",
        kb.admin_nav([[kb.btn("🔄 إدارة التحديثات", "a:updates", style="primary")]]), bot,
    )


_BOOT_EPOCH = __import__("time").time()


# --- v6.11.0 thread-offload helpers ---
def _analytics_page_rows():
    """(v6.11.0 thread-offload helper) plans grid + today/total analyses counters."""
    with db.conn() as c:
        plans = c.execute("SELECT code, title, enabled, max_analyses_per_day FROM plans ORDER BY price").fetchall()
        today = c.execute("SELECT COUNT(*) n FROM analyses WHERE created_at>=?", (db.day_start_utc(),)).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM analyses").fetchone()["n"]
    return plans, today, total


def _anal_quota_row(code: str):
    """(v6.11.0 thread-offload helper) current analyses quota of one plan."""
    with db.conn() as c:
        return c.execute("SELECT max_analyses_per_day FROM plans WHERE code=?", (code,)).fetchone()


def _anal_quota_set(code: str, val: int) -> None:
    """(v6.11.0 thread-offload helper) write the analyses quota of one plan."""
    with db.conn() as c:
        c.execute("UPDATE plans SET max_analyses_per_day=? WHERE code=?", (val, code))


def _notification_rows():
    """(v6.11.0 thread-offload helper) unread counter + recent notification events."""
    with db.conn() as c:
        unread = c.execute("SELECT COUNT(*) n FROM notification_events WHERE read=0").fetchone()["n"]
        recent = c.execute("SELECT * FROM notification_events ORDER BY id DESC LIMIT 8").fetchall()
    return unread, recent


def _broadcast_page_rows():
    """(v6.11.0 thread-offload helper) recent broadcasts + active/pending count."""
    with db.conn() as c:
        recent = c.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 6").fetchall()
        active = c.execute("SELECT COUNT(*) n FROM broadcasts WHERE status IN ('PENDING','RUNNING')").fetchone()["n"]
    return recent, active


def _plan_code_title_rows():
    """(v6.11.0 thread-offload helper) plan picker rows ordered by price."""
    with db.conn() as c:
        return c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()


async def admin_webapp_info(q, uid, context, bot, notice: str = ""):
    """v7.2.0 Telegram Mini App & Admin Web Panel status + Bot-Hosting Zero-Config Auto-HTTPS Tunnel."""
    from fysb.webapp import tunnel as _tun
    ready = config.webapp_ready()
    auto_on = (await afn.run(db.get_setting, "webapp_auto_tunnel", "1") or "1") == "1"
    st = _tun.tunnel_status()
    rows_kb = []
    if ready:
        rows_kb.append([kb.webapp_btn("🖥 فتح لوحة الإدارة الويب", f"{config.WEBAPP_URL}/admin", style="success")])
        rows_kb.append([kb.webapp_btn("📱 معاينة التطبيق المصغر للمستخدم", f"{config.WEBAPP_URL}/", style="primary")])
        src_txt = (
            "⚡ نفق HTTPS تلقائي مجاني (Bot-Hosting بدون أي موقع)"
            if config.WEBAPP_TUNNEL_SOURCE == "auto_tunnel"
            else "🔗 رابط HTTPS مخصص (.env)"
        )
        body = (
            "🌐 <b><u>التطبيق المصغر الملكي ولوحة الإدارة الويب (v7.2.0)</u></b>\n"
            "<blockquote>"
            f"▎🟢 الحالة: <b>مفعّل وجاهز عبر HTTPS</b>\n"
            f"▎🛰 المصدر: <b>{src_txt}</b>\n"
            f"▎🔗 الرابط: <code>{esc(config.WEBAPP_URL)}</code>\n"
            f"▎🖥 الخادم المدمج: <code>127.0.0.1:{config.WEBAPP_PORT}</code>\n"
            "▎🔐 المصادقة: توقيع <b>Telegram WebApp HMAC-SHA256</b> + حارس <b>ADMIN_ID</b>."
            "</blockquote>"
        )
    else:
        err_line = f"\n▎⚠️ آخر ملاحظة: <code>{esc(st['last_error'])}</code>" if st.get("last_error") else ""
        body = (
            "🌐 <b><u>التطبيق المصغر ولوحة الإدارة الويب (Mini App v7.2.0)</u></b>\n"
            "<blockquote>"
            f"▎⏳ الحالة: <b>{'جاري تهيئة النفق التلقائي...' if auto_on else 'متوقف حالياً'}</b>\n"
            "▎🚀 <b>وضع Bot-Hosting (بدون أي موقع أو دومين):</b>\n"
            "  اضغط <b>«🔄 تشغيل / تجديد نفق HTTPS الآن»</b> ليتم إنشاء رابط HTTPS مجاني تلقائياً خلال ثوانٍ!"
            f"{err_line}"
            "</blockquote>"
        )
    if notice:
        body += f"\n\n<i>{esc(notice)}</i>"
    if not config.WEBAPP_URL_STATIC:
        rows_kb.append([kb.btn("🔄 تشغيل / تجديد نفق HTTPS الآن", "a:webapptunnel_restart", style="success")])
        rows_kb.append([kb.btn(
            f"🛰 النفق التلقائي على Bot-Hosting: {'🟢 مفعّل' if auto_on else '⚪ متوقف'}",
            "a:webapptunnel_toggle",
            style="primary" if auto_on else "danger",
        )])
    rows_kb.append([kb.btn("↩️ النظام والأمان", "a:hub:system")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_webapp_tunnel_toggle(q, uid, context, bot):
    from fysb.webapp import tunnel as _tun
    cur = (await afn.run(db.get_setting, "webapp_auto_tunnel", "1") or "1") == "1"
    nxt = not cur
    await afn.run(db.set_setting, "webapp_auto_tunnel", "1" if nxt else "0")
    await afn.run(audit_log.log, uid, f"webapp_auto_tunnel:{int(nxt)}")
    if not nxt:
        await _tun.stop_tunnel()
        await admin_webapp_info(q, uid, context, bot, notice="⏹ تم إيقاف النفق التلقائي للتطبيق المصغّر.")
    else:
        await admin_webapp_tunnel_restart(q, uid, context, bot)


async def admin_webapp_tunnel_restart(q, uid, context, bot):
    from fysb.handlers import background as _bg
    from fysb.webapp import server as _wsrv
    from fysb.webapp import tunnel as _tun
    await afn.run(db.set_setting, "webapp_auto_tunnel", "1")
    if getattr(_bg.post_init, "_webapp_srv", None) is None:
        _bg.post_init._webapp_srv = await _wsrv.start_webapp_server()
    await _tun.stop_tunnel()
    url = await _tun.start_tunnel(port=config.WEBAPP_PORT, timeout=40.0)
    if url:
        await afn.run(audit_log.log, uid, f"webapp_tunnel_started:{url}")
        await admin_webapp_info(q, uid, context, bot, notice="✅ تم تشغيل رابط HTTPS المجاني بنجاح! التطبيق جاهز الآن.")
    else:
        err = _tun.tunnel_status().get("last_error") or "تعذر الاتصال بـ Cloudflare حالياً"
        await admin_webapp_info(q, uid, context, bot, notice=f"⚠️ {err}")


def _audit_total() -> int:
    """(v6.11.0 thread-offload helper) total audit rows for pagination."""
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM audit").fetchone()["n"]
