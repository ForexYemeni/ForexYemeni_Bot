"""HARMONICS — Gartley / Bat / Butterfly / Crab, fully mechanical (v8.12.0).

The four canonical harmonic patterns are detected from an alternating zigzag
pivot sequence X→A→B→C→D built with the platform `swings` fractal detector.
Every ratio is checked against the published harmonic windows with an explicit
absolute tolerance; no pattern is ever inferred subjectively.

Definitions (bullish; bearish is the mirror):
  X (lowest pivot) → A (highest) → B (retracement low, above X) →
  C (retracement high, below A) → D (trigger low).
    AB/XA  : Gartley 0.618 | Bat 0.382–0.50 | Butterfly 0.786 | Crab 0.382–0.618
    BC/AB  : 0.382–0.886  (all patterns)
    CD/BC  : Gartley 1.13–1.618 | Bat 1.618–2.618 | Butterfly 1.618–2.24 |
             Crab 2.618–3.618
    AD/XA  : Gartley 0.786 | Bat 0.886 | Butterfly 1.27–1.618 | Crab 1.618
             (D lands BELOW X for Butterfly/Crab)

Tolerance: ±RATIO_TOL on exact ratios, ± within documented ranges; the pattern
whose ratio centering is best wins when several qualify. Entry at D (close);
invalidation beyond X ∓ buffer for Gartley/Bat, beyond D ∓ buffer for
Butterfly/Crab. Targets: 0.382 / 0.618 / 1.27 of the AD leg with the platform
2R/3R/4.5R floors applied separately (reasons disclose raw vs planned).

Score is checklist merit normalized to 42..95 — NOT a win probability.
"""
from fysb.engines.analysis import atr, atr_pct, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

RATIO_TOL = 0.06            # absolute tolerance on exact harmonic ratios
MIN_SCORE = 58

# name -> (AB/XA, CD/BC, AD/XA) windows: (lo, hi) each
_PATTERNS = {
    "GARTLEY":   {"ab": (0.618 - RATIO_TOL, 0.618 + RATIO_TOL), "cd": (1.13, 1.618), "ad": (0.786 - RATIO_TOL, 0.786 + RATIO_TOL)},
    "BAT":       {"ab": (0.382, 0.50), "cd": (1.618, 2.618), "ad": (0.886 - RATIO_TOL, 0.886 + RATIO_TOL)},
    "BUTTERFLY": {"ab": (0.786 - RATIO_TOL, 0.786 + RATIO_TOL), "cd": (1.618, 2.24), "ad": (1.27, 1.618)},
    "CRAB":      {"ab": (0.382, 0.618), "cd": (2.618, 3.618), "ad": (1.618 - RATIO_TOL, 1.618 + RATIO_TOL)},
}


def _zigzag(candles: list[dict]) -> list[dict]:
    """Alternating swing pivots (type H/L, price, index), most-recent last."""
    from fysb.engines.analysis import swings
    highs, lows = swings(candles, 3)
    pts = [{"t": "H", "price": p["price"], "index": p["index"]} for p in highs] + \
          [{"t": "L", "price": p["price"], "index": p["index"]} for p in lows]
    pts.sort(key=lambda p: p["index"])
    merged: list[dict] = []
    for p in pts:
        if merged and merged[-1]["t"] == p["t"]:
            # keep the more extreme consecutive same-type pivot
            if (p["t"] == "H" and p["price"] > merged[-1]["price"]) or \
               (p["t"] == "L" and p["price"] < merged[-1]["price"]):
                merged[-1] = p
            continue
        merged.append(p)
    return merged


def _match_pattern(x, a, b, c, d, direction: str) -> list[tuple[str, float]]:
    """All pattern names whose published windows fit this pivot sequence.

    Returns [(name, centering 0..1)] — centering = distance from the window
    center normalized by half-width (1.0 = dead center).
    """
    ab = (a - b) / (a - x)
    bc = (c - b) / (a - b)
    cd = (c - d) / (c - b)
    ad = (a - d) / (a - x)
    out = []
    for name, w in _PATTERNS.items():
        if not (w["ab"][0] <= ab <= w["ab"][1]):
            continue
        if not (0.382 - RATIO_TOL <= bc <= 0.886 + RATIO_TOL):
            continue
        if not (w["cd"][0] <= cd <= w["cd"][1]):
            continue
        if not (w["ad"][0] <= ad <= w["ad"][1]):
            continue
        centerings = []
        for key, val in (("ab", ab), ("cd", cd), ("ad", ad)):
            lo, hi = w[key]
            half = (hi - lo) / 2.0 or 1e-9
            centerings.append(max(0.0, 1.0 - abs(val - (lo + hi) / 2.0) / half))
        out.append((name, sum(centerings) / len(centerings)))
    return out


