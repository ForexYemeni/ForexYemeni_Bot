"""SMC — project-specific BOS/CHOCH and swing-derived supply/demand model.

A BOS/CHOCH determines direction. Demand/supply uses an intact-side swing and
an ATR-width zone, not measured resting orders. HTF alignment is a score bonus,
not a mandatory rule inside this module. Entry uses zone edge clipped to close.
Stop: protective swing +/- 0.85 ATR, widened if tight; targets 2R/3R/4.5R.
Weights: labels14, BOS12, CHOCH10, zone14, HTF12, volatility4, RR6
(total72). Minimum score58; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, structure_labels, swings, support_resistance
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    close = candles[-1]["close"]
    struct = structure_labels(candles)
    bias = structure_labels(ctx.bias_candles)

    labels = struct["labels"]
    # Recent-structure read: only the last few labels drive the direction so an
    # old long trend in history cannot outvote the fresh BOS/CHOCH.
    recent = labels[-6:]
    hh = recent.count("HH")
    hl = recent.count("HL")
    lh = recent.count("LH")
    ll = recent.count("LL")
    up_count, down_count = (hh + hl), (lh + ll)

    # v8.0.0 FIX (SIG-1): On CHOCH, `bos_dir` gives the NEW reversal break direction;
    # NEVER use the pre-reversal `struct["trend"]` which points the OPPOSITE way!
    direction = ""
    if struct["bos"] and struct.get("bos_dir"):
        direction = "BUY" if struct["bos_dir"] == "up" else "SELL"
    elif struct["choch"]:
        if struct.get("bos_dir") in ("up", "down"):
            direction = "BUY" if struct["bos_dir"] == "up" else "SELL"
        elif struct["trend"] == "down":
            direction = "BUY"
        elif struct["trend"] == "up":
            direction = "SELL"
        else:
            direction = "BUY" if close >= float(candles[-1]["open"]) else "SELL"
    if not direction:
        return None

    # --- Demand / Supply zone from the last INTACT opposite swing (v6.20.0) --
    # Skip broken swings where price already closed beyond the zone boundary.
    highs, lows = swings(candles, 3)
    a = atr(candles)
    if a <= 0:
        return None
    zone = None
    if direction == "BUY" and lows:
        for base in reversed(lows):
            if close >= base["price"]:
                zone = (base["price"], base["price"] + 1.2 * a)
                break
        if zone is None:
            return None  # All demand zones violated — structure is broken
        entry = min(zone[1], close)
    elif direction == "SELL" and highs:
        for base in reversed(highs):
            if close <= base["price"]:
                zone = (base["price"] - 1.2 * a, base["price"])
                break
        if zone is None:
            return None  # All supply zones violated — structure is broken
        entry = max(zone[0], close)
    else:
        entry = close

    # --- Stop beyond the protective swing (v6.20.6: 0.85 ATR breathing room) --
    if direction == "BUY":
        stop = (zone[0] - 0.85 * a) if zone else entry - 1.8 * a
    else:
        stop = (zone[1] + 0.85 * a) if zone else entry + 1.8 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.15, fallback_atr=1.8, max_atr=2.8, cap_atr=2.4)

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

    # --- Normalized Fair Score (v8.0.0) ------------------------------------
    earned = 0.0
    max_pts = 72.0
    checks = {}
    checks["labels"] = (direction == "BUY" and up_count > down_count) or (direction == "SELL" and down_count > up_count)
    earned += 14 if checks["labels"] else 0
    checks["bos"] = struct["bos"]
    earned += 12 if checks["bos"] else 0
    checks["choch"] = struct["choch"]
    earned += 10 if checks["choch"] else 0
    checks["zone"] = zone is not None
    earned += 14 if zone else 0
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    earned += 12 if checks["htf_bias"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    # v8.12.0 — institutional anchor confluence: the SMC zone landing on a
    # classic daily pivot / opening anchor is institutional-grade supply/demand.
    try:
        from fysb.engines.intelligence import detect_anchor_confluence
        _anch = detect_anchor_confluence(candles, entry, direction, atr_value=a)
        checks["anchor_confluence"] = bool(_anch["detected"])
        max_pts += 6
        earned += 6 if _anch["detected"] else 0
    except Exception:
        checks["anchor_confluence"] = None

    score = normalized_score(earned, max_pts)
    if score < 58:
        return None

    sr = support_resistance(candles)
    reasons = [
        f"structure labels: {','.join(labels[-6:]) or 'n/a'}",
        f"BOS={'yes' if struct['bos'] else 'no'} / CHOCH={'yes' if struct['choch'] else 'no'}",
        f"entry retraces {'demand' if direction=='BUY' else 'supply'} zone {zone[0]:.5g}-{zone[1]:.5g}" if zone else "entry at market",
        f"HTF bias={bias['trend']}",
        f"S/R: support={sr['support'] if sr['support'] is None else format(sr['support'], '.5g')}, resistance={sr['resistance'] if sr['resistance'] is None else format(sr['resistance'], '.5g')}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SMC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
