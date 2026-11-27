"""SQUEEZE — Bollinger/Keltner squeeze breakout (TTM-style, declared subset).

SQUEEZE STATE at the bar before the trigger: Bollinger bands (SMA20 ± 2σ)
fully INSIDE the Keltner channel (EMA20 ± 1.5×ATR20) — the classic TTM quiet
condition — AND bandwidth at a historical extreme: either in its lowest
quintile of the available history (rank ≤ 20%) OR within 10% of the minimum
historical bandwidth (a homogeneous quiet series ranks 1.0 against itself,
so the minimum test keeps the detection honest instead of silent). Both
conditions are pure arithmetic; nothing subjective.
Honest subset: the Keltner midline here is EMA20 (not the TTM true-range
median of (H+L+C)/3), and ATR is Wilder's — the difference is declared, not
hidden.

FIRE: the last CLOSED candle closes BEYOND the Bollinger band in its own
direction (a real exit, not a wick) with body ≥ 0.3 ATR, while bandwidth
EXPANDS versus the squeeze minimum. RSI must agree (BUY ≥ 55, SELL ≤ 45).
Entry: close. Stop: the opposite Keltner band ± 0.5 ATR (squeeze breakouts
re-enter the box often). Targets: 2R/3R/4.5R floors.

Score is checklist merit normalized to 42..95 — NOT a win probability.
"""
from fysb.engines.analysis import atr, atr_pct, ema, rsi, structure_labels
from fysb.engines.strategies.base import (
    diagnosis_record,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)

BB_N, BB_K = 20, 2.0
KC_N, KC_M = 20, 1.5
RANK_QUINTILE = 0.20        # bandwidth in the lowest 20% of history ...
MIN_BAND_TOL = 1.10         # ... or within 10% of the historical minimum
MIN_SCORE = 58


def _sma(values: list[float], n: int) -> float:
    win = values[-n:]
    return sum(win) / len(win) if win else 0.0


def _stddev(values: list[float], n: int) -> float:
    win = values[-n:]
    if len(win) < 2:
        return 0.0
    m = sum(win) / len(win)
    return (sum((v - m) ** 2 for v in win) / (len(win) - 1)) ** 0.5


def _bands_closes(closes: list[float]):
    mid = _sma(closes, BB_N)
    sd = _stddev(closes, BB_N)
    return mid, mid + BB_K * sd, mid - BB_K * sd


def _bands(candles: list[dict]):
    return _bands_closes([float(c["close"]) for c in candles])


def _keltner(candles: list[dict], a: float):
    closes = [float(c["close"]) for c in candles]
    mid = ema(closes, KC_N)
    return mid + KC_M * a, mid - KC_M * a


def _bandwidth_rank(closes_at: list[float]) -> float:
    """Rank (0..1) of the CURRENT bandwidth within the whole supplied history."""
    cur_mid, cur_up, cur_lo = _bands_closes(closes_at)
    cur_bw = (cur_up - cur_lo) / cur_mid if cur_mid > 0 else 0.0
    if len(closes_at) < BB_N + 10:
        return 1.0                    # not enough history — never call it a squeeze
    ranks = []
    for end in range(BB_N, len(closes_at) + 1):
        w = closes_at[:end]
        m, u, l = _bands_closes(w)
        if m > 0:
            ranks.append((u - l) / m)
    if not ranks:
        return 1.0
    below = sum(1 for r in ranks if r <= cur_bw)
    return below / len(ranks)


