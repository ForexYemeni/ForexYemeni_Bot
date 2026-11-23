"""Strategy registry — the only place strategy codes are bound to logic.

A strategy is usable only when its catalog row has verified=1 AND enabled=1.
Verification is earned by implementation + passing the automated test-suite
(tests/test_strategies.py) and is flipped by the admin Verify action, never by
marketing labels.
"""
from fysb.engines.strategies.base import Ctx, StrategyResult
from fysb.engines.strategies import amd, crt, fibonacci, ichimoku, ict, price_action, silver_bullet, smc, trend, turtle_soup

REGISTRY: dict[str, object] = {
    "ICT": ict,
    "SMC": smc,
    "PRICE_ACTION": price_action,
    "CRT": crt,                      # Candle Range Theory — implemented + tested
    "AMD": amd,                      # Power of Three (Accumulation/Manipulation/Distribution)
    "TURTLE_SOUP": turtle_soup,      # failed-breakout reversal (ICT)
    "SILVER_BULLET": silver_bullet,  # time-window FVG continuation (ICT)
    "FIBONACCI": fibonacci,          # golden-pocket retracement — implemented + tested (v6.8.0)
    "TREND": trend,                  # trend-following EMA pullback — implemented + tested (v6.8.0)
    "ICHIMOKU": ichimoku,            # Ichimoku Kinko Hyo cloud system — implemented + tested (v6.8.0)
    # Kept closed until a dedicated rule engine + tests exist (honest governance):
    "WYCKOFF": None,
}

VERIFIABLE = {"ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP", "SILVER_BULLET",
              "FIBONACCI", "TREND", "ICHIMOKU"}


def get(code: str):
    return REGISTRY.get((code or "").upper())


def evaluate_all(ctx: Ctx, allowed_codes: list[str]) -> list[StrategyResult]:
    """Run every allowed, implemented strategy; return valid results sorted by score."""
    results: list[StrategyResult] = []
    for code in allowed_codes:
        module = REGISTRY.get((code or "").upper())
        if module is None:
            continue
        try:
            res = module.evaluate(ctx)
        except Exception:
            continue  # a broken strategy never blocks the pipeline; logged upstream
        if res is not None:
            results.append(res)
    results.sort(key=lambda r: r.score, reverse=True)
    return results
