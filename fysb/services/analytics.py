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


_CONFIRM_TF_SPEC: dict[str, tuple[str, str, str]] = {
    # analyzed_tf: (stability_tf, stability_ar, confirm_tf_ar)
    "M1": ("M1", "إغلاق شمعتَي M1 (دقيقة) متتاليتين بجسم حقيقي", "M1 (الدقيقة)"),
    "M5": ("M5", "إغلاق شمعة M5 (5 دقائق) كاملة بجسم حقيقي", "M1 (الدقيقة)"),
    "M15": ("M15", "إغلاق شمعة M15 (ربع ساعة) كاملة بجسم حقيقي", "M5 (5 دقائق)"),
    "M30": ("M30", "إغلاق شمعة M30 (نصف ساعة) كاملة بجسم حقيقي", "M15 (ربع ساعة) أو M5"),
    "H1": ("H1", "إغلاق شمعة H1 (الساعة) كاملة بجسم حقيقي", "M15 (ربع ساعة)"),
    "H4": ("H4", "إغلاق شمعة H4 (4 ساعات) كاملة بجسم حقيقي", "H1 (الساعة) أو M15"),
    "D1": ("D1", "إغلاق شمعة D1 (يومي) أو H4 كاملة بجسم حقيقي", "H1 (الساعة)"),
}

_TF_SECONDS_MAP: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}


def _parse_candle_open_utc(ts_raw: str):
    from datetime import datetime, timezone
    s = str(ts_raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for fmt_s in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt_s).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _latest_closed_candle_for_tf(candles: list[dict], tf: str) -> tuple[dict, bool]:
    """Return `(closed_candle, last_is_forming)` aligned 100% with True Analysis radar.

    Provider timestamps mark candle OPEN time in UTC. If the newest candle's
    `open_dt + tf_sec > now_utc`, it is still forming and we evaluate the
    preceding truly closed candle instead so the Analysis Card and Live Radar
    NEVER contradict each other!
    """
    from datetime import datetime, timedelta, timezone
    if not candles:
        return {}, False
    tf_sec = _TF_SECONDS_MAP.get(str(tf or "M15").upper(), 900)
    now_dt = datetime.now(timezone.utc)
    last_c = candles[-1]
    last_open = _parse_candle_open_utc(last_c.get("datetime") or "")
    if last_open is not None and (last_open + timedelta(seconds=tf_sec)) > now_dt:
        if len(candles) >= 2:
            return candles[-2], True
        return {}, True
    return last_c, False


def _detect_pullback_zone(
    *,
    direction: str,
    cur_close: float,
    activation_px: float,
    invalidation_px: float,
    atr: float,
    obs: list[dict],
    fvgs: list[dict],
) -> dict:
    """Detect an actionable Institutional OB / FVG retracement zone (v8.5.0).

    Returns `{entry_mode, zone_lo, zone_hi, zone_kind}`:
      - `PULLBACK_ZONE`: clean bullish OB/FVG below/at price (BUY) or bearish OB/FVG
        above/at price (SELL) strictly inside safe structure and within 2.4x ATR.
      - `BREAKOUT`: default structural breakout mode.
    """
    is_buy = str(direction).upper() == "BUY"
    want_kind = "bullish" if is_buy else "bearish"
    max_dist = max(atr * 2.4, abs(activation_px - cur_close) * 1.5, 1e-6)
    candidates: list[tuple[float, str, float, float]] = []

    for src_kind, items in (("OB", obs or []), ("FVG", fvgs or [])):
        for z in items:
            if str(z.get("kind") or "").lower() != want_kind:
                continue
            lo = float(z.get("lo") or 0.0)
            hi = float(z.get("hi") or 0.0)
            if lo <= 0 or hi <= 0 or hi <= lo:
                continue
            if is_buy:
                # Demand zone must sit at/below current price & activation, and above invalidation
                if hi <= cur_close * 1.0015 and lo > invalidation_px and hi < activation_px:
                    dist = max(0.0, cur_close - hi)
                    if dist <= max_dist:
                        candidates.append((dist, src_kind, lo, hi))
            else:
                # Supply zone must sit at/above current price & activation, and below invalidation
                if lo >= cur_close * 0.9985 and hi < invalidation_px and lo > activation_px:
                    dist = max(0.0, lo - cur_close)
                    if dist <= max_dist:
                        candidates.append((dist, src_kind, lo, hi))

    if not candidates:
        return {"entry_mode": "BREAKOUT", "zone_lo": 0.0, "zone_hi": 0.0, "zone_kind": ""}

    # Pick closest valid institutional zone (prefer OB on near-ties)
    candidates.sort(key=lambda t: (round(t[0], 6), 0 if t[1] == "OB" else 1))
    _, z_kind, z_lo, z_hi = candidates[0]
    return {"entry_mode": "PULLBACK_ZONE", "zone_lo": z_lo, "zone_hi": z_hi, "zone_kind": z_kind}


