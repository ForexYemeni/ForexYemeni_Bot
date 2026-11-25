"""Analytics service — the user-facing «التحليلات» engine. 100% real data.

The flow is FULLY AUTOMATIC: no "tool chooser" step. Every analysis runs the
complete institutional composite at once — market structure, liquidity sweeps,
fair-value gaps, order blocks (supply & demand), premium/discount, momentum
and volatility — and presents it in one luxury Arabic card.

Selections (all gated by the user's plan):
  * market   : plan markets only
  * timeframe: plan timeframes, or "ALL" -> top-down multi-timeframe reading
  * strategy : plan strategies, or "ALL" -> run every allowed methodology and
               lead with the highest-scoring verdict

Quota: plans.max_analyses_per_day (0 = disabled). The INSERT is the atomic
consumption inside a transaction.
"""
import json

from fysb import config, db, instruments
from fysb.engines import market_data
from fysb.engines.analysis import (
    atr, atr_pct, breakout_retest, candle_patterns, ema, fair_value_gaps,
    kill_zone, liquidity_sweep, order_blocks, premium_discount, rsi,
    structure_labels, support_resistance,
)
from fysb.engines.strategies import REGISTRY, evaluate_all
from fysb.engines.strategies.base import Ctx
from fysb.theme import KILLZONE_AR, PATTERN_AR, STRUCTURE_AR, strategy_name

ANALYSIS_TYPES = [
    ("SCALPING", "سكالبينغ — دقائق إلى ساعة", ["M1", "M5", "M15"]),
    ("INTRADAY", "يومي — خلال اليوم", ["M15", "M30", "H1"]),
    ("SWING", "سوينغ — أيام", ["H1", "H4", "D1"]),
    ("POSITION", "متوسط المدى — أسابيع", ["D1", "W1"]),
]

ALL_TF_ORDER = ["M5", "M15", "M30", "H1", "H4", "D1"]   # cap at 4 lower-up

BIAS_TF_BY_TYPE = {"SCALPING": "1h", "INTRADAY": "4h", "SWING": "1day", "POSITION": "1week"}


def analytics_enabled() -> bool:
    return db.get_setting("analytics_enabled", "1") == "1"


def quota_state(user_id: int) -> dict:
    """Daily analyses quota for the user's plan (server-enforced)."""
    from fysb.services.subscriptions import plan_for
    plan = plan_for(user_id)
    quota = int(plan["max_analyses_per_day"] or 0) if plan else 0
    with db.conn() as c:
        used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                         (user_id, db.day_start_utc())).fetchone()["n"]
    return {"plan": plan["code"] if plan else "FREE", "quota": quota,
            "used": used, "remaining": max(0, quota - used)}


