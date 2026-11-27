"""LONDON_BREAKOUT — classic London opening-range breakout model.

The opening range = the first TWO hours of the London session (08:00–09:59
Europe/London). A FRESH directional break after 10:00 London with a
directional body issues the trade in the break direction; the range's
opposite side is the structural stop (classic LB), widened/kept honest via
cap_stop. Targets: measured range projection first, platform floors 2R/3R/4.5R
separately. Fake-out guards: a bar sweeping BOTH sides of the range, or a
close back inside the range, is rejected; the break must be FRESH (the prior
closed candle did not already close beyond the same boundary).

Score is checklist merit normalized to 42..95 — NOT a win probability.
"""
from datetime import timezone
from zoneinfo import ZoneInfo

from fysb.engines.analysis import atr, atr_pct, structure_labels
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

_LDN = ZoneInfo("Europe/London")
MIN_SCORE = 58


def _ldn_parts(dt_iso: str):
    from fysb.engines.analysis import ny_hour  # noqa: F401 (kept off; local parse)
    dt = dt_iso.replace("Z", "+00:00")
    from datetime import datetime
    d = datetime.fromisoformat(dt)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(_LDN)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 30:
        return None
    last = candles[-1]
    ldn_last = _ldn_parts(last["datetime"])
    a = atr(candles)
    if a <= 0:
        return None

    # Opening range: 08:00–09:59 London of TODAY (at least 2 candles present).
    rng = [c for c in candles
           if _ldn_parts(c["datetime"]).date() == ldn_last.date()
           and 8 <= _ldn_parts(c["datetime"]).hour < 10]
    if len(rng) < 2:
        return None
    hi = max(float(c["high"]) for c in rng)
    lo = min(float(c["low"]) for c in rng)
    width = hi - lo
    if width <= 0 or width > 3.0 * a:
        return None            # degenerate or absurdly wide range — no trade

    hour = ldn_last.hour
    if not (10 <= hour < 15):
        return None            # signal window: after the range, before NY PM fade

    close = float(last["close"])
    prev = candles[-2]
    fresh = False
    direction = ""
    if close > hi + 0.1 * a and float(prev["close"]) <= hi and float(last["low"]) >= lo - 1e-9:
        direction, fresh = "BUY", True
    elif close < lo - 0.1 * a and float(prev["close"]) >= lo and float(last["high"]) <= hi + 1e-9:
        direction, fresh = "SELL", True
    if not direction:
        return None
    body = abs(close - float(last["open"]))
    if body < 0.25 * a:
        return None            # no conviction — a wicky break is a trap, not a break

    entry = close
    if direction == "BUY":
        stop = lo - 0.5 * a
    else:
        stop = hi + 0.5 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.0, fallback_atr=1.8, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # Measured projection of the opening range first, floors applied separately:
    if direction == "BUY":
        raw_tp1, raw_tp2, raw_tp3 = entry + width, entry + width * 1.618, entry + width * 2.618
    else:
        raw_tp1, raw_tp2, raw_tp3 = entry - width, entry - width * 1.618, entry - width * 2.618
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)

    earned = 0.0
    max_pts = 76.0
    checks: dict = {}
    checks["range"] = True
    earned += 8
    checks["fresh_break"] = fresh
    earned += 18
    checks["body"] = body >= 0.25 * a
    earned += 12
    bias = structure_labels(ctx.bias_candles)
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or \
                         (bias["trend"] == "down" and direction == "SELL")
    earned += 14 if checks["htf_bias"] else 0
    # expansion: the breakout bar's range should not be a shrinking fade
    checks["expansion"] = (float(last["high"]) - float(last["low"])) >= 0.6 * a
    earned += 8 if checks["expansion"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 12 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        return None

    reasons = [
        f"London opening range (08:00–09:59 UK) {lo:.5g}–{hi:.5g} (width {width / a:.2f} ATR)",
        f"fresh {direction} break with conviction body (prior candle had NOT broken)",
        f"stop at the opposite range side {stop:.5g} (classic LB)",
        "targets: measured range projection, platform 2R/3R/4.5R floors applied",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="LONDON_BREAKOUT", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
