"""Strategy base — every strategy returns a measurable, auditable result.

Contract:
  evaluate(ctx) -> StrategyResult | None
  * ctx carries entry-TF candles, bias-TF candles, instrument spec, market row.
  * score = 50 base + weighted bonuses from PASSED, MEASURABLE checks, capped 95.
  * reasons list every contributing rule (documented in docs/STRATEGIES.md).
  * A result is None when the strategy's entry conditions are simply not met —
    that is not an error, the pipeline records it as NO_SETUP.
"""
from dataclasses import dataclass, field


@dataclass
class Ctx:
    symbol: str
    timeframe: str            # entry timeframe code (M15, H1...)
    candles: list[dict]       # entry timeframe, oldest -> newest
    bias_candles: list[dict]  # higher timeframe for bias, may be same list
    market_row: dict = None   # market_catalog row (pip specs etc.)
    kill_zone: str = ""


@dataclass
class StrategyResult:
    code: str
    direction: str            # BUY | SELL
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    score: int                # 0..100 measurable confidence
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.code}:{self.direction}:{self.score} ({'; '.join(self.reasons[:3])})"


def clamp_score(score: float) -> int:
    return int(max(0, min(95, round(score))))


def normalized_score(
    earned: float,
    max_possible: float,
    *,
    floor: float = 42.0,
    ceiling: float = 95.0,
) -> int:
    """v8.0.0 Fair Merit Score Normalizer shared across ALL 13 strategies.

    Eliminates structural base-score bias (previously Base=50 for ICT/SMC/PA vs
    Base=32 for Wyckoff/Ichimoku/Trend/SK/Classic) so every methodology competes
    strictly on the fraction of its own institutional checklist passed.
    """
    if max_possible <= 0:
        return clamp_score(floor)
    ratio = max(0.0, min(1.0, float(earned) / float(max_possible)))
    return clamp_score(floor + ratio * (ceiling - floor))


def ensure_min_tp_rr(
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    direction: str,
    *,
    min_rr1: float = 2.0,
    min_rr2: float = 3.0,
    min_rr3: float = 4.5,
) -> tuple[float, float, float, float]:
    """v8.0.0 Royal Target Floor — guarantees TP1 >= 2.0 * risk (twice SL!),
    TP2 >= 3.0 * risk, TP3 >= 4.5 * risk, strictly monotonic in trade direction.
    Returns (tp1, tp2, tp3, rr1)."""
    e = float(entry)
    s = float(stop)
    risk = abs(e - s)
    if risk <= 1e-12:
        return float(tp1), float(tp2), float(tp3), 0.0
    d = (direction or "BUY").upper()
    if d == "BUY":
        t1 = max(float(tp1), e + risk * min_rr1)
        t2 = max(float(tp2), t1 + risk * 0.5, e + risk * min_rr2)
        t3 = max(float(tp3), t2 + risk * 0.75, e + risk * min_rr3)
    else:
        t1 = min(float(tp1), e - risk * min_rr1)
        t2 = min(float(tp2), t1 - risk * 0.5, e - risk * min_rr2)
        t3 = min(float(tp3), t2 - risk * 0.75, e - risk * min_rr3)
    rr1 = round(abs(t1 - e) / risk, 2)
    return t1, t2, t3, rr1


def safe_rr(entry: float, stop: float, tp: float, direction: str) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = abs(tp - entry)
    if direction == "BUY" and tp <= entry:
        return 0.0
    if direction == "SELL" and tp >= entry:
        return 0.0
    return reward / risk


def cap_stop(
    entry: float,
    stop: float,
    direction: str,
    atr_val: float,
    *,
    min_atr: float = 0.8,
    fallback_atr: float = 1.6,
    max_atr: float = 2.6,
    cap_atr: float = 2.4,
) -> float:
    """v6.24.0 Universal stop sanitizer shared across all 13 strategies.

    * Guarantees strict directional side (BUY: stop < entry; SELL: stop > entry).
    * Prevents ultra-tight stops (< min_atr * ATR) from noise stop-outs.
    * Caps abnormal news-spike wick stops (> max_atr * ATR) to cap_atr * ATR
      so high-quality setups are preserved with institutional risk sizing instead
      of blowing out to 888-pip stops or getting rejected unnecessarily.
    """
    a = float(atr_val or 0.0)
    if a <= 0:
        return float(stop)
    d = (direction or "").upper()
    e = float(entry)
    s = float(stop)
    if d == "BUY":
        if s >= e or (e - s) < min_atr * a:
            return e - fallback_atr * a
        if (e - s) > max_atr * a:
            return e - cap_atr * a
        return s
    if d == "SELL":
        if s <= e or (s - e) < min_atr * a:
            return e + fallback_atr * a
        if (s - e) > max_atr * a:
            return e + cap_atr * a
        return s
    return s
