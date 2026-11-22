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
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 45:
        return None

    # 1) Reference range = previous candle (the CRT candle)
    ref = candles[-2]
    r_hi, r_lo = ref["high"], ref["low"]
    if r_hi <= r_lo:
        return None
    mid = (r_hi + r_lo) / 2
    last = candles[-1]
    close = last["close"]

    # 2+3) Sweep & close-back-inside within the last few candles
    sweep_side, sweep_level = "", 0.0
    confirmed = False
    for c in candles[-6:-1]:
        if c["high"] > r_hi and c["close"] < r_hi:
            sweep_side, sweep_level = "high", r_hi
            confirmed = True
            break
        if c["low"] < r_lo and c["close"] > r_lo:
            sweep_side, sweep_level = "low", r_lo
            confirmed = True
            break
    if not confirmed:
        return None

    direction = "SELL" if sweep_side == "high" else "BUY"
    bias = structure_labels(ctx.bias_candles)
    mss = structure_labels(candles)
    bias_ok = (bias["trend"] == "up" and direction == "BUY") or \
              (bias["trend"] == "down" and direction == "SELL") or \
              (mss["choch"] and mss["bos_dir"] == ("up" if direction == "BUY" else "down"))

    # 4) Entry: CRT mid (50% of range) — only accept when price can still reach it
    a = atr(candles)
    if a <= 0:
        return None
    if direction == "BUY":
        entry = mid if close >= mid - 0.25 * a else close
    else:
        entry = mid if close <= mid + 0.25 * a else close

    # 5) Invalidation = swept extreme + buffer
    if direction == "BUY":
        stop = min(sweep_level, r_lo) - 0.5 * a if sweep_side == "low" else r_lo - 0.5 * a
        stop = min(stop, entry - 0.8 * a)
    else:
        stop = max(sweep_level, r_hi) + 0.5 * a if sweep_side == "high" else r_hi + 0.5 * a
        stop = max(stop, entry + 0.8 * a)
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

    # --- Deterministic score ---
    score = 40.0
    checks = {}
    checks["range"] = (r_hi - r_lo) > 0
    score += 6
    checks["sweep"] = True
    score += 16
    checks["confirm_close"] = confirmed
    score += 16
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
    score += 8 if at_mid else 0
    kz = ctx.kill_zone or (kill_zone(candles[-1]["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 8 if rr >= 2.0 else 0

    if score < 55:
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
