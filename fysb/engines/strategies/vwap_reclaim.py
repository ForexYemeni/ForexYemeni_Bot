"""VWAP_RECLAIM — institutional session-value reclaim / rejection model.

The session VWAP (or the honestly-labelled unweighted session MEAN when the
provider supplies no volume) is the institutional execution benchmark. Two
mechanical setups:
  * RECLAIM (BUY): day traded above the level, a recent pullback TOUCHED it,
    and the latest closed candle reclaimed it with directional conviction.
  * REJECT (SELL): mirror below the level.
Stop beyond the touch/pullback extreme ±0.5 ATR; targets 2R/3R/4.5R.
The level's honest kind (VWAP vs SESSION_MEAN) is disclosed in the reasons.

Score is checklist merit normalized to 42..95 — NOT a win probability.
"""
from fysb import instruments  # noqa: F401 (kept for spec-aware callers)
from fysb.engines.anchors import session_vwap
from fysb.engines.analysis import atr, atr_pct, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record, diagnosis_refuse,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

MIN_SCORE = 58


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 30:
        diagnosis_refuse("VWAP_RECLAIM", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
        return None
    vw = session_vwap(candles)
    if not vw:
        diagnosis_refuse("VWAP_RECLAIM", "لا جلسة كافية لبناء VWAP")
        return None
    level = float(vw["level"])
    a = atr(candles)
    if a <= 0:
        diagnosis_refuse("VWAP_RECLAIM", "التقلب غير قابل للقياس الآن")
        return None
    last = candles[-1]
    close = float(last["close"])
    window = candles[-12:-1]  # the day so far, excluding the trigger candle

    direction = ""
    if close > level:
        # RECLAIM (BUY): enough day-time above the level, recent touch, conviction reclaim
        above = [c for c in window if c["close"] > level]
        touch = any(c["low"] <= level <= c["high"] for c in candles[-6:-1]) or \
                candles[-2]["low"] <= level
        conviction = close > float(last["open"]) and abs(close - float(last["open"])) >= 0.25 * a
        if len(above) >= max(3, len(window) // 2) and touch and conviction:
            direction = "BUY"
    elif close < level:
        below = [c for c in window if c["close"] < level]
        touch = any(c["low"] <= level <= c["high"] for c in candles[-6:-1]) or \
                candles[-2]["high"] >= level
        conviction = close < float(last["open"]) and abs(close - float(last["open"])) >= 0.25 * a
        if len(below) >= max(3, len(window) // 2) and touch and conviction:
            direction = "SELL"
    if not direction:
        diagnosis_refuse("VWAP_RECLAIM", "لا اتجاه مؤسسي واضح للنموذج")
        return None

    pullback = candles[-6:-1]
    if direction == "BUY":
        stop = min((min(c["low"] for c in pullback) if pullback else level), level) - 0.5 * a
    else:
        stop = max((max(c["high"] for c in pullback) if pullback else level), level) + 0.5 * a
    stop = cap_stop(close, stop, direction, a, min_atr=0.9, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(close - stop)
    if risk <= 0:
        diagnosis_refuse("VWAP_RECLAIM", "حساب الوقف غير سليم (مخاطرة صفرية)")
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        close, stop,
        close + risk * 2.0 if direction == "BUY" else close - risk * 2.0,
        close + risk * 3.0 if direction == "BUY" else close - risk * 3.0,
        close + risk * 4.5 if direction == "BUY" else close - risk * 4.5,
        direction,
    )

    earned = 0.0
    max_pts = 74.0
    checks: dict = {}
    checks["level_kind"] = vw["kind"]
    checks["volume_used"] = bool(vw["volume_used"])
    earned += 10          # an honest benchmark level exists at all
    checks["session_side"] = bool(above if direction == "BUY" else below)
    earned += 14          # majority of the day on the signal side (trend context)
    checks["touch"] = True
    earned += 16          # mandatory interaction with the benchmark
    checks["reclaim_close"] = True
    earned += 16          # mandatory conviction reclaim
    bias = structure_labels(ctx.bias_candles)
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or \
                         (bias["trend"] == "down" and direction == "SELL")
    earned += 10 if checks["htf_bias"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 4 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        diagnosis_record("VWAP_RECLAIM", checks, earned, max_pts)
        return None

    reasons = [
        f"benchmark: {vw['kind']} {level:.5g} ({'حجم حقيقي' if vw['volume_used'] else 'بلا حجم — متوسط جلسة مرجّح بصدق'})",
        f"session majority on the {direction} side + recent interaction with the level",
        f"reclaim/reject candle closes {direction} with conviction (body ≥ 0.25 ATR)",
        f"invalidation beyond the pullback extreme {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="VWAP_RECLAIM", direction=direction, entry=close, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