def _market_row(symbol: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def _fetch(symbol: str, tf_code: str, size: int = 150):
    row = _market_row(symbol)
    provider = row["provider_symbol"] if row else symbol
    interval = market_data.parse_interval(tf_code)
    payload = market_data.fetch_candles(provider, interval, config.market_api_key(), outputsize=size)
    return payload, row


def _one_frame_reading(symbol: str, tf: str, row) -> dict:
    """All measurable primitives for ONE timeframe (pure computation)."""
    payload, row = _fetch(symbol, tf)
    candles = payload["values"]
    closes = [c["close"] for c in candles]
    struct = structure_labels(candles)
    bias_struct = struct
    sweep = liquidity_sweep(candles)
    fvgs = fair_value_gaps(candles, 3)
    obs = order_blocks(candles, 2)
    pd = premium_discount(candles)
    sr = support_resistance(candles)
    patterns = candle_patterns(candles, 3)
    breakout = breakout_retest(candles)
    r14 = rsi(closes)
    a14 = atr(candles)
    a_pct = atr_pct(candles)
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    kz = kill_zone(candles[-1]["datetime"]) or ""

    bull = bear = 0
    if struct["trend"] == "up":
        bull += 1
    elif struct["trend"] == "down":
        bear += 1
    if e9 > e21 > e50:
        bull += 1
    elif e9 < e21 < e50:
        bear += 1
    if sweep["sell_side"]:
        bull += 1
    if sweep["buy_side"]:
        bear += 1
    if breakout["breakout"] == "UP":
        bull += 1
    elif breakout["breakout"] == "DOWN":
        bear += 1
    if pd["zone"] == "discount":
        bull += 1
    elif pd["zone"] == "premium":
        bear += 1

    return {
        "tf": tf, "candles": candles, "payload": payload, "row": row,
        "struct": struct, "sweep": sweep, "fvgs": fvgs, "obs": obs, "pd": pd,
        "sr": sr, "patterns": patterns, "breakout": breakout,
        "rsi": r14, "atr": a14, "atr_pct": a_pct,
        "e9": e9, "e21": e21, "e50": e50, "kz": kz,
        "bull": bull, "bear": bear,
        "close": closes[-1],
    }


def _dir_word(direction: str) -> str:
    return {"BUY": "صاعد", "SELL": "هابط", "NEUTRAL": "محايد/انتظاري"}.get(direction, "محايد")


def _pattern_ar(code: str) -> str:
    return PATTERN_AR.get(code, code)


def run_analysis(user_id: int, symbol: str, timeframe: str, analysis_type: str = "",
                 tool: str = "", strategy: str = "", enforce_plan: bool = True) -> dict:
    """Generate ONE real-data composite analysis.

    timeframe == "ALL"  -> multi-timeframe top-down reading (plan TFs, max 4)
    strategy  == "ALL"  -> evaluate every allowed methodology, lead with best
    enforce_plan    -> server-side package gate (markets/timeframes/strategy);
                       False only for admin-channel scheduled posts.
    Returns dict with ok/analysis/error (+quota when ok).
    """
    if not analytics_enabled():
        return {"ok": False, "error": "قسم التحليلات معطل حالياً من الإدارة"}
    symbol = instruments.normalize(symbol)
    p_tfs: list[str] = []
    if enforce_plan:
        # Real-user path: server-side package gate + atomic daily quota.
        state = quota_state(user_id)
        if state["quota"] <= 0:
            return {"ok": False, "quota_exhausted": True, "quota": state,
                    "error": "التحليلات غير مفعلة لباقتك — تواصل مع الإدارة أو رقّ باقتك"}
        if state["remaining"] <= 0:
            return {"ok": False, "quota_exhausted": True, "quota": state,
                    "error": f"انتهت حصة التحليلات اليومية ({state['used']}/{state['quota']}) — تتجدد يومياً"}
        from fysb.services.subscriptions import plan_for
        plan = plan_for(user_id)
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        if p_markets and symbol.upper() not in p_markets:
            return {"ok": False, "error": "⛔ هذا السوق غير مشمول في باقتك — افتح بطاقة الترقية لفتحه"}
        if (timeframe or "").upper() != "ALL" and p_tfs and timeframe.upper() not in p_tfs:
            return {"ok": False, "error": "⛔ هذا الإطار الزمني غير مشمول في باقتك"}
        if (strategy or "").upper() != "ALL" and strategy and p_strats \
                and strategy.upper() not in p_strats:
            return {"ok": False, "error": "⛔ هذه المنهجية غير مشمولة في باقتك"}

    row = _market_row(symbol)
    multi_tf = (timeframe or "").upper() == "ALL"
    allowed_tfs = list(ALL_TF_ORDER[:4])
    if enforce_plan and multi_tf and p_tfs:
        # v8.0.0 (SIG-2): Respect user's plan timeframes when timeframe == "ALL"
        plan_tf_set = {t.upper() for t in p_tfs}
        filtered_tfs = [t for t in ALL_TF_ORDER if t in plan_tf_set]
        if filtered_tfs:
            allowed_tfs = filtered_tfs[:4]
    try:
        if multi_tf:
            frames = [_one_frame_reading(symbol, tf, row) for tf in allowed_tfs]
        else:
            frames = [_one_frame_reading(symbol, timeframe, row)]
    except RuntimeError as e:
        return {"ok": False, "error": f"تعذر جلب بيانات السوق: {e}"}

    from fysb.engines.analysis import confirmed_candles as _confirmed_candles
    main = frames[-1]          # the highest requested TF drives the card levels
    # v8.0.0 (SIG-2): Strip forming unclosed candle AND resolve real HTF bias_candles!
    candles = _confirmed_candles(main["candles"], main["tf"]) or main["candles"]
    if len(candles) < 40:
        return {"ok": False, "error": f"بيانات غير كافية ({len(candles)} شمعة)"}

    bias_tf_map = {"M5": "H1", "M15": "H1", "M30": "H4", "H1": "H4", "H4": "D1", "D1": "D1"}
    bias_tf = bias_tf_map.get(main["tf"].upper(), "H4")
    bias_candles = candles
    if len(frames) > 1 and frames[-1]["tf"] != frames[0]["tf"]:
        bias_candles = _confirmed_candles(frames[-1]["candles"], frames[-1]["tf"]) or frames[-1]["candles"]
        candles = _confirmed_candles(frames[0]["candles"], frames[0]["tf"]) or frames[0]["candles"]
    elif bias_tf != main["tf"].upper():
        try:
            htf_fr = _one_frame_reading(symbol, bias_tf, row)
            if len(htf_fr["candles"]) >= 30:
                bias_candles = _confirmed_candles(htf_fr["candles"], bias_tf) or htf_fr["candles"]
        except Exception:
            bias_candles = candles

    payload = main["payload"]
    market_open = market_data.market_state((row or {}).get("category") if row else instruments.spec(symbol).category)
    stale = market_data.is_stale(payload)

    # --- composite direction votes (weighted: higher TF counts double) ---
    bull = bear = 0
    for i, fr in enumerate(frames):
        weight = 2 if i == len(frames) - 1 and len(frames) > 1 else 1
        bull += fr["bull"] * weight
        bear += fr["bear"] * weight
    if bull >= bear + 3:
        direction = "BUY"
    elif bear >= bull + 3:
        direction = "SELL"
    else:
        direction = "NEUTRAL"
    quality = int(min(95, 45 + max(bull, bear) * 6))

    # --- strategy lens (plan-gated; ALL = evaluate every allowed methodology + consensus_filter) ---
    lens_result = None
    lens_name = strategy_name(strategy) if strategy else ""
    if (strategy or "").upper() == "ALL":
        from fysb.engines.strategies import VERIFIABLE, consensus_filter
        from fysb.db import IMPLEMENTED_STRATEGIES
        with db.conn() as c:
            ok_strats = [r["code"] for r in c.execute(
                "SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchall()]
        if not ok_strats:
            ok_strats = list(IMPLEMENTED_STRATEGIES)
        allowed = [s for s in ok_strats if s in VERIFIABLE]
        ctx = Ctx(symbol=symbol, timeframe=main["tf"], candles=candles,
                  bias_candles=bias_candles, market_row=row, kill_zone=main["kz"])
        try:
            raw_res = evaluate_all(ctx, allowed)
            results, _cnote = consensus_filter(raw_res)
            if results:
                lens_result = results[0]
                lens_name = "منظومة مؤسسية مركبة"
        except Exception:
            lens_result = None
    elif strategy:
        module = REGISTRY.get((strategy or "").upper())
        if module is not None:
            try:
                ctx = Ctx(symbol=symbol, timeframe=main["tf"], candles=candles,
                          bias_candles=bias_candles, market_row=row, kill_zone=main["kz"])
                lens_result = module.evaluate(ctx)
            except Exception:
                lens_result = None

    # --- display helpers ---
    def fmt(p):
        return instruments.fmt_price(symbol, p, row)

    struct_ar = "، ".join(STRUCTURE_AR.get(x, x) for x in main["struct"]["labels"][-4:]) or "—"

    zones = []
    if main["sr"].get("resistance"):
        zones.append(f"🟥 مقاومة هيكلية: <code>{fmt(main['sr']['resistance'])}</code>")
    if main["sr"].get("support"):
        zones.append(f"🟩 دعم هيكلي: <code>{fmt(main['sr']['support'])}</code>")
    for f in main["fvgs"][:2]:
        zones.append(f"{'🟢' if f['kind'] == 'bullish' else '🔴'} فجوة قيمة {'صاعدة' if f['kind'] == 'bullish' else 'هابطة'}: "
                     f"<code>{fmt(f['lo'])} — {fmt(f['hi'])}</code>")
    for ob in main["obs"][:2]:
        zones.append(f"{'🟢' if ob['kind'] == 'bullish' else '🔴'} منطقة أوامر مؤسسية ({'شراء' if ob['kind'] == 'bullish' else 'بيع'}): "
                     f"<code>{fmt(ob['lo'])} — {fmt(ob['hi'])}</code>")
    if main["pd"].get("high") and main["pd"].get("low"):
        zone_word = {"premium": "منطقة تعزز بيع", "discount": "منطقة تعزز شراء",
                     "equilibrium": "توازن سعري", "unknown": "غير محدد"}[main["pd"]["zone"]]
        zones.append(f"⚖️ نطاق التعامل: <code>{fmt(main['pd']['low'])} — {fmt(main['pd']['high'])}</code> (الوضع: <b>{zone_word}</b>)")
    if main["sweep"].get("level"):
        side = "سيولة شراء BSL" if main["sweep"]["buy_side"] else "سيولة بيع SSL"
        zones.append(f"💧 {side} مسحوبة عند: <code>{fmt(main['sweep']['level'])}</code> — الكسور الكاذبة هنا تُصنع ثم تُعاد")

    summary = []
    if multi_tf:
        votes = " · ".join(f"{f['tf']} {_dir_word('BUY' if f['bull'] > f['bear'] else 'SELL' if f['bear'] > f['bull'] else 'NEUTRAL')}"
                           for f in frames)
        summary.append(f"قراءة الأطباق من الأدنى للأعلى: {votes}")
    summary += [
        f"الاتجاه الهيكلي: <b>{ {'up':'صاعد','down':'هابط','range':'عرضي/نطاق'}[main['struct']['trend']] }</b> — آخر العلامات: {struct_ar}",
        f"كسر هيكلي (BOS): <b>{'نعم' if main['struct']['bos'] else 'لا'}</b> · تغير اتجاهي (CHOCH): <b>{'نعم' if main['struct']['choch'] else 'لا'}</b>",
        f"المتوسطات المتحركة 9/21/50: {'ترتيب صاعد' if main['e9'] > main['e21'] > main['e50'] else 'ترتيب هابط' if main['e9'] < main['e21'] < main['e50'] else 'متداخل — سوق بلا اتجاه واضح'}",
        f"مؤشر القوة النسبية: <b>{main['rsi']:.1f}</b> ({'تشبع شرائي' if main['rsi'] >= 70 else 'تشبع بيعي' if main['rsi'] <= 30 else 'نطاق متوازن'})",
        f"مدى التقلب: <code>{main['atr']:.5g}</code> نقطة · التقلب النسبي: <b>{main['atr_pct']:.2f}%</b> "
        f"({'مرتفع' if main['atr_pct'] > 3 else 'طبيعي' if main['atr_pct'] > 0.5 else 'منخفض'})",
        f"أنماط الشموع الحديثة: {'، '.join(_pattern_ar(p) for p in main['patterns']) or 'لا أنماط بارزة'}",
    ]
    if main["breakout"]["breakout"]:
        summary.append(f"كسر {'صاعد' if main['breakout']['breakout'] == 'UP' else 'هابط'} عند <code>{fmt(main['breakout']['level'])}</code>"
                       + (" مع إعادة اختبار ناجحة" if main["breakout"]["retest"] else " (بدون إعادة اختبار بعد)"))
    if main["kz"]:
        summary.append(f"نافذة زمنية نشطة: <b>{KILLZONE_AR.get(main['kz'], main['kz'])}</b>")

    # --- recommendation ---
    if lens_result is not None:
        direction = lens_result.direction
        quality = max(quality, lens_result.score)
        lens_dir = "شراء" if lens_result.direction == "BUY" else "بيع"
        recommendation = (f"{lens_name} تمنح توصية <b>{lens_dir}</b> على إطار {main['tf']} "
                          f"بجودة سياق {lens_result.score}/100 — "
                          f"{'البحث عن فرص الشراء في مناطق القيمة' if direction == 'BUY' else 'البحث عن فرص البيع في مناطق التعزز' if direction == 'SELL' else 'انتظار سياق أوضح'}")
    else:
        recommendation = {
            "BUY": "انحياز صاعد — البحث عن فرص شراء في مناطق القيمة والخصم",
            "SELL": "انحياز هابط — البحث عن فرص بيع في مناطق التعزز والعلاوة",
            "NEUTRAL": "انتظاري — لا انحياز واضح، الانتظار أفضل من الإجبار"}[direction]

    if direction == "BUY":
        activation = (f"ثبات الشموع فوق <code>{fmt(main['sr']['resistance'])}</code>"
                      if main["sr"].get("resistance") else
                      f"إغلاق فوق آخر قمة متأرجحة <code>{fmt(max(c['high'] for c in candles[-10:]))}</code>")
        invalidation = fmt(min(c["low"] for c in candles[-10:]))
    elif direction == "SELL":
        activation = (f"كسر وثبات تحت <code>{fmt(main['sr']['support'])}</code>"
                      if main["sr"].get("support") else
                      f"إغلاق تحت آخر قاع متأرجح <code>{fmt(min(c['low'] for c in candles[-10:]))}</code>")
        invalidation = fmt(max(c["high"] for c in candles[-10:]))
    else:
        activation = "انتظار كسر مؤكد لأحد طرفي النطاق مع إغلاق خارج النطاق"
        invalidation = "غير منطبق في الوضع الانتظاري"

    risk_note = "التزم بحجم مخاطرة 1-2% من رأس المال لكل فكرة، وطبّق وقف الخسارة المخطط بلا مساومة — الانضباط التنفيذي هو ما يحول التحليل إلى نتائج."

    tf_label = "متعدد الأطباق (من الأدنى للأعلى)" if multi_tf else main["tf"]
    analysis = {
        "symbol": symbol, "timeframe": tf_label, "tool": "", "tool_name": "منظومة مؤسسية مركبة",
        "analysis_type": analysis_type, "strategy": strategy,
        "strategy_name": lens_name or "قراءة مؤسسية شاملة",
        "price": main["close"], "price_time": payload["last_candle_iso"],
        "direction": direction, "quality": quality,
        "summary": summary, "zones": zones,
        "recommendation": recommendation, "activation": activation,
        "invalidation": invalidation, "risk_note": risk_note,
        "market_open": market_open, "stale": stale,
    }
    try:
        if enforce_plan:
            # Quota consumption is atomic and applies to REAL users only —
            # system/channel posts (enforce_plan=False) never burn quota.
            with db.tx(immediate=True) as c:
                used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                                 (user_id, db.day_start_utc())).fetchone()["n"]
                plan_quota = quota_state(user_id)["quota"]
                if plan_quota <= 0 or used >= plan_quota:
                    return {"ok": False, "quota_exhausted": True,
                            "error": f"انتهت حصة التحليلات اليومية ({used}/{plan_quota}) — تتجدد يومياً"}
                c.execute("""INSERT INTO analyses(user_id,symbol,timeframe,tool,analysis_type,strategy,
                             price,direction,quality,summary,detail,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (user_id, symbol, tf_label, "", analysis_type, strategy,
                           main["close"], direction, quality, recommendation,
                           json.dumps({"summary": summary, "zones": zones,
                                       "activation": activation, "invalidation": invalidation}),
                           config.now()))
    except Exception as e:
        return {"ok": False, "error": f"تعذر حفظ التحليل: {type(e).__name__}"}
    return {"ok": True, "analysis": analysis, "quota": quota_state(user_id) if enforce_plan else None}
