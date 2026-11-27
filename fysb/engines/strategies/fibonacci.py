"""FIBONACCI — largest directed impulse in a rolling40-bar window.

Minimum impulse2.5 ATR; current close inside61.8..78.6% retracement.
HTF/MSS and pocket sweep are bonuses, not mandatory candle-confirmation rules.
Entry: close. Stop beyond impulse origin +/-0.5 ATR; never tighten inward.
Raw targets: swing extreme, extreme +/-0.272 impulse, +/-0.618 impulse.
Apply platform2R/3R/4.5R floors separately; reasons disclose raw vs planned levels.
Weights: impulse12, pocket18, orderly8, pocket-sweep8, bias12,
kill-zone6, volatility4, RR8 (total76). Minimum score58; minimum45 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

IMPULSE_ATR = 2.5
POCKET_LO, POCKET_HI = 0.618, 0.786


def _impulse_leg(window: list[dict], direction: str) -> tuple[int, int, float] | None:
    """(origin_idx, extreme_idx, impulse_size) for the last clean leg."""
    if direction == "BUY":
        origin = min(range(len(window)), key=lambda i: float(window[i]["low"]))
        after = [i for i in range(origin + 1, len(window))]
        if not after:
            return None
        ext = max(after, key=lambda i: float(window[i]["high"]))
        size = float(window[ext]["high"]) - float(window[origin]["low"])
        return (origin, ext, size) if size > 0 else None
    origin = max(range(len(window)), key=lambda i: float(window[i]["high"]))
    after = [i for i in range(origin + 1, len(window))]
    if not after:
        return None
    ext = min(after, key=lambda i: float(window[i]["low"]))
    size = float(window[origin]["high"]) - float(window[ext]["low"])
    return (origin, ext, size) if size > 0 else None


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 45:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    window = candles[-40:]
    last = candles[-1]
    close = float(last["close"])

    best: tuple[str, int, int, float] | None = None
    for direction in ("BUY", "SELL"):
        leg = _impulse_leg(window, direction)
        if leg and (best is None or leg[2] > best[3]):
            best = (direction, leg[0], leg[1], leg[2])
    if not best:
        return None
    direction, o_idx, e_idx, impulse = best
    origin_px = float(window[o_idx]["low"] if direction == "BUY" else window[o_idx]["high"])
    extreme_px = float(window[e_idx]["high"] if direction == "BUY" else window[e_idx]["low"])

    checks = {}
    earned = 0.0
    max_pts = 76.0
    checks["impulse"] = impulse >= IMPULSE_ATR * a
    if not checks["impulse"]:
        return None
    earned += 12

    depth = (extreme_px - close) / impulse if direction == "BUY" else (close - extreme_px) / impulse
    in_pocket = POCKET_LO <= depth <= POCKET_HI
    checks["golden_pocket"] = in_pocket
    if not in_pocket:
        return None
    earned += 18

    orderly = (close > origin_px) if direction == "BUY" else (close < origin_px)
    checks["orderly"] = orderly
    if not orderly:
        return None
    earned += 8

    if direction == "BUY":
        swept = any(float(c["low"]) <= extreme_px - POCKET_HI * impulse
                    and float(c["close"]) >= extreme_px - POCKET_LO * impulse
                    for c in window[e_idx:])
    else:
        swept = any(float(c["high"]) >= extreme_px + POCKET_HI * impulse
                    and float(c["close"]) <= extreme_px + POCKET_LO * impulse
                    for c in window[e_idx:])
    checks["pocket_sweep"] = swept
    checks["sweep"] = swept
    earned += 8 if swept else 0

    bias = structure_labels(ctx.bias_candles)
    mss = structure_labels(candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL") or \
              (mss["choch"] and mss["bos_dir"] == ("up" if direction == "BUY" else "down"))
    checks["htf_bias"] = bias_ok
    earned += 12 if bias_ok else 0

    kz = ctx.kill_zone or (kill_zone(candles[-1]["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    entry = close
    stop = (origin_px - 0.5 * a) if direction == "BUY" else (origin_px + 0.5 * a)
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # Fibonacci extensions are measured from the swing EXTREME, not from a
    # TP1 already moved by the platform's RR floor. Keep both meanings explicit.
    sign = 1.0 if direction == "BUY" else -1.0
    raw_tp1 = extreme_px
    raw_tp2 = extreme_px + sign * 0.272 * impulse
    raw_tp3 = extreme_px + sign * 0.618 * impulse
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)
    checks["targets_adjusted"] = any(abs(x-y) > 1e-9 for x, y in zip(
        (tp1, tp2, tp3), (raw_tp1, raw_tp2, raw_tp3)))
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if checks["rr_ok"] else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) formal OTE naming + sweet spot: the golden pocket IS the OTE band;
    # the 0.705 center grades higher (graded, never mandatory — the pocket
    # rule itself is unchanged).
    ote_sweet = abs(depth - 0.705) <= 0.03
    checks["ote_depth"] = round(depth, 3)
    checks["ote_sweet"] = ote_sweet
    earned += 3 if ote_sweet else 0

    # 2) equilibrium (50%): named discount/premium side of the leg — entry
    # depth beyond 0.5 is the discount (BUY) / premium (SELL) side, disclosed.
    eq = 0.5
    eq_side = "EQ+" if depth >= eq else "EQ-"
    checks["eq_side"] = eq_side
    earned += 2 if depth >= eq else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("FIBONACCI", checks, earned, max_pts)
        return None

    reasons = [
        f"impulse leg {origin_px:.5g} -> {extreme_px:.5g} ({impulse:.5g} = {impulse / a:.1f} ATR)",
        f"golden-pocket retrace {depth * 100:.1f}% (61.8-78.6%) @ {entry:.5g}",
        f"HTF bias={bias['trend']} / MSS dir={mss['bos_dir'] or '—'}",
        f"invalidation beyond 100% Fib = {stop:.5g}",
        f"raw Fibonacci levels: 0%={raw_tp1:.5g} / -27.2%={raw_tp2:.5g} / -61.8%={raw_tp3:.5g}",
        f"planned targets after RR floors: {tp1:.5g} / {tp2:.5g} / {tp3:.5g} (adjusted={checks['targets_adjusted']})",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    if ote_sweet:
        reasons.append("formal OTE sweet spot 0.705 ±0.03")
    reasons.append(f"equilibrium side: {eq_side} (depth {depth * 100:.1f}% of the leg)")
    return StrategyResult(
        code="FIBONACCI", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
