"""WYCKOFF — extended mechanical subset: Spring/Upthrust + LPS/LPSY + effort/result.

Core (unchanged): prior26-bar width<=3 ATR with>=3 midline crossings. Latest
bar sweeps ONE boundary and closes strictly INSIDE the range; dual-sided
rejected. Full positive volume requires event volume>=1.1x window average;
missing/zero volume skips honestly. HTF bonus. Entry: event close; stop beyond
event wick ±0.5 ATR; targets opposite boundary/measured extensions with
2R/3R/4.5R floors.

v8.12.0 full-school extensions (all mechanical, none subjective):
  * LPS / LPSY secondary entry — the classic Wyckoff SECOND entry: a Spring
    (or Upthrust) inside the last 10 bars, a rally back through the broken
    boundary (Sign Of Strength/Weakness), a shallow pullback that HOLDS above
    the boundary (Last Point of Support/Supply), and a directional close.
    Pullback volume health (< 0.8x window average, when volume exists) is a
    scored factor, skipped honestly without volume data.
  * Effort vs Result — a sweeping event with LARGE volume but a NARROW spread
    (body < 35% of range) is absorption (effort without result): penalized.
  * Shortening of Thrust — on the event path, the two most recent down-swings
    inside the range (BUY) / up-swings (SELL) measured shallower = buyers
    stepping in earlier: bonus factor.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

RANGE_WINDOW = 26          # candles defining the trading range (excluding the event)
RANGE_MAX_ATR = 3.0        # range width ceiling in ATR — consolidation, not drift
MIN_ALTERNATIONS = 3       # midline crossings that prove a two-sided auction
VOLUME_SURGE = 1.1         # event volume vs window average (when data exists)
MIN_SCORE = 55


# ---------------------------------------------------------------------------
# v8.12.0 full-school helpers (pure functions on OHLCV)
# ---------------------------------------------------------------------------

def _sub_swing_extremes(candles: list[dict], start: int, end: int, direction: str) -> float:
    """Depth of the LAST counter-move inside [start, end): the swing extreme
    reached before the final move toward the event boundary. For a BUY event
    (Spring path) it is the LOWEST low of the most recent downswing; for SELL
    the mirror. Returns NaN when no qualifying swing exists."""
    import math
    if end - start < 4:
        return math.nan
    win = candles[start:end]
    if direction == "BUY":
        # last downswing: find the lowest low of the final down-leg before the event
        lows = [float(c["low"]) for c in win]
        return min(lows[-max(2, len(lows) // 2):]) if len(lows) >= 2 else math.nan
    highs = [float(c["high"]) for c in win]
    return max(highs[-max(2, len(highs) // 2):]) if len(highs) >= 2 else math.nan


def _shortening_of_thrust(candles: list[dict], ev: int, direction: str) -> bool:
    """True when the most recent counter-swing is shallower than the previous
    one (buyers/sellers stepping in earlier inside the range). Measured on
    fractal swing lows (BUY) / highs (SELL) strictly before the event bar."""
    from fysb.engines.analysis import swings
    highs, lows = swings(candles[:ev], 2)
    pts = lows if direction == "BUY" else highs
    if len(pts) < 2:
        return False
    p_new, p_old = float(pts[-1]["price"]), float(pts[-2]["price"])
    # BUY: the later swing low is HIGHER (declines are shallower);
    # SELL: the later swing high is LOWER (rallies are shallower).
    return p_new > p_old if direction == "BUY" else p_new < p_old


def _effort_vs_result(event: dict, avg_v: float | None) -> str:
    """'ABSORPTION' when large effort produced a narrow spread; 'NORMAL'
    otherwise; 'SKIPPED' when no usable volume."""
    v = float(event.get("volume") or 0)
    rng = float(event["high"]) - float(event["low"])
    if v <= 0 or avg_v is None or avg_v <= 0 or rng <= 0:
        return "SKIPPED"
    body = abs(float(event["close"]) - float(event["open"]))
    if v >= VOLUME_SURGE * avg_v and body < 0.35 * rng:
        return "ABSORPTION"
    return "NORMAL"


def _detect_lps(candles: list[dict], a: float, direction: str) -> dict | None:
    """LPS/LPSY: Spring (BUY) or Upthrust (SELL) within the last 10 closed
    bars, boundary reclaimed, shallow pullback holding, directional close.
    Each candidate uses its OWN clean range window BEFORE the event, so the
    event's sweep wick cannot contaminate the range definition."""
    n = len(candles)
    if n < RANGE_WINDOW + 12:
        return None
    for ev in range(max(1, n - 10), n - 2):
        win = candles[max(0, ev - RANGE_WINDOW):ev]
        if len(win) < RANGE_WINDOW - 5:
            continue
        rl = min(float(c["low"]) for c in win)
        rh = max(float(c["high"]) for c in win)
        if rh - rl <= 0 or (rh - rl) > RANGE_MAX_ATR * a:
            continue
        e = candles[ev]
        e_close = float(e["close"])
        if direction == "BUY":
            sprang = float(e["low"]) < rl and e_close > rl
        else:
            sprang = float(e["high"]) > rh and e_close < rh
        if not sprang:
            continue
        after = candles[ev + 1:]
        if not after:
            continue
        if direction == "BUY":
            reclaimed = max(float(c["close"]) for c in after) > rl + 0.2 * a
            hold = all(float(c["low"]) > rl - 0.15 * a for c in after)
            last_dir = float(candles[-1]["close"]) > float(candles[-1]["open"])
            boundary = rl
        else:
            reclaimed = min(float(c["close"]) for c in after) < rh - 0.2 * a
            hold = all(float(c["high"]) < rh + 0.15 * a for c in after)
            last_dir = float(candles[-1]["close"]) < float(candles[-1]["open"])
            boundary = rh
        if reclaimed and hold and last_dir:
            return {"event_index": ev, "boundary": boundary,
                    "range_low": rl, "range_high": rh}
    return None


