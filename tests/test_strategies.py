"""Strategy tests — each implemented strategy MUST produce a valid, documented
setup on qualifying synthetic data, and NO_SETUP on insufficient data."""
import unittest

from tests.helpers import (ict_bullish_sweep_setup, smc_bos_setup, trend_pullback_setup,
                           uptrend_candles, downtrend_candles, make_candles)
from fysb.engines.strategies.base import Ctx
from fysb.engines.strategies import evaluate_all, ict, smc, price_action, trend, ichimoku


class TestICTStrategy(unittest.TestCase):
    def test_bullish_ict_setup(self):
        candles = ict_bullish_sweep_setup()
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = ict.evaluate(ctx)
        self.assertIsNotNone(res, "ICT must produce a setup on a sweep+FVG fixture")
        self.assertEqual(res.direction, "BUY")
        self.assertGreater(res.score, 55)
        self.assertTrue(all(0 < x for x in (res.entry, res.stop, res.tp1, res.tp2, res.tp3)))
        self.assertGreaterEqual(res.rr, 1.5)
        self.assertTrue(res.checks.get("sweep"), "sweep check must be documented as passed")
        self.assertTrue(res.reasons, "reasons must explain the entry")

    def test_insufficient_data_returns_none(self):
        ctx = Ctx(symbol="X", timeframe="M15", candles=uptrend_candles(20), bias_candles=uptrend_candles(20))
        self.assertIsNone(ict.evaluate(ctx))

    def test_score_is_deterministic(self):
        candles = ict_bullish_sweep_setup()
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        r1, r2 = ict.evaluate(ctx), ict.evaluate(ctx)
        self.assertEqual(r1.score, r2.score, "confidence must be deterministic, never random")


