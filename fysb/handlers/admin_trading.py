"""Admin trading sections: Signals, Engine, Strategies, Modes, Risk, Markets,
Timeframes, Brokers, Accounts, Demo, Live — everything wired to real logic."""
import asyncio
import json

from telegram.ext import ContextTypes

from fysb import afn, config, db
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
# v6.10.0 (owner report 5) — the signals center now carries the FULL lifecycle
# categories (بانتظار التنفيذ · قيد التنفيذ في السوق · أهداف جزئية · ضربت الوقف
# · حققت الأهداف · ملغاة/منتهية) plus a REAL profit/loss accounting page per
# instrument — every number from the banked pips the tracker recorded.
SIG_CATS = {
    "pending": ("⛔ بانتظار التنفيذ", "ACTIVE", "بانتظار"),
    "running": ("🚀 قيد التنفيذ في السوق", "RUNNING", "قيد التنفيذ"),
    "partial": ("🏁 أهداف جزئية", "TP1_HIT,TP2_HIT", "أهداف جزئية"),
    "sl": ("🛑 ضربت الوقف", "SL_HIT", "ضربت الوقف"),
    "wins": ("👑 حققت الأهداف", "TP3_HIT", "حققت الأهداف"),
    "dead": ("🧹 ملغاة/منتهية", "CANCELLED,EXPIRED", "ملغاة/منتهية"),
    "all": ("🗂 الكل", "", "الكل"),
}


def _signals_page_body(statuses: tuple, page: int, size: int = 8) -> tuple[str, list, int]:
    with db.conn() as c:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            total = c.execute(f"SELECT COUNT(*) n FROM signals WHERE status IN ({marks})", statuses).fetchone()["n"]
            rows = c.execute(f"""SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq
                                 FROM signals WHERE status IN ({marks})
                                 ORDER BY id DESC LIMIT ? OFFSET ?""",
                             (*statuses, size, page * size)).fetchall()
        else:
            total = c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
            rows = c.execute("""SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq
                                 FROM signals ORDER BY id DESC LIMIT ? OFFSET ?""",
                             (size, page * size)).fetchall()
    rows_kb = [[kb.btn(render_signal_compact(dict(r)), f"a:signalview:{r['id']}")] for r in rows]
    return total, rows_kb, len(rows)


async def _render_signals_center(q, bot, cat: str = "all", page: int = 0):
    title, st_csv, tab = SIG_CATS.get(cat, SIG_CATS["all"])
    statuses = tuple(s for s in st_csv.split(",") if s)
    total, rows_kb, shown = await afn.run(_signals_page_body, statuses, page)
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️", f"a:sigcat:{cat}:{page - 1}"))
    if shown == 8:
        nav_row.append(kb.btn("▶️", f"a:sigcat:{cat}:{page + 1}"))
    if nav_row:
        rows_kb.append(nav_row)
    cat_row = [kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigcat:{key}:0")
               for key in list(SIG_CATS)[:4]]
    cat_row2 = [kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigcat:{key}:0")
                for key in list(SIG_CATS)[4:]]
    rows_kb.insert(0, cat_row)
    rows_kb.insert(1, cat_row2)
    rows_kb.append([kb.btn("💰 محاسبة الربح والخسارة — كل الأدوات", "a:sigpl")])
    rows_kb.append([kb.btn("⚡ إشارة الآن (بمراحل)", "a:signow", style="success"), kb.btn("🎯 أسواق إشاراتي", "a:adminmkts", style="primary")])
    rows_kb.append([kb.btn("➕ نشر إشارة يدوية", "a:newsignal"), kb.btn("🔄 فحص مفتوحة الآن", "a:tracknow")])
    rows_kb.append([kb.btn("🧹 تنظيف الملغاة والمنتهية", "a:signalpurge")])
    await render(q, f"📡 <b><u>مركز إدارة الإشارات</u></b> — {title}\n"
                    f"<blockquote>▎الإجمالي في هذا التصنيف: <b>{total}</b> · صفحة {page + 1}\n"
                    "▎الترقيم تسلسلي حقيقي — نفس الرقم في كل التنبيهات والبطاقات</blockquote>",
                 kb.admin_nav(rows_kb), bot)


async def admin_signals(q, uid, context, bot, page: int = 0):
    await _render_signals_center(q, bot, "all", page)


async def admin_signals_cat(q, uid, context, bot, cat: str, page: int = 0):
    await _render_signals_center(q, bot, cat, page)


