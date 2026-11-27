"""AMD — Asian-range preference with an explicit local-range fallback.

Prefer current-day Asian 00..06 UTC range; otherwise use eight local bars
followed by six event candidates. First single-sided sweep closing strictly
inside the reference range defines the trade in the opposite direction.
The last close must not be back beyond the swept level. HTF/expansion/FVG
are bonuses. Entry is close or a clipped retracement. Stop stays beyond the
ACTUAL manipulation wick +/-0.5 ATR. Range targets have 2R/3R/4.5R floors.
Weights: range8, manipulation16, reclaim14, HTF12, expansion8,
kill-zone6, volatility4, RR8, FVG8 (total84).
Minimum score58; minimum40 bars. Fallback is NOT a full daily AMD cycle.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import (
    atr, atr_pct, fair_value_gaps, kill_zone, session_levels, structure_labels,
)
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

OPEN_WINDOW = 8          # fallback candles forming the accumulation range


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None

    # v8.0.0 (SIG-6): Prefer true Asian Session Accumulation (00:00–06:00 UTC)
    sess = session_levels(candles)
    asian_used = False
    window = candles[-(OPEN_WINDOW + 6):]
    if sess.get("asian_high") is not None and sess.get("asian_low") is not None and sess["asian_high"] > sess["asian_low"]:
        hi, lo = float(sess["asian_high"]), float(sess["asian_low"])
        scan_bars = candles[-8:]
        asian_used = True
    else:
        acc = window[:OPEN_WINDOW]
        hi = max(c["high"] for c in acc)
        lo = min(c["low"] for c in acc)
        scan_bars = window[OPEN_WINDOW:]
    if hi <= lo:
        return None

    manip_side, manip_level = "", 0.0
    manip_candle = None
    for c in scan_bars:
        if not lo < c["close"] < hi or (c["high"] > hi and c["low"] < lo):
            continue
        if c["high"] > hi and c["close"] < hi:
            manip_side, manip_level = "high", hi
            manip_candle = c
            break
        if c["low"] < lo and c["close"] > lo:
            manip_side, manip_level = "low", lo
            manip_candle = c
            break
    if not manip_side and asian_used:
        acc = window[:OPEN_WINDOW]
        hi = max(c["high"] for c in acc)
        lo = min(c["low"] for c in acc)
        asian_used = False
        if hi > lo:
            for c in window[OPEN_WINDOW:]:
                if not lo < c["close"] < hi or (c["high"] > hi and c["low"] < lo):
                    continue
                if c["high"] > hi and c["close"] < hi:
                    manip_side, manip_level = "high", hi
                    manip_candle = c
                    break
                if c["low"] < lo and c["close"] > lo:
                    manip_side, manip_level = "low", lo
                    manip_candle = c
                    break
    if not manip_side:
        return None

    direction = "SELL" if manip_side == "high" else "BUY"
    last = candles[-1]
    close = last["close"]
    bias = structure_labels(ctx.bias_candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL")

    a = atr(candles)
    if a <= 0:
        return None
    if direction == "BUY":
        if close < manip_level:
            return None
        raw_entry = close if close < hi else (hi - 0.25 * a)
        entry = min(raw_entry, close)
        stop = cap_stop(entry, min(float(manip_candle["low"]), lo) - 0.5 * a, direction, a, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
        raw_tp1, raw_tp2, raw_tp3 = hi + (hi - lo) * 0.5, hi + (hi - lo), hi + (hi - lo) * 1.5
    else:
        if close > manip_level:
            return None
        raw_entry = close if close > lo else (lo + 0.25 * a)
        entry = max(raw_entry, close)
        stop = cap_stop(entry, max(float(manip_candle["high"]), hi) + 0.5 * a, direction, a, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
        raw_tp1, raw_tp2, raw_tp3 = lo - (hi - lo) * 0.5, lo - (hi - lo), lo - (hi - lo) * 1.5
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)

    earned = 0.0
    max_pts = 84.0
    checks = {}
    checks["range"] = (hi - lo) > 0
    checks["asian_range"] = asian_used
    earned += 8
    checks["manipulation"] = True
    checks["sweep"] = True
    earned += 16
    checks["close_inside"] = True
    earned += 14
    checks["htf_bias"] = bias_ok
    earned += 12 if bias_ok else 0
    expanding = (direction == "BUY" and close > (hi + lo) / 2) or \
                (direction == "SELL" and close < (hi + lo) / 2)
    checks["expansion"] = expanding
    earned += 8 if expanding else 0
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if rr >= 2.0 else 0
    fvgs = fair_value_gaps(candles[-12:], 3)
    kind = "bullish" if direction == "BUY" else "bearish"
    checks["fvg_confluence"] = any(f["kind"] == kind for f in fvgs)
    earned += 8 if checks["fvg_confluence"] else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) post-manipulation POI: an FVG/OB created BY the displacement leg
    # that followed the manipulation wick — the school's re-entry point.
    poi = None
    try:
        m_idx = next(i for i, c in enumerate(candles) if c is manip_candle)
        from fysb.engines.analysis import order_blocks as _ob, fair_value_gaps as _fvg
        disp = candles[m_idx:m_idx + 5]
        if len(disp) >= 2:
            for f in _fvg(disp, 2):
                if f["kind"] == kind:
                    poi = ("FVG", float(f["lo"]), float(f["hi"]))
                    break
            if poi is None:
                for ob in _ob(disp, 2):
                    if ob["kind"] == kind:
                        poi = ("OB", float(ob["lo"]), float(ob["hi"]))
                        break
    except Exception:
        poi = None
    checks["post_manip_poi"] = poi
    earned += 4 if poi else 0

    # 2) manipulation during the LONDON killzone — the school's preferred
    # manipulation session (scored, not mandatory).
    manip_kz = kill_zone(manip_candle["datetime"]) or ""
    london_manip = manip_kz == "LONDON"
    checks["london_manip"] = london_manip
    earned += 3 if london_manip else 0

    # 3) D-distribution full-range equation: raw TPs are 0.5×/1.0×/1.5× of the
    # accumulation range projected from the opposite boundary — the classic
    # Power-of-Three measured distribution. Named + disclosed as such.
    range_height = hi - lo
    checks["d_distribution_eq"] = f"0.5x/1.0x/1.5x of {range_height:.5g}"

    score = normalized_score(earned, max_pts)
    if score < 58:
        return None

    reasons = [
        f"{'Asian ' if asian_used else ''}accumulation range {lo:.5g}-{hi:.5g}",
        f"manipulation swept {manip_side} @ {manip_level:.5g} and closed back inside",
        f"distribution direction={direction} (opposite of the fake move)",
        f"HTF bias={bias['trend']}",
        f"invalidation beyond {stop:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    if poi:
        reasons.append(f"post-manipulation POI: {poi[0]} {poi[1]:.5g}-{poi[2]:.5g}")
    if london_manip:
        reasons.append("manipulation during the London killzone (preferred session)")
    reasons.append(f"D-distribution equation: targets 0.5×/1.0×/1.5× of range {range_height:.5g}")
    return StrategyResult(
        code="AMD", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
