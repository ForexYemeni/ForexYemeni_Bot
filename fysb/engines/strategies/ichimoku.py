"""ICHIMOKU — full mechanical Ichimoku continuation set (extended v8.12.0).

Tenkan9/Kijun26; current cloud from calculations26 bars back with SpanB52.
Require close outside cloud, TK alignment, matching forward cloud, current close
beyond close26 bars ago (Chikou proxy), and bias close beyond its Kijun.
A fresh TK cross is a bonus, NOT mandatory. Entry: close.
Stop: Kijun +/-0.5 ATR, widened if tight (not the cloud boundary).
Targets2R/3R/5R. Weights: cloud14, TK10, fresh-cross8, forward-cloud8,
Chikou8, bias8, kill-zone6, volatility4, RR5 (base71) PLUS the v8.12.0
full-school quality factors: Kijun-bounce/TK-re-test entry quality10,
Kumo-twist freshness8 (twist within the last 10 bars), Chikou open-space8
(close clears the whole lag-window structure, not one reference close),
Kijun proximity6 (continuation not over-extended). Total max103.
Minimum score58; minimum83 bars.

Ichimoku "time elements" (Ochi numerical counts) are intentionally NOT
implemented — they are numerology without mechanical edge evidence and the
engine honestly declares their absence instead of faking them.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, kill_zone
from fysb.engines.strategies.base import (
    diagnosis_record, diagnosis_refuse,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

TENKAN_P, KIJUN_P, SENKOU_B_P, DISPLACEMENT = 9, 26, 52, 26


def _mid(candles: list[dict], period: int) -> float:
    win = candles[-period:]
    return (max(float(c["high"]) for c in win) + min(float(c["low"]) for c in win)) / 2.0


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < SENKOU_B_P + DISPLACEMENT + 5:
        diagnosis_refuse("ICHIMOKU", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
        return None
    a = atr(candles)
    if a <= 0:
        diagnosis_refuse("ICHIMOKU", "التقلب غير قابل للقياس الآن")
        return None
    close = float(candles[-1]["close"])

    tenkan = _mid(candles, TENKAN_P)
    kijun = _mid(candles, KIJUN_P)
    # the cloud the CURRENT candle trades in = values displaced 26 bars back
    past = candles[:-(DISPLACEMENT)]
    if len(past) < SENKOU_B_P:
        diagnosis_refuse("ICHIMOKU", "تاريخ الشموع المزاح أقصر من حد السحابة المؤسسية")
        return None
    senkou_a = (_mid(past, TENKAN_P) + _mid(past, KIJUN_P)) / 2.0
    senkou_b = _mid(past, SENKOU_B_P)
    cloud_top, cloud_bot = max(senkou_a, senkou_b), min(senkou_a, senkou_b)
    # forward cloud (computed now, plots 26 ahead)
    fut_a = (tenkan + kijun) / 2.0
    fut_b = _mid(candles, SENKOU_B_P)

    checks: dict = {}
    earned = 0.0
    max_pts = 71.0

    if close > cloud_top:
        direction = "BUY"
    elif close < cloud_bot:
        direction = "SELL"
    else:
        diagnosis_refuse("ICHIMOKU", "السعر داخل السحابة (منطقة لا قرار)")
        return None
    checks["cloud"] = True
    earned += 14

    tk_aligned = (tenkan > kijun) if direction == "BUY" else (tenkan < kijun)
    checks["tk"] = tk_aligned
    if not tk_aligned:
        diagnosis_refuse("ICHIMOKU", "تقاطع Tenkan/Kijun عكس الاتجاه المطلوب")
        return None
    earned += 10
    fresh = False
    for i in range(len(candles) - 5, len(candles)):
        win = candles[:i + 1]
        t, k = _mid(win, TENKAN_P), _mid(win, KIJUN_P)
        if (direction == "BUY" and t <= k) or (direction == "SELL" and t >= k):
            fresh = True
            break
    checks["fresh_cross"] = fresh
    earned += 8 if fresh else 0

    # v8.12.0 — Kijun-bounce / TK re-test: price pulled back into the
    # Tenkan–Kijun corridor within the last 3 closed bars and closed back on
    # the signal side — the classic Ichimoku continuation entry quality.
    corridor_hi, corridor_lo = max(tenkan, kijun), min(tenkan, kijun)
    bounced = False
    for c in candles[-4:-1]:
        if direction == "BUY" and float(c["low"]) <= corridor_hi + 0.05 * a and float(c["close"]) > corridor_hi:
            bounced = True
        if direction == "SELL" and float(c["high"]) >= corridor_lo - 0.05 * a and float(c["close"]) < corridor_lo:
            bounced = True
    checks["kijun_bounce"] = bounced
    earned += 10 if bounced else 0

    # v8.12.0 — Kumo twist FRESHNESS: the forward-cloud regime flipped within
    # the last 10 bars (an aged twist carries less continuation room).
    twist_age = None
    prev_bull = None
    for i in range(max(SENKOU_B_P, len(candles) - 60), len(candles)):
        win = candles[:i + 1]
        fa = (_mid(win, TENKAN_P) + _mid(win, KIJUN_P)) / 2.0
        fb = _mid(win, SENKOU_B_P)
        bull = fa > fb
        if prev_bull is not None and bull != prev_bull:
            twist_age = len(candles) - 1 - i
        prev_bull = bull
    twist_fresh = twist_age is not None and twist_age <= 10
    checks["kumo_twist_fresh"] = twist_fresh
    earned += 8 if twist_fresh else 0

    # v8.12.0 — Chikou in OPEN SPACE: the current close, plotted 26 bars back,
    # clears the whole price structure around the lag window (not just the
    # single reference close).
    lag_win = candles[-(DISPLACEMENT + 5):-(DISPLACEMENT - 2)] if len(candles) >= DISPLACEMENT + 5 else []
    if lag_win:
        chikou_open = (all(close > float(c["high"]) for c in lag_win) if direction == "BUY"
                       else all(close < float(c["low"]) for c in lag_win))
    else:
        chikou_open = False
    checks["chikou_open_space"] = chikou_open
    earned += 8 if chikou_open else 0

    # v8.12.0 — Kijun proximity: continuation entry not over-extended from
    # its mean-reversion anchor.
    kijun_near = abs(close - kijun) <= 2.0 * a
    checks["kijun_proximity"] = kijun_near
    earned += 6 if kijun_near else 0

    fut_bullish = fut_a > fut_b
    checks["future_cloud"] = (direction == "BUY" and fut_bullish) or (direction == "SELL" and not fut_bullish)
    if not checks["future_cloud"]:
        diagnosis_refuse("ICHIMOKU", "السحابة المستقبلية عكس الاتجاه")
        return None
    earned += 8

    ref_26 = float(candles[-1 - DISPLACEMENT]["close"])
    chikou_ok = (close > ref_26) if direction == "BUY" else (close < ref_26)
    checks["chikou"] = chikou_ok
    if not chikou_ok:
        diagnosis_refuse("ICHIMOKU", "تأكيد Chikou غير متوفر")
        return None
    earned += 8

    bias_close = float(ctx.bias_candles[-1]["close"]) if ctx.bias_candles else close
    bias_kijun = _mid(ctx.bias_candles, KIJUN_P) if len(ctx.bias_candles) >= KIJUN_P else bias_close
    bias_ok = (bias_close > bias_kijun) if direction == "BUY" else (bias_close < bias_kijun)
    checks["htf_bias"] = bias_ok
    if not bias_ok:
        diagnosis_refuse("ICHIMOKU", "اتجاه الفريم الأعلى يعاكس النموذج")
        return None
    earned += 8

    last = candles[-1]
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    entry = close
    stop = (kijun - 0.5 * a) if direction == "BUY" else (kijun + 0.5 * a)
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.5, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        diagnosis_refuse("ICHIMOKU", "حساب الوقف غير سليم (مخاطرة صفرية)")
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0,
        direction,
    )
    checks["rr_ok"] = rr >= 2.0
    earned += 5 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("ICHIMOKU", checks, earned, max_pts)
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
        score=score, reasons=reasons, checks=checks,
    )
