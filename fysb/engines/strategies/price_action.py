"""Price Action strategy — classic patterns, breakout/retest, S/R confluence.

Entry thesis: a confirmed candlestick pattern or breakout-retest event aligned
with EMA trend and support/resistance location.

Score weights (documented, deterministic):
  Pattern confirmation .................. +16 (engulfing/pin bar in direction)
  Breakout + retest ..................... +16
  EMA9/EMA21 trend alignment ............ +10
  HTF bias alignment .................... +10
  S/R confluence (entry near level) ..... +8
  RSI in trend regime ................... +6
  ATR regime normal ..................... +4
  RR >= 2.0 ............................. +6
Base 50. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, breakout_retest, candle_patterns, ema, rsi, support_resistance
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score


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
    if direction == "BUY":
        stop = min(c["low"] for c in candles[-6:]) - 0.85 * a
        if stop >= entry or (entry - stop) < 1.2 * a:
            stop = entry - 1.8 * a
        elif (entry - stop) > 2.6 * a:
            # v6.23.0: cap abnormal news-wick stops so SL/TP3 stay institutional
            stop = entry - 2.4 * a
    else:
        stop = max(c["high"] for c in candles[-6:]) + 0.85 * a
        if stop <= entry or (stop - entry) < 1.2 * a:
            stop = entry + 1.8 * a
        elif (stop - entry) > 2.6 * a:
            # v6.23.0: cap abnormal news-wick stops so SL/TP3 stay institutional
            stop = entry + 2.4 * a

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tps = tuple(entry + risk * m if direction == "BUY" else entry - risk * m for m in (2.0, 3.0, 4.5))
    rr = abs(tps[0] - entry) / risk

    # --- Score -------------------------------------------------------------
    score = 50.0
    checks = {}
    pattern_dir = any(p in (("ENGULFING_BULL", "PIN_BAR_BULL") if direction == "BUY" else ("ENGULFING_BEAR", "PIN_BAR_BEAR")) for p in patterns)
    checks["pattern"] = pattern_dir
    score += 16 if pattern_dir else 0
    checks["breakout_retest"] = br["breakout"] == ("UP" if direction == "BUY" else "DOWN") and br["retest"]
    score += 16 if checks["breakout_retest"] else 0
    checks["ema_trend"] = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_down)
    score += 10 if checks["ema_trend"] else 0
    htf_up = ema([c["close"] for c in ctx.bias_candles[-50:]], 21) > ema([c["close"] for c in ctx.bias_candles[-50:]], 50) if len(ctx.bias_candles) >= 50 else trend_up
    checks["htf_bias"] = (direction == "BUY" and htf_up) or (direction == "SELL" and not htf_up)
    score += 10 if checks["htf_bias"] else 0
    sr = support_resistance(candles)
    near_level = False
    if direction == "BUY" and sr["support"] and abs(entry - sr["support"]) < 1.5 * a:
        near_level = True
    if direction == "SELL" and sr["resistance"] and abs(entry - sr["resistance"]) < 1.5 * a:
        near_level = True
    checks["sr_confluence"] = near_level
    score += 8 if near_level else 0
    checks["rsi_regime"] = (52 <= r <= 75) if direction == "BUY" else (25 <= r <= 48)
    score += 6 if checks["rsi_regime"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 6 if checks["rr_ok"] else 0

    # v6.20.6: block isolated counter-HTF noise patterns without S/R or breakout retest
    if not checks["htf_bias"] and not (checks["sr_confluence"] or checks["breakout_retest"]):
        return None
    if score < 70:
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
        tp1=tps[0], tp2=tps[1], tp3=tps[2], rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