async def admin_signal_pl(q, uid, context, bot):
    """v6.10.0 (owner report 5) — REAL profit/loss accounting per instrument,
    from the banked pips of closed signals only (TP1/TP2/TP3/SL rows). Every
    USD figure uses the same 0.01-lot ruler the alerts use — no invented wins."""
    from fysb import instruments
    rows = await afn.run(_pl_account_rows)
    lines = ["💰 <b><u>محاسبة الربح والخسارة — من كل التوصيات المغلقة</u></b>",
             "<blockquote>▎الحساب حقيقي 100%: النقاط المصروفة فعلياً من المتتبع "
             "(أهداف مكتملة أو وقف مضروب) — لا شيء متوقع ولا مصطنع.\n"
             "▎💵 الدولار = نفس مسطرة التنبيهات (0.01 لوت).</blockquote>"]
    grand_pips = 0.0
    grand_wins = grand_losses = 0
    grand_usd = 0.0
    for r in rows:
        net = float(r["net_pips"] or 0)
        usd = instruments.usd_move(r["symbol"], net, 0.01)
        wr = (int(r["wins"] or 0) / max(1, int(r["n"]))) * 100.0
        grand_pips += net
        grand_wins += int(r["wins"] or 0)
        grand_losses += int(r["losses"] or 0)
        grand_usd += usd
        lines.append(f"▎{esc(r['symbol'])}: <b>{r['n']}</b> مغلقة · رابحة <b>{wr:.0f}%</b> "
                     f"({r['wins'] or 0}W/{r['losses'] or 0}L) · صافي <b>{net:+.1f} pips</b> · 💵 <b>{usd:+,.2f} $</b>")
    if len(lines) == 3:
        lines.append("▎لا توجد إشارات مغلقة بعد — تُبنى المحاسبة تلقائياً مع كل إغلاق فعلي.")
    else:
        total_n = grand_wins + grand_losses
        lines.append("▎─────────────")
        lines.append(f"▎👑 <b>الإجمالي العام:</b> {total_n} إشارة مغلقة · رابحة "
                     f"<b>{(grand_wins / max(1, total_n)) * 100:.0f}%</b> · صافي <b>{grand_pips:+.1f} pips</b> "
                     f"· 💵 <b>{grand_usd:+,.2f} $</b> لكل 0.01 لوت (مجموع الأدوات)")
    await render(q, "\n".join(lines),
                 kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_signal_view(q, uid, context, bot, sid: int):
    r, deliveries = await afn.run(_signal_with_deliveries, sid)
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
            from fysb.engines.tracker import drift_pips
            entry = float(sig["entry"])
            drift = drift_pips(sig["symbol"], sig["direction"], entry, price)
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
    r, deliveries = await afn.run(_signal_with_deliveries, sid)
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
    sig, d, s = await afn.run(_signal_delete_tx, sid)
    if not s:
        await render(q, "الإشارة غير موجودة (ربما حُذفت سابقاً).", kb.admin_nav(), bot)
        return
    await afn.run(audit_log.log, uid, f"signal_deleted_forever:{sid}")
    body = (f"✅ <b>تم حذف الإشارة #{sid} نهائياً</b>"
            + (f" ({esc(sig['symbol'])} {esc(sig['direction'])})" if sig else "")
            + f"\n<blockquote>▎🗑 سجل الإشارة: مُمحى\n▎📬 سجلات التسليم المُمحاة: <b>{d}</b>\n"
              f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>")
    await render(q, body, kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_signal_recipients(q, uid, context, bot, sid: int):
    rows = await afn.run(_signal_recipients, sid)
    lines = [f"📬 <b>مستلمو الإشارة #{sid}</b>"]
    lines += [f"  <code>{r['user_id']}</code> · {human_dt(r['delivered_at'])}" for r in rows] or ["  لا يوجد مستلمون."]
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)


async def admin_signal_purge(q, uid, context, bot):
    """Bulk permanent deletion of CANCELLED/EXPIRED signals (double confirm)."""
    n = await afn.run(_dead_signals_count)
    if not n:
        await render(q, "✅ لا توجد إشارات ملغاة أو منتهية — السجل نظيف بالكامل.",
                     kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals")]]), bot)
        return
    await render(q, f"🧹 <b>تنظيف الإشارات الميتة</b>\n<blockquote>▎إشارات ملغاة/منتهية: <b>{n}</b>\n"
                    f"▎⚠️ حذف نهائي بلا أرشيف — يشمل سجلات التسليم المرتبطة.</blockquote>",
                 kb.admin_nav([[kb.btn(f"⚠️ حذف {n} إشارة نهائياً", "a:signalpurgego")],
                               [kb.btn("↩️ تراجع", "a:signals")]]), bot)


async def admin_signal_purge_go(q, uid, context, bot):
    d, s = await afn.run(_signal_purge_tx)
    await afn.run(audit_log.log, uid, f"signals_purged:{s}")
    await render(q, f"✅ <b>تم التنظيف النهائي</b>\n<blockquote>▎🗑 إشارات مُمحاة: <b>{s}</b>\n▎📬 تسليمات مُمحاة: <b>{d}</b>\n"
                    f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>",
                 kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_cancel_signal(q, uid, context, bot, sid: int):
    cancelled = await afn.run(_cancel_signal_tx, sid)
    await afn.run(audit_log.log, uid, f"cancel_signal:{sid}")
    if cancelled:
        from fysb.engines.tracker import TERMINAL, seq_of
        from fysb.theme import dir_badge, esc, status_ar
        sig = await afn.run(_signal_row_full, sid)
        seq = await afn.run(seq_of, sid)
        text = (f"⛔ <b><u>تحديث الإشارة الملكية #{seq}</u></b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])}\n"
                f"<blockquote>▎📌 الحالة الجديدة: <b>{status_ar('CANCELLED')}</b>\n"
                f"▎📌 السبب: قرار إداري موثق من الإدارة الملكية</blockquote>")
        uids = await afn.run(_signal_recipient_uids, sid)
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
    expired = await afn.run(tracker.expire_stale_signals)
    stats = result["stats"]
    await render(q, f"✅ <b>نتيجة الفحص</b>\nفُحصت: {stats['checked']} · حُدثت: <b>{stats['updated']}</b>\n"
                    f"تخطيت بسبب قدم البيانات: {stats['skipped_stale']} · أخطاء مزود: {stats['errors']} · انتهت صلاحيتها: {expired}",
                 kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals")]]), bot)


async def update_message(q, text):
    """Send a transient progress note (fresh message pattern)."""
    await render(q, text, None)


# --- Trading engine --------------------------------------------------------------
async def admin_engine(q, uid, context, bot):
    enabled = await afn.run(db.get_setting, "engine_enabled", "0") == "1"
    interval = await afn.run(db.get_setting_int, "engine_interval_seconds", 300)
    min_score = await afn.run(db.get_setting_int, "engine_min_score", 80)
    markets, runs = await afn.run(_engine_page_rows)
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
        [kb.btn("🧪 اختبار تاريخي (Backtest)", "a:backtest")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


def _enabled_backtest_markets() -> list[str]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY id LIMIT 12"
        ).fetchall()
    return [r["symbol"] for r in rows] or ["XAUUSD", "EURUSD", "BTCUSD"]


async def admin_backtest(q, uid, context, bot):
    mkts = await afn.run(_enabled_backtest_markets)
    rows_kb = []
    for sym in mkts[:8]:
        rows_kb.append([
            kb.btn(f"🧪 {sym} M15", f"a:btrun:{sym}:M15"),
            kb.btn(f"🧪 {sym} H1", f"a:btrun:{sym}:H1"),
        ])
    rows_kb.append([kb.btn("🧠 العودة للمحرك", "a:engine")])
    body = (
        "🧪 <b><u>مختبر الاختبار التاريخي المؤسسي (Walk-Forward Backtest)</u></b>\n"
        "<blockquote>"
        "▎📊 يختبر جميع الاستراتيجيات الـ 13 على شموع السوق التاريخية المغلقة (بلا Repainting).\n"
        "▎🛡 يطبّق فلتر الإجماع الاتجاهي، وقاعدة SL-أولاً، وتأمين نقطة الدخول بعد TP1.\n"
        "▎📈 يحسب نسبة النجاح، عامل الربح (Profit Factor)، صافي R، وأقصى تراجع (Max DD)."
        "</blockquote>\n"
        "اختر السوق والإطار الزمني لبدء الاختبار:"
    )
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_backtest_run(q, uid, context, bot, symbol: str, tf: str):
    from fysb.engines.backtest import format_report, run_backtest
    sym = (symbol or "XAUUSD").strip().upper()
    tf_u = (tf or "M15").strip().upper()
    await render(q, f"🧪 <b>جارٍ تشغيل الاختبار التاريخي لـ {esc(sym)} ({esc(tf_u)})…</b>\n<i>يرجى الانتظار بضع ثوانٍ.</i>", None, bot)
    rep = await asyncio.to_thread(run_backtest, sym, tf_u, api_key=config.market_api_key())
    await afn.run(audit_log.log, uid, f"backtest:{sym}:{tf_u}:n={rep.filled_trades}:r={rep.net_r}")
    rows_kb = [
        [kb.btn(f"🔄 إعادة {sym} {tf_u}", f"a:btrun:{sym}:{tf_u}"), kb.btn("🧪 سوق آخر", "a:backtest")],
        [kb.btn("🧠 المحرك", "a:engine")],
    ]
    await render(q, format_report(rep), kb.admin_nav(rows_kb), bot)


async def admin_engine_toggle(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "engine_enabled", "0") == "1" else "1"
    await afn.run(db.set_setting, "engine_enabled", val)
    await afn.run(audit_log.log, uid, f"engine:{val}")
    await admin_engine(q, uid, context, bot)


async def admin_engine_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"interval": ("engine_interval_seconds", 120, 3600), "minscore": ("engine_min_score", 50, 95)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    setting_key, lo, hi = bounds[key]
    cur = await afn.run(db.get_setting_int, setting_key, 300 if key == "interval" else 80)
    await afn.run(db.set_setting, setting_key, int(max(lo, min(hi, cur + delta))))
    val_after = await afn.run(db.get_setting, setting_key)
    await afn.run(audit_log.log, uid, f"engine_setting:{setting_key}:{val_after}")
    await admin_engine(q, uid, context, bot)


async def admin_engine_now(q, uid, context, bot):
    from fysb.engines.signal_engine import generate_signal
    await render(q, "🚀 جارٍ تشغيل دورة فورية للمحرك على الأسواق المفعلة...", None)
    markets = await afn.run(_enabled_verified_market_dicts)
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
    cleaned = await afn.run(_engine_runs_clean)
    await afn.run(audit_log.log, uid, f"engine_runs_cleaned:{cleaned}")
    await render(q, f"🧹 أُرشفت وأُزيلت {abs(cleaned)} سجل دورة قديم (بقي آخر 500).", kb.admin_nav(), bot)


# --- Strategies ------------------------------------------------------------------
# v6.9.1 — the full world catalog (36 methodologies) exceeds Telegram's 4096
# character message limit in one board (measured: 4678 chars ⇒ BadRequest
# "Message is too long" ⇒ the generic error card the owner reported).
# The royal fix: paginate — 10 strategies per page, global rank numbers
# preserved across pages, prev/next navigation, and every action button
# carries its page so toggling/verifying lands back on the same page.
STRATS_PER_PAGE = 10


async def admin_strategies(q, uid, context, bot, page: int = 0):
    """v6.10.0 (owner report 9) — the plain-text wall is retired: every
    methodology is now its own BUTTON «🟢 ICT · 82.5» opening a REAL identity
    page (rules of the engine + measured stats + verify/toggle actions).
    The board always fits Telegram's limits — no more «حدث خطأ» on tap."""
    from fysb.services import strategy_stats
    rows = await afn.run(_strategy_catalog_rows)
    codes = await afn.run(strategy_stats.ordered_codes, [r["code"] for r in rows])
    by_code = {r["code"]: r for r in rows}
    ordered = [by_code[c] for c in codes if c in by_code] + [r for r in rows if r["code"] not in codes]
    board = {d["code"]: d for d in await afn.run(strategy_stats.leaderboard)}
    total = len(ordered)
    verified_n = sum(1 for r in ordered if r["verified"])
    active_n = sum(1 for r in ordered if r["enabled"] and r["verified"])
    per_page = 12
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page or 0), pages - 1))
    chunk = ordered[page * per_page:(page + 1) * per_page]
    body = (f"🧩 <b><u>حوكمة الاستراتيجيات — مرتبة من الأقوى</u></b>\n"
            f"<blockquote>▎📄 الصفحة <b>{page + 1}</b> من <b>{pages}</b> — الترتيب عالمي متصل\n"
            f"▎🗂 إجمالي المنهجيات: <b>{total}</b> · موثقة ومختبرة: <b>{verified_n}</b> · جاهزة للإشارات: <b>{active_n}</b>\n"
            "▎اضغط أي استراتيجية لفتح بطاقتها الملكية: القواعد الميكانيكية الحقيقية + الأداء المقاس + التفعيل/التحقق</blockquote>")
    rows_kb = []
    grid = []
    for r in chunk:
        rank = page * per_page + len(grid) + 1
        state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
        d = board.get(r["code"])
        strength = f" · {d['strength']:.0f}" if d else ""
        grid.append(kb.btn(f"{state} {rank}. {r['code']}{strength}",
                           f"a:strategyinfo:{r['code']}:{page}"))
        if len(grid) == 2:
            rows_kb.append(grid)
            grid = []
    if grid:
        rows_kb.append(grid)
    pager = []
    if page > 0:
        pager.append(kb.btn("◀️ الأقوى السابقة", f"a:strategies:{page - 1}"))
    pager.append(kb.btn(f"📄 {page + 1}/{pages}", "a:plannoop"))
    if page < pages - 1:
        pager.append(kb.btn("الأدنى التالية ▶️", f"a:strategies:{page + 1}"))
    rows_kb.append(pager)
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_strategy_info(q, uid, context, bot, code: str, page: int = 0):
    """The royal identity card of ONE strategy — real rules, real stats, and
    the actions (verify / toggle) the old error message promised but the board
    never actually offered."""
    from fysb.services import strategy_info
    from fysb.services import strategy_stats
    code = (code or "").upper()
    r = await afn.run(_strategy_row, code)
    if not r:
        await render(q, "الاستراتيجية غير موجودة.", kb.admin_nav(), bot)
        return
    rec = dict(r)
    body = await afn.run(strategy_info.info_page, code)
    board = {d["code"]: d for d in await afn.run(strategy_stats.leaderboard)}
    d = board.get(code)
    if d:
        body += (f"\n\n📈 <b>قياس مباشر:</b> {d['n']} مغلقة · رابحة {d['win_rate']:.0f}% · "
                 f"TP3 {d['tp3_share']:.0f}% · متوسط R {d['avg_r']:+.2f} · قوة {d['strength']:.1f}")
    rows_kb = []
    from fysb.engines.strategies import VERIFIABLE
    if code in VERIFIABLE and not rec["verified"]:
        rows_kb.append([kb.btn("✅ تحقق وتفعيل (محرك منفذ ومختبَر آلياً)", f"a:strategy_verify:{code}:{page}")])
    elif rec["verified"]:
        action = "إيقاف" if rec["enabled"] else "تفعيل"
        rows_kb.append([kb.btn(f"{'🟢' if rec['enabled'] else '⚪'} {action} الاستراتيجية", f"a:strategy_toggle:{code}:{page}")])
    else:
        rows_kb.append([kb.btn("🔒 مغلقة — تُفتح بعد تنفيذ محركها واختباره آلياً", "a:plannoop")])
    rows_kb.append([kb.btn("↩️ لوحة الاستراتيجيات", f"a:strategies:{page}")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_strategy_toggle(q, uid, context, bot, code: str, page: int = 0):
    r = await afn.run(_strategy_toggle_state, code)
    if not r:
        await render(q, "الاستراتيجية غير موجودة.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ الاستراتيجية غير مختبرة؛ لا يمكن تفعيلها. استخدم زر التحقق إن كانت ضمن المنفذة.",
                     kb.admin_nav(), bot)
        return
    await afn.run(_strategy_toggle_apply, code, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"strategy_toggle:{code}")
    await admin_strategies(q, uid, context, bot, page)


async def admin_strategy_verify(q, uid, context, bot, code: str, page: int = 0):
    if code not in VERIFIABLE:
        await render(q, "⛔ هذه الاستراتيجية غير منفذة بعد؛ لا يمكن التحقق منها.", kb.admin_nav(), bot)
        return
    await afn.run(_strategy_verify_apply, code)
    await afn.run(audit_log.log, uid, f"strategy_verified:{code}")
    await admin_strategies(q, uid, context, bot, page)


# --- Operating modes -------------------------------------------------------------
async def admin_modes(q, uid, context, bot):
    rows, current = await afn.run(_modes_page_rows)
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
    r = await afn.run(_mode_row, code)
    if not r or not r["executable"] or not r["enabled"]:
        await render(q, "⛔ هذا الوضع غير قابل للتنفيذ في هذه النسخة.", kb.admin_nav(), bot)
        return
    if code == "C":
        # LIVE requires the explicit double-confirmation flow; never settable here.
        await render(q, "⛔ الوضع LIVE يُفعّل حصراً عبر قسم «التداول الحقيقي» بتأكيد مزدوج.", kb.admin_nav(), bot)
        return
    await afn.run(db.set_setting, "trading_mode", r["code"])
    await afn.run(audit_log.log, uid, f"mode_set:{code}")
    await admin_modes(q, uid, context, bot)


# --- Risk management -------------------------------------------------------------
async def admin_risk(q, uid, context, bot):
    keys = [
        ("risk_per_signal_pct", "المخاطرة لكل إشارة", 0.1, 0.1),
        ("max_daily_risk_pct", "أقصى مخاطرة يومية", 0.5, 0.5),
        ("max_daily_loss_pct", "أقصى خسارة يومية", 0.5, 1.0),
        ("max_open_per_symbol", "أقصى إشارات مفتوحة لكل سوق", 1, 3),
        ("max_open_signals", "أقصى إشارات مفتوحة (الكلي)", 1, 12),
        ("max_correlated_signals", "أقصى صفقات مترابطة بنفس الاتجاه", 1, 3),
        ("min_rr", "أدنى عائد/مخاطرة", 0.1, 0.5),
        ("atr_pct_max", "أقصى تقلب ATR%", 0.5, 1.0),
    ]
    ks = await afn.run(db.kill_switch_on)
    vf = await afn.run(db.get_setting, "volatility_filter", "1")
    lines = ["🛡 <b>محرك إدارة المخاطر</b>",
             f"⛔ Kill Switch: <b>{'مفعل — يمنع أي إشارة جديدة' if ks else 'غير مفعل'}</b>",
             f"🌊 فلتر التقلب: <b>{'مفعل' if vf == '1' else 'متوقف'}</b>",
             "<blockquote>▎✨ <b>سقف السوق</b> يمنع تكدّس الإشارات على سوق واحد دون خنق باقي الأسواق.\n"
             "▎👑 <b>⚡ إشارة الآن للإدارة</b> مستثناة من سقف الإشارات الآلية ولا تستهلك حصتها.</blockquote>",
             "<b>الحدود (تطبق على السيرفر قبل أي إشارة):</b>"]
    rows_kb = []
    for key, title, step, _d in keys:
        val = await afn.run(db.get_setting_float, key, 0)
        lines.append(f"  {title}: <b>{val:g}</b>")
        rows_kb.append([kb.btn("➖", f"a:riskadj:{key}:-{step}"), kb.btn(f"{title} {val:g}", "a:plannoop"), kb.btn("➕", f"a:riskadj:{key}:{step}")])
    rows_kb.append([kb.btn("⛔ تبديل Kill Switch", "a:killswitch", style="danger"),
                    kb.btn("🌊 فلتر التقلب", "a:volfilter", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_risk_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"risk_per_signal_pct": (0.1, 5.0), "max_daily_risk_pct": (1.0, 20.0),
              "max_daily_loss_pct": (1.0, 30.0), "max_open_per_symbol": (1, 10),
              "max_open_signals": (1, 50), "max_correlated_signals": (1, 10),
              "min_rr": (1.0, 10.0), "atr_pct_max": (1.0, 15.0)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    lo, hi = bounds[key]
    cur = await afn.run(db.get_setting_float, key, lo)
    await afn.run(db.set_setting, key, round(max(lo, min(hi, cur + delta)), 2))
    val_after = await afn.run(db.get_setting, key)
    await afn.run(audit_log.log, uid, f"risk_setting:{key}:{val_after}")
    await admin_risk(q, uid, context, bot)


async def admin_kill_switch(q, uid, context, bot):
    val = "0" if await afn.run(db.kill_switch_on) else "1"
    await afn.run(db.set_setting, "global_kill_switch", val)
    await afn.run(audit_log.log, uid, f"kill_switch:{val}")
    await admin_risk(q, uid, context, bot)


async def admin_vol_filter(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "volatility_filter", "1") == "1" else "1"
    await afn.run(db.set_setting, "volatility_filter", val)
    await afn.run(audit_log.log, uid, f"volatility_filter:{val}")
    await admin_risk(q, uid, context, bot)


# --- Markets ---------------------------------------------------------------------
CATEGORY_ORDER = ["metal", "forex", "crypto", "index", "stock", "commodity"]


async def admin_markets(q, uid, context, bot):
    """v6.8.1 — LUXURY CATALOG BOARD: every market inside its royal category
    section (المعادن · الفوركس · الرقمية · المؤشرات · الأسهم · السلع), with
    real counts, live-state markers and a 2-per-row button grid — the old
    flat alphabetical wall is gone."""
    from fysb.theme import CATEGORY_AR, CATEGORY_ICON
    rows = await afn.run(_market_catalog_all)
    if not rows:
        await render(q, "🌐 لا توجد أسواق في الفهرس بعد.", kb.admin_nav([[kb.btn("➕ إضافة سوق", "a:market_add")]]), bot)
        return
    total = len(rows)
    verified = sum(1 for r in rows if r["verified"])
    enabled = sum(1 for r in rows if r["enabled"])
    live = sum(1 for r in rows if r["enabled"] and r["verified"])
    lines = ["🌐 <b><u>إدارة الأسواق — الفهرس الملكي بأقسامه</u></b>",
             "<blockquote>"
             f"▎🗂 إجمالي الأدوات: <b>{total}</b> · موثقة: <b>{verified}</b> · مفعلة: <b>{enabled}</b> · جاهزة للتداول: <b>{live}</b>\n"
             "▎السوق الجديد يبقى مغلقاً حتى يثبت المزود بياناته فعلياً (زر تحقق)</blockquote>"]
    sections: dict[str, list] = {}
    for r in rows:
        sections.setdefault((r.get("category") or "forex"), []).append(r)
    ordered = [c for c in CATEGORY_ORDER if c in sections] + \
              [c for c in sections if c not in CATEGORY_ORDER]
    rows_kb = []
    for cat in ordered:
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)
        grp = sections[cat]
        sec_ver = sum(1 for r in grp if r["verified"])
        sec_en = sum(1 for r in grp if r["enabled"])
        lines.append(f"\n{icon} <b><u>{title}</u></b> — {len(grp)} أدوات · موثقة {sec_ver} · مفعلة {sec_en}")
        for r in grp:
            state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
            lines.append(f"▎{state} <b>{esc(r['symbol'])}</b> — {esc(r['title'])} · <code>{esc(r['provider_symbol'])}</code>"
                         f" · pip={r['pip_size']} · digits={r['digits']}")
        # 2-per-row toggle grid per section (compact luxury board)
        grid = []
        for r in grp:
            mark = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
            grid.append(kb.btn(f"{mark} {r['symbol']}: {'إيقاف' if r['enabled'] else 'تفعيل'}",
                               f"a:market_toggle:{r['symbol']}"))
            if len(grid) == 2:
                rows_kb.append(grid)
                grid = []
        if grid:
            rows_kb.append(grid)
        unverified = [r for r in grp if not r["verified"]]
        while unverified:
            pair, unverified = unverified[:2], unverified[2:]
            rows_kb.append([kb.btn(f"✅ تحقق {r['symbol']} من المزود", f"a:market_verify:{r['symbol']}") for r in pair])
    rows_kb.append([kb.btn("➕ إضافة سوق جديد", "a:market_add")])
    rows_kb.append([kb.btn("🎯 أسواق إشارات حساب الإدارة", "a:adminmkts", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


# --- Admin personal signal-receive market filter (v6.17.0) ----------------------
def _parse_csv_syms(raw: str) -> list[str]:
    out = []
    for x in (raw or "").split(","):
        s = x.strip().upper().replace("/", "")
        if s and s != "ALL" and s not in out:
            out.append(s)
    return out


async def admin_signal_markets(q, uid, context, bot, notice: str = ""):
    """v6.17.0: multi-toggle market filter for ADMIN_ID's own personal signal &
    tracker alerts — NEVER disables any market for users or channels."""
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    raw = (await afn.run(db.get_setting, "admin_signal_markets", "") or "").strip()
    selected = _parse_csv_syms(raw)
    all_mode = (not selected)
    active_set = set(live) if all_mode else (set(selected) & set(live))
    if not all_mode and not active_set and live:
        # fallback cleanly if previously-selected markets were disabled
        all_mode = True
        active_set = set(live)

    summary = "🌍 جميع الأسواق المفعّلة" if all_mode else " · ".join(sorted(active_set))
    lines = [
        "🎯 <b><u>أسواق إشارات حساب الإدارة</u></b>",
        "<blockquote>"
        f"▎📌 ما يصلك حالياً على حسابك: <b>{esc(summary)}</b>\n"
        f"▎🌐 إجمالي الأسواق المفعّلة على المنصة: <b>{len(live)}</b>\n"
        "▎✨ هذا الإعداد يتحكم بما يصلك <b>أنت شخصياً</b> من الإشارات وتحديثات التتبع فقط — "
        "ولا يغلق أي سوق على المستخدمين أو القنوات."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")
    if not live:
        lines.append("⚠️ لا توجد أسواق مفعّلة وموثّقة حالياً — فعّل الأسواق من قسم 🌐 الأسواق أولاً.")
    else:
        lines.append("<b>اضغط أي سوق لتشغيله ✅ أو إيقافه ⚪ على حساب الإدارة:</b>")

    rows_kb = []
    rows_kb.append([kb.btn(
        ("✅ " if all_mode else "🌍 ") + "استلام جميع الأسواق المفعّلة",
        "a:adminmktsall",
        style="success" if all_mode else "primary",
    )])
    if "XAUUSD" in live:
        gold_only = (not all_mode and active_set == {"XAUUSD"})
        rows_kb.append([kb.btn(
            ("⭐ " if gold_only else "🥇 ") + "تحديد الذهب فقط (XAUUSD)",
            "a:adminmktsonly:XAUUSD",
            style="success" if gold_only else "primary",
        )])
    for i in range(0, len(live), 3):
        rows_kb.append([
            kb.btn(
                ("✅ " if s in active_set else "⚪ ") + s,
                f"a:adminmktstog:{s}",
                style="success" if s in active_set else "",
            )
            for s in live[i:i + 3]
        ])
    rows_kb.append([kb.btn("📡 مركز الإشارات", "a:signals"), kb.btn("🌐 إدارة الأسواق", "a:markets")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_signal_markets_toggle(q, uid, context, bot, symbol: str):
    symbol = (symbol or "").strip().upper().replace("/", "")
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    if symbol not in live:
        await admin_signal_markets(q, uid, context, bot, notice=f"السوق {symbol} غير مفعّل حالياً.")
        return
    raw = (await afn.run(db.get_setting, "admin_signal_markets", "") or "").strip()
    selected = _parse_csv_syms(raw)
    cur = list(live) if not selected else [s for s in selected if s in live]
    if not cur:
        cur = list(live)
    notice = ""
    if symbol in cur:
        if len(cur) <= 1:
            notice = "يجب إبقاء سوق واحد على الأقل نشطاً (أو أوقف دفع الإشارات من الإشعارات)."
        else:
            cur = [s for s in cur if s != symbol]
            notice = f"تم إيقاف استلام إشارات {symbol} على حساب الإدارة (تبقى مفعّلة للمستخدمين)."
    else:
        cur.append(symbol)
        notice = f"تم تفعيل استلام إشارات {symbol} على حساب الإدارة."
    new_val = "" if set(cur) >= set(live) else ",".join(sorted(set(cur)))
    await afn.run(db.set_setting, "admin_signal_markets", new_val)
    await afn.run(audit_log.log, uid, f"admin_signal_markets:{new_val or 'ALL'}")
    await admin_signal_markets(q, uid, context, bot, notice=notice)


async def admin_signal_markets_only(q, uid, context, bot, symbol: str):
    symbol = (symbol or "").strip().upper().replace("/", "")
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    if symbol not in live:
        await admin_signal_markets(q, uid, context, bot, notice=f"السوق {symbol} غير مفعّل حالياً.")
        return
    new_val = "" if live == [symbol] else symbol
    await afn.run(db.set_setting, "admin_signal_markets", new_val)
    await afn.run(audit_log.log, uid, f"admin_signal_markets:only:{symbol}")
    await admin_signal_markets(
        q, uid, context, bot,
        notice=f"تم ضبط حساب الإدارة لاستلام إشارات {symbol} فقط (بينما باقي الأسواق تعمل للمستخدمين والقنوات).",
    )


async def admin_signal_markets_all(q, uid, context, bot):
    await afn.run(db.set_setting, "admin_signal_markets", "")
    await afn.run(audit_log.log, uid, "admin_signal_markets:ALL")
    await admin_signal_markets(q, uid, context, bot, notice="تم تفعيل استلام جميع الأسواق المفعّلة على حساب الإدارة.")


async def admin_market_toggle(q, uid, context, bot, symbol: str):
    r = await afn.run(_market_state, symbol)
    if not r:
        await render(q, "السوق غير موجود.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ السوق غير موثق لدى المزود — لا يمكن تفعيله قبل التحقق.", kb.admin_nav(), bot)
        return
    await afn.run(_market_toggle_apply, symbol, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"market_toggle:{symbol}")
    await admin_markets(q, uid, context, bot)


async def admin_market_verify(q, uid, context, bot, symbol: str):
    """REAL provider verification: fetch a live candle through the data chain."""
    r = await afn.run(_market_provider_symbol, symbol)
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
    await afn.run(_market_verify_apply, symbol, 1 if ok else 0)
    await afn.run(audit_log.log, uid, f"market_verify:{symbol}:{ok}")
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
    rows = await afn.run(_timeframe_rows)
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
    r = await afn.run(_tf_state, code)
    if not r:
        await render(q, "الإطار غير موجود.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ الإطار غير موثق لدى المزود.", kb.admin_nav(), bot)
        return
    await afn.run(_tf_toggle_apply, code, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"timeframe_toggle:{code}")
    await admin_timeframes(q, uid, context, bot)


async def admin_tf_verify(q, uid, context, bot, code: str):
    from fysb.engines.market_data import parse_interval
    interval = parse_interval(code)
    r = await afn.run(_verified_provider_symbol)
    symbol = r["provider_symbol"] if r else "XAU/USD"
    try:
        payload = await asyncio.to_thread(market_data.fetch_candles, symbol, interval, config.market_api_key(), 40, False)
        ok, detail = True, f"استلم {len(payload['values'])} شمعة بإطار {interval}"
    except RuntimeError as e:
        ok, detail = False, str(e)
    await afn.run(_tf_verify_apply, code, 1 if ok else 0)
    await afn.run(audit_log.log, uid, f"tf_verify:{code}:{ok}")
    await render(q, f"{'✅' if ok else '❌'} <b>تحقق الإطار {code}</b>\n{esc(detail)}", kb.admin_nav(), bot)


# --- Brokers (real adapter registry: add API → test → approve) ----------------------
def _safe_broker_cfg(raw: str) -> tuple[dict, bool]:
    from fysb.services import secrets as secrets_service
    try:
        return secrets_service.decrypt_json(raw or ""), True
    except Exception:
        return {}, False


async def admin_brokers(q, uid, context, bot):
    rows = await afn.run(_broker_rows)
    body = [f"🏦 <b><u>إدارة الوسطاء (Broker Adapters)</u></b>",
            "▎سجّل وسيطاً بمفاتيح API حقيقية، افحص الاتصال فعلياً، ثم اعتمده من هنا.",
            "▎المفاتيح لا تُعرض ولا تُطبع أبداً — تُخزن مشفرة (ENC1) في قاعدة البيانات فقط.", ""]
    rows_kb = []
    for r in rows:
        cfg, dec_ok = _safe_broker_cfg(r["config_json"] or "")
        state_icon = {"CONFIGURED": "🔵", "VERIFIED": "🟡", "ACTIVE": "🟢", "DISABLED": "⚪"}.get(r["status"], "🔵")
        body.append(f"▎{state_icon} <b>#{r['id']} {esc(r['title'])}</b> · {esc(r['key'])} · <b>{esc(r['status'])}</b>")
        if not dec_ok:
            body.append("   ⚠️ تعذر فك تشفير المفاتيح (تحقق من BROKER_SECRET_KEY أو أعد إدخال الوسيط)")
        elif cfg.get("account_id"):
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
    r = await afn.run(_broker_adapter, bid)
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    cfg, dec_ok = _safe_broker_cfg(r["config_json"] or "")
    if not dec_ok:
        await render(q, "❌ تعذر فك تشفير مفاتيح الوسيط — تحقق من <code>BROKER_SECRET_KEY</code> أو احذف الوسيط وأعد إضافته.",
                     kb.admin_nav([[kb.btn("↩️ الوسطاء", "a:brokers")]]), bot)
        return
    await render(q, f"🔌 جارٍ فحص الاتصال الحقيقي مع <b>{esc(r['title'])}</b>...", None)
    ok, detail = await asyncio.to_thread(broker_test_connectivity, cfg, r["key"])
    new_status = "VERIFIED" if ok else ("CONFIGURED" if r["status"] not in ("ACTIVE",) else "ACTIVE")
    await afn.run(_broker_status_apply, bid, new_status)
    await afn.run(audit_log.log, uid, f"broker_test:{bid}:{ok}")
    icon = "✅" if ok else "❌"
    note = "يمكنك اعتماد الوسيط الآن." if ok else "لم ينجح الفحص — راجع المفاتيح/العنوان ثم أعد الفحص."
    await render(q, f"{icon} <b>نتيجة فحص الاتصال</b>\n▎الوسيط: <b>{esc(r['title'])}</b>\n▎النتيجة: <b>{esc(detail)}</b>\n\n{note}",
                 kb.admin_nav([[kb.btn("↩️ الوسطاء", "a:brokers")]]), bot)


async def admin_broker_approve(q, uid, context, bot, bid: int):
    r = await afn.run(_broker_status, bid)
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    if r["status"] != "VERIFIED":
        await render(q, "⛔ لا يُعتمد وسيط قبل فحص اتصال ناجح — اضغط «فحص اتصال» أولاً.", kb.admin_nav(), bot)
        return
    await afn.run(_broker_approve_apply, bid)
    await afn.run(audit_log.log, uid, f"broker_approved:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_broker_delete(q, uid, context, bot, bid: int):
    await afn.run(_broker_delete_row, bid)
    await afn.run(audit_log.log, uid, f"broker_deleted:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_accounts(q, uid, context, bot):
    rows = await afn.run(_account_rows)
    lines = ["🔗 <b>حسابات التداول المرتبطة</b>", ""]
    if rows:
        for r in rows:
            lines.append(f"  #{r['id']} {esc(r['full_name'] or '')} · {esc(r['broker'])} · {esc(r['account_type'])} · {esc(r['status'])}")
    else:
        lines.append("لا توجد حسابات وسطاء مرتبطة (متاح بعد توثيق Broker Adapter).")
    await render(q, "\n".join(lines), kb.admin_nav(), bot)


# --- Demo overview (admin) ----------------------------------------------------------
async def admin_demo(q, uid, context, bot):
    accounts, open_n, closed, recent = await afn.run(_demo_overview_rows)
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
    live = await afn.run(db.get_setting, "live_enabled", "0") == "1"
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


# --- v6.11.0 thread-offload helpers ---
def _pl_account_rows() -> list[dict]:
    """(v6.11.0 thread-offload helper) closed-signals P/L accounting per symbol."""
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT symbol, COUNT(*) n, SUM(pips_result>0) wins, SUM(pips_result<=0) losses,
                      SUM(pips_result) net_pips, ROUND(AVG(pips_result),1) avg_pips
               FROM signals WHERE status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
               GROUP BY symbol ORDER BY net_pips DESC""").fetchall()]


def _signal_with_deliveries(sid: int):
    """(v6.11.0 thread-offload helper) full signal row + delivery count, ONE trip."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        deliveries = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE signal_id=?", (sid,)).fetchone()["n"]
    return r, deliveries


def _signal_delete_tx(sid: int):
    """(v6.11.0 thread-offload helper) erase one signal + its deliveries (tx)."""
    with db.tx() as c:
        sig = c.execute("SELECT symbol, direction FROM signals WHERE id=?", (sid,)).fetchone()
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id=?", (sid,)).rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE id=?", (sid,)).rowcount)
    return sig, d, s


def _signal_recipients(sid: int) -> list:
    """(v6.11.0 thread-offload helper) last delivery rows of one signal."""
    with db.conn() as c:
        return c.execute("""SELECT d.user_id, d.delivered_at FROM signal_deliveries d
                            WHERE d.signal_id=? ORDER BY d.delivered_at DESC LIMIT 15""", (sid,)).fetchall()


def _signal_recipient_uids(sid: int) -> list[int]:
    """(v6.11.0 thread-offload helper) user ids that received one signal (excludes channel rows <=0)."""
    with db.conn() as c:
        return [r["user_id"] for r in c.execute(
            "SELECT user_id FROM signal_deliveries WHERE signal_id=? AND user_id>0", (sid,))]


def _signal_row_full(sid: int) -> dict:
    """(v6.11.0 thread-offload helper) one full signal row as dict."""
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())


def _dead_signals_count() -> int:
    """(v6.11.0 thread-offload helper) count of CANCELLED/EXPIRED signals."""
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('CANCELLED','EXPIRED')").fetchone()["n"]


def _signal_purge_tx():
    """(v6.11.0 thread-offload helper) bulk-erase dead signals + deliveries (tx)."""
    with db.tx() as c:
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id IN "
                          "(SELECT id FROM signals WHERE status IN ('CANCELLED','EXPIRED'))").rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE status IN ('CANCELLED','EXPIRED')").rowcount)
    return d, s


def _cancel_signal_tx(sid: int) -> int:
    """(v6.11.0 thread-offload helper) cancel an open signal (tx) — rowcount."""
    with db.tx() as c:
        cur = c.execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=? AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                        (config.now(), sid))
        return cur.rowcount


def _engine_page_rows():
    """(v6.11.0 thread-offload helper) armed-markets count + last engine runs."""
    with db.conn() as c:
        markets = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        runs = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT 10").fetchall()
    return markets, runs


def _enabled_verified_market_dicts() -> list[dict]:
    """(v6.11.0 thread-offload helper) enabled+verified market rows."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()]


def _engine_runs_clean() -> int:
    """(v6.11.0 thread-offload helper) archive-trim engine_runs to last 500 — rowcount."""
    with db.conn() as c:
        cur = c.execute("""DELETE FROM engine_runs WHERE id NOT IN
                           (SELECT id FROM engine_runs ORDER BY id DESC LIMIT 500)""")
        return cur.rowcount


def _strategy_catalog_rows() -> list[dict]:
    """(v6.11.0 thread-offload helper) the whole strategy_catalog as dicts."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM strategy_catalog").fetchall()]


def _strategy_row(code: str):
    """(v6.11.0 thread-offload helper) one strategy_catalog row by code."""
    with db.conn() as c:
        return c.execute("SELECT * FROM strategy_catalog WHERE code=?", (code,)).fetchone()


def _strategy_toggle_state(code: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one strategy."""
    with db.conn() as c:
        return c.execute("SELECT enabled,verified FROM strategy_catalog WHERE code=?", (code,)).fetchone()


def _strategy_toggle_apply(code: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one strategy."""
    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (new_val, config.now(), code))


def _strategy_verify_apply(code: str) -> None:
    """(v6.11.0 thread-offload helper) mark one strategy verified+enabled."""
    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET verified=1,enabled=1,updated_at=? WHERE code=?", (config.now(), code))


def _modes_page_rows():
    """(v6.11.0 thread-offload helper) mode registry rows + current trading mode."""
    with db.conn() as c:
        rows = c.execute("SELECT * FROM mode_registry ORDER BY code").fetchall()
        current = db.get_setting("trading_mode", "SIGNALS_ONLY")
    return rows, current


def _mode_row(code: str):
    """(v6.11.0 thread-offload helper) one mode_registry row by code."""
    with db.conn() as c:
        return c.execute("SELECT * FROM mode_registry WHERE code=?", (code,)).fetchone()


def _market_catalog_all() -> list[dict]:
    """(v6.11.0 thread-offload helper) the whole market_catalog as dicts."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM market_catalog ORDER BY symbol").fetchall()]


def _market_state(symbol: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one market."""
    with db.conn() as c:
        return c.execute("SELECT enabled,verified FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()


def _market_toggle_apply(symbol: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one market."""
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET enabled=?,updated_at=? WHERE symbol=?",
                  (new_val, config.now(), symbol))


def _market_provider_symbol(symbol: str):
    """(v6.11.0 thread-offload helper) provider symbol of one market row."""
    with db.conn() as c:
        return c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()


def _market_verify_apply(symbol: str, verified: int) -> None:
    """(v6.11.0 thread-offload helper) write the verified flag of one market."""
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET verified=?, updated_at=? WHERE symbol=?", (verified, config.now(), symbol))


def _timeframe_rows() -> list:
    """(v6.11.0 thread-offload helper) the whole timeframe_catalog."""
    with db.conn() as c:
        return c.execute("SELECT * FROM timeframe_catalog ORDER BY code").fetchall()


def _tf_state(code: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one timeframe."""
    with db.conn() as c:
        return c.execute("SELECT enabled,verified FROM timeframe_catalog WHERE code=?", (code,)).fetchone()


def _tf_toggle_apply(code: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one timeframe."""
    with db.conn() as c:
        c.execute("UPDATE timeframe_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (new_val, config.now(), code))


def _verified_provider_symbol():
    """(v6.11.0 thread-offload helper) one verified market to probe TFs against."""
    with db.conn() as c:
        return c.execute("SELECT provider_symbol FROM market_catalog WHERE verified=1 LIMIT 1").fetchone()


def _tf_verify_apply(code: str, verified: int) -> None:
    """(v6.11.0 thread-offload helper) write the verified flag of one timeframe."""
    with db.conn() as c:
        c.execute("UPDATE timeframe_catalog SET verified=?, updated_at=? WHERE code=?", (verified, config.now(), code))


def _broker_rows() -> list:
    """(v6.11.0 thread-offload helper) all broker_adapters, newest first."""
    with db.conn() as c:
        return c.execute("SELECT * FROM broker_adapters ORDER BY id DESC").fetchall()


def _broker_adapter(bid: int):
    """(v6.11.0 thread-offload helper) one full broker_adapter row."""
    with db.conn() as c:
        return c.execute("SELECT * FROM broker_adapters WHERE id=?", (bid,)).fetchone()


def _broker_status(bid: int):
    """(v6.11.0 thread-offload helper) approval status of one broker_adapter."""
    with db.conn() as c:
        return c.execute("SELECT status FROM broker_adapters WHERE id=?", (bid,)).fetchone()


def _broker_status_apply(bid: int, new_status: str) -> None:
    """(v6.11.0 thread-offload helper) write broker_adapter status after a test."""
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status=?, updated_at=? WHERE id=?", (new_status, config.now(), bid))


def _broker_approve_apply(bid: int) -> None:
    """(v6.11.0 thread-offload helper) mark one broker_adapter ACTIVE+enabled."""
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status='ACTIVE', enabled=1, updated_at=? WHERE id=?", (config.now(), bid))


def _broker_delete_row(bid: int) -> None:
    """(v6.11.0 thread-offload helper) erase one broker_adapter."""
    with db.conn() as c:
        c.execute("DELETE FROM broker_adapters WHERE id=?", (bid,))


def _account_rows() -> list:
    """(v6.11.0 thread-offload helper) latest linked user_accounts with names."""
    with db.conn() as c:
        return c.execute("""SELECT a.*, u.full_name FROM user_accounts a LEFT JOIN users u ON u.user_id=a.user_id
                            ORDER BY a.id DESC LIMIT 10""").fetchall()


def _demo_overview_rows():
    """(v6.11.0 thread-offload helper) demo counters + recent positions, ONE trip."""
    with db.conn() as c:
        accounts = c.execute("SELECT COUNT(*) n FROM demo_accounts").fetchone()["n"]
        open_n = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE status='OPEN'").fetchone()["n"]
        closed = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) s FROM demo_positions WHERE status!='OPEN'").fetchone()
        recent = c.execute("""SELECT p.*, u.full_name FROM demo_positions p LEFT JOIN users u ON u.user_id=p.user_id
                              ORDER BY p.id DESC LIMIT 8""").fetchall()
    return accounts, open_n, closed, recent