def _lps_pullback_volume_healthy(candles: list[dict], ev: int, avg_v: float | None) -> bool | None:
    """Pullback volume health after the Spring/Upthrust: dry pullback (< 0.8x
    window average) supports the secondary entry. None when volume absent."""
    if avg_v is None or avg_v <= 0 or ev >= len(candles):
        return None
    after = candles[ev + 1:]
    vols = [float(c.get("volume") or 0) for c in after]
    if not vols or any(v <= 0 for v in vols):
        return None
    return max(vols) < 0.8 * avg_v


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
    """Usable positive provider volume; this does not prove centralized spot volume."""
    return all(float(c.get("volume") or 0) > 0 for c in candles)


@checked_strategy
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
    earned = 0.0
    max_pts = 76.0
    reasons: list[str] = []

    width_ok = (range_high - range_low) <= RANGE_MAX_ATR * a
    checks["range_ok"] = width_ok
    if not width_ok:
        return None
    earned += 10
    reasons.append(f"range {range_low:.5g}–{range_high:.5g} (width {(range_high - range_low) / a:.2f} ATR)")

    alts = _alternations([float(c["close"]) for c in window], mid)
    checks["alternations"] = alts
    if alts < MIN_ALTERNATIONS:
        return None
    earned += 8

    inside_prev = range_low <= float(prev["close"]) <= range_high
    checks["range_intact_before"] = inside_prev
    if not inside_prev:
        return None

    lo = float(event["low"])
    hi = float(event["high"])
    # A close outside the opposite boundary is a breakout, not a range reclaim.
    if not range_low < close < range_high:
        return None
    # An outside bar sweeping BOTH boundaries has no unambiguous event side.
    if lo < range_low and hi > range_high:
        return None
    if lo < range_low and close > range_low:
        direction = "BUY"
        wick_extreme = lo
    elif hi > range_high and close < range_high:
        direction = "SELL"
        wick_extreme = hi
    else:
        # v8.12.0: not a fresh Spring/Upthrust on THIS bar — try the Wyckoff
        # SECONDARY entry (LPS/LPSY): a Spring/Upthrust within the last 10
        # closed bars, boundary reclaimed, shallow pullback holding.
        lps_buy = _detect_lps(candles, a, "BUY")
        lps_sell = _detect_lps(candles, a, "SELL")
        if lps_buy and not lps_sell:
            chosen = (lps_buy, "BUY")
        elif lps_sell and not lps_buy:
            chosen = (lps_sell, "SELL")
        elif lps_buy and lps_sell:
            chosen = (lps_buy, "BUY") if close > mid else (lps_sell, "SELL")
        else:
            chosen = None
        if not chosen:
            return None
        lps, direction = chosen
        # adopt the clean pre-event range for the remainder of the evaluation
        range_low = float(lps["range_low"])
        range_high = float(lps["range_high"])
        height = range_high - range_low
        mid = (range_high + range_low) / 2.0
        if height <= 0 or height > RANGE_MAX_ATR * a:
            return None
        checks["range_ok"] = True
        reasons.append(f"clean pre-event range {range_low:.5g}–{range_high:.5g} (width {height / a:.2f} ATR)")
        checks["lps_event_index"] = lps["event_index"]
        wick_extreme = float(candles[lps["event_index"]]["low" if direction == "BUY" else "high"])
    checks["event"] = "SPRING" if direction == "BUY" else "UPTHRUST"
    if "lps_event_index" in checks:
        checks["event"] += "+LPS"
        earned += 16
        reasons.append(
            f"{checks['event']}: sweep at bar -{len(candles) - checks['lps_event_index']} reclaimed the "
            "boundary; shallow pullback is holding — Wyckoff secondary entry"
        )
    else:
        checks["sweep"] = True
        earned += 16
        reasons.append(f"{checks['event']}: wick swept {'below support' if direction == 'BUY' else 'above resistance'} then closed back inside")

    checks["close_inside"] = range_low <= close <= range_high
    earned += 8 if checks["close_inside"] else 0

    avg_v_any: float | None = None
    if _real_volume(candles[-RANGE_WINDOW - 1:]):
        avg_v = sum(float(c.get("volume") or 0) for c in window) / len(window)
        avg_v_any = avg_v
        surge_bar = candles[checks["lps_event_index"]] if "lps_event_index" in checks else event
        surge = float(surge_bar.get("volume") or 0) >= VOLUME_SURGE * avg_v
        checks["volume_surge"] = surge
        if not surge:
            return None
        earned += 8
        reasons.append(f"event volume {float(surge_bar.get('volume') or 0):.0f} >= {VOLUME_SURGE:g}× window average {avg_v:.0f}")
    else:
        checks["volume_surge"] = None
        max_pts -= 8  # Don't penalize FX instruments that lack tick volume
        reasons.append("volume data absent — effort check skipped (honest)")

    # v8.12.0 — effort vs result: large effort with a narrow spread is
    # absorption (someone is filling against the move), downgrade honestly.
    effort = _effort_vs_result(event, avg_v_any)
    checks["effort_vs_result"] = effort
    if effort == "ABSORPTION":
        earned -= 6
        reasons.append("effort vs result: large volume with a narrow spread — absorption, downgraded")

    # v8.12.0 — LPS/LPSY pullback volume health (secondary entries only)
    if "lps_event_index" in checks:
        pull_vol = _lps_pullback_volume_healthy(candles, checks["lps_event_index"], avg_v_any)
        checks["pullback_volume_dry"] = pull_vol
        if pull_vol is None:
            reasons.append("pullback volume check skipped (no volume data — honest)")
        elif pull_vol:
            earned += 4
            reasons.append("pullback volume dried up (<0.8× average) — healthy secondary entry")
        else:
            reasons.append("pullback volume not dry — secondary entry retained without the volume bonus")

    # v8.12.0 — shortening of thrust inside the range: counter-swings getting
    # shallower before the event = the auction is already turning.
    sot = _shortening_of_thrust(candles, len(candles) - 1, direction)
    checks["shortening_of_thrust"] = sot
    earned += 4 if sot else 0

    # v8.0.0: Real HTF bias check from ctx.bias_candles
    bias = structure_labels(ctx.bias_candles)
    htf_ok = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    checks["htf_bias"] = htf_ok
    earned += 12 if htf_ok else 0

    kz = ctx.kill_zone or (kill_zone(event["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0

    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    entry = close
    if direction == "BUY":
        stop = wick_extreme - 0.5 * a
    else:
        stop = wick_extreme + 0.5 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    min_step = max(0.5 * risk, 1e-6)
    if direction == "BUY":
        raw_tp1 = max(range_high, entry + 2.0 * risk)
        raw_tp2 = max(raw_tp1 + min_step, entry + height)
        raw_tp3 = max(raw_tp2 + min_step, entry + 1.618 * height)
    else:
        raw_tp1 = min(range_low, entry - 2.0 * risk)
        raw_tp2 = min(raw_tp1 - min_step, entry - height)
        raw_tp3 = min(raw_tp2 - min_step, entry - 1.618 * height)
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)
    checks["rr_ok"] = rr >= 2.0
    earned += 4 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        diagnosis_record("WYCKOFF", checks, earned, max_pts)
        return None

    reasons.append(f"HTF bias={bias['trend']} · stop beyond event wick = {stop:.5g}")
    reasons.append(f"targets 2R/measure/1.618ext = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}")
    reasons.append(f"kill zone: {kz}" if kz else "outside kill zones")
    reasons.append(f"RR(TP1)=1:{rr:.2f}")
    return StrategyResult(
        code="WYCKOFF", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=score, reasons=reasons, checks=checks,
    )