def _at_historical_minimum(closes_at: list[float]) -> bool:
    """True when the current bandwidth is within 10% of the historical minimum.

    A homogeneous quiet series ranks 1.0 against itself (every window ties),
    so the percentile alone would stay silent forever on a calm tape. This
    minimum test restores the honest TTM intent: 'bandwidth at its extreme'.
    """
    cur_mid, cur_up, cur_lo = _bands_closes(closes_at)
    cur_bw = (cur_up - cur_lo) / cur_mid if cur_mid > 0 else 0.0
    if len(closes_at) < BB_N + 10:
        return False
    vals = []
    for end in range(BB_N, len(closes_at) + 1):
        m, u, l = _bands_closes(closes_at[:end])
        if m > 0:
            vals.append((u - l) / m)
    if not vals:
        return False
    return cur_bw <= min(vals) * MIN_BAND_TOL


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 60:
        return None
    a = atr(candles, 14)
    if a <= 0:
        return None
    last = candles[-1]
    close = float(last["close"])
    opens = float(last["open"])

    # SQUEEZE STATE evaluated at the bar BEFORE the trigger (the trigger bar
    # itself is already the expansion).
    base = candles[:-1]
    closes_base = [float(c["close"]) for c in base]
    if len(closes_base) < BB_N + 10:
        return None
    bw_rank = _bandwidth_rank(closes_base)
    at_min = _at_historical_minimum(closes_base)
    sq_quiet = (bw_rank <= RANK_QUINTILE) or at_min
    bb_mid, bb_up, bb_lo = _bands(base)
    kc_up, kc_lo = _keltner(base, a)
    ttm_inside = bb_up <= kc_up and bb_lo >= kc_lo
    if not (sq_quiet and ttm_inside):
        return None                   # no squeeze — nothing to break out of

    direction = ""
    if close > bb_up and close > opens:
        direction = "BUY"
    elif close < bb_lo and close < opens:
        direction = "SELL"
    if not direction:
        return None
    body = abs(close - opens)
    if body < 0.3 * a:
        return None                   # a wicky exit is not a fire

    entry = close
    stop = (kc_lo - 0.5 * a) if direction == "BUY" else (kc_up + 0.5 * a)
    stop = cap_stop(entry, stop, direction, a, min_atr=1.0, fallback_atr=1.6, max_atr=2.8, cap_atr=2.4)
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

    earned = 0.0
    max_pts = 104.0
    checks: dict = {}
    checks["squeeze_quiet"] = sq_quiet
    checks["bandwidth_rank"] = round(bw_rank, 3)
    checks["at_hist_min_bandwidth"] = at_min
    earned += 22                      # the squeeze itself is the mandatory core
    checks["ttm_inside"] = ttm_inside
    earned += 12                      # BB inside KC — quiet arithmetic confirmed
    checks["band_exit_close"] = True
    earned += 16                      # mandatory directional band exit on close
    checks["body"] = body >= 0.3 * a
    earned += 10
    r = rsi([float(c["close"]) for c in candles], 14)
    checks["rsi_side"] = (r >= 55) if direction == "BUY" else (r <= 45)
    earned += 10 if checks["rsi_side"] else 0
    # expansion: trigger bandwidth strictly above the squeeze median
    cur_mid, cur_up, cur_lo = _bands(candles)
    checks["bandwidth_expanding"] = cur_mid > 0 and (cur_up - cur_lo) > (bb_up - bb_lo)
    earned += 8 if checks["bandwidth_expanding"] else 0
    bias = structure_labels(ctx.bias_candles)
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or \
                         (bias["trend"] == "down" and direction == "SELL")
    earned += 10 if checks["htf_bias"] else 0
    from fysb.engines.analysis import kill_zone
    kz = ctx.kill_zone or (kill_zone(last["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if kz else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < MIN_SCORE:
        diagnosis_record("SQUEEZE", checks, earned, max_pts)
        return None

    reasons = [
        f"squeeze confirmed: bandwidth rank {bw_rank:.2f}"
        + (" (lowest 20%)" if bw_rank <= RANK_QUINTILE else " (at the historical minimum)") +
        " + Bollinger inside Keltner (EMA20±1.5 ATR)",
        f"{direction} close beyond the Bollinger band with body {body / a:.2f} ATR and expanding bandwidth",
        f"RSI {r:.1f} on the breakout side",
        f"invalidation beyond the opposite Keltner band {stop:.5g}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SQUEEZE", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
