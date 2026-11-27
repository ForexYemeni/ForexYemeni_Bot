"""CLASSIC — EMA50 trend + nearby horizontal level + recent candle pattern.

Require price beyond EMA50 by0.1 ATR, agreeing EMA50 slope over six bars,
S/R level within2 ATR and the correct side tolerance0.2 ATR, plus an engulfing/
pin pattern in the latest THREE bars. This does not prove a historical
resistance-to-support flip. HTF is a bonus. Entry: close.
Stop: level/local-six-bar extreme +/-0.5 ATR; targets2R/3R/4.5R.
Weights: trend14, slope8, level12, side8, pattern12, HTF12,
kill-zone6, volatility4, RR5 (total81). Minimum score58; minimum55 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, candle_patterns, ema, kill_zone, psych_level, role_flip, structure_labels, support_resistance
from fysb.engines.strategies.base import (
    diagnosis_record, diagnosis_refuse,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

PROXIMITY_ATR = 2.0


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 55:
        diagnosis_refuse("CLASSIC", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
        return None
    a = atr(candles)
    if a <= 0:
        diagnosis_refuse("CLASSIC", "التقلب غير قابل للقياس الآن")
        return None
    last = candles[-1]
    close = float(last["close"])
    checks: dict = {}
    earned = 0.0
    max_pts = 81.0

    e50_now = ema([float(c["close"]) for c in candles], 50)
    e50_prev = ema([float(c["close"]) for c in candles[:-6]], 50)

    # 1) trend side — exactly one direction qualifies
    direction = ""
    if close > e50_now + 0.10 * a:
        direction = "BUY"
    elif close < e50_now - 0.10 * a:
        direction = "SELL"
    checks["trend_side"] = bool(direction)
    if not direction:
        diagnosis_refuse("CLASSIC", "لا اتجاه مؤسسي واضح للنموذج")
        return None
    earned += 14

    # 2) EMA50 slope agrees with the direction
    slope_ok = (e50_now > e50_prev) if direction == "BUY" else (e50_now < e50_prev)
    checks["ema50_slope"] = slope_ok
    if not slope_ok:
        diagnosis_refuse("CLASSIC", "ميل EMA50 عكس الاتجاه المطلوب")
        return None
    earned += 8

    # 3) real horizontal level retested from the correct side
    sr = support_resistance(candles)
    level = None
    if direction == "BUY" and sr.get("support") is not None:
        lv_f = float(sr["support"])
        if abs(close - lv_f) <= PROXIMITY_ATR * a:
            level = lv_f
    elif direction == "SELL" and sr.get("resistance") is not None:
        lv_f = float(sr["resistance"])
        if abs(close - lv_f) <= PROXIMITY_ATR * a:
            level = lv_f
    checks["level_retest"] = level is not None
    if level is None:
        diagnosis_refuse("CLASSIC", "لا مستوى مرجعي مُختبر للنموذج")
        return None
    earned += 12
    side_ok = (close >= level - 0.2 * a) if direction == "BUY" else (close <= level + 0.2 * a)
    checks["correct_side"] = side_ok
    if not side_ok:
        diagnosis_refuse("CLASSIC", "السعر في الجهة المعاكسة للمستوى")
        return None
    earned += 8

    # 4) candlestick confirmation in the trade direction
    patterns = candle_patterns(candles, limit=3)
    bull_pats = ("ENGULFING_BULL", "PIN_BAR_BULL")
    bear_pats = ("ENGULFING_BEAR", "PIN_BAR_BEAR")
    confirm = any(p in (bull_pats if direction == "BUY" else bear_pats) for p in patterns)
    checks["candle_confirm"] = confirm
    if not confirm:
        diagnosis_refuse("CLASSIC", "لا تأكيد إغلاق نمطي للنموذج")
        return None
    earned += 12

    # v8.0.0: Real HTF bias awareness from ctx.bias_candles
    bias = structure_labels(ctx.bias_candles)
    htf_ok = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    checks["htf_bias"] = htf_ok
    earned += 12 if htf_ok else 0

    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0

    recent = candles[-6:]
    if direction == "BUY":
        stop = min(level, min(float(c["low"]) for c in recent)) - 0.5 * a
    else:
        stop = max(level, max(float(c["high"]) for c in recent)) + 0.5 * a
    entry = close
    stop = cap_stop(entry, stop, direction, a, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        diagnosis_refuse("CLASSIC", "حساب الوقف غير سليم (مخاطرة صفرية)")
        return None

    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5,
        direction,
    )
    checks["rr_ok"] = rr >= 2.0
    earned += 5 if checks["rr_ok"] else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) historical role-flip proof: the level provably flipped sides in the
    # candle history (old resistance broken then retested as support, mirror
    # for supply). Pure arithmetic — no "it looks like it held".
    flipped = role_flip(candles, level, a, direction) if level else False
    checks["role_flip_proof"] = flipped
    earned += 6 if flipped else 0

    # 2) psychological big-figure proximity as an independent location layer.
    psy = psych_level(close, a)
    psych = psy is not None and abs(close - psy) <= 0.25 * a
    checks["psych_level"] = psy if psych else None
    earned += 3 if psych else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("CLASSIC", checks, earned, max_pts)
        return None

    reasons = [
        f"price {close:.5g} trades on the {'bull' if direction == 'BUY' else 'bear'} side of EMA50 ({e50_now:.5g}, sloping)",
        f"horizontal level retest at {level:.5g} (within {PROXIMITY_ATR} ATR)",
        "confirmation candle pattern: " + (", ".join(patterns) or "-"),
        f"HTF bias={bias['trend']}",
        f"stop beyond level & local extreme = {stop:.5g}",
        f"targets 2R/3R/4.5R = {tp1:.5g} / {tp2:.5g} / {tp3:.5g}",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    if flipped:
        reasons.append("role-flip PROVEN in history: the level previously traded/broke on the other side")
    if psych:
        reasons.append(f"psychological big figure {psy:.5g} at the location")
    return StrategyResult(
        code="CLASSIC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=rr,
        score=score, reasons=reasons, checks=checks,
    )
