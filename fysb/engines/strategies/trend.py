"""TREND — EMA9/21/50 stack, historical value visit, then resumption.

Require stack layers separated by0.25 entry ATR, agreeing bias EMA21/50
separated by0.1 entry ATR. One of SIX preceding bars must intersect the band
EMA21 +/-1 ATR computed using history through THAT bar (not today's EMA).
Latest directional candle closes past EMA9. Entry: close.
Stop: last-eight-bar extreme +/-0.5 ATR; targets2R/3R/5R.
Weights: stack14, HTF10, pullback12, resumption10, momentum6,
kill-zone6, volatility4, RR5 (total67). Minimum score58; minimum55 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, ema, kill_zone
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

STACK_ATR = 0.25      # each EMA layer must be separated by >= 0.25 ATR —
                      # an EMAs-equal market is NOT a trend, never chase noise
PULLBACK_ATR = 1.0


def _stack(candles: list[dict]) -> tuple[float, float, float]:
    closes = [float(c["close"]) for c in candles]
    return ema(closes, 9), ema(closes, 21), ema(closes, 50)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 55:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])

    e9, e21, e50 = _stack(candles)
    checks: dict = {}
    earned = 0.0
    max_pts = 67.0

    direction = ""
    if e9 > e21 + STACK_ATR * a and e21 > e50 + STACK_ATR * a:
        direction = "BUY"
    elif e9 < e21 - STACK_ATR * a and e21 < e50 - STACK_ATR * a:
        direction = "SELL"
    checks["stack"] = bool(direction)
    if not direction:
        return None
    earned += 14

    b9, b21, b50 = _stack(ctx.bias_candles)
    bias_ok = (direction == "BUY" and b21 > b50 + 0.1 * a) or (direction == "SELL" and b21 < b50 - 0.1 * a)
    checks["htf_bias"] = bias_ok
    if not bias_ok:
        return None
    earned += 10

    visited = False
    # Six bars BEFORE resumption. Compare each bar with the EMA/ATR known at
    # that time; today's EMA must not rewrite an earlier pullback location.
    for i in range(max(21, len(candles) - 7), len(candles) - 1):
        history = candles[:i + 1]
        historical_ema = ema([float(c["close"]) for c in history], 21)
        historical_atr = atr(history)
        c = candles[i]
        band = PULLBACK_ATR * historical_atr
        if float(c["low"]) <= historical_ema + band and float(c["high"]) >= historical_ema - band:
            visited = True
            break
    checks["pullback"] = visited
    if not visited:
        return None
    earned += 12

    resumed = (close > e9 and float(last["close"]) > float(last["open"])) if direction == "BUY" \
        else (close < e9 and float(last["close"]) < float(last["open"]))
    checks["resumption"] = resumed
    if not resumed:
        return None
    earned += 10

    momentum = abs(close - e21) >= 0.25 * a
    checks["momentum"] = momentum
    earned += 6 if momentum else 0

    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    recent = candles[-8:]
    if direction == "BUY":
        stop = min(float(c["low"]) for c in recent) - 0.5 * a
    else:
        stop = max(float(c["high"]) for c in recent) + 0.5 * a
    entry = close
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0,
        direction,
    )
    checks["rr_ok"] = rr >= 2.0
    earned += 5 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        return None

    reasons = [
        f"EMA stack aligned ({e9:.5g}/{e21:.5g}/{e50:.5g}) — {direction} trend",
        f"HTF bias confirms ({b21:.5g} vs {b50:.5g})",
        f"pullback visited the EMA21 value zone within {PULLBACK_ATR} ATR",
        "resumption candle closed back in trend direction",
        f"stop beyond pullback extreme = {stop:.5g}",
        f"targets 2R/3R/5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="TREND", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=score, reasons=reasons, checks=checks,
    )
