"""Admin core sections: Dashboard, Statistics, Users, Subscriptions, Plans,
Payments, Payment Methods — all real-DB, all server-guarded, luxury cards."""
from telegram.ext import ContextTypes

from fysb import config, db
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
    body = reports_service.dashboard_view()
    await render(q, body, kb.admin_nav([[kb.btn("📈 إحصائيات مفصلة", "a:statistics"), kb.btn("🖥 حالة النظام", "a:health")]]), bot)


async def admin_statistics(q, uid, context, bot):
    body = "\n".join([
        reports_service.users_report(), "",
        reports_service.signals_report(), "",
        reports_service.payments_report(), "",
        reports_service.subscriptions_report(), "",
        reports_service.analyses_report(),
    ])
    await render(q, body, kb.admin_nav(), bot)


# --- Users ---------------------------------------------------------------------
async def admin_users(q, uid, context, bot, page: int = 0):
    data = users_service.page_list(page)  # admin excluded inside the service
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
    u = users_service.get_user(target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    profile = users_service.profile_summary(target)
    rows_kb = [
        [kb.btn("⛔ حظر" if not u["is_blocked"] else "🔓 إلغاء الحظر", f"a:toggleuser:{target}"),
         kb.btn("🔔 تبديل الإشعارات", f"a:togglenotify:{target}")],
        [kb.btn("💳 مدفوعاته", f"a:userpayments:{target}"), kb.btn("📡 ما استلمه", f"a:userdeliveries:{target}")],
        [kb.btn("✏️ تعديل الاشتراك", f"a:subedit:{target}"), kb.btn("🧪 حسابه التجريبي", f"a:demoacct:{target}")],
        [kb.btn("↩️ دليل المستخدمين", "a:users")],
    ]
    await render(q, render_user_card(u, profile), kb.admin_nav(rows_kb), bot)


async def admin_toggle_user(q, uid, context, bot, target: int):
    if target == config.ADMIN_ID:
        await render(q, "⛔ لا يمكن حظر حساب المدير.", kb.admin_nav(), bot)
        return
    u = users_service.get_user(target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    users_service.set_blocked(target, not u["is_blocked"], uid)
    await admin_user_detail(q, uid, context, bot, target)


async def admin_toggle_notify(q, uid, context, bot, target: int):
    u = users_service.get_user(target)
    if not u:
        await render(q, "المستخدم غير موجود.", kb.admin_nav(), bot)
        return
    users_service.set_notifications(target, not u["notifications"])
    await admin_user_detail(q, uid, context, bot, target)


async def admin_user_payments(q, uid, context, bot, target: int):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 10", (target,)).fetchall()]
    rows_kb = [[kb.btn(f"🧾 #{r['id']} · {esc(r['plan_code'])} · {esc(r['status'])}", f"a:paymentdetail:{r['id']}")] for r in rows]
    if not rows_kb:
        rows_kb = [[kb.btn("لا توجد مدفوعات", "a:plannoop")]]
    rows_kb.append([kb.btn("↩️ الملف", f"a:userdetail:{target}")])
    await render(q, f"💳 <b>مدفوعات المستخدم {target}</b>", kb.admin_nav(rows_kb), bot)


async def admin_user_deliveries(q, uid, context, bot, target: int):
    with db.conn() as c:
        rows = c.execute("""SELECT d.delivered_at, s.id sid, s.symbol, s.direction, s.status, s.score
                            FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
                            WHERE d.user_id=? ORDER BY d.delivered_at DESC LIMIT 10""", (target,)).fetchall()
    lines = [f"📡 <b>الإشارات التي استلمها المستخدم {target}</b>"]
    for r in rows:
        lines.append(f"  #{r['sid']} {esc(r['symbol'])} {r['direction']} · {esc(r['status'])} · {human_dt(r['delivered_at'])}")
    if not rows:
        lines.append("  لا شيء بعد.")
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ الملف", f"a:userdetail:{target}")]]), bot)


async def admin_sub_edit(q, uid, context, bot, target: int):
    with db.conn() as c:
        plans = c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()
    profile = users_service.profile_summary(target)
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
    res = users_service.admin_edit_subscription(target, code, uid)
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
    await render(q, note, kb.admin_nav([[kb.btn("↩️ الملف", f"a:userdetail:{target}")]]), bot)


async def admin_sub_extend(q, uid, context, bot, target: int, days: int):
    profile = users_service.profile_summary(target)
    code = profile.get("plan_code", "FREE")
    res = subs.grant_subscription(target, code, source="ADMIN_EXTEND", actor=uid, override_days=days)
    if res.get("ok"):
        audit_log.log(uid, f"subscription_extended:{target}:{days}d")
        try:
            await bot.send_message(target, f"⏳ تم تمديد اشتراكك {days} يوماً من قبل الإدارة.")
        except Exception:
            pass
        await render(q, f"✅ تم تمديد الاشتراك {days} يوماً للمستخدم {target}.", kb.admin_nav(), bot)
    else:
        await render(q, "⛔ فشل التمديد.", kb.admin_nav(), bot)


async def admin_sub_cancel(q, uid, context, bot, target: int):
    ok = subs.cancel_subscription(target, uid)
    if ok:
        audit_log.log(uid, f"subscription_cancelled:{target}")
        try:
            await bot.send_message(target, "⛔ أوقفت الإدارة اشتراكك. تواصل مع الدعم للاستفسار.")
        except Exception:
            pass
    await render(q, "✅ أُوقف الاشتراك." if ok else "لا يوجد اشتراك نشط.", kb.admin_nav(), bot)


async def admin_demo_acct(q, uid, context, bot, target: int):
    from fysb.engines import demo as demo_engine
    acc = demo_engine.get_or_create_account(target)
    with db.conn() as c:
        open_n = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE account_id=? AND status='OPEN'", (acc["id"],)).fetchone()["n"]
        closed = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) s FROM demo_positions WHERE account_id=? AND status!='OPEN'", (acc["id"],)).fetchone()
    body = (f"🧪 <b>الحساب التجريبي للمستخدم {target}</b>\n\n"
            f"💰 الرصيد: <b>{acc['balance']:,.2f}</b> {esc(acc.get('currency', 'USD'))}\n"
            f"📉 أعلى Equity: {acc['peak_equity']:,.2f}\n"
            f"📂 مفتوحة: <b>{open_n}</b>\n"
            f"📜 مغلقة: <b>{closed['n']}</b> (صافي {closed['s']:+,.2f})")
    await render(q, body, kb.admin_nav([[kb.btn("🔄 تصفير حسابه", f"a:demoreset_admin:{target}")]]), bot)


async def admin_demo_reset(q, uid, context, bot, target: int):
    from fysb.engines import demo as demo_engine
    demo_engine.reset_account(target)
    audit_log.log(uid, f"demo_reset:{target}")
    await render(q, f"✅ تم تصفير الحساب التجريبي للمستخدم {target}.", kb.admin_nav(), bot)


# --- Subscriptions -------------------------------------------------------------
async def admin_subscriptions(q, uid, context, bot):
    with db.conn() as c:
        active = c.execute("""SELECT s.*, u.full_name FROM subscriptions s LEFT JOIN users u ON u.user_id=s.user_id
                              WHERE s.status='ACTIVE' ORDER BY s.id DESC LIMIT 10""").fetchall()
        expired = c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='EXPIRED'").fetchone()["n"]
    rows_kb = [[kb.btn(f"💎 #{r['id']} {esc(r['full_name'] or '')} {r['user_id']} · {esc(r['plan_code'])} · {human_dt(r['ends_at'])[:10] if r['ends_at'] else 'دائم'}",
                       f"a:userdetail:{r['user_id']}")] for r in active]
    await render(q, f"💎 <b><u>الاشتراكات النشطة (آخر 10)</u></b>\n▎منتهية/مستبدلة تاريخياً: <b>{expired}</b>\nاضغط للانتقال لملف المستخدم:",
                 kb.admin_nav(rows_kb), bot)


# --- Plans (guided creation — no pipe input) -----------------------------------
async def admin_plans(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM plans ORDER BY price").fetchall()
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
    await render(q, "👑 <b><u>إنشاء باقة جديدة — التدفق الموجّه</u></b>\n\n"
                    "الخطوة 1/3: أرسل <b>رمز الباقة</b> CODE (أحرف لاتينية كبيرة، مثال: <code>VIP</code>):",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:plancancel")]]), bot)


async def _plan_reply_current(q, uid, context, bot, d: dict, note: str):
    """Render the remaining guided steps as luxury buttons."""
    stage = d.get("stage", "days")
    rows = []
    if stage == "days":
        rows = [[kb.btn(f"📅 {n} يوم", f"a:plandays:{n}") for n in (7, 14, 30)],
                [kb.btn(f"📅 {n} يوم", f"a:plandays:{n}") for n in (90, 180, 365)],
                [kb.btn("♾ دائم (0 يوم)", "a:plandays:0")]]
    elif stage == "score":
        rows = [[kb.btn(f"⭐ {n}", f"a:planscore:{n}") for n in (50, 60, 70)],
                [kb.btn(f"⭐ {n}", f"a:planscore:{n}") for n in (80, 90, 95)]]
    elif stage == "quota":
        rows = [[kb.btn(f"📡 {n}/يوم", f"a:planquota:{n}") for n in (2, 5, 10)],
                [kb.btn(f"📡 {n}/يوم", f"a:planquota:{n}") for n in (20, 50, 100)]]
    elif stage == "analyses":
        rows = [[kb.btn(f"🔬 {n}/يوم", f"a:plananal:{n}") for n in (0, 3, 5)],
                [kb.btn(f"🔬 {n}/يوم", f"a:plananal:{n}") for n in (10, 30, 100)]]
    elif stage == "markets":
        with db.conn() as c:
            mkts = [r["symbol"] for r in c.execute("SELECT symbol FROM market_catalog ORDER BY symbol").fetchall()]
        rows = [[kb.btn(("✅ " if s in d["markets"] else "") + s, f"a:planmkt:{s}") for s in mkts[i:i + 3]]
                for i in range(0, len(mkts), 3)]
        rows.append([kb.btn("➡️ التالي: الأطر", "a:planstage:timeframes")])
    elif stage == "timeframes":
        tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        rows = [[kb.btn(("✅ " if t in d["timeframes"] else "") + t, f"a:plantf:{t}") for t in tfs[i:i + 4]]
                for i in range(0, len(tfs), 4)]
        rows.append([kb.btn("➡️ التالي: الاستراتيجيات", "a:planstage:strategies")])
    elif stage == "strategies":
        with db.conn() as c:
            strat_rows = c.execute("SELECT code, enabled FROM strategy_catalog WHERE verified=1 ORDER BY code").fetchall()
        rows = [[kb.btn(("✅ " if r["code"] in d["strategies"] else "") + r["code"], f"a:planstrat:{r['code']}")
                 for r in strat_rows[i:i + 3]] for i in range(0, len(strat_rows), 3)]
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
        plan_persist(d)
        audit_log.log(uid, f"plan_created:{d['code']}")
        code = d["code"]
        context.user_data.pop("plan_draft", None)
        await render(q, f"✅ <b>حُفظت الباقة <b>{esc(code)}</b> بنجاح</b>", kb.admin_nav([[kb.btn("💠 عرض الباقات", "a:plans")]]), bot)
    elif action == "cancel":
        context.user_data.pop("plan_draft", None)
        await render(q, "🗑 أُلغي إنشاء الباقة.", kb.admin_nav([[kb.btn("💠 الباقات", "a:plans")]]), bot)


async def admin_plan_edit(q, uid, context, bot, code: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM plans WHERE code=?", (code,)).fetchone()
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    p = dict(r)
    markets = [x.strip() for x in (p["markets"] or "").split(",") if x.strip()]
    tfs = [x.strip() for x in (p["timeframes"] or "").split(",") if x.strip()]
    strats = [x.strip() for x in (p["strategies"] or "").split(",") if x.strip()]
    with db.conn() as c:
        all_mkts = [row["symbol"] for row in c.execute("SELECT symbol FROM market_catalog ORDER BY symbol").fetchall()]
        all_strats = [row["code"] for row in c.execute("SELECT code FROM strategy_catalog WHERE verified=1 ORDER BY code").fetchall()]
    all_tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
    mkt_row = " ".join(("✅" + m) if m in markets else ("▫️" + m) for m in all_mkts)
    tf_row = " ".join(("✅" + t) if t in tfs else ("▫️" + t) for t in all_tfs)
    st_row = " ".join(("✅" + s) if s in strats else ("▫️" + s) for s in all_strats)
    rows_kb = [
        [kb.btn("➖ السعر", f"a:planadj:{code}:price:-1"), kb.btn(f"💰 {p['price']:g} {p['currency']}", "a:plannoop"), kb.btn("➕ السعر", f"a:planadj:{code}:price:1")],
        [kb.btn("➖ الأيام", f"a:planadj:{code}:days:-5"), kb.btn(f"📅 {p['duration_days']} يوم", "a:plannoop"), kb.btn("➕ الأيام", f"a:planadj:{code}:days:5")],
        [kb.btn("➖ الإشارات", f"a:planadj:{code}:quota:-1"), kb.btn(f"📡 {p['max_signals_per_day']}/يوم", "a:plannoop"), kb.btn("➕ الإشارات", f"a:planadj:{code}:quota:1")],
        [kb.btn("➖ التحليلات", f"a:planadj:{code}:anal:-1"), kb.btn(f"🔬 {p['max_analyses_per_day'] or 0}/يوم", "a:plannoop"), kb.btn("➕ التحليلات", f"a:planadj:{code}:anal:1")],
        [kb.btn("➖ الجودة", f"a:planadj:{code}:score:-5"), kb.btn(f"⭐ {p['min_score']}/100", "a:plannoop"), kb.btn("➕ الجودة", f"a:planadj:{code}:score:5")],
    ]
    # Feature toggles — what THIS plan may use (markets / timeframes / strategies)
    body = (render_plan_card(p) +
            f"\n\n🌐 <b>الأسواق المسموحة:</b>\n<blockquote>{esc(mkt_row)}</blockquote>" +
            f"⏱ <b>الأطر الزمنية المسموحة:</b>\n<blockquote>{esc(tf_row)}</blockquote>" +
            f"🧠 <b>الاستراتيجيات المسموحة:</b>\n<blockquote>{esc(st_row)}</blockquote>" +
            "\n<i>اضغط أي عنصر بالأسفل لتفعيله/إيقافه لهذه الباقة — التغيير يطبق فوراً على كل مستخدميها.</i>")
    rows_kb.append([kb.btn("🌐 الأسواق ⬇️", f"a:planfeatpage:{code}:markets")])
    rows_kb.append([kb.btn("⏱ الأطر ⬇️", f"a:planfeatpage:{code}:timeframes")])
    rows_kb.append([kb.btn("🧠 الاستراتيجيات ⬇️", f"a:planfeatpage:{code}:strategies")])
    rows_kb.append([kb.btn(f"{'🟢 مفعلة' if p['enabled'] else '⚪ معطلة'} · تفعيل/تعطيل", f"a:toggleplan:{code}")])
    rows_kb.append([kb.btn("🗑 حذف الباقة", f"a:plandel:{code}") if code not in ("FREE",) else kb.btn("🔒 الباقة المجانية محمية", "a:plannoop")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_plan_feat_page(q, uid, context, bot, code: str, kind: str):
    """Toggle grid for one plan feature kind (markets / timeframes / strategies)."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM plans WHERE code=?", (code,)).fetchone()
    if not r:
        await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
        return
    p = dict(r)
    current = [x.strip() for x in (p.get("markets") if kind == "markets" else
                                    p.get("timeframes") if kind == "timeframes" else
                                    p.get("strategies") or "").split(",") if x.strip()]
    if kind == "markets":
        with db.conn() as c:
            items = [row["symbol"] for row in c.execute("SELECT symbol FROM market_catalog ORDER BY symbol").fetchall()]
        title, icon = "الأسواق", "🌐"
    elif kind == "timeframes":
        items = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        title, icon = "الأطر الزمنية", "⏱"
    else:
        with db.conn() as c:
            items = [row["code"] for row in c.execute("SELECT code FROM strategy_catalog WHERE verified=1 ORDER BY code").fetchall()]
        title, icon = "الاستراتيجيات", "🧠"
    from fysb.theme import strategy_name
    rows = [[kb.btn(("✅ " if it in current else "▫️ ") +
                    (strategy_name(it) if kind == "strategies" else it),
                    f"a:planfeat:{code}:{kind}:{it}") for it in items[i:i + 3]]
            for i in range(0, len(items), 3)]
    rows.append([kb.btn("↩️ محرر الباقة", f"a:planedit:{code}")])
    note = f"{'🌐' if kind == 'markets' else '⏱' if kind == 'timeframes' else '🧠'} <b>تعديل {title} للباقة {esc(p['title'])}</b>\nاضغط للتبديل — التغيير فوري ومحفوظ:"
    await render(q, note, kb.admin_nav(rows), bot)


async def admin_plan_feat_toggle(q, uid, context, bot, code: str, kind: str, item: str):
    with db.conn() as c:
        r = c.execute("SELECT markets,timeframes,strategies FROM plans WHERE code=?", (code,)).fetchone()
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
    with db.conn() as c:
        c.execute(f"UPDATE plans SET {col}=? WHERE code=?", (",".join(current), code))
    audit_log.log(uid, f"plan_feature:{code}:{kind}:{item}:{'on' if item in current else 'off'}")
    await admin_plan_feat_page(q, uid, context, bot, code, kind)


async def admin_plan_adjust(q, uid, context, bot, code: str, field: str, delta: float):
    fields = {"price": ("price", 0.0, 1.0), "days": ("duration_days", 0, 5),
              "quota": ("max_signals_per_day", 1, 1), "anal": ("max_analyses_per_day", 0, 1),
              "score": ("min_score", 40, 5)}
    if field not in fields:
        await render(q, "حقل غير صالح.", kb.admin_nav(), bot)
        return
    col, minimum, step = fields[field]
    with db.conn() as c:
        r = c.execute(f"SELECT {col} value FROM plans WHERE code=?", (code,)).fetchone()
        if not r:
            await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
            return
        value = max(minimum, (r["value"] or 0) + delta * step)
        if field == "score":
            value = min(100, value)
        c.execute(f"UPDATE plans SET {col}=? WHERE code=?", (value, code))
    audit_log.log(uid, f"plan_adjust:{code}:{field}:{value:g}")
    await admin_plan_edit(q, uid, context, bot, code)


async def admin_plan_toggle(q, uid, context, bot, code: str):
    with db.conn() as c:
        r = c.execute("SELECT enabled FROM plans WHERE code=?", (code,)).fetchone()
        if not r:
            await render(q, "الباقة غير موجودة.", kb.admin_nav(), bot)
            return
        new = 0 if r["enabled"] else 1
        c.execute("UPDATE plans SET enabled=? WHERE code=?", (new, code))
    audit_log.log(uid, f"plan_toggle:{code}:{new}")
    await admin_plan_edit(q, uid, context, bot, code)


async def admin_plan_delete(q, uid, context, bot, code: str):
    if code == "FREE":
        await render(q, "⛔ الباقة المجانية محمية ولا تُحذف.", kb.admin_nav(), bot)
        return
    with db.conn() as c:
        active = c.execute("SELECT COUNT(*) n FROM subscriptions WHERE plan_code=? AND status='ACTIVE'", (code,)).fetchone()["n"]
    if active:
        await render(q, f"⛔ لا يمكن الحذف: <b>{active}</b> اشتراك نشط مرتبط بالباقة. أوقفها بدلاً من حذفها.", kb.admin_nav(), bot)
        return
    with db.conn() as c:
        c.execute("DELETE FROM plans WHERE code=?", (code,))
    audit_log.log(uid, f"plan_deleted:{code}")
    await admin_plans(q, uid, context, bot)


async def admin_noop(q, uid, context, bot):
    await render(q, "استخدم أزرار الصفحة للتنقل.", kb.admin_nav(), bot)


# --- Payments ------------------------------------------------------------------
async def admin_payments(q, uid, context, bot, status: str = "PENDING"):
    rows = payments_service.list_requests(status if status != "ALL" else None)
    stats = payments_service.stats()
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
    p = payments_service.get_request(pid)
    if not p:
        await render(q, "الطلب غير موجود.", kb.admin_nav(), bot)
        return
    with db.conn() as c:
        plan = c.execute("SELECT title FROM plans WHERE code=?", (p["plan_code"],)).fetchone()
    rows_kb = []
    if p["status"] == "PENDING":
        rows_kb.append([kb.btn("✅ قبول الطلب", f"a:approvepay:{pid}"), kb.btn("❌ رفض الطلب", f"a:rejectpay:{pid}")])
    if p["receipt_file_id"]:
        rows_kb.append([kb.btn("🖼 عرض الإيصال", f"a:viewreceipt:{pid}")])
    rows_kb.append([kb.btn("👤 ملف المستخدم", f"a:userdetail:{p['user_id']}"), kb.btn("↩️ المدفوعات", "a:payments")])
    await render(q, render_payment_card(p, plan["title"] if plan else ""), kb.admin_nav(rows_kb), bot)


async def admin_payment_approve(q, uid, context, bot, pid: int):
    res = payments_service.approve_request(pid, uid)
    if not res.get("ok"):
        await render(q, f"⛔ {esc(res.get('error', 'فشل'))}", kb.admin_nav(), bot)
        return
    p = res["payment"]
    audit_log.log(uid, f"payment_approved:{pid}")
    try:
        await bot.send_message(p["user_id"],
                               f"✅ تمت الموافقة على طلبك #{pid} وتفعيل باقة <b>{esc(p['plan_code'])}</b>.",
                               parse_mode="HTML")
    except Exception:
        pass
    await admin_payment_detail(q, uid, context, bot, pid)


async def admin_payment_reject(q, uid, context, bot, pid: int):
    context.user_data["reject_payment_id"] = pid
    await render(q, f"📝 أرسل <b>سبب رفض</b> الطلب #{pid} — سيصل للمستخدم كما تكتبه:", kb.admin_nav(), bot)


async def admin_view_receipt(q, uid, context, bot, pid: int):
    p = payments_service.get_request(pid)
    if not p or not p["receipt_file_id"]:
        await render(q, "لا يوجد إيصال مرفق.", kb.admin_nav(), bot)
        return
    await bot.send_photo(uid, p["receipt_file_id"], caption=f"🧾 إيصال الطلب #{pid}")


# --- Payment methods (guided presets incl. Yemeni wallets) ----------------------
async def admin_paymethods(q, uid, context, bot):
    methods = payments_service.list_methods()
    rows_kb = [[kb.btn(f"{'🟢' if m['enabled'] else '⚪'} {esc(m['title'])}{' · ' + esc(m['country']) if m['country'] else ''}",
                       f"a:pmview:{m['id']}")] for m in methods]
    body = (f"{ICON['bank']} <b><u>وسائل الدفع</u></b>\n"
            "▎أضف وسيلة دفع بتدفق موجّه: محافظ يمنية، عملات رقمية، حوالات — مع بيانات الحساب والتعليمات وربط الباقات.")
    await render(q, body, kb.admin_nav(rows_kb + [[kb.btn("➕ إضافة وسيلة دفع", "a:pmadd")]]), bot)


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
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    with db.conn() as c:
        plans = c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()
    kb_rows = [[InlineKeyboardButton(("✅ " if p["code"] in d["linked"] else "") + p["title"],
                                     callback_data=f"a:pmplan:{p['code']}")] for p in plans]
    kb_rows.append([InlineKeyboardButton("💾 حفظ الوسيلة", callback_data="a:pmsave"),
                    InlineKeyboardButton("🗑 إلغاء", callback_data="a:pmcancel")])
    await render(q, f"💎 الباقات المرتبطة: <b>{', '.join(d['linked']) or 'الكل'}</b>",
                 InlineKeyboardMarkup(kb_rows), bot)


async def admin_pm_save(q, uid, context, bot):
    d = context.user_data.get("pm_draft")
    if not d or not d.get("title"):
        await render(q, "لا توجد وسيلة قيد الإنشاء.", kb.admin_nav(), bot)
        return
    mid = payments_service.add_method(d["title"], d.get("instructions", ""), d.get("account_ref", ""),
                                      d.get("country", ""), ",".join(d.get("linked", [])))
    context.user_data.pop("pm_draft", None)
    audit_log.log(uid, f"pm_added:{mid}:{d['title'][:30]}")
    await render(q, f"✅ <b>حُفظت وسيلة الدفع «{esc(d['title'])}»</b> ومتاحة للمستخدمين الآن.",
                 kb.admin_nav([[kb.btn("🏦 وسائل الدفع", "a:paymethods")]]), bot)


async def admin_pm_cancel(q, uid, context, bot):
    context.user_data.pop("pm_draft", None)
    await render(q, "🗑 أُلغيت إضافة الوسيلة.", kb.admin_nav([[kb.btn("🏦 وسائل الدفع", "a:paymethods")]]), bot)


async def admin_pm_view(q, uid, context, bot, mid: int):
    m = payments_service.get_method(mid)
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
    m = payments_service.get_method(mid)
    if not m:
        await render(q, "الوسيلة غير موجودة.", kb.admin_nav(), bot)
        return
    payments_service.update_method(mid, enabled=0 if m["enabled"] else 1)
    audit_log.log(uid, f"pm_toggle:{mid}")
    await admin_pm_view(q, uid, context, bot, mid)


async def admin_pm_delete(q, uid, context, bot, mid: int):
    ok = payments_service.delete_method(mid)
    audit_log.log(uid, f"pm_delete:{mid}:{ok}")
    await admin_paymethods(q, uid, context, bot)
