"""CLASSIC — Classic Technical Analysis (trend + S/R + candle confirmation).

The oldest school in the book, implemented 100% mechanically (no marketing):
  1. TREND     : price on the correct side of EMA50 AND EMA50 sloping with the
                 trade direction (rising for BUY, falling for SELL) — a market
                 hugging its mean is not a classic trend.
  2. LEVEL     : a REAL horizontal level from support_resistance() — price is
                 within 1.2 ATR of it, retesting from the correct side
                 (resistance-turned-support for BUY, support-turned-ceiling
                 for SELL).
  3. CONFIRM   : the last candle is a confirmation pattern from
                 candle_patterns() (engulfing / pin bar) IN the trade direction.
  4. ENTRY     : last close.
  5. STOP      : beyond the level (and the last-6-candle extreme) - 0.5 ATR.
  6. TARGETS   : TP1 = 2R (platform floor) · TP2 = 3R · TP3 = 4.5R — the
                 classic measured-move ladder.

Score weights (deterministic): trend +20 · level proximity +18 ·
direction-correct side +14 · confirmation pattern +16 · EMA50 slope +10 ·
kill zone +6 · volatility regime +4 · RR>=2 +8. Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, candle_patterns, ema, kill_zone, structure_labels, support_resistance
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score,
)

PROXIMITY_ATR = 2.0


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 55:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])
    checks: dict = {}
    earned = 0.0
    max_pts = 81.0

    e50_now = ema([float(c["close"]) for c in candles], 50)
    e50_prev = ema([float(c["close"]) for c in candles[:-6]], 50)

    # 1) trend side — exactly one direction qualifies
    direction = ""
    if close > e50_now + 0.10 * a:
        direction = "BUY"
    elif close < e50_now - 0.10 * a:
        direction = "SELL"
    checks["trend_side"] = bool(direction)
    if not direction:
        return None
    earned += 14

    # 2) EMA50 slope agrees with the direction
    slope_ok = (e50_now > e50_prev) if direction == "BUY" else (e50_now < e50_prev)
    checks["ema50_slope"] = slope_ok
    if not slope_ok:
        return None
    earned += 8

    # 3) real horizontal level retested from the correct side
    sr = support_resistance(candles)
    level = None
    if direction == "BUY" and sr.get("support") is not None:
        lv_f = float(sr["support"])
        if abs(close - lv_f) <= PROXIMITY_ATR * a:
            level = lv_f
    elif direction == "SELL" and sr.get("resistance") is not None:
        lv_f = float(sr["resistance"])
        if abs(close - lv_f) <= PROXIMITY_ATR * a:
            level = lv_f
    checks["level_retest"] = level is not None
    if level is None:
        return None
    earned += 12
    side_ok = (close >= level - 0.2 * a) if direction == "BUY" else (close <= level + 0.2 * a)
    checks["correct_side"] = side_ok
    if not side_ok:
        return None
    earned += 8

    # 4) candlestick confirmation in the trade direction
    patterns = candle_patterns(candles, limit=3)
    bull_pats = ("ENGULFING_BULL", "PIN_BAR_BULL")
    bear_pats = ("ENGULFING_BEAR", "PIN_BAR_BEAR")
    confirm = any(p in (bull_pats if direction == "BUY" else bear_pats) for p in patterns)
    checks["candle_confirm"] = confirm
    if not confirm:
        return None
    earned += 12

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

    recent = candles[-6:]
    if direction == "BUY":
        stop = min(level, min(float(c["low"]) for c in recent)) - 0.5 * a
    else:
        stop = max(level, max(float(c["high"]) for c in recent)) + 0.5 * a
    entry = close
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
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
        return None

    reasons = [
        f"price {close:.5g} trades on the {'bull' if direction == 'BUY' else 'bear'} side of EMA50 ({e50_now:.5g}, sloping)",
        f"horizontal level retest at {level:.5g} (within {PROXIMITY_ATR} ATR)",
        "confirmation candle pattern: " + (", ".join(patterns) or "-"),
        f"HTF bias={bias['trend']}",
        f"stop beyond level & local extreme = {stop:.5g}",
        f"targets 2R/3R/4.5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="CLASSIC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=score, reasons=reasons, checks=checks,
    )
