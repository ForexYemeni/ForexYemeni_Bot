"""ICHIMOKU — Ichimoku Kinko Hyo (classic Japanese system, 100% mechanical).

Documented rules (no invented components):
  * Tenkan-sen  = (highest high + lowest low) / 2 over 9 periods
  * Kijun-sen   = same over 26 periods
  * Senkou A/B  = (Tenkan+Kijun)/2 and (HH+LL)/2 over 52 — computed 26 periods
                  ago so they describe the CLOUD the current candle trades in
  * Chikou span = the close plotted 26 periods back — above (BUY) the price of
                  that moment

Entry rules (BUY; mirrored for SELL):
  1. CLOUD    : close is ABOVE the Kumo (both Senkou A and B) — cloud breakout
  2. TK CROSS : Tenkan > Kijun (or a fresh cross within the last 5 candles)
  3. FUTURE   : the forward cloud (computed NOW, plotted 26 ahead) is bullish
                (SenkouA_now > SenkouB_now) — the trend has continuation room
  4. CHIKOU   : the close 26 periods ago is above the close 52 periods ago —
                the lagging line confirms the momentum regime
  5. HTF BIAS : bias-TF close sits above its own Kijun — trend-with-trend
  ENTRY = last close · STOP = Kijun ± 0.5 ATR (the classic invalidation)
  TARGETS = 2R / 3R / 5R measured ladder (platform floor 2R).

Score weights (deterministic): cloud position +18 · TK alignment +14 ·
fresh cross bonus +6 · future cloud color +12 · chikou confirm +10 ·
HTF bias +12 · kill zone +6 · volatility +4 · RR +8. Base 40. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone
from fysb.engines.strategies.base import Ctx, StrategyResult, cap_stop, clamp_score

TENKAN_P, KIJUN_P, SENKOU_B_P, DISPLACEMENT = 9, 26, 52, 26


def _mid(candles: list[dict], period: int) -> float:
    win = candles[-period:]
    return (max(float(c["high"]) for c in win) + min(float(c["low"]) for c in win)) / 2.0


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < SENKOU_B_P + DISPLACEMENT + 5:
        return None
    a = atr(candles)
    if a <= 0:
        return None
    close = float(candles[-1]["close"])

    tenkan = _mid(candles, TENKAN_P)
    kijun = _mid(candles, KIJUN_P)
    # the cloud the CURRENT candle trades in = values displaced 26 bars back
    past = candles[:-(DISPLACEMENT)]
    if len(past) < SENKOU_B_P:
        return None
    senkou_a = (_mid(past, TENKAN_P) + _mid(past, KIJUN_P)) / 2.0
    senkou_b = _mid(past, SENKOU_B_P)
    cloud_top, cloud_bot = max(senkou_a, senkou_b), min(senkou_a, senkou_b)
    # forward cloud (computed now, plots 26 ahead)
    fut_a = (tenkan + kijun) / 2.0
    fut_b = _mid(candles, SENKOU_B_P)

    checks: dict = {}
    score = 32.0

    # 1) cloud position decides the direction (exactly one side)
    if close > cloud_top:
        direction = "BUY"
    elif close < cloud_bot:
        direction = "SELL"
    else:
        return None
    checks["cloud"] = True
    score += 14

    tk_aligned = (tenkan > kijun) if direction == "BUY" else (tenkan < kijun)
    checks["tk"] = tk_aligned
    if not tk_aligned:
        return None
    score += 10
    # fresh cross: tenkan/kijun order flipped within the last 5 closes
    fresh = False
    for i in range(len(candles) - 5, len(candles)):
        win = candles[:i + 1]
        t, k = _mid(win, TENKAN_P), _mid(win, KIJUN_P)
        if (direction == "BUY" and t <= k) or (direction == "SELL" and t >= k):
            fresh = True
            break
    checks["fresh_cross"] = fresh
    score += 8 if fresh else 0

    fut_bullish = fut_a > fut_b
    checks["future_cloud"] = (direction == "BUY" and fut_bullish) or (direction == "SELL" and not fut_bullish)
    if not checks["future_cloud"]:
        return None
    score += 8

    # v6.20.0 FIX: True Chikou Span compares CURRENT close (plotted 26 bars back)
    # against the price 26 bars ago — NOT 26-ago vs 52-ago!
    ref_26 = float(candles[-1 - DISPLACEMENT]["close"])
    chikou_ok = (close > ref_26) if direction == "BUY" else (close < ref_26)
    checks["chikou"] = chikou_ok
    if not chikou_ok:
        return None
    score += 8

    bias_close = float(ctx.bias_candles[-1]["close"]) if ctx.bias_candles else close
    bias_kijun = _mid(ctx.bias_candles, KIJUN_P) if len(ctx.bias_candles) >= KIJUN_P else bias_close
    bias_ok = (bias_close > bias_kijun) if direction == "BUY" else (bias_close < bias_kijun)
    checks["htf_bias"] = bias_ok
    if not bias_ok:
        return None
    score += 8

    last = candles[-1]
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    score += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0

    entry = close
    stop = (kijun - 0.5 * a) if direction == "BUY" else (kijun + 0.5 * a)
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp1 = entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0
    tp2 = entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0
    tp3 = entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0
    rr = 2.0
    checks["rr_ok"] = True
    score += 5

    if score < 55:
        return None

    reasons = [
        f"close {close:.5g} outside the Kumo ({cloud_bot:.5g}–{cloud_top:.5g}) — {direction} side",
        f"Tenkan {tenkan:.5g} {'>' if direction == 'BUY' else '<'} Kijun {kijun:.5g}"
        + (" (fresh cross)" if fresh else ""),
        f"forward cloud {'bullish' if fut_bullish else 'bearish'} — continuation room",
        f"chikou span confirms the regime · HTF bias ok",
        f"stop beyond Kijun = {stop:.5g}",
        f"targets 2R/3R/5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="ICHIMOKU", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
