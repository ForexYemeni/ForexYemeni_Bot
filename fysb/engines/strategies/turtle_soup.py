"""Turtle Soup (ICT) — failed breakout of a 20-bar extreme, fully mechanical.

Documented rules:
  1. Prior 20-bar high/low defines the level everyone watches (stop clusters).
  2. A candle pierces the level (stop-hunt) but CLOSES back on the other side.
  3. Trade AGAINST the failed breakout (the "soup"): failed upside break -> SELL,
     failed downside break -> BUY.
  4. Stop beyond the sweep wick; targets are risk multiples (1R/2R/3.5R style).

Score weights (deterministic):
  20-bar level defined ....... +6
  false breakout up/down ..... +18
  close-back margin >= 25% ....+12
  HTF bias alignment ......... +12
  wick dominance (rejection). +10
  kill zone .................. +6
  volatility regime ok ....... +4
  RR >= 2 .................... +8
Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone, structure_labels
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score

LOOKBACK = 20


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < LOOKBACK + 12:
        return None
    last = candles[-1]
    prior = candles[-(LOOKBACK + 1):-1]
    hi = max(c["high"] for c in prior)
    lo = min(c["low"] for c in prior)
    a = atr(candles)
    if a <= 0:
        return None

    direction, level = "", 0.0
    body = abs(last["close"] - last["open"])
    range_ = max(last["high"] - last["low"], 1e-12)
    if last["high"] > hi and last["close"] < hi:
        direction, level = "SELL", hi          # failed upside breakout
    elif last["low"] < lo and last["close"] > lo:
        direction, level = "BUY", lo           # failed downside breakout
    if not direction:
        return None

    bias = structure_labels(ctx.bias_candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL")

    entry = last["close"]
    if direction == "BUY":
        stop = min(last["low"], lo) - 0.3 * a
        risk = entry - stop
        tp1, tp2, tp3 = entry + risk * 2.0, entry + risk * 3.0, entry + risk * 4.5
    else:
        stop = max(last["high"], hi) + 0.3 * a
        risk = stop - entry
        tp1, tp2, tp3 = entry - risk * 2.0, entry - risk * 3.0, entry - risk * 4.5
    if risk <= 0:
        return None
    rr = abs(tp1 - entry) / risk

    score = 40.0
    checks = {}
    checks["level"] = hi > lo
    score += 6
    checks["false_break"] = True
    score += 18
    if direction == "BUY":
        margin = (last["close"] - lo) / max(range_, 1e-12)
    else:
        margin = (hi - last["close"]) / max(range_, 1e-12)
    checks["close_back_margin"] = margin >= 0.25
    score += 12 if checks["close_back_margin"] else 0
    checks["htf_bias"] = bias_ok
    score += 12 if bias_ok else 0
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]
    reject = (direction == "SELL" and upper_wick > body) or (direction == "BUY" and lower_wick > body)
    checks["wick_rejection"] = reject
    score += 10 if reject else 0
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 8 if rr >= 2.0 else 0

    if score < 55:
        return None

    reasons = [
        f"20-bar {('high' if direction == 'SELL' else 'low')} level @ {level:.5g}",
        f"pierced then closed back inside (margin {margin:.0%})",
        f"HTF bias={bias['trend']}",
        "rejection wick dominant" if reject else "rejection wick weak",
        f"invalidation beyond {stop:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="TURTLE_SOUP", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
