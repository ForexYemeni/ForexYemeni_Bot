"""⚖️ v8.14.23 — «الميزان الملكي» — the Royal Scale UI.

The owner's measure-before-trust instrument: pick the market, timeframe,
history depth and strategy (or ALL), run the REAL walk-forward backtest
engine through fysb/services/royal_scale.py, and receive the honest
before/after board — baseline, the TP1-bank honest floor, the quality grid,
the quarantine simulation and (deep, owner-triggered) the relaxed-gate
"what if" variants. Nothing is fabricated; empty histories say so; errors
carry their real reason. The decision stays the owner's.
"""
from __future__ import annotations

from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.ui import keyboards as kb

ALL_LABEL = "الكل"
DEPTH_PRESETS = ((400, "سريع · 400 شمعة"), (800, "قياسي · 800 شمعة"), (1500, "عميق · 1500 شمعة"))
TF_PRESETS = ("M5", "M15", "M30", "H1", "H4")
STRAT_QUICK = ("TREND", "ICT", "SMC", "PRICE_ACTION", "FIBONACCI", "ICHIMOKU", "CLASSIC", "SK")


def _get_state(ud: dict) -> dict:
    st = ud.get("scale_state")
    if not isinstance(st, dict):
        st = {"symbol": "XAUUSD", "timeframe": "M15", "outputsize": 800, "strategy": None}
        ud["scale_state"] = st
    return st


def _card(st: dict) -> str:
    strat = st.get("strategy") or ALL_LABEL
    depth = dict(DEPTH_PRESETS).get(st.get("outputsize"), st.get("outputsize"))
    return (
        "⚖️ <b><u>الميزان الملكي — القياس قبل الثقة</u></b>\n"
        "<blockquote>"
        f"▎📊 السوق: <b>{st.get('symbol')}</b> · ⏱ الإطار: <b>{st.get('timeframe')}</b>\n"
        f"▎🗂 الاستراتيجية: <b>{strat}</b> · 📚 العمق: <b>{depth}</b>\n"
        "▎المحرك: walk-forward بلا إعادة رسم · بوابات الإنتاج نفسها · تسوية متحفظة\n"
        "▎الأرقام نموذجية على التاريخ — القياس لا يعد، القرار لك</blockquote>"
    )


def _home_kb(st: dict):
    return kb.admin_nav([
        [kb.btn("📊 السوق", "a:scale:m", style="primary"),
         kb.btn("⏱ الإطار", "a:scale:t", style="primary")],
        [kb.btn("🗂 الاستراتيجية", "a:scale:s", style="primary"),
         kb.btn("📚 العمق", "a:scale:d", style="primary")],
        [kb.btn("⚖️ شغّل الميزان", "a:scale:run", style="success")],
        [kb.btn("🧪 القياس العميق (ماذا لو الحواجز)", "a:scale:deep", style="primary")],
        [kb.btn("🧭 مركز التقارير", "a:reports", style="primary")],
    ])


async def scale_home(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    st = _get_state(context.user_data)
    await render(q, _card(st), _home_kb(st), bot)


def _top_symbols(limit: int = 24) -> list[str]:
    with db.conn() as c:
        live = [str(r["v"]) for r in c.execute(
            "SELECT symbol v, COUNT(*) n FROM signals GROUP BY symbol ORDER BY n DESC LIMIT ?",
            (limit,)).fetchall()]
    with db.conn() as c:
        gold = [str(r["symbol"]) for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 ORDER BY symbol LIMIT ?",
            (limit,)).fetchall()]
    seen: set[str] = set()
    out: list[str] = []
    for v in live + gold + ["XAUUSD"]:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out[:limit]


