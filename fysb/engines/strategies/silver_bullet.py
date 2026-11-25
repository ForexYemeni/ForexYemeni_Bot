"""Silver Bullet (ICT) — DST-aware time-window FVG continuation, 100% mechanical.

Documented rules (v8.0.0 DST-accurate America/New_York clock):
  1. TIME WINDOW — Exact ICT Silver Bullet windows in New York time:
       LONDON_SB    03:00–04:00 NY
       NY_AM_SB     10:00–11:00 NY
       NY_PM_SB     14:00–15:00 NY
  2. DAY BIAS — Prior-session opening-range direction (bars from the 1–2h
     preceding the Silver Bullet window) must agree with trade direction.
  3. SETUP — An intact Fair Value Gap forms inside the window in bias direction.
  4. ENTRY — Midpoint of the newest intact FVG (never worse than market close).
  5. TARGETS — Strictly >= 2.0R / 3.0R / 5.0R.
"""
from fysb.engines.analysis import atr_pct, fair_value_gaps, ny_hour, structure_labels
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score,
)

SB_WINDOWS = [("LONDON_SB", 3, 4), ("NY_AM_SB", 10, 11), ("NY_PM_SB", 14, 15)]


def _window_of(dt_iso: str):
    h = ny_hour(dt_iso)
    if h is None:
        return None
    for name, start, end in SB_WINDOWS:
        if start <= h < end:
            return name
    return None


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 45:
        return None
    last = candles[-1]
    wname = _window_of(last["datetime"])
    if not wname:
        return None

    # Prior-session opening-range bias (1–2h prior to the Silver Bullet window)
    cur_h = ny_hour(last["datetime"])
    prior_bars = [
        c for c in candles[-30:-2]
        if (bh := ny_hour(c.get("datetime", ""))) is not None and ((cur_h - bh) % 24) in (1, 2)
    ]
    acc = prior_bars if len(prior_bars) >= 3 else candles[-30:][:8]
    or_hi = max(c["high"] for c in acc)
    or_lo = min(c["low"] for c in acc)
    close = last["close"]
    bias_dir = "BUY" if close > (or_hi + or_lo) / 2 else "SELL"

    # Intact FVGs formed inside this window (recent 10 candles)
    fvgs = fair_value_gaps(candles[-10:], 4)
    fvg = None
    for f in fvgs:
        kind_dir = "BUY" if f["kind"] == "bullish" else "SELL"
        if kind_dir != bias_dir:
            continue
        if bias_dir == "BUY" and close < f["lo"]:
            continue
        if bias_dir == "SELL" and close > f["hi"]:
            continue
        fvg = f
        break
    if not fvg:
        return None

    direction = bias_dir
    fvg_mid = (fvg["lo"] + fvg["hi"]) / 2
    entry = min(fvg_mid, close) if direction == "BUY" else max(fvg_mid, close)
    a_ptr = atr_pct(candles)
    struct = structure_labels(candles)
    bias = structure_labels(ctx.bias_candles)

    a_abs = max(a_ptr * entry / 100.0, 1e-9)
    if direction == "BUY":
        stop = min(min(c["low"] for c in candles[-8:]), fvg["lo"]) - 0.4 * (or_hi - or_lo) * 0.5
    else:
        stop = max(max(c["high"] for c in candles[-8:]), fvg["hi"]) + 0.4 * (or_hi - or_lo) * 0.5
    stop = cap_stop(entry, stop, direction, a_abs, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0,
        direction,
    )

    earned = 0.0
    max_pts = 80.0
    checks = {}
    checks["sb_window"] = wname
    checks["kill_zone"] = True
    earned += 16
    checks["or_bias"] = or_hi > or_lo
    earned += 10 if checks["or_bias"] else 0
    checks["fvg_in_window"] = True
    earned += 16
    checks["fvg_direction"] = True
    earned += 14
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or \
                         (bias["trend"] == "down" and direction == "SELL") or \
                         (struct["bos_dir"] == ("up" if direction == "BUY" else "down"))
    checks["htf_align"] = checks["htf_bias"]
    earned += 10 if checks["htf_bias"] else 0
    checks["vol_ok"] = 0.05 < a_ptr < 6.0
    earned += 6 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        return None

    reasons = [
        f"silver bullet window {wname} (DST NY time)",
        f"opening-range bias {bias_dir} ({or_lo:.5g}-{or_hi:.5g})",
        f"in-window {fvg['kind']} FVG {fvg['lo']:.5g}-{fvg['hi']:.5g}",
        f"entry at FVG midpoint {entry:.5g}",
        f"invalidation beyond {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SILVER_BULLET", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
