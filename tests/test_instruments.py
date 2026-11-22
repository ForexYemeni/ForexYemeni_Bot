"""Unit tests: instruments (pip/point/P&L/RR/position sizing)."""
import unittest

from tests.helpers import *  # noqa
from fysb import instruments


class TestInstruments(unittest.TestCase):
    def test_forex_pip_vs_point(self):
        # 5-digit forex: 1 pip = 0.0001, 1 point = 0.00001
        d = instruments.pips("EURUSD", 1.1000, 1.1050)
        self.assertAlmostEqual(d, 50.0)
        pts = instruments.points("EURUSD", 1.1000, 1.1050)
        self.assertAlmostEqual(pts, 500.0)

    def test_jpy_pip(self):
        d = instruments.pips("USDJPY", 155.00, 155.50)
        self.assertAlmostEqual(d, 50.0)  # JPY pip = 0.01

    def test_gold_pip(self):
        # Gold: pip = 0.1 → 3500.00 → 3501.00 = 10 pips
        d = instruments.pips("XAUUSD", 3500.0, 3501.0)
        self.assertAlmostEqual(d, 10.0)

    def test_crypto_pip(self):
        d = instruments.pips("BTCUSD", 100000, 100500)
        self.assertAlmostEqual(d, 500.0)  # BTC pip = 1.0

    def test_formatting_digits(self):
        self.assertEqual(instruments.fmt_price("EURUSD", 1.10001), "1.10001")
        self.assertEqual(instruments.fmt_price("XAUUSD", 3500.5), "3,500.50")
        self.assertEqual(instruments.fmt_price("USDJPY", 155.123), "155.123")

    def test_position_size_gold(self):
        # balance 10k, risk 1% = 100; SL distance 5.0 on gold; contract 100
        lots = instruments.position_size(10000, 1.0, 3500.0, 3495.0, "XAUUSD")
        self.assertAlmostEqual(lots, 0.2, places=2)

    def test_position_size_forex(self):
        # risk 100 USD, distance 0.0050, contract 100000 → 0.2 lots
        lots = instruments.position_size(10000, 1.0, 1.1000, 1.0950, "EURUSD")
        self.assertAlmostEqual(lots, 0.2, places=2)

    def test_pnl_buy(self):
        # gold buy 1 lot (100 oz), entry 3500 exit 3502 → +200
        pnl = instruments.pnl("XAUUSD", "BUY", 1.0, 3500.0, 3502.0)
        self.assertAlmostEqual(pnl, 200.0)

    def test_pnl_sell(self):
        pnl = instruments.pnl("EURUSD", "SELL", 0.1, 1.1000, 1.0950)
        self.assertAlmostEqual(pnl, 50.0)

    def test_rr(self):
        self.assertAlmostEqual(instruments.rr_ratio(3500, 3495, 3515, "BUY"), 3.0)
        self.assertAlmostEqual(instruments.rr_ratio(3500, 3505, 3485, "SELL"), 3.0)
        # invalid direction → 0
        self.assertEqual(instruments.rr_ratio(3500, 3495, 3490, "BUY"), 0.0)
        self.assertEqual(instruments.rr_ratio(3500, 3500, 3510, "BUY"), 0.0)

    def test_normalize(self):
        self.assertEqual(instruments.normalize("xau/usd"), "XAUUSD")


if __name__ == "__main__":
    unittest.main()
