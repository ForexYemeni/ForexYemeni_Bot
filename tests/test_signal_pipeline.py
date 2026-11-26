"""Signal pipeline tests — with mocked provider: rejections, duplicates, storage."""
import unittest
from unittest.mock import patch

from tests.helpers import *
from fysb import db
from fysb.engines import market_data, signal_engine
from fysb.services import users as users_service


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "sig"
        self.full_name = "Signal User"


def _valid_payload(n=120):
    from tests.helpers import uptrend_candles
    candles = uptrend_candles(n)
    return {"symbol": "XAU/USD", "interval": "15min", "values": candles,
            "fetched_at": db.now(), "fetched_epoch": 0, "last_candle_iso": candles[-1]["datetime"],
            "age_seconds": 30}


class TestPipeline(unittest.TestCase):
    uid = 555200

    def setUp(self):
        db.init_db()
        db.set_setting("news_guard_enabled", "0")
        try:
            users_service.ensure_user(FakeUser(self.uid))
        except Exception:
            pass
        db.set_setting("global_kill_switch", "0")
        # Freeze the session calendar OPEN so pipeline semantics are tested
        # deterministically regardless of the real UTC day (market gate itself
        # is covered separately in test_royal_upgrade.TestMarketState).
        patcher = patch.object(market_data, "market_state", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Ensure the catalog gate allows the pipeline: enable M15 + a strategy
        with db.conn() as c:
            c.execute("UPDATE timeframe_catalog SET enabled=1,verified=1 WHERE code='M15'")
            c.execute("UPDATE strategy_catalog SET enabled=1,verified=1 WHERE code='PRICE_ACTION'")
            c.execute("UPDATE market_catalog SET enabled=1,verified=1 WHERE symbol='XAUUSD'")

    def tearDown(self):
        db.set_setting("global_kill_switch", "1")

    def test_kill_switch_blocks_everything(self):
        db.set_setting("global_kill_switch", "1")
        res = signal_engine.generate_signal("XAUUSD")
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("kill switch", res["reason"])

    def test_stale_data_rejected(self):
        with patch.object(market_data, "fetch_candles", return_value={**_valid_payload(), "age_seconds": 999999}):
            res = signal_engine.generate_signal("XAUUSD")
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("stale", res["reason"])

    def test_provider_failure_logged_not_silent(self):
        with patch.object(market_data, "fetch_candles", side_effect=RuntimeError("provider down")):
            res = signal_engine.generate_signal("XAUUSD")
        self.assertEqual(res["status"], "ERROR")
        self.assertEqual(res["reason"], "provider down")
        with db.conn() as c:
            r = c.execute("SELECT result FROM engine_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(r["result"], "DATA_ERROR", "failure must be recorded in engine_runs")

    def test_duplicate_rejected(self):
        # The Real-Entry gate needs a live quote; in this suite the candles are
        # synthetic — freeze the quote to the payload's own close (no network)
        # Bypass entry/geometry gates: synthetic trend prices are not a gold
        # setup; this test isolates duplicate semantics, not stop sizing.
        payload = _valid_payload()
        with patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "fetch_price", side_effect=RuntimeError("no quote in tests")), \
             patch.object(signal_engine, "is_stale_entry", return_value=(False, "")), \
             patch.object(signal_engine, "validate_signal_geometry", return_value=(True, "")), \
             patch("fysb.engines.intelligence.dxy_bias_check", return_value={"delta": 0, "note": ""}), \
             patch("fysb.engines.signal_engine._allowed_strategies", return_value=["PRICE_ACTION"]):
            with db.conn() as c:
                c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
                             strategy,reason,status,source,created_by,created_at)
                             VALUES('XAUUSD','BUY','M15',100,95,110,115,120,'',2,70,'PRICE_ACTION','test dup','ACTIVE','AUTO',0,?)""",
                          (db.now(),))
            res = signal_engine.generate_signal("XAUUSD")
        self.assertEqual(res["status"], "DUPLICATE")

    def test_no_setup_not_manufactured(self):
        # flat noise data never yields a setup
        vals = []
        px = 100.0
        for i in range(120):
            d = 0.2 if i % 2 == 0 else -0.2
            vals.append((px, px + 0.3, px - 0.3, px + d))
            px += d
        payload = {**_valid_payload(), "values": [{"datetime": f"2026-11-19 {8 + i // 60:02d}:{i % 60:02d}:00",
                                                   "open": o, "high": h, "low": l, "close": c}
                                                  for i, (o, h, l, c) in enumerate(vals)]}
        with patch.object(market_data, "fetch_candles", return_value=payload), \
             patch("fysb.engines.signal_engine._allowed_strategies", return_value=["PRICE_ACTION"]):
            res = signal_engine.generate_signal("XAUUSD")
        self.assertEqual(res["status"], "NO_SETUP")

    def test_manual_signal_validation_rules(self):
        # reimplemented rules check (the handler uses the same constraints)
        entry, sl, tp1 = 100.0, 95.0, 110.0
        rr = instruments.rr_ratio(entry, sl, tp1, "BUY")
        self.assertGreaterEqual(rr, 2.0)
        bad = instruments.rr_ratio(100.0, 95.0, 101.0, "BUY")
        self.assertLess(bad, 2.0)


from fysb import instruments  # noqa: E402

if __name__ == "__main__":
    unittest.main()