class TestSMCStrategy(unittest.TestCase):
    def test_bos_bullish(self):
        candles = smc_bos_setup()
        ctx = Ctx(symbol="EURUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = smc.evaluate(ctx)
        self.assertIsNotNone(res, "SMC must produce a setup on a BOS fixture")
        self.assertEqual(res.direction, "BUY")
        self.assertTrue(res.checks.get("bos"))
        self.assertGreaterEqual(res.rr, 1.5)

    def test_no_setup_in_choppy_data(self):
        # alternating noise: no BOS
        vals = []
        px = 100.0
        for i in range(45):
            d = 0.5 if i % 2 == 0 else -0.5
            vals.append((px, max(px, px + d) + 0.3, min(px, px + d) - 0.3, px + d))
            px += d
        ctx = Ctx(symbol="X", timeframe="M15", candles=make_candles(vals), bias_candles=make_candles(vals))
        self.assertIsNone(smc.evaluate(ctx))


class TestPriceActionStrategy(unittest.TestCase):
    def test_engulfing_in_uptrend(self):
        candles = uptrend_candles(60, base=100, step=0.4)
        # append a bullish engulfing at the top of the uptrend
        last_close = candles[-1]["close"]
        vals = []
        for c in candles:
            vals.append((c["open"], c["high"], c["low"], c["close"]))
        vals.append((last_close - 0.2, last_close + 0.1, last_close - 0.4, last_close - 0.3))  # bearish
        vals.append((last_close - 0.45, last_close + 1.6, last_close - 0.5, last_close + 1.5))  # engulfing bull
        candles = make_candles(vals)
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = price_action.evaluate(ctx)
        self.assertIsNotNone(res, "PA must catch engulfing continuation in uptrend")
        self.assertEqual(res.direction, "BUY")
        self.assertTrue(res.checks.get("pattern") or res.checks.get("breakout_retest"))

    def test_no_setup_in_downtrend_for_buy_patterns_only(self):
        ctx = Ctx(symbol="X", timeframe="M15", candles=downtrend_candles(60), bias_candles=downtrend_candles(60))
        res = price_action.evaluate(ctx)
        if res is not None:
            self.assertEqual(res.direction, "SELL", "in a clean downtrend, PA must never call BUY")


class TestRegistry(unittest.TestCase):
    def test_unimplemented_strategies_are_closed(self):
        from fysb.engines.strategies import REGISTRY, VERIFIABLE
        # v6.8.0: TREND + ICHIMOKU + FIBONACCI are implemented & tested; only
        # v6.11.0 — WYCKOFF graduated: implemented + tested + verified on boot.
        self.assertIsNotNone(REGISTRY.get("WYCKOFF"), "WYCKOFF must now be implemented+tested")
        self.assertIn("WYCKOFF", VERIFIABLE)
        for code in ("FIBONACCI", "TREND", "ICHIMOKU"):
            self.assertIsNotNone(REGISTRY[code], f"{code} must be implemented")
            self.assertIn(code, VERIFIABLE)

    def test_ten_strategies_shipped(self):
        """The owner contract: 18 real, tested strategies live (v8.12.0 added
        HARMONIC/VWAP_RECLAIM/LONDON_BREAKOUT/DIVERGENCE/SQUEEZE to the 13)."""
        from fysb.db import IMPLEMENTED_STRATEGIES
        from fysb.engines.strategies import VERIFIABLE
        self.assertEqual(len(IMPLEMENTED_STRATEGIES), 18)
        self.assertIn("CLASSIC", IMPLEMENTED_STRATEGIES)
        self.assertIn("SK", IMPLEMENTED_STRATEGIES)
        self.assertIn("WYCKOFF", IMPLEMENTED_STRATEGIES)
        self.assertTrue(set(IMPLEMENTED_STRATEGIES) <= VERIFIABLE)

    def test_evaluate_all_sorted_by_score(self):
        candles = ict_bullish_sweep_setup()
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        results = evaluate_all(ctx, ["ICT", "SMC", "PRICE_ACTION"])
        scores = [r.score for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestTrendStrategy(unittest.TestCase):
    def test_pullback_continuation_setup(self):
        candles = trend_pullback_setup()
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = trend.evaluate(ctx)
        self.assertIsNotNone(res, "TREND must produce a continuation setup on stack+pullback fixture")
        self.assertEqual(res.direction, "BUY")
        self.assertTrue(res.checks.get("stack"))
        self.assertTrue(res.checks.get("pullback"))
        self.assertTrue(res.checks.get("resumption"))
        self.assertGreaterEqual(res.rr, 2.0)
        self.assertTrue(all(0 < x for x in (res.entry, res.stop, res.tp1, res.tp2, res.tp3)))

    def test_no_setup_in_chop(self):
        vals = []
        px = 100.0
        for i in range(60):
            d = 0.5 if i % 2 == 0 else -0.5
            vals.append((px, max(px, px + d) + 0.3, min(px, px + d) - 0.3, px + d))
            px += d
        ctx = Ctx(symbol="X", timeframe="M15", candles=make_candles(vals), bias_candles=make_candles(vals))
        self.assertIsNone(trend.evaluate(ctx), "no EMA stack in chop -> no trade")

    def test_score_is_deterministic(self):
        candles = trend_pullback_setup()
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        r1, r2 = trend.evaluate(ctx), trend.evaluate(ctx)
        self.assertEqual(r1.score, r2.score)


class TestIchimokuStrategy(unittest.TestCase):
    def test_cloud_breakout_setup(self):
        candles = uptrend_candles(90, base=3000.0, step=2.0)
        ctx = Ctx(symbol="XAUUSD", timeframe="H1", candles=candles, bias_candles=candles)
        res = ichimoku.evaluate(ctx)
        self.assertIsNotNone(res, "ICHIMOKU must produce a cloud-breakout setup on a clean uptrend")
        self.assertEqual(res.direction, "BUY")
        self.assertTrue(res.checks.get("cloud"))
        self.assertTrue(res.checks.get("tk"))
        self.assertTrue(res.checks.get("future_cloud"))
        self.assertTrue(res.checks.get("chikou"))
        self.assertGreaterEqual(res.rr, 2.0)

    def test_no_setup_in_range(self):
        vals = []
        px = 2000.0
        for i in range(90):
            d = 1.0 if i % 2 == 0 else -1.0
            vals.append((px, max(px, px + d) + 0.5, min(px, px + d) - 0.5, px + d))
            px += d
        ctx = Ctx(symbol="X", timeframe="H1", candles=make_candles(vals), bias_candles=make_candles(vals))
        self.assertIsNone(ichimoku.evaluate(ctx), "price inside the Kumo -> no trade")

    def test_downtrend_calls_sell(self):
        candles = downtrend_candles(90, base=3000.0, step=2.0)
        ctx = Ctx(symbol="XAUUSD", timeframe="H1", candles=candles, bias_candles=candles)
        res = ichimoku.evaluate(ctx)
        if res is not None:
            self.assertEqual(res.direction, "SELL", "in a clean downtrend ICHIMOKU must never call BUY")


if __name__ == "__main__":
    unittest.main()
