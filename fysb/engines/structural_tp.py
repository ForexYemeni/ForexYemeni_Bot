"""Structural Take-Profit & Liquidity Obstruction Engine (v8.0.0).

Replaces blind fixed-multiple targets with institutional liquidity targeting
while STRICTLY upholding the Royal minimum floor:
  * TP1 is NEVER below 2.0 × Risk (twice the Stop Loss!).
  * TP2 is NEVER below 3.0 × Risk.
  * TP3 is NEVER below 4.5 × Risk.

How it works:
  1. Collects real structural liquidity levels from LTF + HTF candles:
     - Previous Day High/Low (PDH / PDL)
     - Asian Session High/Low (00:00–06:00 UTC)
     - LTF & HTF Swing Highs / Swing Lows
     - Opposing Order Block & Fair Value Gap boundaries
  2. Snaps TP1 to the nearest clean liquidity pool in [2.0R .. 2.65R] minus a
     0.08 * ATR institutional front-run buffer (clamped strictly >= 2.0R).
     Snaps TP2 in [3.0R .. 3.9R] and TP3 in [4.5R .. 5.8R].
  3. HTF Obstruction Guard (WITHOUT ever shrinking TP1 below 2.0R):
     If a strong HTF level (HTF swing or PDH/PDL) sits directly in the way
     between 0.65R and 1.85R, applies a fair score penalty (-6, or -10 if
     < 1.25R) and records `checks["clear_path_to_2r"] = False`.
"""
from __future__ import annotations

from fysb.engines.analysis import (
    atr,
    fair_value_gaps,
    order_blocks,
    session_levels,
    swings,
)
from fysb.engines.strategies.base import StrategyResult, clamp_score, ensure_min_tp_rr


def _collect_liquidity_levels(candles: list[dict], bias_candles: list[dict], direction: str) -> tuple[list[float], list[float]]:
    """Return (all_targets, htf_obstacles) in the trade direction."""
    d = (direction or "BUY").upper()
    all_lvls: list[float] = []
    htf_lvls: list[float] = []

    sess = session_levels(candles)
    for k in ("pdh", "pdl", "asian_high", "asian_low"):
        v = sess.get(k)
        if v is not None and v > 0:
            all_lvls.append(float(v))
            if k in ("pdh", "pdl"):
                htf_lvls.append(float(v))

    ltf_hi, ltf_lo = swings(candles, 3)
    for h in ltf_hi[-8:]:
        all_lvls.append(float(h["price"]))
    for l in ltf_lo[-8:]:
        all_lvls.append(float(l["price"]))

    if bias_candles and len(bias_candles) >= 20:
        htf_hi, htf_lo = swings(bias_candles, 3)
        for h in htf_hi[-6:]:
            p = float(h["price"])
            all_lvls.append(p)
            htf_lvls.append(p)
        for l in htf_lo[-6:]:
            p = float(l["price"])
            all_lvls.append(p)
            htf_lvls.append(p)

    opp_kind = "bearish" if d == "BUY" else "bullish"
    for ob in order_blocks(candles, 4):
        if ob["kind"] == opp_kind:
            all_lvls.append(float(ob["lo"] if d == "BUY" else ob["hi"]))
    for fvg in fair_value_gaps(candles, 4):
        if fvg["kind"] == opp_kind:
            all_lvls.append(float(fvg["lo"] if d == "BUY" else fvg["hi"]))

    return sorted(set(all_lvls)), sorted(set(htf_lvls))


