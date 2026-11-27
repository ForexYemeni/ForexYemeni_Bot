"""Strategy registry — the only place strategy codes are bound to logic.

A strategy is usable only when its catalog row has verified=1 AND enabled=1.
The database seeds implemented engines as verified; this is internal software
status, not external certification or evidence of profitability. Catalog and
package authorization belong to callers; this registry enforces rule contracts.
"""
import logging

from fysb.engines.strategies.base import (Ctx, StrategyResult, result_error,
                                          diagnosis_begin, diagnosis_snapshot)

log = logging.getLogger(__name__)
from fysb.engines.strategies import amd, classic, crt, divergence, fibonacci, harmonic, ichimoku, ict, london_breakout, price_action, silver_bullet, sk, smc, squeeze, trend, turtle_soup, vwap_reclaim, wyckoff

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
    "CLASSIC": classic,              # classic trend + S/R + candle confirmation (v6.10.0)
    "SK": sk,                        # Scalper Key — close-confirmed momentum break (v6.10.0)
    "WYCKOFF": wyckoff,              # accumulation spring / distribution upthrust (v6.11.0)
    "HARMONIC": harmonic,           # Gartley/Bat/Butterfly/Crab XABCD ratios (v8.12.0)
    "VWAP_RECLAIM": vwap_reclaim,    # session VWAP/mean reclaim or reject (v8.12.0)
    "LONDON_BREAKOUT": london_breakout,  # London opening-range breakout (v8.12.0)
    "DIVERGENCE": divergence,        # Class A/B RSI divergence at swing pivots (v8.12.0)
    "SQUEEZE": squeeze,              # Bollinger/Keltner squeeze fire (v8.12.0)
}

VERIFIABLE = {"ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP", "SILVER_BULLET",
              "FIBONACCI", "TREND", "ICHIMOKU", "CLASSIC", "SK", "WYCKOFF",
              "HARMONIC", "VWAP_RECLAIM", "LONDON_BREAKOUT", "DIVERGENCE", "SQUEEZE"}


def get(code: str):
    return REGISTRY.get((code or "").upper())


def evaluate_all(ctx: Ctx, allowed_codes: list[str]) -> list[StrategyResult]:
    """Run every allowed, implemented strategy; return valid results sorted by score."""
    results: list[StrategyResult] = []
    diagnosis_begin()
    for code in dict.fromkeys(c.upper() for c in allowed_codes):
        module = REGISTRY.get((code or "").upper())
        if module is None:
            continue
        try:
            res = module.evaluate(ctx)
        except Exception:
            log.exception("strategy evaluation failed code=%s symbol=%s timeframe=%s", code, ctx.symbol, ctx.timeframe)
            continue  # isolate one broken engine without hiding its traceback
        if res is not None:
            error = result_error(res, code)
            if error:
                log.error("strategy result rejected code=%s reason=%s", code, error)
                continue
            results.append(res)
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def consensus_filter(results: list[StrategyResult]) -> tuple[list[StrategyResult], str]:
    """v6.20.0 Directional Consensus Guard: when multiple strategies fire on the
    SAME bar in OPPOSITE directions (BUY vs SELL), prevent a lone outlier
    from overriding a clear majority, and reject genuine deadlocked 50/50
    confusion.

    Returns (filtered_candidates_sorted_by_score, conflict_note):
      * Single direction (or <= 1 result): unchanged, no conflict.
      * Equal vote counts with abs(score difference)/total < 0.08: reject.
      * A majority of >= 2 votes wins, or a non-minority with higher score sum.
      * A one-vote minority can win only with a score margin >= 0.12;
        otherwise follow the vote majority. Return score-sorted winners.

    These are deterministic voting heuristics, not independent probabilistic
    evidence (many engines share the same price primitives).
    """
    if len(results) <= 1:
        return list(results), ""
    buys = [r for r in results if (r.direction or "").upper() == "BUY"]
    sells = [r for r in results if (r.direction or "").upper() == "SELL"]
    if not buys or not sells:
        return list(results), ""

    buy_sum = sum(r.score for r in buys)
    sell_sum = sum(r.score for r in sells)
    total = max(buy_sum + sell_sum, 1.0)
    margin = abs(buy_sum - sell_sum) / total

    # Deadlock: equal number of strategies on both sides and total scores nearly tied
    if len(buys) == len(sells) and margin < 0.08:
        return [], (
            f"تعارض منهجيات متكافئ ({len(buys)} شراء مجموع {buy_sum:.0f} مقابل "
            f"{len(sells)} بيع مجموع {sell_sum:.0f}) — لا تُنشر إشارة في سوق متضارب"
        )

    # Majority wins when it has higher vote count AND higher total score,
    # or when it outnumbers the minority by >= 2 strategies.
    if len(buys) >= len(sells) + 2 or (len(buys) >= len(sells) and buy_sum > sell_sum):
        return sorted(buys, key=lambda r: r.score, reverse=True), f"consensus BUY ({len(buys)} vs {len(sells)})"
    if len(sells) >= len(buys) + 2 or (len(sells) >= len(buys) and sell_sum > buy_sum):
        return sorted(sells, key=lambda r: r.score, reverse=True), f"consensus SELL ({len(sells)} vs {len(buys)})"

    # Minority had slightly higher score than a 1-vote larger majority:
    # only allow if score lead is >= 12%, else follow majority.
    if buy_sum > sell_sum:
        winner = buys if margin >= 0.12 else sells
        side = "BUY" if winner is buys else "SELL"
    else:
        winner = sells if margin >= 0.12 else buys
        side = "SELL" if winner is sells else "BUY"
    return sorted(winner, key=lambda r: r.score, reverse=True), f"consensus {side} ({len(buys)}B/{len(sells)}S)"
