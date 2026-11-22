"""AMD — Power of Three (Accumulation · Manipulation · Distribution).

Documented ICT/forex-session logic, fully mechanical:
  1. ACCUMULATION: the opening window (first N candles of the covered session)
     defines a dealing range.
  2. MANIPULATION: price breaks ONE side of the opening range (judaas swing /
     fake move) and then closes back inside the range.
  3. DISTRIBUTION: expansion toward the OPPOSITE side — the trade direction is
     opposite to the manipulated side.

Score weights (deterministic):
  opening range defined ......... +8
  manipulation sweep ............ +16
  close back inside range ....... +14
  HTF bias alignment ............ +12
  expansion already started ..... +8
  kill zone / session timing .... +6
  volatility regime ok .......... +4
  RR(TP1) >= 2 .................. +8
  CRT/FVG confluence ............ +8
Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, fair_value_gaps, kill_zone, structure_labels
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score

OPEN_WINDOW = 8          # candles forming the accumulation range


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None

    window = candles[-(OPEN_WINDOW + 6):]
    acc = window[:OPEN_WINDOW]
    hi = max(c["high"] for c in acc)
    lo = min(c["low"] for c in acc)
    if hi <= lo:
        return None

    # Manipulation: sweep one side then close back inside (scan post-window)
    manip_side, manip_level = "", 0.0
    for c in window[OPEN_WINDOW:]:
        if c["high"] > hi and c["close"] < hi:
            manip_side, manip_level = "high", hi
            break
        if c["low"] < lo and c["close"] > lo:
            manip_side, manip_level = "low", lo
            break
    if not manip_side:
        return None

    direction = "SELL" if manip_side == "high" else "BUY"
    last = candles[-1]
    close = last["close"]
    bias = structure_labels(ctx.bias_candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL")

    a = atr(candles)
    if a <= 0:
        return None
    entry = close if direction == "BUY" and close < hi else \
        close if direction == "SELL" and close > lo else \
        (hi - 0.25 * a) if direction == "BUY" else (lo + 0.25 * a)

    if direction == "BUY":
        stop = min(manip_level, lo) - 0.5 * a
        stop = min(stop, entry - 0.8 * a)
        risk = entry - stop
        tp1 = hi + (hi - lo) * 0.5
        tp2 = hi + (hi - lo)
        tp3 = hi + (hi - lo) * 1.5
    else:
        stop = max(manip_level, hi) + 0.5 * a
        stop = max(stop, entry + 0.8 * a)
        risk = stop - entry
        tp1 = lo - (hi - lo) * 0.5
        tp2 = lo - (hi - lo)
        tp3 = lo - (hi - lo) * 1.5
    if risk <= 0:
        return None
    rr = abs(tp1 - entry) / risk

    score = 40.0
    checks = {}
    checks["range"] = (hi - lo) > 0
    score += 8
    checks["manipulation"] = True
    score += 16
    checks["close_inside"] = True
    score += 14
    checks["htf_bias"] = bias_ok
    score += 12 if bias_ok else 0
    expanding = (direction == "BUY" and close > (hi + lo) / 2) or \
                (direction == "SELL" and close < (hi + lo) / 2)
    checks["expansion"] = expanding
    score += 8 if expanding else 0
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 8 if rr >= 2.0 else 0
    fvgs = fair_value_gaps(candles[-12:], 3)
    kind = "bullish" if direction == "BUY" else "bearish"
    checks["fvg_confluence"] = any(f["kind"] == kind for f in fvgs)
    score += 8 if checks["fvg_confluence"] else 0

    if score < 55:
        return None

    reasons = [
        f"accumulation range {lo:.5g}-{hi:.5g} ({OPEN_WINDOW} bars)",
        f"manipulation swept {manip_side} @ {manip_level:.5g} and closed back inside",
        f"distribution direction={direction} (opposite of the fake move)",
        f"HTF bias={bias['trend']}",
        f"invalidation beyond {stop:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="AMD", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