async def scale_pick(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, kind: str):
    st = _get_state(context.user_data)
    if kind == "m":
        syms = await afn.run(_top_symbols)
        rows = [[kb.btn("XAUUSD", "a:scale:set:m:XAUUSD", style="success")]]
        pair: list = []
        for s in syms[:22]:
            if s == "XAUUSD":
                continue
            pair.append(kb.btn(s, f"a:scale:set:m:{s}", style="primary"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([kb.btn("↩️ الميزان", "a:scale", style="primary")])
        await render(q, "📊 <b>اختر سوق الميزان</b>\n<i>الأنشط في سجلك أولاً + الأسواق المفعّلة.</i>",
                     kb.admin_nav(rows), bot)
        return
    if kind == "t":
        rows = []
        pair: list = []
        for tf in TF_PRESETS:
            pair.append(kb.btn(tf, f"a:scale:set:t:{tf}", style="primary"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([kb.btn("↩️ الميزان", "a:scale", style="primary")])
        await render(q, "⏱ <b>اختر إطار الميزان</b>", kb.admin_nav(rows), bot)
        return
    if kind == "d":
        rows = [[kb.btn(label, f"a:scale:set:d:{size}", style="primary")]
                for size, label in DEPTH_PRESETS]
        rows.append([kb.btn("↩️ الميزان", "a:scale", style="primary")])
        await render(q, "📚 <b>اختر عمق التاريخ</b>\n<i>كلما زاد العمق زادت صدق العينة — وأطول القياس.</i>",
                     kb.admin_nav(rows), bot)
        return
    if kind == "s":
        rows = [[kb.btn(f"🌐 {ALL_LABEL}", "a:scale:set:s:-", style="success")]]
        pair = []
        for code in STRAT_QUICK:
            pair.append(kb.btn(code, f"a:scale:set:s:{code}", style="primary"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([kb.btn("↩️ الميزان", "a:scale", style="primary")])
        await render(q, "🗂 <b>اختر استراتيجية الميزان</b>\n<i>أو «الكل» لقياس المحفظة كاملة.</i>",
                     kb.admin_nav(rows), bot)
        return


async def scale_set(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, kind: str, val: str):
    st = _get_state(context.user_data)
    if kind == "m":
        st["symbol"] = (val or "XAUUSD")[:16]
    elif kind == "t":
        st["timeframe"] = (val or "M15")[:4]
    elif kind == "d":
        try:
            st["outputsize"] = max(200, min(2000, int(val)))
        except (TypeError, ValueError):
            st["outputsize"] = 800
    elif kind == "s":
        st["strategy"] = None if val in ("-", "") else val[:24]
    await render(q, _card(st), _home_kb(st), bot)


async def scale_run(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, deep: bool = False):
    """Run the REAL engine through afn.run (sync helper) — honest errors surface."""
    st = _get_state(context.user_data)
    from fysb.services import royal_scale
    strategies = [st["strategy"]] if st.get("strategy") else None
    await render(q,
                 f"⏳ <b>الميزان يعمل الآن على {st.get('symbol')} · {st.get('timeframe')}</b>\n"
                 "<blockquote>▎يفحص التاريخ شمعة بشمعة ببوابات الإنتاج — القياس العميق "
                 "قد يستغرق دقيقة أو أكثر.</blockquote>",
                 kb.admin_nav([[kb.btn("🧭 مركز التقارير", "a:reports", style="primary")]]), bot)

    def _job():
        return royal_scale.run_scale(symbol=st.get("symbol") or "XAUUSD",
                                     timeframe=st.get("timeframe") or "M15",
                                     strategies=strategies,
                                     outputsize=int(st.get("outputsize") or 800),
                                     deep=deep)

    try:
        res = await afn.run(_job)
        body = await afn.run(royal_scale.render_scale_report, res)
        await afn.run(audit_log.log, uid,
                      f"royal_scale:{st.get('symbol')}:{st.get('timeframe')}:"
                      f"{st.get('strategy') or 'ALL'}:{int(st.get('outputsize') or 800)}:"
                      f"{'deep' if deep else 'std'}")
    except Exception as e:
        body = ("⛔ <b>تعذر إتمام القياس — السبب الحقيقي:</b>\n"
                f"<blockquote>▎{str(e)[:200]}</blockquote>\n"
                "<i>جرّب عمقاً أقل أو سوقاً آخر — والسبب يُعرض لا يُبتلع.</i>")
    rows = [
        [kb.btn("🔄 إعادة القياس", "a:scale:run", style="success"),
         kb.btn("🧪 القياس العميق", "a:scale:deep", style="primary")],
        [kb.btn("⚖️ إعدادات الميزان", "a:scale", style="primary"),
         kb.btn("🧭 مركز التقارير", "a:reports", style="primary")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)
