"""CRT — latest bar sweeps one of the four preceding reference candle ranges.

Reference width >=0.55 ATR; reject two-sided sweeps; close must be strictly
inside its range and candle body must point opposite the swept side. Require
HTF structure agreement OR local directional CHOCH. Entry: reachable midpoint,
otherwise close. Stop: swept extreme +/-0.85 ATR; targets range-derived with
2R/3R/4.5R floors (not necessarily the opposite boundary after adjustment).
Weights: range4, sweep12, reclaim10, bias12, displacement10, FVG10,
midpoint6, kill-zone6, volatility4, RR6 (total80).
Minimum score64; minimum45 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, fair_value_gaps, kill_zone, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 45:
        return None

    # 1+2+3) True chronological CRT: the LATEST closed candle (`last = candles[-1]`)
    # MUST sweep beyond a PRIOR reference range candle (`ref` in candles[-5:-1])
    # and CLOSE back inside `ref`'s range (v6.20.6 fixes old backwards lookback
    # where older bars were compared against a newer reference bar).
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = last["close"]

    ref = None
    sweep_side, sweep_level = "", 0.0
    for cand_ref in reversed(candles[-5:-1]):
        rh, rl = float(cand_ref["high"]), float(cand_ref["low"])
        if float(last["high"]) > rh and float(last["low"]) < rl:
            continue  # ambiguous two-sided sweep is not a directional trigger
        if (rh - rl) < 0.55 * a:
            continue  # ignore tiny doji micro-ranges
        if float(last["high"]) > rh and close < rh and close > rl:
            ref = cand_ref
            sweep_side, sweep_level = "high", float(last["high"])
            break
        if float(last["low"]) < rl and close > rl and close < rh:
            ref = cand_ref
            sweep_side, sweep_level = "low", float(last["low"])
            break
    if ref is None:
        return None

    r_hi, r_lo = float(ref["high"]), float(ref["low"])
    mid = (r_hi + r_lo) / 2.0
    confirmed = True

    direction = "SELL" if sweep_side == "high" else "BUY"
    bias = structure_labels(ctx.bias_candles)
    mss = structure_labels(candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL") or \
              (mss["choch"] and mss["bos_dir"] == ("up" if direction == "BUY" else "down"))

    # Require confirmation candle to close with momentum in the trade direction
    # (bullish close after low sweep, bearish close after high sweep) — eliminates
    # weak false CRT sweeps in choppy Gold ranges.
    if direction == "BUY" and close <= float(last["open"]):
        return None
    if direction == "SELL" and close >= float(last["open"]):
        return None

    # 4) Entry: CRT mid (50% of range) — v6.19.0: reject invalidated range
    # breakouts and never place a BUY limit above close or SELL limit below close.
    if direction == "BUY":
        if close < r_lo:
            return None
        entry = mid if close >= mid else close
    else:
        if close > r_hi:
            return None
        entry = mid if close <= mid else close

    # 5) Invalidation = swept extreme + realistic ATR buffer (0.85 ATR survives Gold wicks)
    if direction == "BUY":
        stop = min(sweep_level, r_lo) - 0.85 * a
    else:
        stop = max(sweep_level, r_hi) + 0.85 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.15, fallback_atr=1.6, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # 6) Targets: opposite side first, floored strictly >= 2.0R / 3.0R / 4.5R
    ext = (r_hi - r_lo)
    raw_tp1 = max(r_hi, entry + risk * 2.0) if direction == "BUY" else min(r_lo, entry - risk * 2.0)
    raw_tp2 = (entry + ext * 1.0 + risk) if direction == "BUY" else (entry - ext * 1.0 - risk)
    raw_tp3 = (entry + ext * 2.0 + risk * 1.5) if direction == "BUY" else (entry - ext * 2.0 - risk * 1.5)
    tp1, tp2, tp3, rr = ensure_min_tp_rr(entry, stop, raw_tp1, raw_tp2, raw_tp3, direction)

    # --- Normalized Fair Score (v8.0.0) ------------------------------------
    earned = 0.0
    max_pts = 80.0
    checks = {}
    checks["range"] = (r_hi - r_lo) > 0
    earned += 4
    checks["sweep"] = True
    earned += 12
    checks["confirm_close"] = confirmed
    earned += 10
    checks["htf_bias"] = bias_ok
    earned += 12 if bias_ok else 0
    disp = abs(close - ref["close"]) > a
    checks["displacement"] = disp
    earned += 10 if disp else 0
    fvgs = fair_value_gaps(candles[-12:], 3)
    kind = "bullish" if direction == "BUY" else "bearish"
    fvg_in = any(f["kind"] == kind and f["lo"] >= r_lo - 0.1 * a and f["hi"] <= r_hi + 0.1 * a for f in fvgs)
    checks["fvg_inside"] = fvg_in
    earned += 10 if fvg_in else 0
    at_mid = abs(entry - mid) <= 0.25 * a
    checks["crt_mid"] = at_mid
    earned += 6 if at_mid else 0
    kz = ctx.kill_zone or (kill_zone(candles[-1]["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if rr >= 2.0 else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) higher reference: the sweep reaching the PRIOR UTC DAY's extreme is
    # the school's higher-timeframe CRT. Coverage-honest: the day ref only
    # counts when the series actually holds enough bars of that day.
    day_extreme = None
    try:
        from fysb.engines.analysis import session_levels as _sl
        sl = _sl(candles)
        if sl.get("pdh") is not None and sl.get("pdl") is not None:
            if direction == "BUY" and sweep_side == "low" and float(last["low"]) <= float(sl["pdl"]):
                day_extreme = ("PDL", float(sl["pdl"]))
            elif direction == "SELL" and sweep_side == "high" and float(last["high"]) >= float(sl["pdh"]):
                day_extreme = ("PDH", float(sl["pdh"]))
    except Exception:
        day_extreme = None
    checks["day_ref_swept"] = day_extreme
    earned += 5 if day_extreme else 0

    # 2) confirmation ladder: additional closed candles beyond the sweep bar
    # continuing in the reversal direction (ladder depth 0..2, disclosed).
    ladder = 0
    for c in candles[-3:-1]:
        if direction == "BUY" and float(c["close"]) > float(c["open"]):
            ladder += 1
        elif direction == "SELL" and float(c["close"]) < float(c["open"]):
            ladder += 1
    ladder = min(ladder, 2)
    checks["confirm_ladder"] = ladder
    earned += ladder * 3

    # 3) v8.13.3 — weekly reference (the final declared gap): the sweep reaching
    # a WEEKLY extreme is the school's highest CRT. Coverage-honest: the prior
    # completed week counts only when the series truly samples it (>=12 bars);
    # the current running week only when it spans >=20h — else honest None.
    week_ref = None
    try:
        from fysb.engines.analysis import weekly_reference as _wr
        wr = _wr(candles)
        if wr:
            checks["week_ref_span"] = (wr.get("cur_bars"), wr.get("cur_span_hours"),
                                       wr.get("prev_bars"))
            if wr.get("prev_high") is not None and wr.get("prev_bars", 0) >= 12:
                if direction == "SELL" and sweep_side == "high" and float(last["high"]) >= float(wr["prev_high"]):
                    week_ref = ("PWH", float(wr["prev_high"]))
                elif direction == "BUY" and sweep_side == "low" and float(last["low"]) <= float(wr["prev_low"]):
                    week_ref = ("PWL", float(wr["prev_low"]))
            if week_ref is None and wr.get("cur_span_hours", 0.0) >= 20.0:
                if direction == "SELL" and sweep_side == "high" and float(last["high"]) >= float(wr["cur_high"]):
                    week_ref = ("week-high", float(wr["cur_high"]))
                elif direction == "BUY" and sweep_side == "low" and float(last["low"]) <= float(wr["cur_low"]):
                    week_ref = ("week-low", float(wr["cur_low"]))
    except Exception:
        week_ref = None
    checks["week_ref_swept"] = week_ref
    earned += 5 if week_ref else 0

    if not bias_ok:
        return None
    score = normalized_score(earned, max_pts)
    if score < 64:
        diagnosis_record("CRT", checks, earned, max_pts)
        return None

    reasons = [
        f"CRT range {r_lo:.5g}-{r_hi:.5g} (ref candle)",
        f"swept {sweep_side} @ {sweep_level:.5g} then closed back inside",
        f"HTF bias={bias['trend']} / MSS dir={mss['bos_dir'] or '—'}",
        f"entry at CRT 50% = {mid:.5g}" if at_mid else "entry at market (mid not reachable)",
        f"invalidation beyond {stop:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    if day_extreme:
        reasons.append(f"sweep reached the prior-day extreme {day_extreme[0]} {day_extreme[1]:.5g} (higher CRT)")
    if ladder:
        reasons.append(f"confirmation ladder: {ladder} extra closed candle(s) in the reversal direction")
    if week_ref:
        reasons.append(f"sweep reached the weekly reference {week_ref[0]} {week_ref[1]:.5g} (weekly CRT)")
    return StrategyResult(
        code="CRT", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
