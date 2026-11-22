"""Backward-compatibility facade (V5 import surface).

The current engines supersede this module; existing imports keep working:
    from market_engine import fetch_candles, analyze_candles
`analyze_candles` now delegates to the PRICE_ACTION strategy (same contract dict).
"""
from fysb.engines.market_data import fetch_candles  # noqa: F401


def analyze_candles(candles):
    """Legacy contract: returns a signal-idea dict or None (never random)."""
    from fysb.engines.strategies.base import Ctx
    from fysb.engines.strategies import price_action

    if len(candles) < 40:
        return None
    ctx = Ctx(symbol="LEGACY", timeframe="M15", candles=candles, bias_candles=candles)
    res = price_action.evaluate(ctx)
    if res is None:
        return None
    return {
        "direction": res.direction,
        "entry": res.entry,
        "stop": res.stop,
        "tp1": res.tp1,
        "tp2": res.tp2,
        "tp3": res.tp3,
        "rr": res.rr,
        "score": res.score,
        "reason": " | ".join(res.reasons),
    }
