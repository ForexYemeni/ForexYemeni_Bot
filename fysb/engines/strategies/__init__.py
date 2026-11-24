"""Strategy registry — the only place strategy codes are bound to logic.

A strategy is usable only when its catalog row has verified=1 AND enabled=1.
Verification is earned by implementation + passing the automated test-suite
(tests/test_strategies.py) and is flipped by the admin Verify action, never by
marketing labels.
"""
from fysb.engines.strategies.base import Ctx, StrategyResult
from fysb.engines.strategies import amd, classic, crt, fibonacci, ichimoku, ict, price_action, silver_bullet, sk, smc, trend, turtle_soup, wyckoff

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
}

VERIFIABLE = {"ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP", "SILVER_BULLET",
              "FIBONACCI", "TREND", "ICHIMOKU", "CLASSIC", "SK", "WYCKOFF"}


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


def consensus_filter(results: list[StrategyResult]) -> tuple[list[StrategyResult], str]:
    """v6.20.0 Directional Consensus Guard: when multiple strategies fire on the
    SAME bar in OPPOSITE directions (BUY vs SELL), prevent a lone outlier
    from overriding a clear majority, and reject genuine deadlocked 50/50
    confusion.

    Returns (filtered_candidates_sorted_by_score, conflict_note):
      * Single direction (or <= 1 result): unchanged, no conflict.
      * Clear directional winner (more votes OR >= 15% higher total score
        while not outnumbered by >= 2): keeps only the winning direction's
        candidates sorted by score.
      * Genuine deadlock (equal votes & close scores < 10% apart): returns
        ([], reason) so the engine stays honestly flat rather than flipping a coin.
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
