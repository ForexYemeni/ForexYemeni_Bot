"""SK — this project's Scalper Key model (not an external certification).

Require EMA9/21 separation0.15 ATR; last close beyond prior12-bar key by
0.10 ATR; RSI>=53 BUY or<=47 SELL. Break distance>=0.25 ATR and HTF are
bonuses. Entry: close. Stop beyond breakout candle/key +/-0.3 ATR,
widened if tight; targets2R/3R/4.5R (not4R).
Weights: trend14, key16, RSI10, decisive8, HTF12, kill-zone6,
volatility4, RR5 (total75). Minimum score58; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, ema, kill_zone, rsi, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

KEY_LOOKBACK = 12
STACK_ATR = 0.15
MIN_BREAK_ATR = 0.10


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
    checks: dict = {}
    earned = 0.0
    max_pts = 75.0

    e9 = ema([float(c["close"]) for c in candles], 9)
    e21 = ema([float(c["close"]) for c in candles], 21)

    direction = ""
    if e9 > e21 + STACK_ATR * a:
        direction = "BUY"
    elif e9 < e21 - STACK_ATR * a:
        direction = "SELL"
    checks["micro_trend"] = bool(direction)
    if not direction:
        return None
    earned += 14

    window = candles[-KEY_LOOKBACK - 1:-1]
    if direction == "BUY":
        key = max(float(c["high"]) for c in window)
        broke = close > key + MIN_BREAK_ATR * a
    else:
        key = min(float(c["low"]) for c in window)
        broke = close < key - MIN_BREAK_ATR * a
    checks["key_break"] = broke
    if not broke:
        return None
    earned += 16

    r = rsi([float(c["close"]) for c in candles], 14)
    momentum_ok = (r >= 53.0) if direction == "BUY" else (r <= 47.0)
    checks["rsi_momentum"] = momentum_ok
    if not momentum_ok:
        return None
    earned += 10

    travel = abs(close - key)
    checks["decisive"] = travel >= 0.25 * a
    earned += 8 if checks["decisive"] else 0

    # v8.0.0: Real HTF bias awareness from ctx.bias_candles
    bias = structure_labels(ctx.bias_candles)
    htf_ok = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    checks["htf_bias"] = htf_ok
    earned += 12 if htf_ok else 0

    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    if direction == "BUY":
        stop = min(float(last["low"]), key) - 0.3 * a
    else:
        stop = max(float(last["high"]), key) + 0.3 * a
    entry = close
    stop = cap_stop(entry, stop, direction, a, min_atr=0.5, fallback_atr=1.0, max_atr=2.4, cap_atr=2.0)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5,
        direction,
    )
    checks["rr_ok"] = rr >= 2.0
    earned += 5 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("SK", checks, earned, max_pts)
        return None

    reasons = [
        f"EMA9/21 micro-trend aligned ({e9:.5g}/{e21:.5g}) — {direction}",
        f"{KEY_LOOKBACK}-candle key at {key:.5g} broken with a CLOSE (>{MIN_BREAK_ATR} ATR beyond)",
        f"RSI(14)={r:.1f} confirms the push · HTF bias={bias['trend']}",
        f"tight scalp stop = {stop:.5g}",
        f"targets 2R/3R/4.5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SK", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=score, reasons=reasons, checks=checks,
    )
