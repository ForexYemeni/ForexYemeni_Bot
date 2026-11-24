"""Silver Bullet (ICT) — time-window FVG continuation, fully mechanical.

Documented rules:
  1. TIME WINDOW — Silver Bullet windows (documented UTC approximation):
       LONDON_SB    07:00-08:00 UTC
       NY_AM_SB     15:00-16:00 UTC
       NY_PM_SB     19:00-20:00 UTC
  2. DAY BIAS — the opening-range direction (first 8 candles of the covered
     window) must agree with the trade direction.
  3. SETUP — a Fair Value Gap forms inside the window in the bias direction.
  4. ENTRY — midpoint of the newest in-window FVG; stop beyond the window's
     opposite extreme; targets are risk multiples.

Score weights (deterministic):
  inside a silver-bullet window .. +16
  opening-range bias defined ..... +10
  FVG formed inside window ....... +16
  FVG direction == bias .......... +14
  HTF structure alignment ........ +10
  volatility regime ok ........... +6
  RR >= 2 ........................ +8
Base 40. Cap 95. Outside the windows the strategy simply returns None.
"""
from fysb.engines.analysis import atr_pct, ema, fair_value_gaps, structure_labels
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score
from datetime import datetime

SB_WINDOWS = [("LONDON_SB", 7, 8), ("NY_AM_SB", 15, 16), ("NY_PM_SB", 19, 20)]


def _window_of(dt_iso: str):
    try:
        dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    for name, start, end in SB_WINDOWS:
        if start <= dt.hour < end:
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

    # Opening-range bias (first 8 of the last 30 candles)
    window30 = candles[-30:]
    acc = window30[:8]
    or_hi = max(c["high"] for c in acc)
    or_lo = min(c["low"] for c in acc)
    close = last["close"]
    bias_dir = "BUY" if close > (or_hi + or_lo) / 2 else "SELL"

    # FVGs formed inside this window (recent 10 candles) — INTACT only (v6.19.0):
    # a bullish FVG with close already below fvg['lo'] (or bearish above hi) is
    # invalidated and must never produce a born-in-drawdown signal.
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

    if direction == "BUY":
        stop = min(min(c["low"] for c in candles[-8:]), fvg["lo"]) - 0.4 * (or_hi - or_lo) * 0.5
        stop = min(stop, entry - 0.8 * a_ptr * entry / 100)
        risk = entry - stop
        tp1, tp2, tp3 = entry + risk * 2.0, entry + risk * 3.0, entry + risk * 5.0
    else:
        stop = max(max(c["high"] for c in candles[-8:]), fvg["hi"]) + 0.4 * (or_hi - or_lo) * 0.5
        stop = max(stop, entry + 0.8 * a_ptr * entry / 100)
        risk = stop - entry
        tp1, tp2, tp3 = entry - risk * 2.0, entry - risk * 3.0, entry - risk * 5.0
    if risk <= 0:
        return None
    rr = abs(tp1 - entry) / risk

    score = 40.0
    checks = {}
    checks["sb_window"] = wname
    score += 16
    checks["or_bias"] = or_hi > or_lo
    score += 10
    checks["fvg_in_window"] = True
    score += 16
    checks["fvg_direction"] = True
    score += 14
    checks["htf_align"] = (bias["trend"] == "up" and direction == "BUY") or \
                          (bias["trend"] == "down" and direction == "SELL") or \
                          (struct["bos_dir"] == ("up" if direction == "BUY" else "down"))
    score += 10 if checks["htf_align"] else 0
    checks["vol_ok"] = 0.05 < a_ptr < 6.0
    score += 6 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 8 if rr >= 2.0 else 0

    if score < 55:
        return None

    reasons = [
        f"silver bullet window {wname}",
        f"opening-range bias {bias_dir} ({or_lo:.5g}-{or_hi:.5g})",
        f"in-window {fvg['kind']} FVG {fvg['lo']:.5g}-{fvg['hi']:.5g}",
        f"entry at FVG midpoint {entry:.5g}",
        f"invalidation beyond {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SILVER_BULLET", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
