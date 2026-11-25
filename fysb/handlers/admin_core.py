"""Admin core sections: Dashboard, Statistics, Users, Subscriptions, Plans,
Payments, Payment Methods — all real-DB, all server-guarded, luxury cards."""
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render, safe
from fysb.handlers.inputs import (PM_CATEGORIES, PM_PRESETS, clear_flows,
                                  plan_persist)
from fysb.services import payments as payments_service
from fysb.services import subscriptions as subs
from fysb.services import users as users_service
from fysb.services import audit as audit_log
from fysb.services import reports as reports_service
from fysb.theme import ICON, bar, card, esc, human_dt
from fysb.ui.cards import render_plan_card, render_payment_card, render_user_card
from fysb.ui import keyboards as kb


async def admin_stats(q, uid, context, bot):
    body = await afn.run(reports_service.dashboard_view)
    await render(q, body, kb.admin_nav([[kb.btn("📈 إحصائيات مفصلة", "a:statistics"), kb.btn("🖥 حالة النظام", "a:health")]]), bot)


STAT_SECTIONS = ["users", "signals", "payments", "subscriptions", "analyses"]
STAT_TITLES = {"users": "👥 تقرير المستخدمين", "signals": "📡 تقرير الإشارات",
               "payments": "💳 تقرير المدفوعات", "subscriptions": "💎 تقرير الاشتراكات",
               "analyses": "🔬 تقرير التحليلات"}


def _stat_section_body(key: str) -> str:
    return {"users": reports_service.users_report,
            "signals": reports_service.signals_report,
            "payments": reports_service.payments_report,
            "subscriptions": reports_service.subscriptions_report,
            "analyses": reports_service.analyses_report}[key]()


async def admin_statistics(q, uid, context, bot, page: int = 0):
    """v6.10.0 (owner report 1) — the old single message joined FIVE reports
    and with real data grew past Telegram's 4096-char limit ⇒ BadRequest ⇒
    the generic «حدث خطأ أثناء تنفيذ الزر» card. The royal fix: one LUXURY
    page per report with a navigation strip — every page fits forever, and
    the render safety net truncates nothing silently."""
    page = max(0, min(int(page or 0), len(STAT_SECTIONS) - 1))
    key = STAT_SECTIONS[page]
    section_body = await afn.run(_stat_section_body, key)
    body = (f"📈 <b><u>الإحصائيات الملكية — {STAT_TITLES[key]}</u></b>\n"
            f"<blockquote>▎📄 القسم <b>{page + 1}</b> من <b>{len(STAT_SECTIONS)}</b>"
            f" · حدّث لحظياً من قاعدة البيانات الحقيقية</blockquote>\n"
            + section_body)
    pager = []
    if page > 0:
        pager.append(kb.btn("◀️ السابق", f"a:statistics:{page - 1}"))
    pager.append(kb.btn(f"📄 {page + 1}/{len(STAT_SECTIONS)}", "a:plannoop"))
    if page < len(STAT_SECTIONS) - 1:
        pager.append(kb.btn("التالي ▶️", f"a:statistics:{page + 1}"))
    await render(q, body, kb.admin_nav([pager]), bot)


# --- Users ---------------------------------------------------------------------
async def admin_users(q, uid, context, bot, page: int = 0):
    data = await afn.run(users_service.page_list, page)  # admin excluded inside the service
    rows_kb = [[kb.btn(f"{'⛔' if r['is_blocked'] else '🟢'} {(r['full_name'] or r['username'] or 'مستخدم')[:18]} · {r['user_id']}",
                       f"a:userdetail:{r['user_id']}")] for r in data["rows"]]
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️ السابق", f"a:userspage:{page - 1}"))
    if page < data["pages"] - 1:
        nav_row.append(kb.btn("التالي ▶️", f"a:userspage:{page + 1}"))
    if nav_row:
        rows_kb.append(nav_row)
    body = (f"{ICON['users']} <b><u>دليل المستخدمين</u></b>\n"
            f"▎الإجمالي: <b>{data['total']}</b> · صفحة {page + 1}/{data['pages']}\n"
            f"<i>حساب المدير مستثنى نهائياً — لا يظهر كمحتسب ولا كمستخدم.</i>")
    await render(q, body, kb.admin_nav(rows_kb + [[kb.btn("🔎 بحث عن مستخدم", "a:usersearch")]]), bot)


async def admin_user_search(q, uid, context, bot):
    context.user_data["awaiting_user_search"] = True
    await render(q, "🔎 أرسل الاسم أو اسم المستخدم أو المعرّف الرقمي:", kb.admin_nav(), bot)


