"""Unit tests: analysis primitives — EMA/RSI/ATR, structure, FVG, OB, patterns."""
import unittest

from tests.helpers import make_candles, downtrend_candles, uptrend_candles
from fysb.engines import analysis


class TestIndicators(unittest.TestCase):
    def test_ema_constant(self):
        vals = [10.0] * 50
        self.assertAlmostEqual(analysis.ema(vals, 9), 10.0)

    def test_rsi_all_gains(self):
        vals = [float(i) for i in range(1, 30)]
        self.assertGreaterEqual(analysis.rsi(vals), 99.0)

    def test_atr_positive(self):
        candles = uptrend_candles(40)
        self.assertGreater(analysis.atr(candles), 0)

    def test_atr_pct(self):
        candles = uptrend_candles(40, base=100)
        pct = analysis.atr_pct(candles)
        self.assertTrue(0 < pct < 10)


class TestStructure(unittest.TestCase):
    def test_uptrend_labels_hh_hl(self):
        candles = uptrend_candles(60)
        s = analysis.structure_labels(candles)
        self.assertEqual(s["trend"], "up")
        self.assertIn("HH", s["labels"])
        self.assertIn("HL", s["labels"])

    def test_downtrend_labels_lh_ll(self):
        candles = downtrend_candles(60)
        s = analysis.structure_labels(candles)
        self.assertEqual(s["trend"], "down")
        self.assertIn("LH", s["labels"])
        self.assertIn("LL", s["labels"])

    def test_fvg_detection(self):
        # candle A high 10, candle C low 12 → bullish gap 10..12
        vals = [
            (9, 10.0, 8, 9.5),
            (9.5, 11.5, 9.2, 11.0),   # displacement candle
            (11.0, 13.0, 12.0, 12.5),  # C: low 12 > A high 10 → FVG
        ]
        candles = make_candles(vals)
        gaps = analysis.fair_value_gaps(candles)
        self.assertTrue(any(g["kind"] == "bullish" for g in gaps))

    def test_order_block_bullish(self):
        vals = []
        px = 100.0
        for i in range(12):  # flat-ish
            vals.append((px, px + 0.3, px - 0.3, px + 0.1))
            px += 0.1
        bear = (px, px + 0.2, px - 0.5, px - 0.4)   # last bearish candle (OB)
        vals.append(bear)
        px -= 0.4
        disp = (px, px + 4.0, px - 0.2, px + 3.8)   # displacement bull
        vals.append(disp)
        for i in range(5):
            px += 0.5
            vals.append((px, px + 0.4, px - 0.3, px + 0.3))
        candles = make_candles(vals)
        obs = analysis.order_blocks(candles, 2)
        self.assertTrue(any(o["kind"] == "bullish" for o in obs))

    def test_liquidity_sweep(self):
        # flat range first, then a deeper low, then the sweep above it
        vals = []
        px = 100.0
        for i in range(24):
            d = 0.3 if i % 2 == 0 else -0.3
            vals.append((px, px + 0.5, px - 0.5, px + d))
            px += d
        prior_low = px - 0.9
        # establish the range low
        vals.append((px, px + 0.3, prior_low, prior_low + 0.4))
        px = prior_low + 0.4
        # sweep: pierce the prior low, close back above it, high stays below prior highs
        vals.append((px + 0.2, px + 0.4, prior_low - 0.8, prior_low + 0.6))
        vals.append((prior_low + 0.7, prior_low + 1.0, prior_low + 0.5, prior_low + 0.9))
        candles = make_candles(vals)
        sweep = analysis.liquidity_sweep(candles)
        self.assertTrue(sweep["sell_side"], "sell-side sweep should be detected")
        self.assertIsNotNone(sweep["level"])

    def test_premium_discount(self):
        candles = uptrend_candles(60, base=100)
        pd = analysis.premium_discount(candles)
        self.assertIn(pd["zone"], ("premium", "equilibrium", "discount"))

    def test_patterns_engulfing(self):
        vals = [(100, 100.5, 99.5, 100.0),
                (100.0, 100.4, 99.6, 99.7),       # bearish
                (99.5, 101.0, 99.4, 100.8)]       # bullish engulfing
        candles = make_candles(vals)
        patterns = analysis.candle_patterns(candles)
        self.assertIn("ENGULFING_BULL", patterns)

    def test_kill_zone(self):
        self.assertEqual(analysis.kill_zone("2026-11-19 08:30:00"), "LONDON")
        self.assertEqual(analysis.kill_zone("2026-11-19 14:00:00"), "NEW_YORK")
        self.assertIsNone(analysis.kill_zone("2026-11-19 03:00:00"))


if __name__ == "__main__":
    unittest.main()
