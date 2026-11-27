"""PRICE_ACTION — full candle-reading set: patterns, inside-bar, 2BR, stars,
failed-breakout traps, with location weighting (v8.12.0).

A recent (up to three bars) engulfing/pin pattern with EMA9/21 alignment, a
breakout-with-retest, an inside-bar break, a two-bar reversal, a morning/
evening star (EMA-aligned variants) OR a failed-breakout trap (trend-agnostic,
gated by location) determines direction. Latest body >=0.25 ATR.
Counter-HTF setups need S/R confluence, a confirmed retest, or a failed-
breakout location. Psychological big-figure proximity adds location weight.
Entry: close. Stop: last-six-bar extreme +/-0.85 ATR, minimum1.2 ATR.
Targets 2R/3R/4.5R. RSI bonus: BUY52..75 / SELL25..48.
Weights: pattern16, breakout-retest16, inside-bar/2BR/star14, failed-
breakout16, EMA10, HTF10, S/R8, psych-level6, RSI6, volatility4, RR6
(base76; 96 with a v8.12.0 trigger). Minimum score64; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, breakout_retest, candle_patterns, ema, rsi, support_resistance
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


def _inside_bar_break(candles: list[dict]) -> str:
    """Inside-bar: bar -2 fully contained in bar -3's range; bar -1 closes
    beyond the mother-bar range. Returns 'BUY' / 'SELL' / ''."""
    if len(candles) < 3:
        return ""
    m, ib, last = candles[-3], candles[-2], candles[-1]
    if float(ib["high"]) > float(m["high"]) or float(ib["low"]) < float(m["low"]):
        return ""
    mother_hi = max(float(m["high"]), float(ib["high"]))
    mother_lo = min(float(m["low"]), float(ib["low"]))
    if float(last["close"]) > mother_hi:
        return "BUY"
    if float(last["close"]) < mother_lo:
        return "SELL"
    return ""


def _two_bar_reversal(candles: list[dict]) -> str:
    """Two-bar reversal: bar -2 closes strongly one way, bar -1 reverses it
    and closes beyond bar -2's open. Returns 'BUY' / 'SELL' / ''."""
    if len(candles) < 2:
        return ""
    p, last = candles[-2], candles[-1]
    p_dir = float(p["close"]) - float(p["open"])
    l_dir = float(last["close"]) - float(last["open"])
    if p_dir > 0 and l_dir < 0 and float(last["close"]) < float(p["open"]):
        return "SELL"
    if p_dir < 0 and l_dir > 0 and float(last["close"]) > float(p["open"]):
        return "BUY"
    return ""


def _star(candles: list[dict], a: float) -> str:
    """Morning/evening star: strong bar, small star bar, strong opposite bar
    closing beyond the first bar's open (bodies proxied — no tick gaps)."""
    if len(candles) < 3:
        return ""
    o1 = float(candles[-3]["open"]); c1 = float(candles[-3]["close"])
    o2 = float(candles[-2]["open"]); c2 = float(candles[-2]["close"])
    o3 = float(candles[-1]["open"]); c3 = float(candles[-1]["close"])
    if abs(c2 - o2) > 0.35 * a:
        return ""                       # middle star too big — not a star
    if c1 < o1 and c3 > o1 and abs(c3 - o3) >= 0.6 * abs(c1 - o1):
        return "BUY"
    if c1 > o1 and c3 < o1 and abs(c3 - o3) >= 0.6 * abs(c1 - o1):
        return "SELL"
    return ""


def _failed_breakout(candles: list[dict]) -> str:
    """Trap reversal: the last bar swept the prior swing extreme and closed
    back on the wrong side of it — a failed breakout flips direction."""
    from fysb.engines.analysis import swings
    highs, lows = swings(candles[:-1], 3)
    last = candles[-1]
    if highs and float(last["high"]) > float(highs[-1]["price"]) and float(last["close"]) < float(highs[-1]["price"]):
        return "SELL"
    if lows and float(last["low"]) < float(lows[-1]["price"]) and float(last["close"]) > float(lows[-1]["price"]):
        return "BUY"
    return ""


