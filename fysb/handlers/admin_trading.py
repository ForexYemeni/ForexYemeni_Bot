"""Admin trading sections: Signals, Engine, Strategies, Modes, Risk, Markets,
Timeframes, Brokers, Accounts, Demo, Live — everything wired to real logic."""
import asyncio
import json

from telegram.ext import ContextTypes

from fysb import config, db
from fysb.engines import market_data
from fysb.engines.strategies import VERIFIABLE
from fysb.handlers.common import render, safe
from fysb.handlers.inputs import BROKER_PRESETS, broker_test_connectivity, clear_flows
from fysb.handlers.user_pages import _safe_int
from fysb.services import audit as audit_log
from fysb.theme import ICON, esc, human_dt
from fysb.ui.cards import render_signal_card, render_signal_compact
from fysb.ui import keyboards as kb
from fysb.ui.cards import render_signal_card


# --- Signals management ----------------------------------------------------------
async def admin_signals(q, uid, context, bot, page: int = 0):
    size = 8
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
        rows = c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ? OFFSET ?",
                         (size, page * size)).fetchall()
    rows_kb = [[kb.btn(render_signal_compact(dict(r)), f"a:signalview:{r['id']}")] for r in rows]
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️", f"a:signalspage:{page - 1}"))
    if (page + 1) * size < total:
        nav_row.append(kb.btn("▶️", f"a:signalspage:{page + 1}"))
    if nav_row:
        rows_kb.append(nav_row)
    rows_kb.append([kb.btn("➕ نشر إشارة يدوية", "a:newsignal"), kb.btn("🔄 فحص مفتوحة الآن", "a:tracknow")])
    rows_kb.append([kb.btn("🧹 تنظيف الملغاة والمنتهية", "a:signalpurge")])
    await render(q, f"📡 <b>مركز إدارة الإشارات</b>\nالإجمالي: <b>{total}</b> · صفحة {page + 1}",
                 kb.admin_nav(rows_kb), bot)


