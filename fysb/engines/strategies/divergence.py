"""DIVERGENCE — Class A/B momentum divergence on the last two swing pivots.

Price and momentum are compared AT the swing pivots themselves (not at bar
indices of noise): the last two fractal swing LOWS form the bullish read, the
last two swing HIGHS the bearish read.
  * Class A bullish — price makes a LOWER low while RSI makes a HIGHER low
    (gap ≥ 2.0 RSI points). Class A bearish — mirror.
  * Class B — the RSI side is roughly equal (within ±2.0) instead of clearly
    opposite; scored lower, never skipped silently.
Mandatory trigger: the latest CLOSED candle confirms the divergence direction
with a body ≥ 0.25 ATR (a divergence without a confirming close is a sketch,
not a trade). Entry: close. Stop: beyond the second pivot extreme ±0.5 ATR.
Targets 2R/3R/4.5R.

RSI is the true Wilder RMA already used by the platform. Score is checklist
merit normalized to 42..95 — NOT a win probability.
"""
from fysb.engines.analysis import atr, atr_pct, rsi, swings, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

MIN_SCORE = 58
CLASS_A_GAP = 2.0


def _rsi_at(candles: list[dict], idx: int) -> float:
    return rsi([float(c["close"]) for c in candles[:idx + 1]], 14)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])
    highs, lows = swings(candles, 2)
    if len(lows) < 2 or len(highs) < 2:
        return None

    direction, cls, p1, p2, r1, r2 = "", "", 0.0, 0.0, 0.0, 0.0
    l2, l1 = lows[-1], lows[-2]
    if l2["index"] >= len(candles) - 12 and float(l2["price"]) < float(l1["price"]):
        rr2 = _rsi_at(candles, l2["index"])
        rr1 = _rsi_at(candles, l1["index"])
        if rr2 > rr1 + CLASS_A_GAP:
            direction, cls, p1, p2, r1, r2 = "BUY", "A", l1, l2, rr1, rr2
        elif abs(rr2 - rr1) <= 2.0 and rr2 >= rr1:
            direction, cls, p1, p2, r1, r2 = "BUY", "B", l1, l2, rr1, rr2
    if not direction:
        h2, h1 = highs[-1], highs[-2]
        if h2["index"] >= len(candles) - 12 and float(h2["price"]) > float(h1["price"]):
            rr2 = _rsi_at(candles, h2["index"])
            rr1 = _rsi_at(candles, h1["index"])
            if rr2 < rr1 - CLASS_A_GAP:
                direction, cls, p1, p2, r1, r2 = "SELL", "A", h1, h2, rr1, rr2
            elif abs(rr2 - rr1) <= 2.0 and rr2 <= rr1:
                direction, cls, p1, p2, r1, r2 = "SELL", "B", h1, h2, rr1, rr2
    if not direction:
        return None

    # Mandatory confirming close in the divergence direction:
    body = abs(close - float(last["open"]))
    if body < 0.25 * a:
        return None
    if direction == "BUY" and not close > float(last["open"]):
        return None
    if direction == "SELL" and not close < float(last["open"]):
        return None

    if direction == "BUY":
        stop = float(p2["price"]) - 0.5 * a
    else:
        stop = float(p2["price"]) + 0.5 * a
    stop = cap_stop(close, stop, direction, a, min_atr=0.9, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(close - stop)
    if risk <= 0:
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        close, stop,
        close + risk * 2.0 if direction == "BUY" else close - risk * 2.0,
        close + risk * 3.0 if direction == "BUY" else close - risk * 3.0,
        close + risk * 4.5 if direction == "BUY" else close - risk * 4.5,
        direction,
    )

    earned = 0.0
    max_pts = 80.0
    checks: dict = {}
    checks["divergence"] = True
    earned += 24          # the price/momentum split at pivots is the mandatory core
    checks["class"] = cls
    earned += 16 if cls == "A" else 8
    checks["confirm_close"] = True
    earned += 12
    bias = structure_labels(ctx.bias_candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL")
    checks["htf_bias"] = bias_ok
    # a divergence AGAINST the HTF is exactly the reversal-reversal case the
    # quality gate will additionally police — score it honestly either way:
    earned += 10 if bias_ok else 0
    from fysb.engines.analysis import kill_zone
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        diagnosis_record("DIVERGENCE", checks, earned, max_pts)
        return None

    reasons = [
        f"Class {cls} {'bullish' if direction == 'BUY' else 'bearish'} divergence: "
        f"price {'LL' if direction == 'BUY' else 'HH'} at {float(p2['price']):.5g} while "
        f"RSI {'HL' if direction == 'BUY' else 'LH'} ({r1:.1f} → {r2:.1f})",
        "confirming closed candle with conviction body",
        f"invalidation beyond the second pivot extreme {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="DIVERGENCE", direction=direction, entry=close, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
