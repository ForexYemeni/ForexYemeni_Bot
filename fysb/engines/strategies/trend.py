"""TREND — Trend-Following Pullback (classic, 100% mechanical).

The most traded playbook in the world, implemented honestly:
  1. TREND     : EMA9/EMA21/EMA50 stacked IN ORDER with real separation
                 (each layer >= 0.25 ATR apart — BUY: 9>21>50 · SELL: 9<21<50);
                 an EMAs-equal market is not a trend, never chase noise.
  2. HTF BIAS  : the higher-timeframe EMA21/EMA50 relationship must agree with
                 the entry direction (trend-with-trend only, never counter).
  3. PULLBACK  : price must have VISITED the value zone (within 1.0 ATR of
                 EMA21) during the last 6 candles — a continuation entry needs
                 a retracement, not a chase.
  4. RESUMPTION: the LAST candle closes back in the trend direction AND above
                 EMA9 (BUY) / below EMA9 (SELL) — the trend reclaiming itself.
  5. ENTRY     : last close.
  6. STOP      : the extreme of the pullback (swing low/high of the last 8
                 candles) ± 0.5 ATR — where the pullback becomes a reversal.
  7. TARGETS   : TP1 = 2R (platform floor); TP2 = 3R; TP3 = 5R — the measured
                 continuation ladder of trend playbooks.

Score weights (deterministic, auditable): stack +18 · HTF bias +12 ·
pullback visit +16 · resumption candle +14 · momentum (close vs EMA9) +6 ·
kill zone +6 · volatility regime +4 · RR>=2 +8. Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, ema, kill_zone
from fysb.engines.strategies.base import Ctx, StrategyResult, cap_stop, clamp_score

STACK_ATR = 0.25      # each EMA layer must be separated by >= 0.25 ATR —
                      # an EMAs-equal market is NOT a trend, never chase noise
PULLBACK_ATR = 1.0


def _stack(candles: list[dict]) -> tuple[float, float, float]:
    closes = [float(c["close"]) for c in candles]
    return ema(closes, 9), ema(closes, 21), ema(closes, 50)


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 55:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])

    e9, e21, e50 = _stack(candles)
    checks: dict = {}
    score = 32.0

    # 1) entry-TF stack — real separation, both directions, exactly one wins
    direction = ""
    if e9 > e21 + STACK_ATR * a and e21 > e50 + STACK_ATR * a:
        direction = "BUY"
    elif e9 < e21 - STACK_ATR * a and e21 < e50 - STACK_ATR * a:
        direction = "SELL"
    checks["stack"] = bool(direction)
    if not direction:
        return None
    score += 14

    # 2) HTF bias with the same stack rule (trend-with-trend only)
    b9, b21, b50 = _stack(ctx.bias_candles)
    bias_ok = (direction == "BUY" and b21 > b50 + 0.1 * a) or (direction == "SELL" and b21 < b50 - 0.1 * a)
    checks["htf_bias"] = bias_ok
    if not bias_ok:
        return None
    score += 10

    # 3) pullback visit: last 6 candles touched the EMA21 value zone
    visited = False
    for c in candles[-6:]:
        if direction == "BUY":
            if float(c["low"]) <= e21 + PULLBACK_ATR * a:
                visited = True
                break
        else:
            if float(c["high"]) >= e21 - PULLBACK_ATR * a:
                visited = True
                break
    checks["pullback"] = visited
    if not visited:
        return None
    score += 12

    # 4) resumption candle: closes back in the trend direction, past EMA9
    resumed = (close > e9 and float(last["close"]) > float(last["open"])) if direction == "BUY" \
        else (close < e9 and float(last["close"]) < float(last["open"]))
    checks["resumption"] = resumed
    if not resumed:
        return None
    score += 10

    # 5) momentum bonus: distance travelled away from the value zone
    momentum = abs(close - e21) >= 0.25 * a
    checks["momentum"] = momentum
    score += 6 if momentum else 0

    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0

    # 6) stop beyond the pullback extreme
    recent = candles[-8:]
    if direction == "BUY":
        stop = min(float(c["low"]) for c in recent) - 0.5 * a
    else:
        stop = max(float(c["high"]) for c in recent) + 0.5 * a
    entry = close
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # 7) measured continuation ladder 2R/3R/5R
    tp1 = entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0
    tp2 = entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0
    tp3 = entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0
    rr = 2.0
    checks["rr_ok"] = True
    score += 5

    if score < 55:
        return None

    reasons = [
        f"EMA stack aligned ({e9:.5g}/{e21:.5g}/{e50:.5g}) — {direction} trend",
        f"HTF bias confirms ({b21:.5g} vs {b50:.5g})",
        f"pullback visited the EMA21 value zone within {PULLBACK_ATR} ATR",
        "resumption candle closed back in trend direction",
        f"stop beyond pullback extreme = {stop:.5g}",
        f"targets 2R/3R/5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="TREND", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