def _snap_target(
    entry: float,
    risk: float,
    direction: str,
    levels: list[float],
    min_r: float,
    max_r: float,
    buf: float,
    default_tp: float,
) -> tuple[float, bool]:
    """Snap target just before a liquidity level within [min_r, max_r], NEVER below min_r."""
    d = (direction or "BUY").upper()
    floor_px = (entry + risk * min_r) if d == "BUY" else (entry - risk * min_r)
    ceil_px = (entry + risk * max_r) if d == "BUY" else (entry - risk * max_r)

    if d == "BUY":
        cands = [x for x in levels if floor_px <= x <= ceil_px]
        if cands:
            snapped = max(floor_px, min(cands) - buf)
            return snapped, True
        return max(float(default_tp), floor_px), False
    cands = [x for x in levels if ceil_px <= x <= floor_px]
    if cands:
        snapped = min(floor_px, max(cands) + buf)
        return snapped, True
    return min(float(default_tp), floor_px), False


def apply_structural_targets(
    cand: StrategyResult,
    candles: list[dict],
    bias_candles: list[dict] | None = None,
) -> StrategyResult:
    """Enrich `cand` with structural liquidity targets + HTF obstruction check.

    STRICT INVARIANT: TP1 >= 2.0 * risk, TP2 >= 3.0 * risk, TP3 >= 4.5 * risk.
    """
    entry = float(cand.entry)
    stop = float(cand.stop)
    risk = abs(entry - stop)
    if risk <= 1e-12 or not candles or len(candles) < 20:
        return cand
    # Do not artificially rescue a broken/low-RR candidate (< 2.0R) — let risk_gate reject it honestly.
    orig_rr = abs(float(cand.tp1) - entry) / risk
    if float(cand.rr or 0.0) < 2.0 - 1e-6 or orig_rr < 2.0 - 1e-6:
        return cand

    d = (cand.direction or "BUY").upper()
    a = atr(candles)
    buf = 0.08 * a if a > 0 else 0.03 * risk
    all_lvls, htf_lvls = _collect_liquidity_levels(candles, bias_candles or candles, d)

    # First ensure the base targets satisfy >= 2.0R / 3.0R / 4.5R
    b_tp1, b_tp2, b_tp3, _ = ensure_min_tp_rr(entry, stop, cand.tp1, cand.tp2, cand.tp3, d)

    # Snap each TP onto real liquidity pools when present within institutional bands
    t1, hit1 = _snap_target(entry, risk, d, all_lvls, 2.0, 2.65, buf, b_tp1)
    t2, hit2 = _snap_target(entry, risk, d, all_lvls, 3.0, 3.90, buf, b_tp2)
    t3, hit3 = _snap_target(entry, risk, d, all_lvls, 4.5, 5.80, buf, b_tp3)

    # Final hard invariant enforcement (monotonic & >= 2.0R / 3.0R / 4.5R)
    t1, t2, t3, rr1 = ensure_min_tp_rr(entry, stop, t1, t2, t3, d)

    # HTF Obstruction Guard: detect major HTF wall BEFORE 2.0R without shrinking TP1
    if d == "BUY":
        obs_before_2r = [
            (x - entry) / risk
            for x in htf_lvls
            if (entry + 0.65 * risk) <= x <= (entry + 1.85 * risk)
        ]
    else:
        obs_before_2r = [
            (entry - x) / risk
            for x in htf_lvls
            if (entry - 1.85 * risk) <= x <= (entry - 0.65 * risk)
        ]

    cand.tp1, cand.tp2, cand.tp3 = t1, t2, t3
    cand.rr = rr1
    if cand.checks is None:
        cand.checks = {}
    cand.checks["structural_tp"] = bool(hit1 or hit2 or hit3)
    if obs_before_2r:
        nearest_r = min(obs_before_2r)
        penalty = 10 if nearest_r < 1.25 else 6
        cand.checks["clear_path_to_2r"] = False
        cand.score = clamp_score(float(cand.score) - penalty)
        cand.reasons = list(cand.reasons) + [
            f"HTF obstacle @ {nearest_r:.2f}R before TP1(2.0R) (-{penalty} pts)"
        ]
    else:
        cand.checks["clear_path_to_2r"] = True
        if hit1:
            cand.reasons = list(cand.reasons) + [f"Structural TP1 @ 1:{rr1:.2f} liquidity"]
    return cand