def _build_real_confirmation_spec(
    *,
    symbol: str,
    row,
    tf: str,
    direction: str,
    candles: list[dict],
    main: dict,
    activation_px: float,
    invalidation_px: float,
    fmt,
) -> dict:
    """Derive 100% real, closed-candle-grounded confirmation type, stability TF,
    ATR anti-fakeout filter, institutional pullback zone, and live activation state.
    Zero placeholder or synthetic claims — every figure comes from real OHLC data
    and matches True Analysis live radar 100%.
    """
    tf_u = str(tf or "M15").upper()
    stab_tf, stab_ar, conf_tf_ar = _CONFIRM_TF_SPEC.get(
        tf_u,
        (tf_u, f"إغلاق شمعة {tf_u} كاملة بجسم حقيقي", tf_u),
    )
    live_bar = candles[-1] if candles else {}
    cur_close = float(live_bar.get("close") or main.get("close") or 0.0)
    closed_bar, last_is_forming = _latest_closed_candle_for_tf(candles, stab_tf)
    eval_bar = closed_bar or live_bar
    ev_close = float(eval_bar.get("close") or cur_close)
    ev_open = float(eval_bar.get("open") or ev_close)
    ev_hi = float(eval_bar.get("high") or ev_close)
    ev_lo = float(eval_bar.get("low") or ev_close)
    c_rng = max(ev_hi - ev_lo, 1e-9)
    body_pct = int(round(100.0 * abs(ev_close - ev_open) / c_rng)) if closed_bar else 0
    is_bull_candle = ev_close > ev_open
    is_bear_candle = ev_close < ev_open

    atr = float(main.get("atr") or 0.0)
    buffer_px = max(atr * 0.15, 0.0)
    buffer_pips = round(instruments.pips(symbol, cur_close, cur_close + buffer_px, row), 1) if (buffer_px > 0 and cur_close > 0) else 0.0
    dist_pips = round(instruments.pips(symbol, cur_close, activation_px, row), 1) if (activation_px > 0 and cur_close > 0) else 0.0

    struct = main.get("struct") or {}
    brk = main.get("breakout") or {}
    sweep = main.get("sweep") or {}
    obs = main.get("obs") or []
    fvgs = main.get("fvgs") or []

    anti_fakeout = (
        f"إغلاق جسم شمعة {stab_tf} كاملة (≥ 55% من مدى الشمعة) بفارق حقيقي ≥ <code>{buffer_pips:.1f}</code> نقطة "
        f"(0.15×ATR) — لا يُعتد بالذيل ولا بالشمعة قبل إغلاقها لمنع الكسر الكاذب"
        if buffer_pips > 0 else
        f"إغلاق جسم شمعة {stab_tf} كاملة (≥ 55% من مدى الشمعة) خارج المستوى — لا يُعتد بالذيل ولا بالشمعة قبل إغلاقها"
    )

    if direction == "NEUTRAL":
        hi_rng = float(main["sr"].get("resistance") or max(c["high"] for c in candles[-15:]))
        lo_rng = float(main["sr"].get("support") or min(c["low"] for c in candles[-15:]))
        return {
            "stability_tf": stab_tf,
            "confirm_tf": conf_tf_ar,
            "stability_tf_label": f"<b>{stab_ar}</b> + تأكيد الدخول على <b>{conf_tf_ar}</b>",
            "type": f"كسر وإغلاق جسم شمعة {stab_tf} كاملة (≥ 55%) خارج النطاق <code>{fmt(lo_rng)} — {fmt(hi_rng)}</code> مع ثبات شمعة تالية",
            "anti_fakeout": anti_fakeout,
            "live_state": f"🟡 السوق داخل نطاق تذبذب حالياً عند <code>{fmt(cur_close)}</code> — بانتظار كسر وإغلاق حقيقي خارج النطاق",
            "invalidation_tf_note": "",
            "activated": False,
            "body_pct": body_pct,
            "buffer_pips": buffer_pips,
            "dist_pips": 0.0,
            "entry_mode": "BREAKOUT",
            "zone_lo": 0.0,
            "zone_hi": 0.0,
            "zone_kind": "",
        }

    is_buy = direction == "BUY"
    lvl_txt = f"<code>{fmt(activation_px)}</code>"
    inv_txt = f"<code>{fmt(invalidation_px)}</code>"
    rel_word = "فوق" if is_buy else "تحت"
    inv_word = "تحت" if is_buy else "فوق"
    candle_dir_ok = is_bull_candle if is_buy else is_bear_candle
    closed_beyond = bool(closed_bar) and ((ev_close > activation_px) if is_buy else (ev_close < activation_px))
    forming_beyond = last_is_forming and ((cur_close > activation_px) if is_buy else (cur_close < activation_px))
    clean_buffer_ok = bool(closed_bar) and (
        (ev_close >= activation_px + buffer_px) if is_buy else (ev_close <= activation_px - buffer_px)
    )
    has_retest = bool(brk.get("retest") and ((brk.get("breakout") == "UP") if is_buy else (brk.get("breakout") == "DOWN")))
    has_aligned_sweep = bool(sweep.get("sell_side") if is_buy else sweep.get("buy_side"))
    has_aligned_zone = any((o.get("kind") == ("bullish" if is_buy else "bearish")) for o in obs) or \
                       any((f.get("kind") == ("bullish" if is_buy else "bearish")) for f in fvgs)
    has_struct_break = bool(struct.get("bos") or struct.get("choch"))

    # v8.5.0: Detect actionable Institutional OB/FVG Pullback Zone
    pz = _detect_pullback_zone(
        direction=direction,
        cur_close=cur_close,
        activation_px=activation_px,
        invalidation_px=invalidation_px,
        atr=atr,
        obs=obs,
        fvgs=fvgs,
    )
    entry_mode = pz["entry_mode"]
    zone_lo = float(pz["zone_lo"] or 0.0)
    zone_hi = float(pz["zone_hi"] or 0.0)
    zone_kind = str(pz["zone_kind"] or "")

    activated = False
    if closed_beyond and candle_dir_ok and body_pct >= 55 and clean_buffer_ok:
        activated = True
        if has_retest:
            conf_type = f"كسر حقيقي مع إعادة اختبار ناجحة (Break & Retest مؤكّد) {rel_word} {lvl_txt}"
            live_state = f"🟢 مؤكّد ومفعّل فعلياً على إغلاق شمعة {stab_tf} مغلقة الآن (جسم حقيقي {body_pct}%)"
        else:
            conf_type = f"إغلاق جسم شمعة {'صاعدة' if is_buy else 'هابطة'} مغلقة ({body_pct}%) {rel_word} {lvl_txt} بفارق يتجاوز 0.15×ATR"
            live_state = f"🟢 الكسر مُغلق وثابت فعلياً على شمعة {stab_tf} المغلقة الآن — يُعزّز الدخول بتأكيد {conf_tf_ar}"
    elif closed_beyond and candle_dir_ok and body_pct >= 50:
        # Close is beyond level but inside 0.15xATR buffer -> wait for clean confirmation
        conf_type = f"إغلاق شمعة {stab_tf} {rel_word} {lvl_txt} (جسم {body_pct}%) — قريب من حد المستوى"
        live_state = f"🟡 إغلاق أولي على {stab_tf} قرب المستوى — بانتظار تأكيد شمعة {conf_tf_ar} إضافية لتأكيد الزخم"
    elif forming_beyond:
        conf_type = f"الشمعة الجارية ({stab_tf}) تختبر المستوى {rel_word} {lvl_txt} — التفعيل يتطلب إغلاقاً فعلياً كاملاً"
        live_state = (
            f"🟡 الشمعة الجارية ({stab_tf}) تتجاوز المستوى مؤقتاً عند <code>{fmt(cur_close)}</code> "
            f"— بانتظار إغلاقها الفعلي للتأكيد ومنع الكسر الكاذب"
        )
    elif closed_beyond:
        conf_type = f"اختراق ضعيف بذيول (جسم {body_pct}% فقط) {rel_word} {lvl_txt} — يتطلب إغلاق جسم حقيقي ≥ 55%"
        live_state = f"🟡 لم يثبت بزخم كافٍ بعد على {stab_tf} — انتظر إغلاق شمعة {stab_tf} بجسم قوي {rel_word} {lvl_txt}"
    else:
        if entry_mode == "PULLBACK_ZONE":
            z_ar = "منطقة أوامر مؤسسية (OB)" if zone_kind == "OB" else "فجوة قيمة مؤسسية (FVG)"
            conf_type = (
                f"دخول مؤسسي مزدوج: ارتداد ورفض صريح من {z_ar} "
                f"<code>{fmt(zone_lo)} — {fmt(zone_hi)}</code> أو كسر وإغلاق جسم {stab_tf} (≥ 55%) {rel_word} {lvl_txt}"
            )
        elif has_aligned_sweep:
            sw_tag = "سيولة البيع SSL" if is_buy else "سيولة الشراء BSL"
            conf_type = f"تأكيد بعد سحب {sw_tag} + كسر هيكلي وإغلاق جسم شمعة {stab_tf} كاملة (≥ 55%) {rel_word} {lvl_txt}"
        elif has_aligned_zone:
            conf_type = f"إغلاق جسم شمعة {stab_tf} كاملة (≥ 55%) {rel_word} {lvl_txt} + ثبات إعادة اختبار منطقة الأوامر/الفجوة دون كسر {inv_txt}"
        elif has_struct_break:
            conf_type = f"إغلاق شمعة {stab_tf} كاملة بجسم حقيقي (≥ 55%) {rel_word} {lvl_txt} لتأكيد استمرارية الكسر الهيكلي"
        else:
            conf_type = f"كسر حقيقي وإغلاق جسم شمعة {stab_tf} كاملة (≥ 55%) {rel_word} {lvl_txt} + ثبات شمعة {conf_tf_ar} تالية"
        live_state = f"⏳ بانتظار التفعيل الفعلي — السعر الحي (<code>{fmt(cur_close)}</code>) يبعد <b>{dist_pips:.1f} نقطة</b> عن مستوى التفعيل"

    return {
        "stability_tf": stab_tf,
        "confirm_tf": conf_tf_ar,
        "stability_tf_label": f"<b>{stab_ar}</b> + تأكيد الدخول على <b>{conf_tf_ar}</b>",
        "type": conf_type,
        "anti_fakeout": anti_fakeout,
        "live_state": live_state,
        "invalidation_tf_note": f"بإغلاق شمعة {stab_tf} كاملة {inv_word} المستوى",
        "activated": activated,
        "body_pct": body_pct,
        "buffer_pips": buffer_pips,
        "dist_pips": dist_pips,
        "entry_mode": entry_mode,
        "zone_lo": zone_lo,
        "zone_hi": zone_hi,
        "zone_kind": zone_kind,
    }


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

    # --- activation & invalidation ---
    activation_px = 0.0
    invalidation_px = 0.0
    brk_now = main.get("breakout") or {}
    if direction == "BUY":
        if brk_now.get("breakout") == "UP" and float(brk_now.get("level") or 0) > 0:
            activation_px = float(brk_now["level"])
        elif main["sr"].get("resistance"):
            activation_px = float(main["sr"]["resistance"])
        else:
            activation_px = float(max(c["high"] for c in candles[-10:]))
        invalidation_px = float(min(c["low"] for c in candles[-10:]))
        activation = f"ثبات الشموع فوق <code>{fmt(activation_px)}</code>"
        invalidation = fmt(invalidation_px)
    elif direction == "SELL":
        if brk_now.get("breakout") == "DOWN" and float(brk_now.get("level") or 0) > 0:
            activation_px = float(brk_now["level"])
        elif main["sr"].get("support"):
            activation_px = float(main["sr"]["support"])
        else:
            activation_px = float(min(c["low"] for c in candles[-10:]))
        invalidation_px = float(max(c["high"] for c in candles[-10:]))
        activation = f"كسر وثبات تحت <code>{fmt(activation_px)}</code>"
        invalidation = fmt(invalidation_px)
    else:
        activation = "انتظار كسر مؤكد لأحد طرفي النطاق مع إغلاق خارج النطاق"
        invalidation = "غير منطبق في الوضع الانتظاري"

    confirmation = _build_real_confirmation_spec(
        symbol=symbol,
        row=row,
        tf=main["tf"],
        direction=direction,
        candles=candles,
        main=main,
        activation_px=activation_px,
        invalidation_px=invalidation_px,
        fmt=fmt,
    )

    risk_note = "التزم بحجم مخاطرة 1-2% من رأس المال لكل فكرة، وطبّق وقف الخسارة المخطط بلا مساومة — الانضباط التنفيذي هو ما يحول التحليل إلى نتائج."

    # v8.1.0: Rich real-data payload for Ultra-Luxury Institutional Analysis Charts
    chart_bars = list(main.get("candles") or candles or [])[-95:]
    setup_overlay = None
    if lens_result is not None:
        setup_overlay = {
            "code": str(getattr(lens_result, "code", strategy or "") or "").upper(),
            "direction": str(getattr(lens_result, "direction", direction) or direction).upper(),
            "entry": float(getattr(lens_result, "entry", 0.0) or 0.0),
            "stop": float(getattr(lens_result, "stop", 0.0) or 0.0),
            "tp1": float(getattr(lens_result, "tp1", 0.0) or 0.0),
            "tp2": float(getattr(lens_result, "tp2", 0.0) or 0.0),
            "tp3": float(getattr(lens_result, "tp3", 0.0) or 0.0),
            "rr": float(getattr(lens_result, "rr", 0.0) or 0.0),
            "score": int(getattr(lens_result, "score", quality) or quality),
        }
    tf_votes = [
        (f["tf"], "BUY" if f["bull"] > f["bear"] else ("SELL" if f["bear"] > f["bull"] else "NEUTRAL"))
        for f in frames
    ]
    chart_data = {
        "candles": chart_bars,
        "tf": main["tf"],
        "multi_tf": multi_tf,
        "tf_votes": tf_votes,
        "sr": dict(main.get("sr") or {}),
        "obs": [dict(x) for x in (main.get("obs") or [])[:3]],
        "fvgs": [dict(x) for x in (main.get("fvgs") or [])[:3]],
        "pd": dict(main.get("pd") or {}),
        "sweep": dict(main.get("sweep") or {}),
        "struct": dict(main.get("struct") or {}),
        "breakout": dict(main.get("breakout") or {}),
        "e9": float(main.get("e9") or 0.0),
        "e21": float(main.get("e21") or 0.0),
        "e50": float(main.get("e50") or 0.0),
        "rsi": float(main.get("rsi") or 50.0),
        "atr": float(main.get("atr") or 0.0),
        "atr_pct": float(main.get("atr_pct") or 0.0),
        "kz": str(main.get("kz") or ""),
        "activation_px": activation_px,
        "invalidation_px": invalidation_px,
        "entry_mode": str(confirmation.get("entry_mode") or "BREAKOUT"),
        "zone_lo": float(confirmation.get("zone_lo") or 0.0),
        "zone_hi": float(confirmation.get("zone_hi") or 0.0),
        "zone_kind": str(confirmation.get("zone_kind") or ""),
        "setup": setup_overlay,
    }

    tf_label = "متعدد الأطباق (من الأدنى للأعلى)" if multi_tf else main["tf"]
    analysis = {
        "symbol": symbol, "timeframe": tf_label, "tool": "", "tool_name": "منظومة مؤسسية مركبة",
        "analysis_type": analysis_type, "strategy": strategy,
        "strategy_name": lens_name or "قراءة مؤسسية شاملة",
        "price": main["close"], "price_time": payload["last_candle_iso"],
        "direction": direction, "quality": quality,
        "summary": summary, "zones": zones,
        "recommendation": recommendation, "activation": activation,
        "invalidation": invalidation, "confirmation": confirmation,
        "risk_note": risk_note,
        "market_open": market_open, "stale": stale,
        "chart_data": chart_data,
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
                                       "activation": activation, "invalidation": invalidation,
                                       "confirmation": confirmation}),
                           config.now()))
    except Exception as e:
        return {"ok": False, "error": f"تعذر حفظ التحليل: {type(e).__name__}"}
    return {"ok": True, "analysis": analysis, "quota": quota_state(user_id) if enforce_plan else None}
