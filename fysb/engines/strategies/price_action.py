"""PRICE_ACTION — recent candle-pattern OR breakout continuation with EMA trend.

A recent (up to three bars) engulfing/pin pattern with EMA9/21 alignment OR a
breakout with EMA alignment determines direction. Retest is a BONUS, not always
required. Latest body >=0.25 ATR. Counter-HTF setups need S/R or breakout-retest.
Entry: close. Stop: last-six-bar extreme +/-0.85 ATR, minimum1.2 ATR.
Targets 2R/3R/4.5R. RSI bonus: BUY52..75 / SELL25..48.
Weights: pattern16, breakout-retest16, EMA10, HTF10, S/R8, RSI6,
volatility4, RR6 (total76). Minimum score64; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, breakout_retest, candle_patterns, ema, rsi, support_resistance
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    close = candles[-1]["close"]
    patterns = candle_patterns(candles)
    br = breakout_retest(candles)

    ema_fast = ema([c["close"] for c in candles[-50:]], 9)
    ema_slow = ema([c["close"] for c in candles[-50:]], 21)
    r = rsi([c["close"] for c in candles], 14)
    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    direction = ""
    if any(p in ("ENGULFING_BULL", "PIN_BAR_BULL") for p in patterns) and trend_up:
        direction = "BUY"
    elif any(p in ("ENGULFING_BEAR", "PIN_BAR_BEAR") for p in patterns) and trend_down:
        direction = "SELL"
    elif br["breakout"] == "UP" and trend_up:
        direction = "BUY"
    elif br["breakout"] == "DOWN" and trend_down:
        direction = "SELL"
    if not direction:
        return None

    a = atr(candles)
    if a <= 0:
        return None
    # v6.20.6: require directional candle conviction (reject tiny indecision dojis)
    last_bar = candles[-1]
    body = abs(float(last_bar["close"]) - float(last_bar["open"]))
    if body < 0.25 * a:
        return None
    entry = close
    raw_stop = (min(c["low"] for c in candles[-6:]) - 0.85 * a) if direction == "BUY" else (max(c["high"] for c in candles[-6:]) + 0.85 * a)
    stop = cap_stop(entry, raw_stop, direction, a, min_atr=1.2, fallback_atr=1.8, max_atr=2.6, cap_atr=2.4)

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

    # --- Normalized Fair Score (v8.0.0) ------------------------------------
    earned = 0.0
    max_pts = 76.0
    checks = {}
    pattern_dir = any(p in (("ENGULFING_BULL", "PIN_BAR_BULL") if direction == "BUY" else ("ENGULFING_BEAR", "PIN_BAR_BEAR")) for p in patterns)
    checks["pattern"] = pattern_dir
    earned += 16 if pattern_dir else 0
    checks["breakout_retest"] = br["breakout"] == ("UP" if direction == "BUY" else "DOWN") and br["retest"]
    earned += 16 if checks["breakout_retest"] else 0
    checks["ema_trend"] = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_down)
    earned += 10 if checks["ema_trend"] else 0
    htf_up = ema([c["close"] for c in ctx.bias_candles[-50:]], 21) > ema([c["close"] for c in ctx.bias_candles[-50:]], 50) if len(ctx.bias_candles) >= 50 else trend_up
    checks["htf_bias"] = (direction == "BUY" and htf_up) or (direction == "SELL" and not htf_up)
    earned += 10 if checks["htf_bias"] else 0
    sr = support_resistance(candles)
    near_level = False
    if direction == "BUY" and sr["support"] and abs(entry - sr["support"]) < 1.5 * a:
        near_level = True
    if direction == "SELL" and sr["resistance"] and abs(entry - sr["resistance"]) < 1.5 * a:
        near_level = True
    checks["sr_confluence"] = near_level
    earned += 8 if near_level else 0
    checks["rsi_regime"] = (52 <= r <= 75) if direction == "BUY" else (25 <= r <= 48)
    earned += 6 if checks["rsi_regime"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    if not checks["htf_bias"] and not (checks["sr_confluence"] or checks["breakout_retest"]):
        return None
    score = normalized_score(earned, max_pts)
    if score < 64:
        return None

    reasons = [
        f"patterns: {','.join(patterns) if patterns else 'none'}",
        f"breakout {br['breakout'] or '-'} with retest={'yes' if br['retest'] else 'no'}",
        f"EMA9 {'>' if trend_up else '<'} EMA21 (trend {'up' if trend_up else 'down'})",
        f"RSI={r:.1f}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="PRICE_ACTION", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