async def admin_user_detail(q, uid, context, bot, target: int):
    if target == config.ADMIN_ID:
        await render(q, "⛔ حساب المدير خارج قسم المستخدمين — لا يُعرض ولا يُدار من هنا.", kb.admin_nav(), bot)
        return
    u = await afn.run(users_service.get_user, target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    profile = await afn.run(users_service.profile_summary, target)
    rows_kb = [
        [kb.btn("⛔ حظر" if not u["is_blocked"] else "🔓 إلغاء الحظر", f"a:toggleuser:{target}",
                style="danger" if not u["is_blocked"] else "success"),
         kb.btn("🔔 تبديل الإشعارات", f"a:togglenotify:{target}")],
        [kb.btn("💳 مدفوعاته", f"a:userpayments:{target}"), kb.btn("📡 ما استلمه", f"a:userdeliveries:{target}")],
        [kb.btn("✏️ تعديل الاشتراك", f"a:subedit:{target}", style="primary"), kb.btn("🧪 حسابه التجريبي", f"a:demoacct:{target}")],
        [kb.btn("⚡ سماح بالنسخ", f"a:br_user_set:{target}:ALLOW", style="success"),
         kb.btn("🚫 منع النسخ", f"a:br_user_set:{target}:DENY", style="danger"),
         kb.btn("⚙️ تلقائي", f"a:br_user_set:{target}:AUTO")],
        [kb.btn("🗑 حذف المستخدم نهائياً", f"a:userdelete:{target}", style="danger")],
        [kb.btn("↩️ دليل المستخدمين", "a:users")],
    ]
    await render(q, render_user_card(u, profile), kb.admin_nav(rows_kb), bot)


async def admin_user_delete_confirm(q, uid, context, bot, target: int):
    """Luxury double-confirmation before PERMANENT user deletion."""
    if target == config.ADMIN_ID:
        await render(q, "⛔ حساب المدير لا يُحذف أبداً — محمي على مستوى الخدمة نفسها.", kb.admin_nav(), bot)
        return
    u = await afn.run(users_service.get_user, target)
    if not u:
        await render(q, "المستخدم غير موجود (ربما حُذف سابقاً).", kb.admin_nav(), bot)
        return
    stats = await afn.run(_user_delete_counts, target)
    name = (u["full_name"] or u["username"] or str(target))
    body = (f"🗑 <b>حذف نهائي للمستخدم</b>\n"
            f"<blockquote>▎👤 <b>{esc(name)}</b> · <code>{target}</code>\n"
            f"▎⚠️ هذا <b>حذف نهائي غير قابل للاسترجاع</b> — ليس حظراً مؤقتاً.\n"
            f"▎🧹 سيُمحى معه: اشتراكات ({stats['subscriptions']}) · مدفوعات ({stats['payments']}) · "
            f"إشارات مستلمة ({stats['deliveries']}) · حساب تجريبي ({stats['demo']}) · "
            f"صفقات ({stats['positions']}/{stats['trades']}) · تحليلات ({stats['analyses']}) · "
            f"تذاكر ({stats['tickets']}) · حسابات وسيط ({stats['accounts']})</blockquote>\n"
            f"<i>التأكيد الثاني مطلوب — بعد التنفيذ لا يوجد أي رجوع أو أرشيف.</i>")
    rows_kb = [
        [kb.btn("⚠️ تأكيد الحذف النهائي", f"a:userdeletego:{target}")],
        [kb.btn("↩️ تراجع", f"a:userdetail:{target}")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_user_delete_go(q, uid, context, bot, target: int):
    res = await afn.run(users_service.delete_user_forever, target, uid)
    if not res.get("ok"):
        await render(q, f"⛔ {esc(res.get('error', 'فشل الحذف'))}", kb.admin_nav(), bot)
        return
    try:
        await bot.send_message(target,
            "🗑 تم حذف حسابك نهائياً من قبل الإدارة. شكراً لوقتك معنا — يمكنك البدء من جديد عبر /start إن أردت.",
            parse_mode="HTML")
    except Exception:
        pass
    wiped = res.get("counts", {})
    total = sum(wiped.values())
    body = (f"✅ <b>تم حذف المستخدم نهائياً</b> · <code>{target}</code>\n"
            f"<blockquote>▎🧹 مُحوِّلات: <b>{total}</b> سجل عبر {len(wiped)} جدول\n"
            f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>")
    await render(q, body, kb.admin_nav([[kb.btn("↩️ دليل المستخدمين", "a:users")]]), bot)


async def admin_toggle_user(q, uid, context, bot, target: int):
    if target == config.ADMIN_ID:
        await render(q, "⛔ لا يمكن حظر حساب المدير.", kb.admin_nav(), bot)
        return
    u = await afn.run(users_service.get_user, target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    await afn.run(users_service.set_blocked, target, not u["is_blocked"], uid)
    await admin_user_detail(q, uid, context, bot, target)


async def admin_toggle_notify(q, uid, context, bot, target: int):
    u = await afn.run(users_service.get_user, target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    await afn.run(users_service.set_notifications, target, not u["notifications"])
    await admin_user_detail(q, uid, context, bot, target)


async def admin_user_payments(q, uid, context, bot, target: int):
    rows = await afn.run(_user_payments_rows, target)
    rows_kb = [[kb.btn(f"🧾 #{r['id']} · {esc(r['plan_code'])} · {esc(r['status'])}", f"a:paymentdetail:{r['id']}")] for r in rows]
    if not rows_kb:
        rows_kb = [[kb.btn("لا توجد مدفوعات", "a:plannoop")]]
    rows_kb.append([kb.btn("↩️ الملف", f"a:userdetail:{target}")])
    await render(q, f"💳 <b>مدفوعات المستخدم {target}</b>", kb.admin_nav(rows_kb), bot)


async def admin_user_deliveries(q, uid, context, bot, target: int):
    """v6.10.0 (owner report 2) — the received-signals card shows the SAME
    sequential number as the signals center (ROW_NUMBER seq, not the raw
    internal id after deletions), wrapped in a luxury status card with real
    results (hit targets, banked pips, USD at 0.01 lot)."""
    from fysb.engines.tracker import seq_of
    from fysb import instruments
    from fysb.theme import dir_badge, status_ar
    lines = await afn.run(_delivery_lines, target)
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ الملف", f"a:userdetail:{target}")]]), bot)


async def admin_sub_edit(q, uid, context, bot, target: int):
    plans = await afn.run(_plan_code_title_rows)
    profile = await afn.run(users_service.profile_summary, target)
    current = profile.get("plan_code", "FREE")
    rows_kb = []
    for p in plans:
        mark = "✅ " if p["code"] == current else ""
        rows_kb.append([kb.btn(f"{mark}{esc(p['title'])} ({p['code']})", f"a:subset:{target}:{p['code']}")])
    if current != "FREE":
        rows_kb.append([kb.btn("⏳ تمديد 30 يوم", f"a:subextend:{target}:30"), kb.btn("⏳ تمديد 90 يوم", f"a:subextend:{target}:90")])
        rows_kb.append([kb.btn("⛔ إيقاف الاشتراك", f"a:subcancel:{target}")])
    rows_kb.append([kb.btn("↩️ الملف", f"a:userdetail:{target}")])
    await render(q, f"✏️ <b>تعديل اشتراك المستخدم {target}</b>\nالحالي: <b>{esc(current)}</b>", kb.admin_nav(rows_kb), bot)


async def admin_sub_set(q, uid, context, bot, target: int, code: str):
    if target == config.ADMIN_ID:
        await render(q, "⛔ حساب المدير لا يُدار كاشتراك مستخدم.", kb.admin_nav(), bot)
        return
    res = await afn.run(users_service.admin_edit_subscription, target, code, uid)
    if not res.get("ok"):
        await render(q, f"⛔ {esc(res.get('error', 'فشل'))}", kb.admin_nav(), bot)
        return
    note = f"✅ تم تنشيط باقة <b>{esc(code)}</b> للمستخدم <code>{target}</code>."
    if res.get("ends_at"):
        note += f"\nالانتهاء: {human_dt(res['ends_at'])}"
    try:
        await bot.send_message(target, f"✅ تم تحديث اشتراكك إلى <b>{esc(code)}</b> من قبل الإدارة.", parse_mode="HTML")
    except Exception:
        pass
    try:
        from fysb.services import partners as partners_svc
        await partners_svc.notify_commission_and_vip(bot, target, code, res.get("commission"))
    except Exception:
        pass
    await render(q, note, kb.admin_nav([[kb.btn("↩️ الملف", f"a:userdetail:{target}")]]), bot)


async def admin_sub_extend(q, uid, context, bot, target: int, days: int):
    profile = await afn.run(users_service.profile_summary, target)
    code = profile.get("plan_code", "FREE")
    res = await afn.run(subs.grant_subscription, target, code, source="ADMIN_EXTEND", actor=uid, override_days=days)
    if res.get("ok"):
        await afn.run(audit_log.log, uid, f"subscription_extended:{target}:{days}d")
        try:
            await bot.send_message(target, f"⏳ تم تمديد اشتراكك {days} يوماً من قبل الإدارة.")
        except Exception:
            pass
        await render(q, f"✅ تم تمديد الاشتراك {days} يوماً للمستخدم {target}.", kb.admin_nav(), bot)
    else:
        await render(q, "⛔ فشل التمديد.", kb.admin_nav(), bot)


async def admin_sub_cancel(q, uid, context, bot, target: int):
    ok = await afn.run(subs.cancel_subscription, target, uid)
    if ok:
        await afn.run(audit_log.log, uid, f"subscription_cancelled:{target}")
        try:
            await bot.send_message(target, "⛔ أوقفت الإدارة اشتراكك. تواصل مع الدعم للاستفسار.")
        except Exception:
            pass
    await render(q, "✅ أُوقف الاشتراك." if ok else "لا يوجد اشتراك نشط.", kb.admin_nav(), bot)


async def admin_demo_acct(q, uid, context, bot, target: int):
    from fysb.engines import demo as demo_engine
    acc = await afn.run(demo_engine.get_or_create_account, target)
    open_n, closed = await afn.run(_demo_account_stats, acc["id"])
    body = (f"🧪 <b>الحساب التجريبي للمستخدم {target}</b>\n\n"
            f"💰 الرصيد: <b>{acc['balance']:,.2f}</b> {esc(acc.get('currency', 'USD'))}\n"
            f"📉 أعلى Equity: {acc['peak_equity']:,.2f}\n"
            f"📂 مفتوحة: <b>{open_n}</b>\n"
            f"📜 مغلقة: <b>{closed['n']}</b> (صافي {closed['s']:+,.2f})")
    await render(q, body, kb.admin_nav([[kb.btn("🔄 تصفير حسابه", f"a:demoreset_admin:{target}")]]), bot)


async def admin_demo_reset(q, uid, context, bot, target: int):
    from fysb.engines import demo as demo_engine
    await afn.run(demo_engine.reset_account, target)
    await afn.run(audit_log.log, uid, f"demo_reset:{target}")
    await render(q, f"✅ تم تصفير الحساب التجريبي للمستخدم {target}.", kb.admin_nav(), bot)


# --- Subscriptions -------------------------------------------------------------
async def admin_subscriptions(q, uid, context, bot):
    active, expired = await afn.run(_subs_page_rows)
    rows_kb = [[kb.btn(f"💎 #{r['id']} {esc(r['full_name'] or '')} {r['user_id']} · {esc(r['plan_code'])} · {human_dt(r['ends_at'])[:10] if r['ends_at'] else 'دائم'}",
                       f"a:userdetail:{r['user_id']}")] for r in active]
    await render(q, f"💎 <b><u>الاشتراكات النشطة (آخر 10)</u></b>\n▎منتهية/مستبدلة تاريخياً: <b>{expired}</b>\nاضغط للانتقال لملف المستخدم:",
                 kb.admin_nav(rows_kb), bot)


# --- Plans (guided creation — no pipe input) -----------------------------------
async def admin_plans(q, uid, context, bot):
    rows = await afn.run(_plans_all_rows)
    rows_kb = []
    for r in rows:
        status = "🟢" if r["enabled"] else "⚪"
        rows_kb.append([kb.btn(f"{status} {esc(r['title'])} · {r['price']:g} {r['currency']} · 📡{r['max_signals_per_day']} 🔬{r['max_analyses_per_day'] or 0}/يوم",
                               f"a:planedit:{r['code']}")])
    rows_kb.append([kb.btn("➕ إنشاء باقة جديدة (موجّه)", "a:plannew")])
    body = (f"{ICON['diamond']} <b><u>مركز إدارة الباقات</u></b>\n"
            "▎كل القيم قابلة للتعديل بالأزرار — الإنشاء يتم خطوة خطوة بالأزرار الفاخرة.")
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_plan_new(q, uid, context, bot):
    clear_flows(context.user_data)
    context.user_data["plan_draft"] = {"step": "code", "markets": [], "timeframes": [], "strategies": [], "linked": []}
    await render(q,
                 "👑 <b><u>إنشاء باقة جديدة — التدفق الملكي الموجّه</u></b>\n"
                 "<blockquote>"
                 "▎🧭 <b>10 مراحل بالأزرار الفاخرة:</b> الرمز ← العنوان ← السعر ← العملة ← المدة ← الجودة ← "
                 "حد الإشارات ← حد التحليلات ← الأسواق ← الأطر ← المنهجيات ← الحفظ.\n"
                 "▎✏️ كل مرحلة تقبل قيمة جاهزة بضغطة <b>أو قيمة مخصصة</b> ترسلها رقماً فقط.\n"
                 "▎🌐 الأسواق تعرض المفعلة والموثقة فقط، والمنهجيات تعرض المنفذة والمختبرة فقط — حقيقة 100%."
                 "</blockquote>\n"
                 "✍️ <b>المرحلة 1/10 — أرسل رمز الباقة CODE:</b> أحرف لاتينية كبيرة (مثال: <code>VIP</code>) — "
                 "وقبل الأسماء العربية مثل الذهبية، الماسة، المتقدمة، الاحترافية لمرحلة العنوان.",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:plancancel")]]), bot)


async def _plan_reply_current(q, uid, context, bot, d: dict, note: str):
    """Render the remaining guided steps as luxury buttons (v6.10.0: every
    stage carries a ✏️ CUSTOM-VALUE button — the owner demanded being able to
    enter ANY duration/quota instantly, not only the preset chips)."""
    stage = d.get("stage", "days")
    rows = []
    if stage == "days":
        rows = [[kb.btn(f"📅 {n} يوم", f"a:plandays:{n}") for n in (7, 14, 30)],
                [kb.btn(f"📅 {n} يوم", f"a:plandays:{n}") for n in (90, 180, 365)],
                [kb.btn("♾ دائم (0 يوم)", "a:plandays:0")],
                [kb.btn("✏️ مدة مخصصة (أرسل رقماً)", "a:plancustom:days")]]
    elif stage == "score":
        rows = [[kb.btn(f"⭐ {n}", f"a:planscore:{n}") for n in (50, 60, 70)],
                [kb.btn(f"⭐ {n}", f"a:planscore:{n}") for n in (80, 90, 95)],
                [kb.btn("✏️ جودة مخصصة (أرسل رقماً)", "a:plancustom:score")]]
    elif stage == "quota":
        rows = [[kb.btn(f"📡 {n}/يوم", f"a:planquota:{n}") for n in (2, 5, 10)],
                [kb.btn(f"📡 {n}/يوم", f"a:planquota:{n}") for n in (20, 50, 100)],
                [kb.btn("✏️ حد مخصص (أرسل رقماً)", "a:plancustom:quota")]]
    elif stage == "analyses":
        rows = [[kb.btn(f"🔬 {n}/يوم", f"a:plananal:{n}") for n in (0, 3, 5)],
                [kb.btn(f"🔬 {n}/يوم", f"a:plananal:{n}") for n in (10, 30, 100)],
                [kb.btn("✏️ حد مخصص (أرسل رقماً)", "a:plancustom:analyses")]]
    elif stage == "markets":
        # v6.10.0 (owner report 4): ONLY the markets the admin actually armed
        # (enabled+verified) in the markets center — never the whole catalog.
        mkts = await afn.run(_plan_market_choices)
        if not mkts:
            rows = [[kb.btn("⛔ لا توجد أسواق مفعلة — فعّل سوقاً من قسم الأسواق أولاً", "a:plannoop")]]
        else:
            rows = [[kb.btn(("✅ " if s in d["markets"] else "") + s, f"a:planmkt:{s}") for s in mkts[i:i + 3]]
                    for i in range(0, len(mkts), 3)]
        rows.append([kb.btn("➡️ التالي: الأطر", "a:planstage:timeframes")])
    elif stage == "timeframes":
        tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        rows = [[kb.btn(("✅ " if t in d["timeframes"] else "") + t, f"a:plantf:{t}") for t in tfs[i:i + 4]]
                for i in range(0, len(tfs), 4)]
        rows.append([kb.btn("➡️ التالي: الاستراتيجيات", "a:planstage:strategies")])
    elif stage == "strategies":
        # v6.10.0 (owner report 4): ONLY REAL, enabled+verified+IMPLEMENTED
        # engines — in the guaranteed strength order.
        codes = await afn.run(_plan_strategy_choices)
        if not codes:
            rows = [[kb.btn("⛔ لا توجد منهجيات مفعلة — فعّلها من قسم الاستراتيجيات أولاً", "a:plannoop")]]
        else:
            rows = [[kb.btn(("✅ " if s in d["strategies"] else "") + s, f"a:planstrat:{s}")
                     for s in codes[i:i + 3]] for i in range(0, len(codes), 3)]
        rows.append([kb.btn("💾 حفظ الباقة", "a:planconfirm")])
    rows.append([kb.btn("🗑 إلغاء الإنشاء", "a:plancancel")])
    await render(q, note, kb.admin_nav(rows), bot)


async def admin_plan_cb(q, uid, context, bot, action: str, arg: str):
    """All guided-plan button callbacks (cur/days/score/quota/anal/mkt/tf/strat/stage/confirm)."""
    d = context.user_data.get("plan_draft")
    if not d:
        await render(q, "انتهت جلسة الإنشاء — ابدأ من «إنشاء باقة جديدة».", kb.admin_nav([[kb.btn("💠 الباقات", "a:plans")]]), bot)
        return
    if action == "cur":
        d["currency"] = arg
        d["stage"] = "days"
        note = f"💎 العملة: <b>{esc(arg)}</b>\nالخطوة: اختر <b>مدة الاشتراك</b>:"
        await _plan_reply_current(q, uid, context, bot, d, note)
    elif action == "days":
        d["days"] = int(arg)
        d["stage"] = "score"
        await _plan_reply_current(q, uid, context, bot, d, f"📅 المدة: <b>{arg} يوم</b>\nالخطوة: اختر <b>الجودة الدنيا</b>:")
    elif action == "score":
        d["min_score"] = int(arg)
        d["stage"] = "quota"
        await _plan_reply_current(q, uid, context, bot, d, f"⭐ الجودة الدنيا: <b>{arg}</b>\nالخطوة: اختر <b>حد الإشارات اليومي</b>:")
    elif action == "quota":
        d["quota"] = int(arg)
        d["stage"] = "analyses"
        await _plan_reply_current(q, uid, context, bot, d, f"📡 حد الإشارات: <b>{arg}/يوم</b>\nالخطوة: اختر <b>حد التحليلات اليومي</b>:")
    elif action == "anal":
        d["analyses"] = int(arg)
        d["stage"] = "markets"
        await _plan_reply_current(q, uid, context, bot, d, f"🔬 حد التحليلات: <b>{arg}/يوم</b>\nالخطوة: اختر <b>الأسواق</b> (اضغط للتبديل):")
    elif action == "mkt":
        s = arg
        d["markets"] = (d["markets"] - [s]) if s in d["markets"] else (d["markets"] + [s])
        await _plan_reply_current(q, uid, context, bot, d, f"🌐 الأسواق المختارة: <b>{', '.join(d['markets']) or '—'}</b>")
    elif action == "stage" and arg == "timeframes":
        d["stage"] = "timeframes"
        await _plan_reply_current(q, uid, context, bot, d, "⏱ اختر <b>الأطر الزمنية</b> (اضغط للتبديل):")
    elif action == "tf":
        t = arg
        d["timeframes"] = (d["timeframes"] - [t]) if t in d["timeframes"] else (d["timeframes"] + [t])
        await _plan_reply_current(q, uid, context, bot, d, f"⏱ الأطر المختارة: <b>{', '.join(d['timeframes']) or '—'}</b>")
    elif action == "stage" and arg == "strategies":
        d["stage"] = "strategies"
        await _plan_reply_current(q, uid, context, bot, d, "🧠 اختر <b>الاستراتيجيات</b> (اضغط للتبديل):")
    elif action == "strat":
        s = arg
        d["strategies"] = (d["strategies"] - [s]) if s in d["strategies"] else (d["strategies"] + [s])
        await _plan_reply_current(q, uid, context, bot, d, f"🧠 الاستراتيجيات: <b>{', '.join(d['strategies']) or '—'}</b>")
    elif action == "confirm":
        await afn.run(plan_persist, d)
        await afn.run(audit_log.log, uid, f"plan_created:{d['code']}")
        code = d["code"]
        context.user_data.pop("plan_draft", None)
        await render(q, f"✅ <b>حُفظت الباقة {esc(code)} بنجاح</b>", kb.admin_nav([[kb.btn("💠 عرض الباقات", "a:plans")]]), bot)
    elif action == "cancel":
        context.user_data.pop("plan_draft", None)
        await render(q, "🗑 أُلغي إنشاء الباقة.", kb.admin_nav([[kb.btn("💠 الباقات", "a:plans")]]), bot)
    elif action == "custom":
        # v6.10.0 — the ✏️ custom-value buttons (days/score/quota/analyses)
        context.user_data["awaiting_plan_custom"] = arg
        labels = {"days": "المدة بالأيام (0 = دائم)", "score": "الجودة الدنيا (40-100)",
                  "quota": "حد الإشارات اليومي", "analyses": "حد التحليلات اليومي"}
        await render(q, f"✏️ أرسل <b>{labels.get(arg, 'القيمة')}</b> رقماً فقط:",
                     kb.admin_nav([[kb.btn("🗑 إلغاء", f"a:plancancel")]]), bot)


async def admin_plan_edit(q, uid, context, bot, code: str):
    r = await afn.run(_plan_row_any, code)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    p = dict(r)
    markets = [x.strip() for x in (p["markets"] or "").split(",") if x.strip()]
    tfs = [x.strip() for x in (p["timeframes"] or "").split(",") if x.strip()]
    strats = [x.strip() for x in (p["strategies"] or "").split(",") if x.strip()]
    all_mkts, all_strats = await afn.run(_plan_edit_catalog)
    all_tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
    mkt_row = " ".join(("✅" + m) if m in markets else ("▫️" + m) for m in all_mkts)
    tf_row = " ".join(("✅" + t) if t in tfs else ("▫️" + t) for t in all_tfs)
    st_row = " ".join(("✅" + s) if s in strats else ("▫️" + s) for s in all_strats)
    # v6.10.0 (owner report 4) — DIRECT-ENTRY editor: the old +/- stepper rows
    # are retired («أراه سيء للغاية وليس فاخر»). One tap opens a single-number
    # prompt; any value lands instantly. Blocks stay as fast chip rows.
    dur_val = int(p["duration_days"] or 0)
    dur_label = ("♾️ مفتوحة دائماً" if dur_val == 0 else f"{dur_val} يوم") if code == "FREE" else (
        "دائم" if dur_val == 0 else f"{dur_val} يوم"
    )
    rows_kb = [
        [kb.btn(f"💰 السعر: {p['price']:g} {p['currency']}", f"a:planfield:{code}:price", style="primary"),
         kb.btn(f"📅 {'مدة التجربة' if code == 'FREE' else 'المدة'}: {dur_label}", f"a:planfield:{code}:days", style="primary")],
    ]
    if code == "FREE":
        # v6.22.0: 1-tap FREE Trial Duration presets right inside Admin -> FREE Plan
        rows_kb.append([
            kb.btn(
                (("✅ " if dur_val == d else "") + ("♾️ مفتوحة" if d == 0 else f"{d} يوم")),
                f"a:plantrial:FREE:{d}",
                style="success" if dur_val == d else "primary",
            )
            for d in (3, 7, 14, 30, 0)
        ])
    rows_kb.extend([
        [kb.btn(f"📡 الإشارات: {p['max_signals_per_day']}/يوم", f"a:planfield:{code}:quota", style="primary"),
         kb.btn(f"🔬 التحليلات: {p['max_analyses_per_day'] or 0}/يوم", f"a:planfield:{code}:anal", style="primary")],
        [kb.btn(f"⭐ الجودة: {p['min_score']}/100", f"a:planfield:{code}:score", style="primary")],
    ])
    # Feature toggles — what THIS plan may use (markets / timeframes / strategies)
    free_note = (
        "\n⏳ <b>ضبط الفترة المجانية لكل مستخدم:</b> اختر مدة جاهزة بالأسفل (3 / 7 / 14 / 30 يوم أو ♾️ مفتوحة دائماً) "
        "أو اضغط «📅 مدة التجربة» لإدخال أي عدد أيام مخصص — تنتهي تلقائياً لكل مستخدم بعد مضي المدة من انضمامه ويُعرض له عرض ترقية فاخر.\n"
        if code == "FREE" else "\n"
    )
    body = (render_plan_card(p) +
            f"\n\n🌐 <b>الأسواق المسموحة:</b>\n<blockquote>{esc(mkt_row)}</blockquote>" +
            f"⏱ <b>الأطر الزمنية المسموحة:</b>\n<blockquote>{esc(tf_row)}</blockquote>" +
            f"🧠 <b>الاستراتيجيات المسموحة:</b>\n<blockquote>{esc(st_row)}</blockquote>" +
            free_note +
            "<i>اضغط أي قيمة بالأسفل لكتابة القيمة الجديدة مباشرة (سعر/مدة/حدود/جودة)، "
            "واضغط صفوف الأسواق والأطر والاستراتيجيات للتبديل الفوري — كل شيء سهل جداً وبضغطة واحدة.</i>")
    rows_kb.append([kb.btn("🌐 الأسواق ⬇️", f"a:planfeatpage:{code}:markets", style="primary"),
                    kb.btn("⏱ الأطر ⬇️", f"a:planfeatpage:{code}:timeframes", style="primary")])
    rows_kb.append([kb.btn("🧠 الاستراتيجيات ⬇️", f"a:planfeatpage:{code}:strategies", style="primary")])
    rows_kb.append([kb.btn(f"{'🟢 مفعلة' if p['enabled'] else '⚪ معطلة'} · تفعيل/تعطيل", f"a:toggleplan:{code}",
                           style="success" if p["enabled"] else "danger")])
    rows_kb.append([kb.btn("🗑 حذف الباقة", f"a:plandel:{code}", style="danger") if code not in ("FREE",)
                    else kb.btn("🔒 الباقة المجانية محمية", "a:plannoop", style="primary")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_plan_days_preset(q, uid, context, bot, code: str, days: int):
    """v6.22.0: 1-tap duration preset (e.g. FREE trial 3/7/14/30 days or 0=forever)."""
    n = max(0, min(3650, int(days)))
    await afn.run(_plan_field_update, code, "duration_days", n)
    await afn.run(subs.invalidate_plans_cache)
    await afn.run(audit_log.log, uid, f"plan_days_preset:{code}:{n}")
    await admin_plan_edit(q, uid, context, bot, code)


PLAN_FIELD_LABELS = {"price": "السعر (رقم، مثال 19 أو 19.5)",
                     "days": "المدة بالأيام (0 = دائم)",
                     "quota": "حد الإشارات اليومي (عدد)",
                     "anal": "حد التحليلات اليومي (عدد، 0 = بدون)",
                     "score": "الجودة الدنيا (40-100)"}


def plan_field_apply(code: str, field: str, raw: str) -> str:
    """v6.10.0 — one-value direct edit for plan fields (validated server-side).
    Returns the confirmation message; raises ValueError with the exact reason."""
    if field not in PLAN_FIELD_LABELS:
        raise ValueError("حقل غير معروف")
    raw = (raw or "").strip().replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        raise ValueError("أرسل رقماً فقط — " + PLAN_FIELD_LABELS[field])
    with db.conn() as c:
        r = c.execute("SELECT 1 FROM plans WHERE code=?", (code,)).fetchone()
        if not r:
            raise ValueError("الباقة غير موجودة")
        if field == "price":
            if not (0 <= val <= 100000):
                raise ValueError("السعر بين 0 و 100000")
            c.execute("UPDATE plans SET price=? WHERE code=?", (val, code))
            subs.invalidate_plans_cache()
            return f"حُفظ سعر {code}: {val:g} {c.execute('SELECT currency FROM plans WHERE code=?', (code,)).fetchone()['currency']}"
        if field == "days":
            n = int(val)
            if not (0 <= n <= 3650):
                raise ValueError("المدة بين 0 و 3650 يوماً (0 = دائم)")
            c.execute("UPDATE plans SET duration_days=? WHERE code=?", (n, code))
            subs.invalidate_plans_cache()
            return f"حُفظت مدة {code}: {'دائم' if n == 0 else f'{n} يوم'}"
        if field == "quota":
            n = int(val)
            if not (1 <= n <= 1000):
                raise ValueError("حد الإشارات بين 1 و 1000")
            c.execute("UPDATE plans SET max_signals_per_day=? WHERE code=?", (n, code))
            if code.upper() == "FREE":
                db.set_setting("free_daily_signals", str(n))
            subs.invalidate_plans_cache()
            return f"حُفظ حد إشارات {code}: {n}/يوم"
        if field == "anal":
            n = int(val)
            if not (0 <= n <= 1000):
                raise ValueError("حد التحليلات بين 0 و 1000")
            c.execute("UPDATE plans SET max_analyses_per_day=? WHERE code=?", (n, code))
            subs.invalidate_plans_cache()
            return f"حُفظ حد تحليلات {code}: {n}/يوم" + (" (بدون تحليلات)" if n == 0 else "")
        if field == "score":
            n = int(val)
            if not (40 <= n <= 100):
                raise ValueError("الجودة الدنيا بين 40 و 100")
            c.execute("UPDATE plans SET min_score=? WHERE code=?", (n, code))
            subs.invalidate_plans_cache()
            return f"حُفظت الجودة الدنيا لـ {code}: {n}/100"
    raise ValueError("حقل غير معروف")


async def admin_plan_field(q, uid, context, bot, code: str, field: str):
    context.user_data["awaiting_plan_field"] = f"{code}:{field}"
    await render(q, f"✏️ أرسل <b>{PLAN_FIELD_LABELS.get(field, 'القيمة')}</b> رقماً فقط "
                    f"— يُحفظ فوراً في الباقة <b>{esc(code)}</b>:",
                 kb.admin_nav([[kb.btn("↩️ الباقة", f"a:planedit:{code}")]]), bot)


async def admin_plan_feat_page(q, uid, context, bot, code: str, kind: str):
    """Toggle grid for one plan feature kind (markets / timeframes / strategies).
    v6.8.1 — strategies show the FULL world catalog (not only verified), in the
    guaranteed baseline order; markets group by royal category. The render()
    edit-in-place keeps every toggle on ONE page surface."""
    r = await afn.run(_plan_row_any, code)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    p = dict(r)
    current = [x.strip() for x in (p.get("markets") if kind == "markets" else
                                    p.get("timeframes") if kind == "timeframes" else
                                    p.get("strategies") or "").split(",") if x.strip()]
    if kind == "markets":
        from fysb.handlers.admin_trading import CATEGORY_ORDER
        recs = await afn.run(_market_category_pairs)
        by_cat: dict[str, list] = {}
        for rec in recs:
            by_cat.setdefault(rec.get("category") or "forex", []).append(rec["symbol"])
        items = [s for cat in [c for c in CATEGORY_ORDER if c in by_cat] + [c for c in by_cat if c not in CATEGORY_ORDER]
                 for s in sorted(by_cat[cat])]
        title, icon = "الأسواق", "🌐"
    elif kind == "timeframes":
        items = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        title, icon = "الأطر الزمنية", "⏱"
    else:
        recs = await afn.run(_strategy_light_rows)
        from fysb.services import strategy_stats
        items = await afn.run(strategy_stats.ordered_codes, [x["code"] for x in recs])
        verified = {x["code"] for x in recs if x["verified"]}
        prefix = {c: ("✳️ " if c in verified else "🔒 ") for c in items}
        title, icon = "الاستراتيجيات (الموثقة ✳️ والمغلقة 🔒)", "🧠"
    if kind == "strategies":
        # compact code buttons — the governance board carries the full names;
        # callback_data always carries the RAW code (never the status prefix)
        rows = [[kb.btn(("✅ " if it in current else "▫️ ") + prefix.get(it, "") + it,
                        f"a:planfeat:{code}:{kind}:{it}") for it in items[i:i + 2]]
                for i in range(0, len(items), 2)]
    else:
        rows = [[kb.btn(("✅ " if it in current else "▫️ ") + it,
                        f"a:planfeat:{code}:{kind}:{it}") for it in items[i:i + 3]]
                for i in range(0, len(items), 3)]
    rows.append([kb.btn("↩️ محرر الباقة", f"a:planedit:{code}")])
    note = f"{icon} <b>تعديل {title} للباقة {esc(p['title'])}</b>\nاضغط للتبديل — التغيير فوري ومحفوظ، والصفحة تُحدَّث في مكانها:"
    await render(q, note, kb.admin_nav(rows), bot)


async def admin_plan_feat_toggle(q, uid, context, bot, code: str, kind: str, item: str):
    r = await afn.run(_plan_feature_cols, code)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    col = {"markets": "markets", "timeframes": "timeframes", "strategies": "strategies"}.get(kind)
    if not col:
        await render(q, "نوع غير صالح.", kb.admin_nav(), bot)
        return
    current = [x.strip() for x in (r[col] or "").split(",") if x.strip()]
    if item.upper() in [x.upper() for x in current]:
        current = [x for x in current if x.upper() != item.upper()]
    else:
        current.append(item)
    await afn.run(_plan_feature_apply, code, col, ",".join(current))
    await afn.run(audit_log.log, uid, f"plan_feature:{code}:{kind}:{item}:{'on' if item in current else 'off'}")
    await admin_plan_feat_page(q, uid, context, bot, code, kind)


async def admin_plan_adjust(q, uid, context, bot, code: str, field: str, delta: float):
    fields = {"price": ("price", 0.0, 1.0), "days": ("duration_days", 0, 5),
              "quota": ("max_signals_per_day", 1, 1), "anal": ("max_analyses_per_day", 0, 1),
              "score": ("min_score", 40, 5)}
    if field not in fields:
        await render(q, "حقل غير صالح.", kb.admin_nav(), bot)
        return
    col, minimum, step = fields[field]
    r = await afn.run(_plan_field_value, code, col)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    value = max(minimum, (r["value"] or 0) + delta * step)
    if field == "score":
        value = min(100, value)
    await afn.run(_plan_field_update, code, col, value)
    await afn.run(audit_log.log, uid, f"plan_adjust:{code}:{field}:{value:g}")
    await admin_plan_edit(q, uid, context, bot, code)


async def admin_plan_toggle(q, uid, context, bot, code: str):
    r = await afn.run(_plan_enabled_row, code)
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    new = 0 if r["enabled"] else 1
    await afn.run(_plan_enabled_set, code, new)
    await afn.run(audit_log.log, uid, f"plan_toggle:{code}:{new}")
    await admin_plan_edit(q, uid, context, bot, code)


async def admin_plan_delete(q, uid, context, bot, code: str):
    if code == "FREE":
        await render(q, "⛔ الباقة المجانية محمية ولا تُحذف.", kb.admin_nav(), bot)
        return
    active = await afn.run(_plan_active_subs_count, code)
    if active:
        await render(q, f"⛔ لا يمكن الحذف: <b>{active}</b> اشتراك نشط مرتبط بالباقة. أوقفها بدلاً من حذفها.", kb.admin_nav(), bot)
        return
    await afn.run(_plan_delete_row, code)
    await afn.run(audit_log.log, uid, f"plan_deleted:{code}")
    await admin_plans(q, uid, context, bot)


async def admin_noop(q, uid, context, bot):
    await render(q, "استخدم أزرار الصفحة للتنقل.", kb.admin_nav(), bot)


# --- Payments ------------------------------------------------------------------
async def admin_payments(q, uid, context, bot, status: str = "PENDING"):
    rows = await afn.run(payments_service.list_requests, status if status != "ALL" else None)
    stats = await afn.run(payments_service.stats)
    rows_kb = []
    for r in rows[:10]:
        rows_kb.append([kb.btn(f"🧾 #{r['id']} · {esc(r['plan_code'])} · {r['amount']:g} · {esc(r['status'])}", f"a:paymentdetail:{r['id']}")])
    filter_row = [kb.btn("المعلقة", "a:payments"), kb.btn("المقبولة", "a:paymentsstatus:APPROVED"),
                  kb.btn("المرفوضة", "a:paymentsstatus:REJECTED"), kb.btn("الكل", "a:paymentsstatus:ALL")]
    body = [f"💳 <b><u>مركز المدفوعات</u></b>",
            f"▎معلقة: <b>{stats['pending']}</b> · مقبولة: <b>{stats['approved']}</b> · مرفوضة: <b>{stats['rejected']}</b>",
            f"▎إجمالي المقبول: <b>{stats['approved_amount']:.2f} USD</b>", "",
            "حدد تصفية ثم افتح الطلب للموافقة أو الرفض:"]
    await render(q, "\n".join(body), kb.admin_nav(rows_kb + [filter_row]), bot)


async def admin_payment_detail(q, uid, context, bot, pid: int):
    p = await afn.run(payments_service.get_request, pid)
    if not p:
        await render(q, "الطلب غير موجود.", kb.admin_nav(), bot)
        return
    plan = await afn.run(_plan_title_row, p["plan_code"])
    rows_kb = []
    if p["status"] == "PENDING":
        rows_kb.append([kb.btn("✅ قبول الطلب", f"a:approvepay:{pid}", style="success"),
                        kb.btn("❌ رفض الطلب", f"a:rejectpay:{pid}", style="danger")])
    if p["receipt_file_id"]:
        rows_kb.append([kb.btn("🖼 عرض الإيصال", f"a:viewreceipt:{pid}")])
    rows_kb.append([kb.btn("👤 ملف المستخدم", f"a:userdetail:{p['user_id']}"), kb.btn("↩️ المدفوعات", "a:payments")])
    await render(q, render_payment_card(p, plan["title"] if plan else ""), kb.admin_nav(rows_kb), bot)


async def admin_payment_approve(q, uid, context, bot, pid: int):
    res = await afn.run(payments_service.approve_request, pid, uid)
    if not res.get("ok"):
        await render(q, f"⛔ {esc(res.get('error', 'فشل'))}", kb.admin_nav(), bot)
        return
    p = res["payment"]
    await afn.run(audit_log.log, uid, f"payment_approved:{pid}")
    try:
        if str(p.get("plan_code") or "").upper() == "ACADEMY":
            await bot.send_message(
                p["user_id"],
                f"🎉 <b><u>تمت الموافقة على طلبك #{pid} وفتح المسار الاحترافي في أكاديمية ForexYemeni!</u></b>\n"
                "<blockquote>▎👑 وصولك الكامل لجميع الدروس الاحترافية والمرفقات والاختبارات والشهادة الملكية مفعّل الآن!</blockquote>",
                reply_markup=kb.user_nav([[kb.btn("🎓 دخول الأكاديمية الاحترافية الآن", "u:acadtrack:PAID", style="success")]]),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(p["user_id"],
                                   f"✅ تمت الموافقة على طلبك #{pid} وتفعيل باقة <b>{esc(p['plan_code'])}</b>.",
                                   parse_mode="HTML")
    except Exception:
        pass
    try:
        from fysb.services import partners as partners_svc
        await partners_svc.notify_commission_and_vip(bot, p["user_id"], p["plan_code"], res.get("commission"))
    except Exception:
        pass
    await admin_payment_detail(q, uid, context, bot, pid)


async def admin_payment_reject(q, uid, context, bot, pid: int):
    # v6.10.0 (owner report 3) — start the rejection flow CLEAN: any stale
    # one-shot flag (e.g. awaiting_usdt_wallet left from a wallet page the
    # admin never answered) used to shadow this text flow and answer with the
    # TRC20 validator instead of saving the rejection reason.
    clear_flows(context.user_data)
    context.user_data["reject_payment_id"] = pid
    await render(q, f"📝 أرسل <b>سبب رفض</b> الطلب #{pid} — سيصل للمستخدم كما تكتبه:", kb.admin_nav(), bot)


async def admin_view_receipt(q, uid, context, bot, pid: int):
    p = await afn.run(payments_service.get_request, pid)
    if not p or not p["receipt_file_id"]:
        await render(q, "لا يوجد إيصال مرفق.", kb.admin_nav(), bot)
        return
    await bot.send_photo(uid, p["receipt_file_id"], caption=f"🧾 إيصال الطلب #{pid}")


# --- Payment methods (guided presets incl. Yemeni wallets) ----------------------
async def admin_paymethods(q, uid, context, bot):
    methods = await afn.run(payments_service.list_methods)
    rows_kb = [[kb.btn(f"{'🟢' if m['enabled'] else '⚪'} {esc(m['title'])}{' · ' + esc(m['country']) if m['country'] else ''}",
                       f"a:pmview:{m['id']}")] for m in methods]
    wallet_row = await afn.run(_chain_wallet_marks)
    chains_ready = await afn.run(payments_service.configured_chains)
    body = (f"{ICON['bank']} <b><u>وسائل الدفع</u></b>\n"
            "▎أضف وسيلة دفع بتدفق موجّه: محافظ يمنية، عملات رقمية، حوالات — مع بيانات الحساب والتعليمات وربط الباقات.\n"
            f"▎⛓ محافظ USDT للتحقق البلوكيشن الفوري — {wallet_row}\n"
            "▎الشبكات الجاهزة: <b>" + (", ".join(chains_ready) or "—") + "</b>")
    await render(q, body, kb.admin_nav(rows_kb + [[kb.btn("➕ إضافة وسيلة دفع", "a:pmadd")],
                                                 [kb.btn("⛓ محافظ USDT — كل الشبكات", "a:usdtwallet")]]), bot)


async def admin_usdt_wallet(q, uid, context, bot, chain: str = "TRC20"):
    """v6.8.1 — MULTI-NETWORK USDT wallets for on-chain instant activation
    (TRC20 + BEP20 + ERC20 + Polygon — the owner asked for more networks).
    Stored in DB settings (no environment variables, per the owner's rule)."""
    from fysb.services.chain_verify import ALL_CHAINS, EVM_CHAINS
    chain = (chain or "TRC20").upper()
    if chain not in ALL_CHAINS:
        chain = "TRC20"
    labels = {"TRC20": "USDT TRC20 (Tron)",
              **{k: v["label"] for k, v in EVM_CHAINS.items()}}
    wallet = await afn.run(payments_service.chain_wallet, chain)
    context.user_data["awaiting_usdt_wallet"] = {"chain": chain}
    wallets = await afn.run(_all_chain_wallets, ALL_CHAINS)
    rows = []
    for ch in ALL_CHAINS:
        w = wallets[ch]
        mark = "🟢" if w else "⚪"
        rows.append([kb.btn(f"{mark} ضبط {labels[ch]}",
                            f"a:usdtwallet:{ch}" if ch != chain else "a:plannoop")])
    await render(q, "⛓ <b><u>محافظ USDT للتحقق البلوكيشن الفوري — كل الشبكات</u></b>\n"
                    "<blockquote>"
                    "▎المستخدم يدفع USDT على الشبكة التي يختارها ← يرسل TXID ← يُفحص البلوكيشن فعلياً\n"
                    "▎(التأكيد + العقد + العنوان الملكي + المبلغ) ← تُفتح الباقة فوراً\n"
                    "▎🔒 ويُحجب الـ TXID نهائياً من أي استخدام آخر\n"
                    f"▎الشبكات الجاهزة للمستخدمين الآن: <b>{', '.join(await afn.run(payments_service.configured_chains)) or '—'}</b>"
                    "</blockquote>\n\n"
                    f"⛓ <b>الشبكة الحالية: {labels[chain]}</b>\n"
                    + ("أرسل <b>عنوان المحفظة</b> الآن:\n"
                       + ("<i>عنوان Tron يبدأ بحرف T وطوله 34 خانة.</i>" if chain == "TRC20"
                          else "<i>عنوان EVM يبدأ بـ 0x وطوله 42 خانة.</i>") + "\n"
                       f"العنوان الحالي: <code>{esc(wallet) if wallet else 'غير مضبوط'}</code>\n"
                       "أرسل <code>-</code> لمسح العنوان."),
                 kb.admin_nav(rows + [[kb.btn("↩️ وسائل الدفع", "a:paymethods")]]), bot)


async def admin_pm_add(q, uid, context, bot):
    clear_flows(context.user_data)
    rows = []
    for cat_key, cat_title, items in PM_CATEGORIES:
        rows.append([kb.btn(cat_title, f"a:pmcat:{cat_key}")])
    rows.append([kb.btn("✏️ وسيلة مخصصة (نص حر)", "a:pmitem:CUSTOM")])
    await render(q, "🏦 <b><u>إضافة وسيلة دفع — اختر التصنيف</u></b>", kb.admin_nav(rows), bot)


async def admin_pm_cat(q, uid, context, bot, cat_key: str):
    meta = next((c for c in PM_CATEGORIES if c[0] == cat_key), None)
    if not meta:
        await render(q, "تصنيف غير معروف.", kb.admin_nav(), bot)
        return
    _, title, items = meta
    rows = [[kb.btn(f"{PM_PRESETS[k][0]}", f"a:pmitem:{k}")] for k in items]
    rows.append([kb.btn("↩️ التصنيفات", "a:pmadd")])
    await render(q, f"🏦 <b>{esc(title)}</b> — اختر وسيلة الدفع:", kb.admin_nav(rows), bot)


async def admin_pm_item(q, uid, context, bot, key: str):
    clear_flows(context.user_data)
    if key == "CUSTOM":
        context.user_data["pm_draft"] = {"step": "name", "title": "", "country": "عالمي", "linked": []}
        await render(q, "✏️ أرسل <b>اسم الوسيلة المخصصة</b> (مثال: سيف كاش):", kb.admin_nav([[kb.btn("🗑 إلغاء", "a:pmcancel")]]), bot)
        return
    meta = PM_PRESETS.get(key)
    if not meta:
        await render(q, "وسيلة غير معروفة.", kb.admin_nav(), bot)
        return
    title, country = meta
    # Country is auto-filled from the preset — the flow goes straight to the
    # account data step (no redundant "أرسل الدولة" question).
    context.user_data["pm_draft"] = {"step": "account", "title": title, "country": country,
                                     "linked": [], "preset": key}
    await render(q, f"🏦 <b>{esc(title)}</b> · 🌍 الدولة: <b>{esc(country)}</b>\n"
                    "✍️ أرسل <b>بيانات الحساب</b> مباشرة (رقم المحفظة / عنوان المحفظة / IBAN):",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:pmcancel")]]), bot)


async def admin_pm_toggle_plan(q, uid, context, bot, code: str):
    d = context.user_data.get("pm_draft")
    if not d:
        await render(q, "انتهت الجلسة — ابدأ من إضافة وسيلة.", kb.admin_nav(), bot)
        return
    d["linked"] = (d["linked"] - [code]) if code in d["linked"] else (d["linked"] + [code])
    from telegram import InlineKeyboardMarkup
    plans = await afn.run(_plan_code_title_rows)
    kb_rows = [[kb.btn(("✅ " if p["code"] in d["linked"] else "▫️ ") + p["title"],
                       f"a:pmplan:{p['code']}",
                       style="success" if p["code"] in d["linked"] else "primary")] for p in plans]
    kb_rows.append([kb.btn("💾 حفظ الوسيلة", "a:pmsave", style="success"),
                    kb.btn("🗑 إلغاء", "a:pmcancel", style="danger")])
    await render(q, f"💎 الباقات المرتبطة: <b>{', '.join(d['linked']) or 'الكل'}</b>",
                 InlineKeyboardMarkup(kb_rows), bot)


async def admin_pm_save(q, uid, context, bot):
    d = context.user_data.get("pm_draft")
    if not d or not d.get("title"):
        await render(q, "لا توجد وسيلة قيد الإنشاء.", kb.admin_nav(), bot)
        return
    mid = await afn.run(payments_service.add_method, d["title"], d.get("instructions", ""), d.get("account_ref", ""),
                        d.get("country", ""), ",".join(d.get("linked", [])))
    context.user_data.pop("pm_draft", None)
    await afn.run(audit_log.log, uid, f"pm_added:{mid}:{d['title'][:30]}")
    await render(q, f"✅ <b>حُفظت وسيلة الدفع «{esc(d['title'])}»</b> ومتاحة للمستخدمين الآن.",
                 kb.admin_nav([[kb.btn("🏦 وسائل الدفع", "a:paymethods")]]), bot)


async def admin_pm_cancel(q, uid, context, bot):
    context.user_data.pop("pm_draft", None)
    await render(q, "🗑 أُلغيت إضافة الوسيلة.", kb.admin_nav([[kb.btn("🏦 وسائل الدفع", "a:paymethods")]]), bot)


async def admin_pm_view(q, uid, context, bot, mid: int):
    m = await afn.run(payments_service.get_method, mid)
    if not m:
        await render(q, "الوسيلة غير موجودة.", kb.admin_nav(), bot)
        return
    rows_kb = [
        [kb.btn(f"{'🟢 إيقاف' if m['enabled'] else '⚪ تفعيل'}", f"a:pmtoggle:{mid}"),
         kb.btn("🗑 حذف", f"a:pmdel:{mid}")],
        [kb.btn("↩️ وسائل الدفع", "a:paymethods")],
    ]
    body = [f"🏦 <b>{esc(m['title'])}</b>",
            f"🌍 الدولة: {esc(m['country'] or '—')}",
            f"🔢 الحساب: <code>{esc(m['account_ref'] or '—')}</code>",
            f"📝 التعليمات: {esc(m['instructions'] or '—')}",
            f"💎 الباقات المرتبطة: <code>{esc(m['linked_plans'] or 'الكل')}</code>",
            f"📌 الحالة: {'🟢 مفعلة' if m['enabled'] else '⚪ معطلة'}"]
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_pm_toggle(q, uid, context, bot, mid: int):
    m = await afn.run(payments_service.get_method, mid)
    if not m:
        await render(q, "الوسيلة غير موجودة.", kb.admin_nav(), bot)
        return
    await afn.run(payments_service.update_method, mid, enabled=0 if m["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"pm_toggle:{mid}")
    await admin_pm_view(q, uid, context, bot, mid)


async def admin_pm_delete(q, uid, context, bot, mid: int):
    ok = await afn.run(payments_service.delete_method, mid)
    await afn.run(audit_log.log, uid, f"pm_delete:{mid}:{ok}")
    await admin_paymethods(q, uid, context, bot)


# --- v6.11.0 thread-offload helpers ---
def _user_delete_counts(target: int) -> dict:
    """(v6.11.0 thread-offload helper) per-table wipe counts for one user."""
    with db.conn() as c:
        n = lambda t: c.execute(f"SELECT COUNT(*) n FROM {t} WHERE user_id=?", (target,)).fetchone()["n"]
        return {"subscriptions": n("subscriptions"), "payments": n("payments"),
                "deliveries": n("signal_deliveries"), "demo": n("demo_accounts"),
                "positions": n("demo_positions"), "trades": n("demo_trades"),
                "analyses": n("analyses"), "tickets": n("tickets"),
                "accounts": n("user_accounts")}


def _user_payments_rows(target: int) -> list[dict]:
    """(v6.11.0 thread-offload helper) last 10 payment requests of one user."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 10", (target,)).fetchall()]


def _delivery_lines(target: int) -> list[str]:
    """(v6.11.0 thread-offload helper) received-signals card lines — rows fetch,
    real sequential numbering and USD results all off the loop (v6.11.0)."""
    from fysb.engines.tracker import seq_of
    from fysb import instruments
    from fysb.theme import dir_badge, signal_status_ar
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT d.delivered_at, s.* FROM signal_deliveries d
               JOIN signals s ON s.id=d.signal_id
               WHERE d.user_id=? ORDER BY d.delivered_at DESC LIMIT 10""", (target,)).fetchall()]
    lines = [f"📡 <b><u>الإشارات التي استلمها المستخدم {target}</u></b>", ""]
    for r in rows:
        seq = seq_of(r["id"])
        d_emoji = "🟢" if r["direction"] == "BUY" else "🔴"
        res = ""
        if r["status"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"):
            usd = instruments.usd_move(r["symbol"], float(r["pips_result"] or 0), 0.01)
            res = f"\n   ▎📈 النتيجة المسجلة: <b>{float(r['pips_result'] or 0):+.1f} pips</b> · <b>{usd:+,.2f} $</b> بـ 0.01 لوت"
        lines.append(
            f"▎{d_emoji} <b>#{seq}</b> {esc(r['symbol'])} {dir_badge(r['direction'])} · ⏱ {r['timeframe']} · {signal_status_ar(r)}" + res)
    if not rows:
        lines.append("▎لا شيء بعد.")
    return lines


def _plan_code_title_rows() -> list:
    """(v6.11.0 thread-offload helper) plan picker rows ordered by price."""
    with db.conn() as c:
        return c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()


def _demo_account_stats(account_id: int):
    """(v6.11.0 thread-offload helper) open count + closed count/sum, ONE trip."""
    with db.conn() as c:
        open_n = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE account_id=? AND status='OPEN'", (account_id,)).fetchone()["n"]
        closed = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) s FROM demo_positions WHERE account_id=? AND status!='OPEN'", (account_id,)).fetchone()
    return open_n, closed


def _subs_page_rows():
    """(v6.11.0 thread-offload helper) active subscriptions + expired counter."""
    with db.conn() as c:
        active = c.execute("""SELECT s.*, u.full_name FROM subscriptions s LEFT JOIN users u ON u.user_id=s.user_id
                              WHERE s.status='ACTIVE' ORDER BY s.id DESC LIMIT 10""").fetchall()
        expired = c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='EXPIRED'").fetchone()["n"]
    return active, expired


def _plans_all_rows() -> list:
    """(v6.11.0 thread-offload helper) every plan row ordered by price."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans ORDER BY price").fetchall()


def _plan_market_choices() -> list[str]:
    """(v6.11.0 thread-offload helper) enabled+verified market symbols."""
    with db.conn() as c:
        return [r["symbol"] for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol").fetchall()]


def _plan_strategy_choices() -> list[str]:
    """(v6.11.0 thread-offload helper) implemented, enabled+verified strategy codes."""
    from fysb.engines.strategies import REGISTRY
    with db.conn() as c:
        strat_rows = c.execute(
            "SELECT code FROM strategy_catalog WHERE verified=1 AND enabled=1").fetchall()
    from fysb.services import strategy_stats
    return strategy_stats.ordered_codes(
        [r["code"] for r in strat_rows if REGISTRY.get(r["code"]) is not None])


def _plan_row_any(code: str):
    """(v6.11.0 thread-offload helper) one plan row by code (any state)."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans WHERE code=?", (code,)).fetchone()


def _plan_edit_catalog():
    """(v6.11.0 thread-offload helper) full market symbols + verified strategy codes."""
    with db.conn() as c:
        all_mkts = [row["symbol"] for row in c.execute("SELECT symbol FROM market_catalog ORDER BY symbol").fetchall()]
        all_strats = [row["code"] for row in c.execute("SELECT code FROM strategy_catalog WHERE verified=1 ORDER BY code").fetchall()]
    return all_mkts, all_strats


def _market_category_pairs() -> list[dict]:
    """(v6.11.0 thread-offload helper) symbol+category pairs for plan scope."""
    with db.conn() as c:
        return [dict(row) for row in c.execute("SELECT symbol, category FROM market_catalog").fetchall()]


def _strategy_light_rows() -> list[dict]:
    """(v6.11.0 thread-offload helper) code+verified pairs for plan scope."""
    with db.conn() as c:
        return [dict(row) for row in c.execute("SELECT code, verified FROM strategy_catalog").fetchall()]


def _plan_feature_cols(code: str):
    """(v6.11.0 thread-offload helper) current markets/timeframes/strategies CSVs."""
    with db.conn() as c:
        return c.execute("SELECT markets,timeframes,strategies FROM plans WHERE code=?", (code,)).fetchone()


def _plan_feature_apply(code: str, col: str, joined: str) -> None:
    """(v6.11.0 thread-offload helper) write one plan feature CSV column."""
    with db.conn() as c:
        c.execute(f"UPDATE plans SET {col}=? WHERE code=?", (joined, code))
    subs.invalidate_plans_cache()


def _plan_field_value(code: str, col: str):
    """(v6.11.0 thread-offload helper) read one plan column for adjustment."""
    with db.conn() as c:
        return c.execute(f"SELECT {col} value FROM plans WHERE code=?", (code,)).fetchone()


def _plan_field_update(code: str, col: str, value) -> None:
    """(v6.11.0 thread-offload helper) write one plan column after adjustment."""
    with db.conn() as c:
        c.execute(f"UPDATE plans SET {col}=? WHERE code=?", (value, code))
    subs.invalidate_plans_cache()


def _plan_enabled_row(code: str):
    """(v6.11.0 thread-offload helper) current enabled flag of one plan."""
    with db.conn() as c:
        return c.execute("SELECT enabled FROM plans WHERE code=?", (code,)).fetchone()


def _plan_enabled_set(code: str, new: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one plan."""
    with db.conn() as c:
        c.execute("UPDATE plans SET enabled=? WHERE code=?", (new, code))
    subs.invalidate_plans_cache()


def _plan_active_subs_count(code: str) -> int:
    """(v6.11.0 thread-offload helper) active subscriptions bound to one plan."""
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM subscriptions WHERE plan_code=? AND status='ACTIVE'", (code,)).fetchone()["n"]


def _plan_delete_row(code: str) -> None:
    """(v6.11.0 thread-offload helper) erase one plan row."""
    with db.conn() as c:
        c.execute("DELETE FROM plans WHERE code=?", (code,))
    subs.invalidate_plans_cache()


def _plan_title_row(code: str):
    """(v6.11.0 thread-offload helper) plan title for payment cards."""
    with db.conn() as c:
        return c.execute("SELECT title FROM plans WHERE code=?", (code,)).fetchone()


def _chain_wallet_marks() -> str:
    """(v6.11.0 thread-offload helper) per-chain USDT wallet mark row."""
    return " · ".join(
        f"{ch}: {'✅' if payments_service.chain_wallet(ch) else '⚪'}"
        for ch in ("TRC20", "BEP20", "ERC20", "POLYGON"))


def _all_chain_wallets(chains) -> dict:
    """(v6.11.0 thread-offload helper) wallet presence for every chain, ONE trip."""
    return {ch: payments_service.chain_wallet(ch) for ch in chains}
