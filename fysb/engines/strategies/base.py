"""Strategy base — every strategy returns a measurable, auditable result.

Contract:
  evaluate(ctx) -> StrategyResult | None
  * ctx carries entry-TF candles, bias-TF candles, instrument spec, market row.
  * score = normalized checklist merit (42..95), NOT a win probability.
  * reasons list every contributing rule (documented in docs/STRATEGIES.md).
  * A result is None when the strategy's entry conditions are simply not met —
    that is not an error, the pipeline records it as NO_SETUP.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import logging
import math
from numbers import Real

log = logging.getLogger(__name__)


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
    score: int                # checklist merit, never a calibrated probability
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
    """Widen tight/invalid stops, NEVER move a valid structural stop inward.

    ``max_atr``/``cap_atr`` remain accepted for call-site compatibility only.
    Oversized stops must be rejected by issuance geometry/risk policy, or sized
    down by execution policy, not cosmetically pulled inside invalidation.
    """
    e, s, a = float(entry), float(stop), float(atr_val or 0.0)
    d = (direction or "").upper()
    if d not in ("BUY", "SELL") or not all(math.isfinite(x) for x in (e, s, a)):
        raise ValueError("invalid stop inputs")
    if a <= 0:
        return s
    distance = max(float(min_atr), float(fallback_atr)) * a
    if d == "BUY":
        return e - distance if s >= e or (e - s) < min_atr * a else s
    return e + distance if s <= e or (s - e) < min_atr * a else s


def _finite_number(value) -> bool:
    try:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def candle_input_error(candles: list[dict]) -> str:
    """Validate rather than sort, coerce, repair or silently drop provider bars.

    UTC-naive timestamps follow the provider contract. This checks chronology,
    NOT whether bars have closed or whether the labelled timeframe is correct;
    those are responsibilities of the data/issuance pipeline.
    """
    if not isinstance(candles, (list, tuple)):
        return "candle series must be a list or tuple"
    previous = None
    for bar in candles:
        try:
            o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
            if not all(_finite_number(v) and v > 0 for v in (o, h, l, c)):
                return "non-finite/non-positive OHLC"
            if not l <= min(o, c) <= max(o, c) <= h:
                return "inconsistent OHLC bounds"
            v = bar.get("volume")
            if v is not None and (not _finite_number(v) or v < 0):
                return "invalid volume"
            dt = datetime.fromisoformat(bar["datetime"].replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            if previous is not None and dt <= previous:
                return "duplicate/out-of-order timestamps"
            previous = dt
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return "malformed candle"
    return ""


def result_error(result: StrategyResult, code: str) -> str:
    """Shared mathematical contract, independent of strategy profitability."""
    if not isinstance(result, StrategyResult) or result.code != code:
        return "invalid strategy identity"
    prices = (result.entry, result.stop, result.tp1, result.tp2, result.tp3)
    if not all(_finite_number(v) and v > 0 for v in prices):
        return "invalid/non-positive result prices"
    e, s, t1, t2, t3 = prices
    if result.direction == "BUY":
        ordered = s < e < t1 < t2 < t3
    elif result.direction == "SELL":
        ordered = s > e > t1 > t2 > t3
    else:
        ordered = False
    if not ordered:
        return "invalid directional geometry"
    rr = abs(t1 - e) / abs(e - s)
    if not _finite_number(result.rr) or abs(result.rr - round(rr, 2)) > 0.011:
        return "RR does not describe TP1"
    if rr < 2.0 - 1e-7 or abs(t2-e)/abs(e-s) < 3.0 - 1e-7 or abs(t3-e)/abs(e-s) < 4.5 - 1e-7:
        return "target floors violated"
    if not _finite_number(result.score) or not 0 <= result.score <= 95:
        return "invalid merit score"
    if not result.reasons or not all(isinstance(r, str) and r.strip() for r in result.reasons):
        return "missing decision reasons"
    if not isinstance(result.checks, dict):
        return "missing decision checks"
    return ""


def checked_strategy(evaluate):
    """Apply the same contract to registry AND direct module callers.

    Invalid inputs/results are rejected with diagnostics. Programming failures
    inside a rule are not swallowed here: evaluate_all logs their traceback.
    No cache is used: callers can mutate/reuse their candle lists.
    """
    code = evaluate.__module__.rsplit(".", 1)[-1].upper()

    @wraps(evaluate)
    def checked(ctx: Ctx) -> StrategyResult | None:
        for label, bars in (("entry", ctx.candles), ("bias", ctx.bias_candles)):
            error = candle_input_error(bars)
            if error:
                log.warning("strategy input rejected code=%s series=%s reason=%s", code, label, error)
                return None
        result = evaluate(ctx)
        if result is not None:
            error = result_error(result, code)
            if error:
                log.error("strategy result rejected code=%s reason=%s", code, error)
                return None
        return result

    return checked
