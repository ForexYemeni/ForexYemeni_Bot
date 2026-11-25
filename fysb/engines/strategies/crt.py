"""CRT — Candle Range Theory (real, rule-based, 100% mechanical).

Documented rules (no indicators, pure range logic):
  1. RANGE     : the reference candle (previous completed candle) defines the
                 CRT range: high = sweep liquidity above, low = liquidity below.
  2. SWEEP     : a later candle TAKES one side of the range (wick beyond it) —
                 that is the liquidity grab (BSL/SSL).
  3. CONFIRM   : the sweeping move CLOSES back inside the range — the failed
                 break is the CRT trigger.
  4. ENTRY     : retrace toward the range midpoint (CRT 50%) or the FVG left by
                 the confirming displacement, in the direction opposite the
                 swept side, aligned with the higher-timeframe bias.
  5. INVALIDATION: beyond the swept extreme (plus ATR buffer) — that is where
                 the CRT narrative is objectively wrong.
  6. TARGETS   : opposite side of the range first, then measured extensions.

Score weights (deterministic, auditable):
  range defined ............ +6
  sweep beyond range ....... +16
  close back inside ........ +16
  HTF bias alignment ....... +12
  displacement out of range  +10
  FVG inside range ......... +10
  entry at CRT mid zone .... +8
  kill zone ................ +6
  volatility regime ok ..... +4
  RR(TP1) >= 2 ............. +8
Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, fair_value_gaps, kill_zone, structure_labels
from fysb.engines.strategies.base import Ctx, StrategyResult, cap_stop, clamp_score


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

    # 6) Targets: opposite side first, then measured extensions
    ext = (r_hi - r_lo)
    if direction == "BUY":
        tp1 = max(r_hi, entry + risk * 2.0)
        tp2, tp3 = entry + ext * 1.0 + risk, entry + ext * 2.0 + risk * 1.5
    else:
        tp1 = min(r_lo, entry - risk * 2.0)
        tp2, tp3 = entry - ext * 1.0 - risk, entry - ext * 2.0 - risk * 1.5
    rr = abs(tp1 - entry) / risk

    # --- Deterministic score (v6.20.6 calibrated: trigger alone = 62, must earn
    # real institutional confluence — HTF bias / displacement / FVG — to pass 72) ---
    score = 36.0
    checks = {}
    checks["range"] = (r_hi - r_lo) > 0
    score += 4
    checks["sweep"] = True
    score += 12
    checks["confirm_close"] = confirmed
    score += 10
    checks["htf_bias"] = bias_ok
    score += 12 if bias_ok else 0
    disp = abs(close - ref["close"]) > a
    checks["displacement"] = disp
    score += 10 if disp else 0
    fvgs = fair_value_gaps(candles[-12:], 3)
    kind = "bullish" if direction == "BUY" else "bearish"
    fvg_in = any(f["kind"] == kind and f["lo"] >= r_lo - 0.1 * a and f["hi"] <= r_hi + 0.1 * a for f in fvgs)
    checks["fvg_inside"] = fvg_in
    score += 10 if fvg_in else 0
    at_mid = abs(entry - mid) <= 0.25 * a
    checks["crt_mid"] = at_mid
    score += 6 if at_mid else 0
    kz = ctx.kill_zone or (kill_zone(candles[-1]["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 6 if rr >= 2.0 else 0

    if not bias_ok:
        return None
    if score < 68:
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
    return StrategyResult(
        code="CRT", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