def _nearest_psych_level(price: float, a: float) -> float | None:
    """Nearest zero-ending psychological level on a grid at least 5 ATR
    dense (so every price is not trivially 'near' a level)."""
    best = None
    g = 0.0001
    while g <= 1e7:
        if g >= 5.0 * a:
            lvl = round(price / g) * g
            d = abs(price - lvl)
            if best is None or d < best[1]:
                best = (lvl, d)
        g *= 10.0
    return None if best is None else best[0]


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    close = candles[-1]["close"]
    patterns = candle_patterns(candles)
    br = breakout_retest(candles)

    ema_fast = ema([c["close"] for c in candles[-50:]], 9)
    ema_slow = ema([c["close"] for c in candles[-50:]], 21)
    r = rsi([c["close"] for c in candles], 14)
    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    a = atr(candles)
    if a <= 0:
        return None

    direction = ""
    trigger = ""
    if any(p in ("ENGULFING_BULL", "PIN_BAR_BULL") for p in patterns) and trend_up:
        direction, trigger = "BUY", "pattern"
    elif any(p in ("ENGULFING_BEAR", "PIN_BAR_BEAR") for p in patterns) and trend_down:
        direction, trigger = "SELL", "pattern"
    elif (ib := _inside_bar_break(candles)) and ((ib == "BUY" and trend_up) or (ib == "SELL" and trend_down)):
        direction, trigger = ib, "inside_bar"
    elif (tbr := _two_bar_reversal(candles)) and ((tbr == "BUY" and trend_up) or (tbr == "SELL" and trend_down)):
        direction, trigger = tbr, "two_bar_reversal"
    elif (st := _star(candles, a)) and ((st == "BUY" and trend_up) or (st == "SELL" and trend_down)):
        direction, trigger = st, "star"
    elif br["breakout"] == "UP" and trend_up:
        direction, trigger = "BUY", "breakout"
    elif br["breakout"] == "DOWN" and trend_down:
        direction, trigger = "SELL", "breakout"
    elif (fb := _failed_breakout(candles)) and fb:
        direction, trigger = fb, "failed_breakout"
    if not direction:
        return None

    # v6.20.6: require directional candle conviction (reject tiny indecision dojis)
    last_bar = candles[-1]
    body = abs(float(last_bar["close"]) - float(last_bar["open"]))
    if body < 0.25 * a:
        return None
    entry = close
    raw_stop = (min(c["low"] for c in candles[-6:]) - 0.85 * a) if direction == "BUY" else (max(c["high"] for c in candles[-6:]) + 0.85 * a)
    stop = cap_stop(entry, raw_stop, direction, a, min_atr=1.2, fallback_atr=1.8, max_atr=2.6, cap_atr=2.4)

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5,
        direction,
    )

    # --- Normalized Fair Score (v8.0.0) ------------------------------------
    earned = 0.0
    max_pts = 76.0
    checks = {}
    pattern_dir = any(p in (("ENGULFING_BULL", "PIN_BAR_BULL") if direction == "BUY" else ("ENGULFING_BEAR", "PIN_BAR_BEAR")) for p in patterns)
    checks["pattern"] = pattern_dir
    earned += 16 if pattern_dir else 0
    checks["breakout_retest"] = br["breakout"] == ("UP" if direction == "BUY" else "DOWN") and br["retest"]
    earned += 16 if checks["breakout_retest"] else 0
    checks["ema_trend"] = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_down)
    earned += 10 if checks["ema_trend"] else 0
    htf_up = ema([c["close"] for c in ctx.bias_candles[-50:]], 21) > ema([c["close"] for c in ctx.bias_candles[-50:]], 50) if len(ctx.bias_candles) >= 50 else trend_up
    checks["htf_bias"] = (direction == "BUY" and htf_up) or (direction == "SELL" and not htf_up)
    earned += 10 if checks["htf_bias"] else 0
    sr = support_resistance(candles)
    near_level = False
    if direction == "BUY" and sr["support"] and abs(entry - sr["support"]) < 1.5 * a:
        near_level = True
    if direction == "SELL" and sr["resistance"] and abs(entry - sr["resistance"]) < 1.5 * a:
        near_level = True
    checks["sr_confluence"] = near_level
    earned += 8 if near_level else 0
    checks["rsi_regime"] = (52 <= r <= 75) if direction == "BUY" else (25 <= r <= 48)
    earned += 6 if checks["rsi_regime"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    # v8.12.0 — extended PA triggers: inside-bar, two-bar reversal, star and
    # failed-breakout carry their own core points (replacing pattern/breakout).
    checks["trigger"] = trigger
    if trigger in ("inside_bar", "two_bar_reversal", "star"):
        earned += 14
    elif trigger == "failed_breakout":
        earned += 16

    # v8.12.0 — location weighting: psychological big-figure proximity.
    psych = _nearest_psych_level(entry, a)
    psych_hit = psych is not None and abs(entry - psych) <= 0.25 * a
    checks["psych_level"] = psych if psych_hit else None
    earned += 6 if psych_hit else 0

    if not checks["htf_bias"] and not (checks["sr_confluence"] or checks["breakout_retest"] or trigger == "failed_breakout"):
        return None
    score = normalized_score(earned, max_pts)
    if score < 64:
        return None

    reasons = [
        f"patterns: {','.join(patterns) if patterns else 'none'}",
        f"trigger: {trigger}",
        f"breakout {br['breakout'] or '-'} with retest={'yes' if br['retest'] else 'no'}",
        f"EMA9 {'>' if trend_up else '<'} EMA21 (trend {'up' if trend_up else 'down'})",
        f"RSI={r:.1f}" + (f" · psych level {psych:.5g}" if psych_hit else ""),
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="PRICE_ACTION", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