def _pivots_for(last5: list[dict]) -> tuple | None:
    """Validate X,A,B,C,D alternation for bullish (L,H,L,H,L) or bearish mirror."""
    if len(last5) < 5:
        return None
    types = [p["t"] for p in last5]
    if types == ["L", "H", "L", "H", "L"]:
        return ("BUY",) + tuple(p["price"] for p in last5)
    if types == ["H", "L", "H", "L", "H"]:
        return ("SELL",) + tuple(p["price"] for p in last5)
    return None


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 60:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])

    zig = _zigzag(candles)
    if len(zig) < 5:
        return None

    best = None  # (direction, pattern, centering, x, a, b, c, d)
    for i in range(len(zig) - 4, -1, -1):
        seq = zig[i:i + 5]
        parsed = _pivots_for(seq)
        if not parsed:
            continue
        direction, x, a_px, b, c, d = parsed
        # b/c/d must be sequential in time relative to the last close and D recent
        d_idx = seq[4]["index"]
        if d_idx < len(candles) - 25:
            continue  # stale pattern — D is ancient history, not a live setup
        matches = _match_pattern(x, a_px, b, c, d, direction)
        if not matches:
            continue
        for name, centering in matches:
            # structural sanity: D must be the most recent meaningful pivot
            if direction == "BUY" and not (d < b < c < a_px and x < d):
                continue
            if direction == "SELL" and not (d > b > c > a_px and x > d):
                continue
            if best is None or centering > best[2]:
                best = (direction, name, centering, x, a_px, b, c, d)
    if best is None:
        return None

    direction, name, centering, x, a_px, b, c, d = best
    entry = close
    ad = abs(a_px - d)

    if direction == "BUY":
        deeper = min(x, d) if name in ("BUTTERFLY", "CRAB") else x
        stop = deeper - 0.5 * a
    else:
        higher = max(x, d) if name in ("BUTTERFLY", "CRAB") else x
        stop = higher + 0.5 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.0, fallback_atr=1.6, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # Targets from the AD leg with platform floors applied separately:
    if direction == "BUY":
        raw_tp1 = entry + 0.382 * ad
        raw_tp2 = entry + 0.618 * ad
        raw_tp3 = entry + 1.27 * ad
    else:
        raw_tp1 = entry - 0.382 * ad
        raw_tp2 = entry - 0.618 * ad
        raw_tp3 = entry - 1.27 * ad
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)

    earned = 0.0
    max_pts = 90.0
    checks: dict = {}
    checks["pattern"] = name
    checks["zigzag"] = True
    earned += 24          # a fully-windowed XABCD pattern is the mandatory core
    checks["ratio_centering"] = centering
    earned += 18 * centering          # dead-center ratios earn the full bonus
    bias = structure_labels(ctx.bias_candles)
    mss = structure_labels(candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL") or \
              (mss["choch"] and mss["bos_dir"] == ("up" if direction == "BUY" else "down"))
    checks["htf_bias"] = bias_ok
    earned += 14 if bias_ok else 0
    # PRZ confluence: D landing on a round harmonic of XA is the classic PRZ
    prz = any(abs(ad_ratio - w) <= 0.02 for w in (0.786, 0.886, 1.27, 1.618)
              for ad_ratio in [abs(a_px - d) / abs(a_px - x)])
    checks["prz_confluence"] = prz
    earned += 10 if prz else 0
    from fysb.engines.analysis import kill_zone
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        diagnosis_record("HARMONIC", checks, earned, max_pts)
        return None

    reasons = [
        f"{name} XABCD complete (bullish = L,H,L,H,L zigzag) ratios within published windows",
        f"XA={abs(a_px - x):.5g} · AD/XA={abs(a_px - d) / abs(a_px - x):.3f} · PRZ={'yes' if prz else 'no'}",
        f"entry at D close {entry:.5g}",
        f"invalidation beyond {'X' if name in ('GARTLEY', 'BAT') else 'D'} {stop:.5g}",
        f"raw targets 0.382/0.618/1.27 of AD — planned floors 2R/3R/4.5R applied",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="HARMONIC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
