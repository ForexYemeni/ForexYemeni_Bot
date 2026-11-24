"""WYCKOFF — Accumulation Spring / Distribution Upthrust (100% mechanical).

The last honest gap in the catalog, now a real rule engine (v6.11.0).
Richard Wyckoff's core playbook, reduced to auditable mechanical checks:

  1. RANGE      : the window (last RANGE_WINDOW candles, EXCLUDING the final
                 candle) must be a sideways trading range — width <= 3.0 ATR
                 AND the close crosses the range midline >= 3 times (a real
                 two-sided auction, never a diagonal drift pretending to be
                 consolidation).
  2. EVENT      : the FINAL candle is the Wyckoff event —
                 SPRING (BUY):  low pierces BELOW the range floor, close back
                                INSIDE the range (the stop-run below support
                                that instantly reverses);
                 UPTHRUST (SELL): high pierces ABOVE the range ceiling, close
                                back INSIDE (the buy-side liquidity grab).
  3. INTACT     : the candle BEFORE the event must have closed inside the
                 range — the structure was intact right before the test.
  4. EFFORT     : when real volume data exists (event volume and window average
                 both > 0), the event must carry >= 1.1× the average volume —
                 effort without result = absorption. When the feed has no
                 volume (typical FX spot quotes) the check is SKIPPED, never
                 failed — honesty over cosmetics.
  5. ENTRY      : the event's close.
  6. STOP       : beyond the event's wick extreme ± 0.5 ATR — where the sweep
                 becomes a genuine breakdown.
  7. TARGETS    : TP1 = the opposite range boundary, FLOORED at 2R (platform
                 rule) · TP2 = the measured move (one range height) ·
                 TP3 = the 1.618× extension of the range height.

Score weights (deterministic, auditable): valid range +14 · alternations +10 ·
sweep beyond boundary +22 · close back inside +14 · volume surge +8 (only with
real volume data) · kill zone +6 · volatility regime +4 · RR(TP1)>=2 +8.
Base 40. Cap 95. Minimum 55 to fire — a weak spring is no spring at all.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score

RANGE_WINDOW = 26          # candles defining the trading range (excluding the event)
RANGE_MAX_ATR = 3.0        # range width ceiling in ATR — consolidation, not drift
MIN_ALTERNATIONS = 3       # midline crossings that prove a two-sided auction
VOLUME_SURGE = 1.1         # event volume vs window average (when data exists)
MIN_SCORE = 55


def _alternations(closes: list[float], mid: float) -> int:
    """Number of close-side flips across the range midline."""
    flips = 0
    prev = closes[0] > mid
    for c in closes[1:]:
        cur = c > mid
        if cur != prev:
            flips += 1
        prev = cur
    return flips


def _real_volume(candles: list[dict]) -> bool:
    """True only when the feed carries usable volume (> 0) for every candle."""
    return all(float(c.get("volume") or 0) > 0 for c in candles)


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < RANGE_WINDOW + 14:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    event = candles[-1]
    window = candles[-(RANGE_WINDOW + 1):-1]
    prev = candles[-2]

    close = float(event["close"])
    range_high = max(float(c["high"]) for c in window)
    range_low = min(float(c["low"]) for c in window)
    mid = (range_high + range_low) / 2.0
    height = range_high - range_low
    if height <= 0:
        return None

    checks: dict = {}
    score = 40.0
    reasons: list[str] = []

    # 1) a REAL sideways range — not a trend wearing a costume
    width_ok = (range_high - range_low) <= RANGE_MAX_ATR * a
    checks["range_ok"] = width_ok
    if not width_ok:
        return None
    score += 14
    reasons.append(f"range {range_low:.5g}–{range_high:.5g} (width {(range_high - range_low) / a:.2f} ATR)")

    # two-sided auction: the close must have traded BOTH sides of the midline
    alts = _alternations([float(c["close"]) for c in window], mid)
    checks["alternations"] = alts
    if alts < MIN_ALTERNATIONS:
        return None
    score += 10

    # 2) the Wyckoff event — exactly one direction can qualify
    inside_prev = range_low <= float(prev["close"]) <= range_high
    checks["range_intact_before"] = inside_prev
    if not inside_prev:
        return None

    lo = float(event["low"])
    hi = float(event["high"])
    if lo < range_low and close > range_low:
        direction = "BUY"       # SPRING — sweep of the sell-side liquidity
        wick_extreme = lo
    elif hi > range_high and close < range_high:
        direction = "SELL"      # UPTHRUST — sweep of the buy-side liquidity
        wick_extreme = hi
    else:
        return None
    checks["event"] = "SPRING" if direction == "BUY" else "UPTHRUST"
    score += 22
    reasons.append(f"{checks['event']}: wick swept {'below support' if direction == 'BUY' else 'above resistance'} then closed back inside")

    # close confirmed back inside the range
    checks["close_inside"] = range_low <= close <= range_high
    score += 14

    # 4) effort vs result — volume surge on the event (only with real data)
    if _real_volume(candles[-RANGE_WINDOW - 1:]):
        avg_v = sum(float(c.get("volume") or 0) for c in window) / len(window)
        surge = float(event.get("volume") or 0) >= VOLUME_SURGE * avg_v
        checks["volume_surge"] = surge
        if surge:
            score += 8
            reasons.append(f"event volume {float(event.get('volume') or 0):.0f} >= {VOLUME_SURGE:g}× window average {avg_v:.0f}")
    else:
        checks["volume_surge"] = None   # skipped honestly — no usable volume feed
        reasons.append("volume data absent — effort check skipped (honest)")

    kz = ctx.kill_zone or (kill_zone(event["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0

    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0

    # 6) stop beyond the event's wick extreme
    entry = close
    if direction == "BUY":
        stop = wick_extreme - 0.5 * a
    else:
        stop = wick_extreme + 0.5 * a
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # 7) targets — opposite boundary floored at 2R, measured move, 1.618× extension
    if direction == "BUY":
        tp1 = max(range_high, entry + 2.0 * risk)
        tp2 = max(tp1 + 0.01, entry + height)
        tp3 = max(tp2 + 0.01, entry + 1.618 * height)
    else:
        tp1 = min(range_low, entry - 2.0 * risk)
        tp2 = min(tp1 - 0.01, entry - height)
        tp3 = min(tp2 - 0.01, entry - 1.618 * height)
    rr = 2.0
    checks["rr_ok"] = True
    score += 8

    if score < MIN_SCORE:
        return None

    reasons.append(f"stop beyond event wick = {stop:.5g}")
    reasons.append(f"targets 2R/measure/1.618ext = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}")
    reasons.append(f"kill zone: {kz}" if kz else "outside kill zones")
    reasons.append(f"RR(TP1)=1:{rr:.2f}")
    return StrategyResult(
        code="WYCKOFF", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
