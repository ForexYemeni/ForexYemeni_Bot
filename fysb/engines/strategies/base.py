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