async def admin_signal_view(q, uid, context, bot, sid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        deliveries = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE signal_id=?", (sid,)).fetchone()["n"]
    if not r:
        await render(q, "الإشارة غير موجودة.", kb.admin_nav(), bot)
        return
    sig = dict(r)
    live_note = ""
    if sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT"):
        try:
            quote = await asyncio.to_thread(market_data.fetch_price, sig["symbol"])
            price = float(quote["price"])
            from fysb import instruments
            entry = float(sig["entry"])
            drift = instruments.pips(sig["symbol"], entry, price)
            side = "أعلى الدخول" if price > entry else ("عند الدخول" if price == entry else "أدنى الدخول")
            live_note = (f"\n💰 <b>سعر السوق الآن:</b> <code>{instruments.fmt_price(sig['symbol'], price)}</code>"
                         f" · {side} · <b>{drift:+.1f} pips</b>")
            sl = float(sig["stop"])
            beyond = price >= sl if sig["direction"] == "SELL" else price <= sl
            if beyond:
                live_note += "\n⚠️ السعر تجاوز وقف الخسارة — اضغط «فحص مفتوحة الآن» لتحصيل الحالة فوراً."
        except Exception:
            live_note = "\n⚠️ تعذر جلب السعر اللحظي الآن (المزود غير متاح لحظياً)."
    rows_kb = []
    if sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT"):
        rows_kb.append([kb.btn("🚫 إلغاء الإشارة", f"a:cancelsignal:{sid}"), kb.btn("🔄 فحص الآن", "a:tracknow")])
    rows_kb.append([kb.btn("🗑 حذف نهائي للإشارة", f"a:delsignal:{sid}")])
    rows_kb.append([kb.btn("👥 المستلمون", f"a:signalrecipients:{sid}"), kb.btn("↩️ الإشارات", "a:signals")])
    body = render_signal_card(sig) + f"\n\n📬 استلمتها <b>{deliveries}</b> نقطة مستخدم.{live_note}"
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_delete_signal_confirm(q, uid, context, bot, sid: int):
    """Luxury double-confirmation before PERMANENT signal deletion."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        deliveries = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE signal_id=?", (sid,)).fetchone()["n"]
    if not r:
        await render(q, "الإشارة غير موجودة (ربما حُذفت سابقاً).", kb.admin_nav(), bot)
        return
    from fysb.theme import status_ar
    sig = dict(r)
    body = (f"🗑 <b>حذف نهائي للإشارة #{sid}</b>\n"
            f"<blockquote>▎🌐 {esc(sig['symbol'])} · {esc(sig['direction'])} · {esc(sig['timeframe'])}\n"
            f"▎📌 الحالة: <b>{status_ar(sig['status'])}</b> · الجودة {sig['score']}/100\n"
            f"▎📬 مستلمون مسجلون: <b>{deliveries}</b>\n"
            f"▎⚠️ سيُمحى السجل <b>بأكمله نهائياً</b> — الإشارة، التسليمات، كل شيء. لا أرشيف ولا رجوع.</blockquote>")
    rows_kb = [
        [kb.btn("⚠️ تأكيد الحذف النهائي", f"a:delsignalgo:{sid}")],
        [kb.btn("↩️ تراجع", f"a:signalview:{sid}")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_delete_signal_go(q, uid, context, bot, sid: int):
    """ERASE the signal row + its deliveries forever (audit-logged)."""
    with db.tx() as c:
        sig = c.execute("SELECT symbol, direction FROM signals WHERE id=?", (sid,)).fetchone()
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id=?", (sid,)).rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE id=?", (sid,)).rowcount)
    if not s:
        await render(q, "الإشارة غير موجودة (ربما حُذفت سابقاً).", kb.admin_nav(), bot)
        return
    audit_log.log(uid, f"signal_deleted_forever:{sid}")
    body = (f"✅ <b>تم حذف الإشارة #{sid} نهائياً</b>"
            + (f" ({esc(sig['symbol'])} {esc(sig['direction'])})" if sig else "")
            + f"\n<blockquote>▎🗑 سجل الإشارة: مُمحى\n▎📬 سجلات التسليم المُمحاة: <b>{d}</b>\n"
              f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>")
    await render(q, body, kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_signal_recipients(q, uid, context, bot, sid: int):
    with db.conn() as c:
        rows = c.execute("""SELECT d.user_id, d.delivered_at FROM signal_deliveries d
                            WHERE d.signal_id=? ORDER BY d.delivered_at DESC LIMIT 15""", (sid,)).fetchall()
    lines = [f"📬 <b>مستلمو الإشارة #{sid}</b>"]
    lines += [f"  <code>{r['user_id']}</code> · {human_dt(r['delivered_at'])}" for r in rows] or ["  لا يوجد مستلمون."]
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)


async def admin_signal_purge(q, uid, context, bot):
    """Bulk permanent deletion of CANCELLED/EXPIRED signals (double confirm)."""
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('CANCELLED','EXPIRED')").fetchone()["n"]
    if not n:
        await render(q, "✅ لا توجد إشارات ملغاة أو منتهية — السجل نظيف بالكامل.",
                     kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals")]]), bot)
        return
    await render(q, f"🧹 <b>تنظيف الإشارات الميتة</b>\n<blockquote>▎إشارات ملغاة/منتهية: <b>{n}</b>\n"
                    f"▎⚠️ حذف نهائي بلا أرشيف — يشمل سجلات التسليم المرتبطة.</blockquote>",
                 kb.admin_nav([[kb.btn(f"⚠️ حذف {n} إشارة نهائياً", "a:signalpurgego")],
                               [kb.btn("↩️ تراجع", "a:signals")]]), bot)


async def admin_signal_purge_go(q, uid, context, bot):
    with db.tx() as c:
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id IN "
                          "(SELECT id FROM signals WHERE status IN ('CANCELLED','EXPIRED'))").rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE status IN ('CANCELLED','EXPIRED')").rowcount)
    audit_log.log(uid, f"signals_purged:{s}")
    await render(q, f"✅ <b>تم التنظيف النهائي</b>\n<blockquote>▎🗑 إشارات مُمحاة: <b>{s}</b>\n▎📬 تسليمات مُمحاة: <b>{d}</b>\n"
                    f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>",
                 kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_cancel_signal(q, uid, context, bot, sid: int):
    with db.tx() as c:
        cur = c.execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=? AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                        (config.now(), sid))
    audit_log.log(uid, f"cancel_signal:{sid}")
    if cur.rowcount:
        from fysb.engines.tracker import TERMINAL
        from fysb.theme import dir_badge, esc
        with db.conn() as c:
            sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
        text = (f"<b>{esc(sig['symbol'])} {dir_badge(sig['direction'])}</b> — تحديث الإشارة #{sig['id']}\n"
                f"الحالة الجديدة: <b>CANCELLED</b> (قرار إداري)")
        with db.conn() as c:
            uids = [r["user_id"] for r in c.execute("SELECT user_id FROM signal_deliveries WHERE signal_id=?", (sid,))]
        for note_uid in uids + [config.ADMIN_ID]:
            try:
                await bot.send_message(note_uid, text, parse_mode="HTML")
            except Exception:
                pass
    await admin_signal_view(q, uid, context, bot, sid)


async def admin_new_signal(q, uid, context, bot):
    context.user_data["awaiting_signal"] = True
    await render(q, "📝 أرسل الإشارة بالتنسيق:\n<code>SYMBOL | BUY/SELL | TF | ENTRY | SL | TP1 | TP2 | TP3 | SCORE | STRATEGY | السبب</code>\n\n"
                    "يُفحص الاتجاه والنسبة (TP1 ≥ 2R) ومنع التكرار آلياً، ثم تُوجّه للمستخدمين المؤهلين ضمن حدود باقاتهم.",
                 kb.admin_nav(), bot)


async def admin_track_now(q, uid, context, bot):
    """Run one tracker pass immediately (admin action) with real provider data."""
    from fysb.engines import tracker
    await render(q, "🔄 جارٍ فحص الإشارات المفتوحة مقابل بيانات السوق اللحظية...", None)
    result = await asyncio.to_thread(tracker.check_open_signals, config.market_api_key())
    for note_uid, text in result["notifications"]:
        try:
            await bot.send_message(note_uid, text, parse_mode="HTML")
        except Exception:
            pass
    expired = tracker.expire_stale_signals()
    stats = result["stats"]
    await render(q, f"✅ <b>نتيجة الفحص</b>\nفُحصت: {stats['checked']} · حُدثت: <b>{stats['updated']}</b>\n"
                    f"تخطيت بسبب قدم البيانات: {stats['skipped_stale']} · أخطاء مزود: {stats['errors']} · انتهت صلاحيتها: {expired}",
                 kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals")]]), bot)


async def update_message(q, text):
    """Send a transient progress note (fresh message pattern)."""
    await render(q, text, None)


# --- Trading engine --------------------------------------------------------------
async def admin_engine(q, uid, context, bot):
    enabled = db.get_setting("engine_enabled", "0") == "1"
    interval = db.get_setting_int("engine_interval_seconds", 300)
    min_score = db.get_setting_int("engine_min_score", 80)
    with db.conn() as c:
        markets = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        runs = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT 10").fetchall()
    lines = ["🧠 <b>محرك السوق والتحليل</b>",
             f"📌 الحالة: <b>{'🟢 يعمل' if enabled else '⚪ متوقف'}</b>",
             f"⏱ فاصل الدورة: {interval} ثانية",
             f"⭐ الحد الأدنى للجودة: {min_score}/100",
             f"🌐 أسواق جاهزة (مفعلة+موثقة): <b>{markets}</b>",
             "",
             "<b>آخر الدورات:</b>"]
    for r in runs:
        lines.append(f"  {r['created_at'][11:16]} {esc(r['symbol'])} {esc(r['timeframe'])}: {esc(r['result'])}")
    rows_kb = [
        [kb.btn(f"{'⏹ إيقاف المحرك' if enabled else '▶️ تشغيل المحرك'}", "a:enginetoggle")],
        [kb.btn("➖ فاصل", f"a:engineadj:interval:-60"), kb.btn(f"⏱ {interval}s", "a:plannoop"), kb.btn("➕ فاصل", f"a:engineadj:interval:60")],
        [kb.btn("➖ جودة", f"a:engineadj:minscore:-5"), kb.btn(f"⭐ {min_score}", "a:plannoop"), kb.btn("➕ جودة", f"a:engineadj:minscore:5")],
        [kb.btn("🚀 دورة فورية الآن", "a:enginenow"), kb.btn("🧹 تنظيف السجل", "a:engineclean")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_engine_toggle(q, uid, context, bot):
    val = "0" if db.get_setting("engine_enabled", "0") == "1" else "1"
    db.set_setting("engine_enabled", val)
    audit_log.log(uid, f"engine:{val}")
    await admin_engine(q, uid, context, bot)


async def admin_engine_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"interval": ("engine_interval_seconds", 120, 3600), "minscore": ("engine_min_score", 50, 95)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    setting_key, lo, hi = bounds[key]
    cur = db.get_setting_int(setting_key, 300 if key == "interval" else 80)
    db.set_setting(setting_key, int(max(lo, min(hi, cur + delta))))
    audit_log.log(uid, f"engine_setting:{setting_key}:{db.get_setting(setting_key)}")
    await admin_engine(q, uid, context, bot)


async def admin_engine_now(q, uid, context, bot):
    from fysb.engines.signal_engine import generate_signal
    await render(q, "🚀 جارٍ تشغيل دورة فورية للمحرك على الأسواق المفعلة...", None)
    with db.conn() as c:
        markets = [dict(r) for r in c.execute("SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()]
    if not markets:
        await render(q, "⛔ لا توجد أسواق مفعلة وموثقة. فعّل سوقاً من قسم الأسواق أولاً (زر التحقق).", kb.admin_nav(), bot)
        return
    results = []
    for m in markets:
        res = await asyncio.to_thread(generate_signal, m["symbol"])
        results.append(f"{esc(m['symbol'])}: <b>{esc(res['status'])}</b>" + (f" ({esc(res.get('reason', ''))[:60]})" if res.get("reason") else ""))
    await render(q, "🚀 <b>نتيجة الدورة الفورية</b>\n" + "\n".join(results),
                 kb.admin_nav([[kb.btn("🧠 المحرك", "a:engine"), kb.btn("📡 الإشارات", "a:signals")]]), bot)


async def admin_engine_clean(q, uid, context, bot):
    with db.conn() as c:
        cur = c.execute("""DELETE FROM engine_runs WHERE id NOT IN
                           (SELECT id FROM engine_runs ORDER BY id DESC LIMIT 500)""")
    audit_log.log(uid, f"engine_runs_cleaned:{cur.rowcount}")
    await render(q, f"🧹 أُرشفت وأُزيلت {abs(cur.rowcount)} سجل دورة قديم (بقي آخر 500).", kb.admin_nav(), bot)


# --- Strategies ------------------------------------------------------------------
async def admin_strategies(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM strategy_catalog ORDER BY code").fetchall()
    lines = ["🧩 <b>حوكمة الاستراتيجيات</b>\n"]
    rows_kb = []
    for r in rows:
        status = "🟢" if (r["enabled"] and r["verified"]) else "⚪"
        verified = "موثقة ومختبرة" if r["verified"] else "غير مختبرة"
        lines.append(f"{status} <b>{esc(r['title'])}</b> · {verified}\n  {esc(r['description'])}")
        row = [kb.btn(f"{esc(r['code'])}: {'إيقاف' if (r['enabled'] and r['verified']) else 'تفعيل'}", f"a:strategy_toggle:{r['code']}")]
        if r["code"] in VERIFIABLE and not r["verified"]:
            row.append(kb.btn("✅ تحقق", f"a:strategy_verify:{r['code']}"))
        rows_kb.append(row)
    lines.append("\n<i>التحقق يُمنح فقط للاستراتيجيات المنفذة والمختبارة آلياً (ICT/SMC/PRICE_ACTION). الباقي يبقى مغلق.</i>")
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_strategy_toggle(q, uid, context, bot, code: str):
    with db.conn() as c:
        r = c.execute("SELECT enabled,verified FROM strategy_catalog WHERE code=?", (code,)).fetchone()
        if not r:
            await render(q, "الاستراتيجية غير موجودة.", kb.admin_nav(), bot)
            return
        if not r["verified"]:
            await render(q, "⛔ الاستراتيجية غير مختبرة؛ لا يمكن تفعيلها. استخدم زر التحقق إن كانت ضمن المنفذة.",
                         kb.admin_nav(), bot)
            return
        c.execute("UPDATE strategy_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (0 if r["enabled"] else 1, config.now(), code))
    audit_log.log(uid, f"strategy_toggle:{code}")
    await admin_strategies(q, uid, context, bot)


async def admin_strategy_verify(q, uid, context, bot, code: str):
    if code not in VERIFIABLE:
        await render(q, "⛔ هذه الاستراتيجية غير منفذة بعد؛ لا يمكن التحقق منها.", kb.admin_nav(), bot)
        return
    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET verified=1,enabled=1,updated_at=? WHERE code=?", (config.now(), code))
    audit_log.log(uid, f"strategy_verified:{code}")
    await admin_strategies(q, uid, context, bot)


# --- Operating modes -------------------------------------------------------------
async def admin_modes(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM mode_registry ORDER BY code").fetchall()
        current = db.get_setting("trading_mode", "SIGNALS_ONLY")
    lines = [f"⚙️ <b>أوضاع التشغيل</b>\nالوضع الحالي: <b>{esc(current)}</b>", ""]
    rows_kb = []
    for r in rows:
        state = "🟢" if r["enabled"] else "⚪"
        exec_state = "قابل للتنفيذ" if r["executable"] else "غير قابل للتنفيذ (مغلق)"
        lines.append(f"{state} <b>{esc(r['code'])} — {esc(r['title'])}</b> ({exec_state})\n  {esc(r['description'])}")
        if r["executable"]:
            rows_kb.append([kb.btn(f"تعيين {esc(r['code'])} كوضع حالي", f"a:mode_set:{r['code']}")])
        else:
            rows_kb.append([kb.btn(f"🔒 {esc(r['code'])} مغلق حتى توثيق محركه", "a:plannoop")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_mode_set(q, uid, context, bot, code: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM mode_registry WHERE code=?", (code,)).fetchone()
        if not r or not r["executable"] or not r["enabled"]:
            await render(q, "⛔ هذا الوضع غير قابل للتنفيذ في هذه النسخة.", kb.admin_nav(), bot)
            return
        if code == "C":
            # LIVE requires the explicit double-confirmation flow; never settable here.
            await render(q, "⛔ الوضع LIVE يُفعّل حصراً عبر قسم «التداول الحقيقي» بتأكيد مزدوج.", kb.admin_nav(), bot)
            return
        db.set_setting("trading_mode", r["code"])
    audit_log.log(uid, f"mode_set:{code}")
    await admin_modes(q, uid, context, bot)


# --- Risk management -------------------------------------------------------------
async def admin_risk(q, uid, context, bot):
    keys = [
        ("risk_per_signal_pct", "المخاطرة لكل إشارة", 0.1, 0.1),
        ("max_daily_risk_pct", "أقصى مخاطرة يومية", 0.5, 0.5),
        ("max_daily_loss_pct", "أقصى خسارة يومية", 0.5, 1.0),
        ("max_open_signals", "أقصى إشارات مفتوحة", 1, 1),
        ("min_rr", "أدنى عائد/مخاطرة", 0.1, 0.5),
        ("atr_pct_max", "أقصى تقلب ATR%", 0.5, 1.0),
    ]
    lines = ["🛡 <b>محرك إدارة المخاطر</b>",
             f"⛔ Kill Switch: <b>{'مفعل — يمنع أي إشارة جديدة' if db.kill_switch_on() else 'غير مفعل'}</b>",
             f"🌊 فلتر التقلب: <b>{'مفعل' if db.get_setting('volatility_filter','1')=='1' else 'متوقف'}</b>", "",
             "<b>الحدود (تطبق على السيرفر قبل أي إشارة):</b>"]
    rows_kb = []
    for key, title, step, _d in keys:
        val = db.get_setting_float(key, 0)
        lines.append(f"  {title}: <b>{val:g}</b>")
        rows_kb.append([kb.btn("➖", f"a:riskadj:{key}:-{step}"), kb.btn(f"{title} {val:g}", "a:plannoop"), kb.btn("➕", f"a:riskadj:{key}:{step}")])
    rows_kb.append([kb.btn("⛔ تبديل Kill Switch", "a:killswitch"), kb.btn(f"🌊 فلتر التقلب", "a:volfilter")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_risk_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"risk_per_signal_pct": (0.1, 5.0), "max_daily_risk_pct": (1.0, 20.0),
              "max_daily_loss_pct": (1.0, 30.0), "max_open_signals": (1, 20),
              "min_rr": (1.0, 10.0), "atr_pct_max": (1.0, 15.0)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    lo, hi = bounds[key]
    cur = db.get_setting_float(key, lo)
    db.set_setting(key, round(max(lo, min(hi, cur + delta)), 2))
    audit_log.log(uid, f"risk_setting:{key}:{db.get_setting(key)}")
    await admin_risk(q, uid, context, bot)


async def admin_kill_switch(q, uid, context, bot):
    val = "0" if db.kill_switch_on() else "1"
    db.set_setting("global_kill_switch", val)
    audit_log.log(uid, f"kill_switch:{val}")
    await admin_risk(q, uid, context, bot)


async def admin_vol_filter(q, uid, context, bot):
    val = "0" if db.get_setting("volatility_filter", "1") == "1" else "1"
    db.set_setting("volatility_filter", val)
    audit_log.log(uid, f"volatility_filter:{val}")
    await admin_risk(q, uid, context, bot)


# --- Markets ---------------------------------------------------------------------
async def admin_markets(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM market_catalog ORDER BY symbol").fetchall()
    lines = ["🌐 <b>إدارة الأسواق</b>\nالسوق الجديد يبقى مغلقاً حتى يثبت المزود بياناته فعلياً (زر تحقق).", ""]
    rows_kb = []
    for r in rows:
        state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵 موثق" if r["verified"] else "⚪ مغلق")
        lines.append(f"{state} <b>{esc(r['symbol'])}</b> · {esc(r['provider_symbol'])} · pip={r['pip_size']} · digits={r['digits']}")
        row = [kb.btn(f"{esc(r['symbol'])}: {'إيقاف' if r['enabled'] else 'تفعيل'}", f"a:market_toggle:{r['symbol']}")]
        if not r["verified"]:
            row.append(kb.btn("✅ تحقق من المزود", f"a:market_verify:{r['symbol']}"))
        rows_kb.append(row)
    rows_kb.append([kb.btn("➕ إضافة سوق", "a:market_add")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_market_toggle(q, uid, context, bot, symbol: str):
    with db.conn() as c:
        r = c.execute("SELECT enabled,verified FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
        if not r:
            await render(q, "السوق غير موجود.", kb.admin_nav(), bot)
            return
        if not r["verified"]:
            await render(q, "⛔ السوق غير موثق لدى المزود — لا يمكن تفعيله قبل التحقق.", kb.admin_nav(), bot)
            return
        c.execute("UPDATE market_catalog SET enabled=?,updated_at=? WHERE symbol=?",
                  (0 if r["enabled"] else 1, config.now(), symbol))
    audit_log.log(uid, f"market_toggle:{symbol}")
    await admin_markets(q, uid, context, bot)


async def admin_market_verify(q, uid, context, bot, symbol: str):
    """REAL provider verification: fetch a live candle through the data chain."""
    with db.conn() as c:
        r = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    if not r:
        await render(q, "السوق غير موجود.", kb.admin_nav(), bot)
        return
    await render(q, f"🔄 جارٍ اختبار <b>{esc(symbol)}</b> مع السوق اللحظي فعلياً...", None)
    try:
        payload = await asyncio.to_thread(market_data.fetch_candles, r["provider_symbol"], "15min", config.market_api_key(), 40, False)
        ok = True
        detail = f"استلم {len(payload['values'])} شمعة حية · آخر شمعة {payload['last_candle_iso']}"
    except RuntimeError as e:
        ok = False
        detail = str(e)
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET verified=?, updated_at=? WHERE symbol=?", (1 if ok else 0, config.now(), symbol))
    audit_log.log(uid, f"market_verify:{symbol}:{ok}")
    icon = "✅" if ok else "❌"
    await render(q, f"{icon} <b>نتيجة التحقق من {esc(symbol)}</b>\n{esc(detail)}\n\n"
                    f"<i>{'أصبح السوق موثقاً — يمكنك تفعيله الآن.' if ok else 'لم يوافق المزود؛ بقي السوق مغلقاً.'}</i>",
                 kb.admin_nav(), bot)


async def admin_market_add(q, uid, context, bot):
    context.user_data["awaiting_market_add"] = True
    await render(q, "➕ أرسل:\n<code>SYMBOL | PROVIDER_SYMBOL | الاسم | التصنيف</code>\nالتصنيف: forex / metal / crypto / index",
                 kb.admin_nav(), bot)


# --- Timeframes ------------------------------------------------------------------
async def admin_timeframes(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM timeframe_catalog ORDER BY code").fetchall()
    lines = ["⏱ <b>إدارة الأطر الزمنية</b>\nالإطار غير الموثق لا يُتاح للمستخدمين أو المحرك.", ""]
    rows_kb = []
    for r in rows:
        state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵 موثق" if r["verified"] else "⚪ مغلق")
        lines.append(f"{state} <b>{r['code']}</b> · {esc(r['provider_interval'])}")
        row = [kb.btn(f"{r['code']}: {'إيقاف' if r['enabled'] else 'تفعيل'}", f"a:tf_toggle:{r['code']}")]
        if not r["verified"]:
            row.append(kb.btn("✅ تحقق", f"a:tf_verify:{r['code']}"))
        rows_kb.append(row)
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_tf_toggle(q, uid, context, bot, code: str):
    with db.conn() as c:
        r = c.execute("SELECT enabled,verified FROM timeframe_catalog WHERE code=?", (code,)).fetchone()
        if not r:
            await render(q, "الإطار غير موجود.", kb.admin_nav(), bot)
            return
        if not r["verified"]:
            await render(q, "⛔ الإطار غير موثق لدى المزود.", kb.admin_nav(), bot)
            return
        c.execute("UPDATE timeframe_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (0 if r["enabled"] else 1, config.now(), code))
    audit_log.log(uid, f"timeframe_toggle:{code}")
    await admin_timeframes(q, uid, context, bot)


async def admin_tf_verify(q, uid, context, bot, code: str):
    from fysb.engines.market_data import parse_interval
    interval = parse_interval(code)
    with db.conn() as c:
        r = c.execute("SELECT provider_symbol FROM market_catalog WHERE verified=1 LIMIT 1").fetchone()
    symbol = r["provider_symbol"] if r else "XAU/USD"
    try:
        payload = await asyncio.to_thread(market_data.fetch_candles, symbol, interval, config.market_api_key(), 40, False)
        ok, detail = True, f"استلم {len(payload['values'])} شمعة بإطار {interval}"
    except RuntimeError as e:
        ok, detail = False, str(e)
    with db.conn() as c:
        c.execute("UPDATE timeframe_catalog SET verified=?, updated_at=? WHERE code=?", (1 if ok else 0, config.now(), code))
    audit_log.log(uid, f"tf_verify:{code}:{ok}")
    await render(q, f"{'✅' if ok else '❌'} <b>تحقق الإطار {code}</b>\n{esc(detail)}", kb.admin_nav(), bot)


# --- Brokers (real adapter registry: add API → test → approve) ----------------------
async def admin_brokers(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM broker_adapters ORDER BY id DESC").fetchall()
    body = [f"🏦 <b><u>إدارة الوسطاء (Broker Adapters)</u></b>",
            "▎سجّل وسيطاً بمفاتيح API حقيقية، افحص الاتصال فعلياً، ثم اعتمده من هنا.",
            "▎المفاتيح لا تُعرض ولا تُطبع أبداً — تُخزن في قاعدة البيانات فقط.", ""]
    rows_kb = []
    for r in rows:
        cfg = json.loads(r["config_json"] or "{}")
        state_icon = {"CONFIGURED": "🔵", "VERIFIED": "🟡", "ACTIVE": "🟢", "DISABLED": "⚪"}.get(r["status"], "🔵")
        body.append(f"▎{state_icon} <b>#{r['id']} {esc(r['title'])}</b> · {esc(r['key'])} · <b>{esc(r['status'])}</b>")
        if cfg.get("account_id"):
            body.append(f"   🆔 حساب: <code>{esc(cfg['account_id'])}</code>")
        row = [kb.btn("🔌 فحص اتصال", f"a:brokertest:{r['id']}")]
        if r["status"] == "VERIFIED":
            row.append(kb.btn("✅ اعتماد", f"a:brokerapprove:{r['id']}"))
        row.append(kb.btn("🗑 حذف", f"a:brokerdel:{r['id']}"))
        rows_kb.append(row)
    if not rows:
        body.append("▎السجل فارغ (0 وسطاء).")
    body.append("")
    body.append("🔒 تنفيذ الأوامر الحقيقي يبقى مغلقاً حتى توثيق محرك تنفيذ كامل — الاعتماد هنا يوثق الاتصال فقط.")
    rows_kb.append([kb.btn("➕ إضافة وسيط (موجّه)", "a:brokeradd")])
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_broker_add(q, uid, context, bot):
    clear_flows(context.user_data)
    rows = [[kb.btn(meta["title"], f"a:brokertype:{key}")] for key, meta in BROKER_PRESETS.items()]
    await render(q, "🏦 <b><u>إضافة وسيط — اختر النوع</u></b>\n▎سيُطلب منك: الاسم ثم API Key ثم Secret ثم معرّف الحساب.",
                 kb.admin_nav(rows), bot)


async def admin_broker_type(q, uid, context, bot, key: str):
    meta = BROKER_PRESETS.get(key)
    if not meta:
        await render(q, "نوع غير معروف.", kb.admin_nav(), bot)
        return
    clear_flows(context.user_data)
    context.user_data["broker_draft"] = {"step": "name", "type": key,
                                         "base_url": meta["base_url"], "api_key": "", "api_secret": "", "account_id": ""}
    await render(q, f"🏦 <b>{esc(meta['title'])}</b>\n▎طريقة المصادقة: <b>{esc(meta['auth'])}</b>\n\n"
                    "✍️ أرسل <b>اسم الوسيط</b> عندك (مثال: حسابي الرئيسي):",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:brokercancel")]]), bot)


async def admin_broker_cancel(q, uid, context, bot):
    context.user_data.pop("broker_draft", None)
    await render(q, "🗑 أُلغيت إضافة الوسيط.", kb.admin_nav([[kb.btn("🏦 الوسطاء", "a:brokers")]]), bot)


async def admin_broker_test(q, uid, context, bot, bid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM broker_adapters WHERE id=?", (bid,)).fetchone()
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    cfg = json.loads(r["config_json"] or "{}")
    await render(q, f"🔌 جارٍ فحص الاتصال الحقيقي مع <b>{esc(r['title'])}</b>...", None)
    ok, detail = await asyncio.to_thread(broker_test_connectivity, cfg, r["key"])
    new_status = "VERIFIED" if ok else ("CONFIGURED" if r["status"] not in ("ACTIVE",) else "ACTIVE")
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status=?, updated_at=? WHERE id=?", (new_status, config.now(), bid))
    audit_log.log(uid, f"broker_test:{bid}:{ok}")
    icon = "✅" if ok else "❌"
    note = "يمكنك اعتماد الوسيط الآن." if ok else "لم ينجح الفحص — راجع المفاتيح/العنوان ثم أعد الفحص."
    await render(q, f"{icon} <b>نتيجة فحص الاتصال</b>\n▎الوسيط: <b>{esc(r['title'])}</b>\n▎النتيجة: <b>{esc(detail)}</b>\n\n{note}",
                 kb.admin_nav([[kb.btn("↩️ الوسطاء", "a:brokers")]]), bot)


async def admin_broker_approve(q, uid, context, bot, bid: int):
    with db.conn() as c:
        r = c.execute("SELECT status FROM broker_adapters WHERE id=?", (bid,)).fetchone()
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    if r["status"] != "VERIFIED":
        await render(q, "⛔ لا يُعتمد وسيط قبل فحص اتصال ناجح — اضغط «فحص اتصال» أولاً.", kb.admin_nav(), bot)
        return
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status='ACTIVE', enabled=1, updated_at=? WHERE id=?", (config.now(), bid))
    audit_log.log(uid, f"broker_approved:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_broker_delete(q, uid, context, bot, bid: int):
    with db.conn() as c:
        c.execute("DELETE FROM broker_adapters WHERE id=?", (bid,))
    audit_log.log(uid, f"broker_deleted:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_accounts(q, uid, context, bot):
    with db.conn() as c:
        rows = c.execute("""SELECT a.*, u.full_name FROM user_accounts a LEFT JOIN users u ON u.user_id=a.user_id
                            ORDER BY a.id DESC LIMIT 10""").fetchall()
    lines = ["🔗 <b>حسابات التداول المرتبطة</b>", ""]
    if rows:
        for r in rows:
            lines.append(f"  #{r['id']} {esc(r['full_name'] or '')} · {esc(r['broker'])} · {esc(r['account_type'])} · {esc(r['status'])}")
    else:
        lines.append("لا توجد حسابات وسطاء مرتبطة (متاح بعد توثيق Broker Adapter).")
    await render(q, "\n".join(lines), kb.admin_nav(), bot)


# --- Demo overview (admin) ----------------------------------------------------------
async def admin_demo(q, uid, context, bot):
    with db.conn() as c:
        accounts = c.execute("SELECT COUNT(*) n FROM demo_accounts").fetchone()["n"]
        open_n = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE status='OPEN'").fetchone()["n"]
        closed = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) s FROM demo_positions WHERE status!='OPEN'").fetchone()
        recent = c.execute("""SELECT p.*, u.full_name FROM demo_positions p LEFT JOIN users u ON u.user_id=p.user_id
                              ORDER BY p.id DESC LIMIT 8""").fetchall()
    lines = ["🧪 <b>مركز التداول التجريبي</b>", "",
             f"👥 حسابات تجريبية: <b>{accounts}</b>",
             f"📂 صفقات مفتوحة: <b>{open_n}</b>",
             f"📜 صفقات مغلقة: <b>{closed['n']}</b> (صافي {closed['s']:+,.2f})", "",
             "<b>أحدث الصفقات:</b>"]
    for r in recent:
        emoji = "📈" if (r["pnl"] or 0) >= 0 else "📉"
        lines.append(f"  {emoji} #{r['id']} {esc(r['full_name'] or '')} {esc(r['symbol'])} {r['direction']} · {r['lots']:g} lots · {esc(r['status'])}")
    if not recent:
        lines.append("  لا توجد صفقات بعد.")
    await render(q, "\n".join(lines), kb.admin_nav(), bot)


# --- LIVE (locked, double confirmation) ----------------------------------------------
async def admin_live(q, uid, context, bot):
    live = db.get_setting("live_enabled", "0") == "1"
    body = [
        "🔒 <b>التداول الحقيقي (LIVE)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 الحالة: <b>{'⚠️ مفعل' if live else '🟢 مغلق — الوضع الآمن الافتراضي'}</b>",
        "",
        "التنفيذ الحقيقي يتطلب تكامل وسيط موثقاً ومختبراً. لا يوجد تكامل في هذه النسخة،",
        "لذلك يبقى LIVE مغلقياً ولا يُنفذ أي أمر حقيقي مهما كانت الإعدادات.",
        "",
        "<i>لا يوجد زر تفعيل هنا عن قصد: تفعيل LIVE دون وسيط موثق = ادعاء زائف، ومرفوض في هذه المنصة.</i>",
    ]
    await render(q, "\n".join(body), kb.admin_nav(), bot)
