"""TURTLE_SOUP — single-sided failed break of the prior 20-bar extreme.

Latest wick pierces one side and closes strictly inside the prior range.
Reject a bar sweeping both sides instead of selecting a direction by if-order.
HTF, rejection wick and reclaim margin are bonuses, not mandatory conditions.
Entry: close. Stop: sweep extreme +/-0.3 ATR, widened if too tight.
Targets: planned 2R/3R/4.5R.
Weights: range6, false-break18, reclaim-margin12, HTF12, wick10,
kill-zone6, volatility4, RR8 (total76). Minimum score58; minimum32 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

LOOKBACK = 20
LONG_LOOKBACK = 55          # v8.13.0 — the classical alternative reference window


@checked_strategy
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
    if last["high"] > hi and last["low"] < lo:
        return None  # both sides swept: do not choose SELL merely by if-order
    if not lo < last["close"] < hi:
        return None
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
    else:
        stop = max(last["high"], hi) + 0.3 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=0.6, fallback_atr=1.2, max_atr=2.6, cap_atr=2.2)
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

    earned = 0.0
    max_pts = 76.0
    checks = {}
    checks["level"] = hi > lo
    earned += 6
    checks["false_break"] = True
    checks["sweep"] = True
    earned += 18
    if direction == "BUY":
        margin = (last["close"] - lo) / max(range_, 1e-12)
    else:
        margin = (hi - last["close"]) / max(range_, 1e-12)
    checks["close_back_margin"] = margin >= 0.25
    earned += 12 if checks["close_back_margin"] else 0
    checks["htf_bias"] = bias_ok
    earned += 12 if bias_ok else 0
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]
    reject = (direction == "SELL" and upper_wick > body) or (direction == "BUY" and lower_wick > body)
    checks["wick_rejection"] = reject
    earned += 10 if reject else 0
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if rr >= 2.0 else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) long-window context: the 55-bar reference. Sweep of the 55 extreme
    # (or proximity to it) marks a MAJOR liquidity grab, not just noise.
    major = False
    if len(candles) >= LONG_LOOKBACK + 2:
        long_prior = candles[-(LONG_LOOKBACK + 1):-1]
        l_hi = max(float(c["high"]) for c in long_prior)
        l_lo = min(float(c["low"]) for c in long_prior)
        if direction == "SELL" and float(last["high"]) >= l_hi - 0.1 * a:
            major = True
        elif direction == "BUY" and float(last["low"]) <= l_lo + 0.1 * a:
            major = True
    checks["long_window_55"] = major
    earned += 4 if major else 0

    # 2) second failed attempt: another sweep of the SAME side within the
    # last 10 bars (soup + soup) — the classical double-trap fuel.
    second_trap = False
    if len(candles) >= LOOKBACK + 11:
        for j in range(2, 11):
            b = candles[-j]
            if direction == "SELL" and float(b["high"]) > hi and float(b["close"]) < hi:
                second_trap = True
                break
            if direction == "BUY" and float(b["low"]) < lo and float(b["close"]) > lo:
                second_trap = True
                break
    checks["second_trap"] = second_trap
    earned += 4 if second_trap else 0

    # 3) session context: named killzone quality (LONDON/NY are the trap
    # sessions; ASIA/OFF get no extra weight).
    trap_session = kz in ("LONDON", "NEW_YORK")
    checks["trap_session"] = trap_session
    earned += 3 if trap_session else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("TURTLE_SOUP", checks, earned, max_pts)
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
    if major:
        reasons.append("sweep at the 55-bar reference extreme — major liquidity grab")
    if second_trap:
        reasons.append("second failed attempt of the same side within 10 bars (double trap)")
    if trap_session:
        reasons.append(f"trap during the {kz} trap session")
    return StrategyResult(
        code="TURTLE_SOUP", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
