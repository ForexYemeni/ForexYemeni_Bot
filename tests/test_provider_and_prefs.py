"""Provider-chain control (PRICE_PROVIDER) + personal strategy delivery gate.

These tests are PURE — no network, no real credentials. They lock the honest
contract added in this build:

  1. config exposes PRICE_PROVIDER / BINANCE_BASE_URL / BYBIT_BASE_URL.
  2. The default hidden chains stay intact; a preferred provider REORDERS the
     chain only (never removes the failover net), unknown values are ignored,
     and GOLD_API can never enter a candle chain (it serves quotes only).
  3. A user's profile strategy preference ("الاستراتيجية المفضلة") is a REAL
     delivery gate in the auto-signal pipeline, not a cosmetic label.
"""
from fysb import config
from fysb.engines import market_data as md
from fysb.engines.signal_engine import passes_personal_filters

import unittest


class ConfigSurfaceTests(unittest.TestCase):
    def test_provider_control_variables_exist(self):
        # Test .env (helpers.py) sets none of them -> documented defaults.
        self.assertEqual(config.PRICE_PROVIDER, "")
        self.assertEqual(config.BINANCE_BASE_URL, "https://api.binance.com")
        self.assertEqual(config.BYBIT_BASE_URL, "https://api.bybit.com")

    def test_symbol_classes(self):
        self.assertEqual(md._class_of("XAUUSD"), "gold")
        self.assertEqual(md._class_of("XAGUSD"), "metal")
        self.assertEqual(md._class_of("EURUSD"), "fx")
        self.assertEqual(md._class_of("BTCUSD"), "crypto")
        self.assertEqual(md._class_of("SOLUSD"), "crypto")
        self.assertEqual(md._class_of("PAXGUSDT"), "crypto")
        self.assertIsNone(md._class_of("UNKNOWNPAIR"))

    def test_default_chains_preserve_failover(self):
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["crypto"], "", True),
            ["binance", "bybit", "twelvedata"])
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["crypto"], "", False),
            ["binance", "bybit"])
        # v8.14.0: fx-ref (XAUUSD=X real spot) joined the gold candle chain
        self.assertEqual(md._ordered_chain(md._CANDLE_CHAINS["gold"], "", False),
                         ["venue-gold", "fx-ref"])
        self.assertEqual(md._ordered_chain(md._QUOTE_CHAINS["gold"], "", False),
                         ["spot-ref", "venue-gold"])
        self.assertEqual(md._ordered_chain(md._QUOTE_CHAINS["metal"], "", False),
                         ["spot-ref"])


class PreferredProviderTests(unittest.TestCase):
    def test_preference_moves_provider_first(self):
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["crypto"], "BYBIT", False),
            ["bybit", "binance"])
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["crypto"], "BINANCE", True),
            ["binance", "bybit", "twelvedata"])

    def test_alias_resolution(self):
        self.assertEqual(
            md._ordered_chain(md._QUOTE_CHAINS["gold"], "GOLDAPI", False),
            ["spot-ref", "venue-gold"])
        self.assertEqual(
            md._ordered_chain(md._QUOTE_CHAINS["gold"], "PAXG", False),
            ["venue-gold", "spot-ref"])
        self.assertEqual(
            md._ordered_chain(md._QUOTE_CHAINS["fx"], "YAHOO", False),
            ["fx-ref"])

    def test_unknown_value_is_ignored(self):
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["crypto"], "NOT_A_PROVIDER", False),
            ["binance", "bybit"])

    def test_goldapi_never_serves_candles(self):
        # Honest rule: the spot reference has no candles anywhere.
        # v8.14.0: fx-ref (XAUUSD=X real spot) joined the gold candle chain
        self.assertEqual(
            md._ordered_chain(md._CANDLE_CHAINS["gold"], "GOLDAPI", True),
            ["venue-gold", "fx-ref", "twelvedata"])


class _U(dict):
    """sqlite3.Row-like accessor used by the filter (dict supports [])."""


class PersonalStrategyGateTests(unittest.TestCase):
    def _u(self, **kw):
        base = {"market": "", "timeframe": "", "strategy": "", "min_score": 0}
        base.update(kw)
        return _U(base)

    def test_strategy_preference_is_enforced(self):
        u = self._u(strategy="ICT")
        self.assertTrue(passes_personal_filters(u, "XAU/USD", "M15", "ICT", 80))
        self.assertFalse(passes_personal_filters(u, "XAU/USD", "M15", "SMC", 80))

    def test_empty_or_all_means_every_methodology(self):
        self.assertTrue(passes_personal_filters(self._u(), "XAU/USD", "M15", "SMC", 80))
        self.assertTrue(passes_personal_filters(self._u(strategy="ALL"), "XAU/USD", "M15", "CRT", 80))

    def test_market_and_timeframe_gates_still_hold(self):
        u = self._u(market="XAU/USD", timeframe="M15")
        self.assertTrue(passes_personal_filters(u, "XAUUSD", "M15", "ICT", 80))
        self.assertFalse(passes_personal_filters(u, "BTCUSD", "M15", "ICT", 80))
        self.assertFalse(passes_personal_filters(u, "XAUUSD", "H1", "ICT", 80))

    def test_min_score_floor(self):
        u = self._u(min_score=70)
        self.assertTrue(passes_personal_filters(u, "XAUUSD", "M15", "ICT", 70))
        self.assertFalse(passes_personal_filters(u, "XAUUSD", "M15", "ICT", 69))

    def test_combined_gates(self):
        u = self._u(market="BTCUSD", timeframe="H1", strategy="SMC", min_score=60)
        self.assertTrue(passes_personal_filters(u, "BTC/USD", "H1", "SMC", 75))
        # One failing gate is enough to reject.
        self.assertFalse(passes_personal_filters(u, "BTCUSD", "H1", "ICT", 75))
        self.assertFalse(passes_personal_filters(u, "BTCUSD", "H1", "SMC", 59))


if __name__ == "__main__":
    unittest.main()
