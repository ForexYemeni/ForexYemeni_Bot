"""Tests for v6.19.0:
1. Multi-candidate gate fallback in signal_engine.generate_signal (stale entry & low RR).
2. Intact-zone / non-drawdown entry enforcement in ICT, SILVER_BULLET, and CRT.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fysb import config, db
from fysb.engines import market_data, signal_engine
from fysb.engines.strategies import StrategyResult, crt, ict, silver_bullet
from fysb.engines.strategies.base import Ctx


def _candles(n=60, start_px=3600.0, step=0.3, start_dt=None):
    base = start_dt or datetime.now(timezone.utc) - timedelta(minutes=15 * n)
    out = []
    px = start_px
    for i in range(n):
        dt = (base + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S")
        o = px
        c = px + step
        h = max(o, c) + 1.5
        lo = min(o, c) - 1.5
        out.append({"datetime": dt, "open": o, "high": h, "low": lo, "close": c, "volume": 100.0})
        px = c
    return out


class TestV619CandidateFallbackAndIntactZones(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = config.DB_FILE
        config.DB_FILE = Path(self._tmp.name) / "v619.db"
        db.init_db()
        with db.conn() as c:
            c.execute("UPDATE market_catalog SET enabled=1, verified=1 WHERE symbol='XAUUSD'")
            for st in ("ICT", "SMC", "PRICE_ACTION", "CRT", "SILVER_BULLET"):
                c.execute("UPDATE strategy_catalog SET enabled=1, verified=1 WHERE code=?", (st,))

    def tearDown(self):
        config.DB_FILE = self._orig_db
        self._tmp.cleanup()

    def _fresh_payload(self, close_px=3618.05):
        cs = _candles(60, start_px=close_px - 18.0, step=0.3)
        cs[-1]["close"] = close_px
        cs[-1]["high"] = close_px + 1.0
        cs[-1]["low"] = close_px - 1.0
        return {
            "values": cs,
            "last_candle_iso": datetime.now(timezone.utc).isoformat(),
            "age_seconds": 10.0,
            "source": "LIVE",
        }

    def test_stale_top_candidate_falls_back_to_live_runner_up(self):
        # Exact user-reported scenario: #1 ICT is stale (entry=3633.03, live=3618.05 -> 60% passed),
        # while #2 SMC has a fresh live entry right at 3618.05!
        stale_ict = StrategyResult(
            code="ICT", direction="BUY", entry=3633.03, stop=3608.06,
            tp1=3683.0, tp2=3708.0, tp3=3745.0, rr=2.0, score=92.0,
            reasons=["stale ICT OB"], checks={},
        )
        fresh_smc = StrategyResult(
            code="SMC", direction="BUY", entry=3618.05, stop=3605.0,
            tp1=3645.0, tp2=3658.0, tp3=3678.0, rr=2.07, score=84.0,
            reasons=["fresh SMC demand"], checks={},
        )
        payload = self._fresh_payload(3618.05)
        with patch.object(market_data, "market_state", return_value=True), \
             patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "fetch_price", return_value={"price": 3618.05}), \
             patch.object(signal_engine, "evaluate_all", return_value=[stale_ict, fresh_smc]):
            res = signal_engine.generate_signal(
                "XAUUSD", "M15", False, ["ICT", "SMC"], "ADMIN_SIGNAL_NOW", 9001
            )

        self.assertEqual(res["status"], "SIGNAL_CREATED", f"Expected fallback to SMC, got: {res}")
        with db.conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (res["signal_id"],)).fetchone()
            fb = c.execute("SELECT * FROM engine_runs WHERE result='CANDIDATE_FALLBACK'").fetchone()
        self.assertEqual(sig["strategy"], "SMC")
        self.assertAlmostEqual(sig["entry"], 3618.05, places=2)
        self.assertIsNotNone(fb, "fallback must be recorded in engine_runs")

    def test_low_rr_top_candidate_falls_back_to_valid_rr_runner_up(self):
        low_rr = StrategyResult(
            code="ICT", direction="BUY", entry=3618.05, stop=3608.05,
            tp1=3630.05, tp2=3640.0, tp3=3650.0, rr=1.2, score=91.0,
            reasons=["low RR"], checks={},
        )
        good_rr = StrategyResult(
            code="PRICE_ACTION", direction="BUY", entry=3618.05, stop=3608.05,
            tp1=3643.05, tp2=3653.0, tp3=3668.0, rr=2.5, score=82.0,
            reasons=["valid RR"], checks={},
        )
        payload = self._fresh_payload(3618.05)
        with patch.object(market_data, "market_state", return_value=True), \
             patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "fetch_price", return_value={"price": 3618.05}), \
             patch.object(signal_engine, "evaluate_all", return_value=[low_rr, good_rr]):
            res = signal_engine.generate_signal(
                "XAUUSD", "M15", False, ["ICT", "PRICE_ACTION"], "ADMIN_SIGNAL_NOW", 9001
            )

        self.assertEqual(res["status"], "SIGNAL_CREATED")
        with db.conn() as c:
            sig = c.execute("SELECT strategy FROM signals WHERE id=?", (res["signal_id"],)).fetchone()
        self.assertEqual(sig["strategy"], "PRICE_ACTION")

    def test_all_candidates_stale_still_rejects_honestly(self):
        stale1 = StrategyResult(
            code="ICT", direction="BUY", entry=3633.03, stop=3608.06,
            tp1=3683.0, tp2=3708.0, tp3=3745.0, rr=2.0, score=90.0,
            reasons=["stale 1"], checks={},
        )
        stale2 = StrategyResult(
            code="CRT", direction="BUY", entry=3635.0, stop=3608.0,
            tp1=3689.0, tp2=3710.0, tp3=3740.0, rr=2.0, score=83.0,
            reasons=["stale 2"], checks={},
        )
        payload = self._fresh_payload(3618.05)
        with patch.object(market_data, "market_state", return_value=True), \
             patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "fetch_price", return_value={"price": 3618.05}), \
             patch.object(signal_engine, "evaluate_all", return_value=[stale1, stale2]):
            res = signal_engine.generate_signal(
                "XAUUSD", "M15", False, ["ICT", "CRT"], "ADMIN_SIGNAL_NOW", 9001
            )
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("stale entry", res["reason"])

    def test_ict_never_picks_broken_zone_above_close_for_buy(self):
        cs = _candles(60, start_px=3600.0, step=0.2)
        cs[-1]["close"] = 3618.05
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=cs, bias_candles=cs)
        broken_fvg = [{"kind": "bullish", "lo": 3630.0, "hi": 3636.0}]
        with patch.object(ict, "fair_value_gaps", return_value=broken_fvg), \
             patch.object(ict, "order_blocks", return_value=[]):
            res = ict.evaluate(ctx)
        if res is not None and res.direction == "BUY":
            self.assertLessEqual(res.entry, cs[-1]["close"],
                                 "ICT BUY entry must NEVER sit above close on a broken FVG")

    def test_silver_bullet_rejects_broken_fvg(self):
        # Timestamp inside NY AM Silver Bullet window (15:00 UTC)
        sb_end = datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc)
        cs = _candles(60, start_px=3600.0, step=0.2, start_dt=sb_end - timedelta(minutes=15 * 59))
        cs[-1]["close"] = 3618.05
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=cs, bias_candles=cs)
        broken_fvg = [{"kind": "bullish", "lo": 3630.0, "hi": 3636.0}]
        with patch.object(silver_bullet, "fair_value_gaps", return_value=broken_fvg):
            res = silver_bullet.evaluate(ctx)
        self.assertIsNone(res, "SILVER_BULLET must return None when only FVG is already broken")

    def test_crt_rejects_broken_range_and_never_places_buy_above_close(self):
        cs = _candles(60, start_px=3600.0, step=0.2)
        # Reference candle cs[-2]: range 3620..3640, mid=3630
        cs[-2]["low"], cs[-2]["high"] = 3620.0, 3640.0
        # Prior candle swept low and closed back inside
        cs[-3]["low"], cs[-3]["close"] = 3618.0, 3625.0
        # Current close broke BELOW range low (3615 < 3620) -> MUST return None!
        cs[-1]["close"] = 3615.0
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=cs, bias_candles=cs)
        self.assertIsNone(crt.evaluate(ctx), "CRT BUY must be invalidated when close < r_lo")

        # Current close inside lower half of range (3628 < mid=3630) -> entry MUST NOT exceed close!
        cs[-1]["close"] = 3628.0
        res = crt.evaluate(ctx)
        if res is not None and res.direction == "BUY":
            self.assertLessEqual(res.entry, 3628.0, "CRT BUY entry must never sit above close")


if __name__ == "__main__":
    unittest.main()
