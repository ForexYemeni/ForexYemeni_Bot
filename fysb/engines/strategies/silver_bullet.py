"""SILVER_BULLET — New York date/window-bound FVG continuation.

Windows (America/New_York, DST-aware): 03..04, 10..11, 14..15.
Require >=3 supplied prior bars in the two hours BEFORE this date's window start.
Bias: last close relative to that opening range midpoint. Require a matching FVG
among the latest ten bars whose THIRD candle formed in the SAME window/date.
Reject gaps with later closes through their invalidation edge; this is not an
untouched-zone/first-mitigation model. Entry: midpoint clipped to current close.
Stop beyond recent eight-bar extreme/FVG plus0.2 opening-range width;
never tighten a valid structural stop. Targets2R/3R/5R.
Weights: window16, opening-range10, in-window-gap16, direction14,
HTF10, volatility6, RR8 (total80). Minimum score58; minimum45 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fysb.engines.analysis import atr_pct, fair_value_gaps, ny_hour, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record, diagnosis_refuse,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

_NY = ZoneInfo("America/New_York")
SB_WINDOWS = [("LONDON_SB", 3, 4), ("NY_AM_SB", 10, 11), ("NY_PM_SB", 14, 15)]


def _ny_datetime(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_NY)


def _window_of(dt_iso: str):
    h = ny_hour(dt_iso)
    if h is None:
        return None
    for name, start, end in SB_WINDOWS:
        if start <= h < end:
            return name
    return None


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 45:
        diagnosis_refuse("SILVER_BULLET", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
        return None
    last = candles[-1]
    wname = _window_of(last["datetime"])
    if not wname:
        diagnosis_refuse("SILVER_BULLET", "خارج نافذة الرصاصة (نيويورك)")
        return None

    # Anchor to this DATE's window start, not the current clock hour or an
    # arbitrary eight-bar fallback. Missing prior-session history means no setup.
    last_ny = _ny_datetime(last["datetime"])
    start_hour = next(start for name, start, _ in SB_WINDOWS if name == wname)
    window_start = last_ny.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    prior_start = window_start - timedelta(hours=2)
    acc = [c for c in candles[:-1]
           if prior_start <= _ny_datetime(c["datetime"]) < window_start]
    if len(acc) < 3:
        diagnosis_refuse("SILVER_BULLET", "شموع نافذة الافتتاح غير كافية للنموذج")
        return None
    or_hi = max(c["high"] for c in acc)
    or_lo = min(c["low"] for c in acc)
    if or_hi <= or_lo:
        diagnosis_refuse("SILVER_BULLET", "نطاق افتتاح نيويورك مسطح بلا مدى")
        return None
    close = last["close"]
    bias_dir = "BUY" if close > (or_hi + or_lo) / 2 else "SELL"

    # Intact FVGs formed inside this window (recent 10 candles)
    recent = candles[-10:]
    fvgs = fair_value_gaps(recent, 4)
    fvg = None
    for f in fvgs:
        idx = f.get("index")  # helper indexes the THIRD candle completing a gap
        if not isinstance(idx, int) or not 2 <= idx < len(recent):
            continue
        formed_ny = _ny_datetime(recent[idx]["datetime"])
        if not window_start <= formed_ny <= last_ny:
            continue
        kind_dir = "BUY" if f["kind"] == "bullish" else "SELL"
        if kind_dir != bias_dir:
            continue
        # A previously broken gap is not made intact by a later price recovery.
        after = recent[idx + 1:]
        if bias_dir == "BUY" and (close < f["lo"] or any(c["close"] < f["lo"] for c in after)):
            continue
        if bias_dir == "SELL" and (close > f["hi"] or any(c["close"] > f["hi"] for c in after)):
            continue
        fvg = f
        break
    if not fvg:
        diagnosis_refuse("SILVER_BULLET", "لا فجوة قيمة عادلة داخل النافذة")
        return None

    direction = bias_dir
    fvg_mid = (fvg["lo"] + fvg["hi"]) / 2
    entry = min(fvg_mid, close) if direction == "BUY" else max(fvg_mid, close)
    a_ptr = atr_pct(candles)
    struct = structure_labels(candles)
    bias = structure_labels(ctx.bias_candles)

    a_abs = max(a_ptr * entry / 100.0, 1e-9)
    if direction == "BUY":
        stop = min(min(c["low"] for c in candles[-8:]), fvg["lo"]) - 0.4 * (or_hi - or_lo) * 0.5
    else:
        stop = max(max(c["high"] for c in candles[-8:]), fvg["hi"]) + 0.4 * (or_hi - or_lo) * 0.5
    stop = cap_stop(entry, stop, direction, a_abs, min_atr=0.8, fallback_atr=1.4, max_atr=2.8, cap_atr=2.4)
    risk = abs(entry - stop)
    if risk <= 0:
        diagnosis_refuse("SILVER_BULLET", "حساب الوقف غير سليم (مخاطرة صفرية)")
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 5.0 if direction == "BUY" else entry - risk * 5.0,
        direction,
    )

    earned = 0.0
    max_pts = 80.0
    checks = {}
    checks["sb_window"] = wname
    checks["kill_zone"] = True
    earned += 16
    checks["or_bias"] = or_hi > or_lo
    earned += 10 if checks["or_bias"] else 0
    checks["fvg_in_window"] = True
    earned += 16
    checks["fvg_direction"] = True
    earned += 14
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or \
                         (bias["trend"] == "down" and direction == "SELL") or \
                         (struct["bos_dir"] == ("up" if direction == "BUY" else "down"))
    checks["htf_align"] = checks["htf_bias"]
    earned += 10 if checks["htf_bias"] else 0
    checks["vol_ok"] = 0.05 < a_ptr < 6.0
    earned += 6 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 8 if checks["rr_ok"] else 0

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (bonus-only)
    # ------------------------------------------------------------------
    # 1) formal 1-2-3 protocol: after the FVG completed, price made a minor
    # pullback (2) and the latest close broke its extreme (3) — the classic
    # entry trigger on top of the bias(1)+FVG(2) core.
    trigger_123 = False
    try:
        gap_idx = int(fvg.get("index") or 0)
        post = candles[len(candles) - len(recent) + gap_idx + 1:]
        if len(post) >= 2:
            from fysb.engines.analysis import swings as _sw
            ph, pl = _sw(post, 1)
            if direction == "BUY" and ph:
                trigger_123 = close > float(ph[-1]["price"])
            elif direction == "SELL" and pl:
                trigger_123 = close < float(pl[-1]["price"])
    except Exception:
        trigger_123 = False
    checks["protocol_123"] = trigger_123
    earned += 5 if trigger_123 else 0

    # 2) NWOG/NDOG alignment: an opening gap in the bias direction adds
    # institutional fuel. Honest skip when the timeline has no gap.
    from fysb.engines.anchors import opening_gaps as _og
    gaps = _og(candles, a_abs)
    gap_align = None
    for key in ("nwog", "ndog"):
        g = gaps.get(key)
        if g and g["dir"] == direction:
            gap_align = key
            break
    checks["gap_align"] = gap_align
    earned += 4 if gap_align else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("SILVER_BULLET", checks, earned, max_pts)
        return None

    reasons = [
        f"silver bullet window {wname} (DST NY time)",
        f"opening-range bias {bias_dir} ({or_lo:.5g}-{or_hi:.5g})",
        f"in-window {fvg['kind']} FVG {fvg['lo']:.5g}-{fvg['hi']:.5g}",
        f"entry at FVG midpoint {entry:.5g}",
        f"invalidation beyond {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    if trigger_123:
        reasons.append("1-2-3 protocol confirmed: minor pullback broken by the latest close")
    else:
        reasons.append("1-2-3 trigger not yet broken (scored, not mandatory)")
    if gap_align:
        reasons.append(f"{gap_align.upper()} opening gap aligned with the bias")
    return StrategyResult(
        code="SILVER_BULLET", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
