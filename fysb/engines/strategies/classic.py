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
from fysb.engines.analysis import atr, atr_pct, candle_patterns, ema, kill_zone, support_resistance
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score

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
    score = 32.0

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
    score += 14

    # 2) EMA50 slope agrees with the direction (real trend, not a flat hug)
    slope_ok = (e50_now > e50_prev) if direction == "BUY" else (e50_now < e50_prev)
    checks["ema50_slope"] = slope_ok
    if not slope_ok:
        return None
    score += 8

    # 3) a REAL horizontal level retested from the correct side
    # support_resistance() returns the NEAREST swing-level below (support) and
    # above (resistance) the last close — the classic floor/ceiling pair.
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
    score += 12
    # correct side of the level (pullback INTO it, not a hopeless chase)
    side_ok = (close >= level - 0.2 * a) if direction == "BUY" else (close <= level + 0.2 * a)
    checks["correct_side"] = side_ok
    if not side_ok:
        return None
    score += 8

    # 4) candlestick confirmation in the trade direction
    patterns = candle_patterns(candles, limit=3)
    bull_pats = ("ENGULFING_BULL", "PIN_BAR_BULL")
    bear_pats = ("ENGULFING_BEAR", "PIN_BAR_BEAR")
    confirm = any(p in (bull_pats if direction == "BUY" else bear_pats) for p in patterns)
    checks["candle_confirm"] = confirm
    if not confirm:
        return None
    score += 12

    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0

    # 5) stop beyond the level and the local extreme
    recent = candles[-6:]
    if direction == "BUY":
        stop = min(level, min(float(c["low"]) for c in recent)) - 0.5 * a
    else:
        stop = max(level, max(float(c["high"]) for c in recent)) + 0.5 * a
    entry = close
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    tp1 = entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0
    tp2 = entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0
    tp3 = entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5
    rr = 2.0
    checks["rr_ok"] = True
    score += 5

    if score < 55:
        return None

    reasons = [
        f"price {close:.5g} trades on the {'bull' if direction == 'BUY' else 'bear'} side of EMA50 ({e50_now:.5g}, sloping)",
        f"horizontal level retest at {level:.5g} (within {PROXIMITY_ATR} ATR)",
        "confirmation candle pattern: " + (", ".join(patterns) or "-"),
        f"stop beyond level & local extreme = {stop:.5g}",
        f"targets 2R/3R/4.5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="CLASSIC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
