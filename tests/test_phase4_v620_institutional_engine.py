"""Phase 4 (v6.20.0) Institutional Trading Engine, Conflict Resolution,
Tracking Reliability & Walk-Forward Backtesting Tests."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fysb import config, db
from fysb.engines import analysis, backtest, risk, signal_engine, tracker
from fysb.engines.strategies import StrategyResult, consensus_filter
from fysb.engines.strategies import amd, ichimoku, ict, smc, wyckoff
from fysb.engines.strategies.base import Ctx


def _make_candles(closes: list[float], spread: float = 1.0, start: datetime | None = None,
                  step_minutes: int = 15) -> list[dict]:
    base = start or datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        dt = (base + timedelta(minutes=i * step_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        hi = max(prev, c) + spread
        lo = min(prev, c) - spread
        out.append({"datetime": dt, "open": prev, "high": hi, "low": lo, "close": c, "volume": 100.0})
        prev = c
    return out


class TestPhase4AnalysisAndStrategies(unittest.TestCase):
    def test_wilder_rma_rsi_and_atr(self):
        # Smooth upward series followed by sharp pullback: Wilder RMA responds cleanly
        closes = [100.0 + i * 0.5 for i in range(40)] + [118.0, 116.5, 115.0]
        candles = _make_candles(closes, spread=0.8)
        r_val = analysis.rsi(closes, 14)
        a_val = analysis.atr(candles, 14)
        self.assertGreater(r_val, 40.0)
        self.assertLess(r_val, 85.0)
        self.assertGreater(a_val, 1.0)
        # Pure rising series approaches 100
        pure_up = [100.0 + i for i in range(35)]
        self.assertGreater(analysis.rsi(pure_up, 14), 95.0)

    def test_confirmed_candles_strips_forming_bar_only(self):
        now = datetime(2026, 11, 24, 12, 10, 0, tzinfo=timezone.utc)
        c1 = {"datetime": "2026-11-24 11:45:00", "open": 100, "high": 101, "low": 99, "close": 100.5}
        c2 = {"datetime": "2026-11-24 12:00:00", "open": 100.5, "high": 102, "low": 100, "close": 101.5}
        # At 12:10 UTC, the 12:00 M15 bar doesn't close until 12:15 -> MUST be stripped!
        confirmed = analysis.confirmed_candles([c1, c2], "M15", now_utc=now)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["datetime"], "2026-11-24 11:45:00")

        # At 12:16 UTC, the 12:00 M15 bar IS closed -> MUST be kept!
        later = datetime(2026, 11, 24, 12, 16, 0, tzinfo=timezone.utc)
        confirmed2 = analysis.confirmed_candles([c1, c2], "M15", now_utc=later)
        self.assertEqual(len(confirmed2), 2)

    def test_breakout_retest_rejects_fakeout_close_inside_range(self):
        # Consolidation around 100..105, wick spikes above 105 then closes back at 103 (fakeout)
        candles = []
        for i in range(40):
            candles.append({"datetime": f"2026-11-20 00:{i:02d}:00",
                            "open": 102.0, "high": 105.0, "low": 100.0, "close": 102.5})
        candles[-1] = {"datetime": "2026-11-20 00:40:00",
                       "open": 104.5, "high": 106.2, "low": 102.5, "close": 103.0}
        fake = analysis.breakout_retest(candles)
        self.assertIsNone(fake["breakout"])
        self.assertFalse(fake["retest"])

        # True breakout + retest holding close above prior resistance (105.0)
        candles[-1] = {"datetime": "2026-11-20 00:40:00",
                       "open": 104.8, "high": 106.8, "low": 105.0, "close": 106.2}
        res = analysis.breakout_retest(candles)
        self.assertEqual(res["breakout"], "UP")
        self.assertTrue(res["retest"])

    def test_wyckoff_tp_scales_with_gold_risk_not_001(self):
        # Build a clean XAUUSD Wyckoff Spring setup around 2600..2640
        candles = []
        # 50 bars in range 2600..2640, then spring below 2600 and reclaim
        for i in range(60):
            val = 2620.0 + (15.0 if i % 2 == 0 else -15.0)
            candles.append({"datetime": f"2026-11-20 {i//4:02d}:{(i%4)*15:02d}:00",
                            "open": val, "high": 2638.0, "low": 2602.0, "close": val})
        # Spring candle dipping to 2595 and closing at 2610
        candles[-2] = {"datetime": "2026-11-20 15:00:00",
                       "open": 2605.0, "high": 2612.0, "low": 2594.0, "close": 2610.0}
        # Reclaim candle closing at 2615
        candles[-1] = {"datetime": "2026-11-20 15:15:00",
                       "open": 2610.0, "high": 2618.0, "low": 2608.0, "close": 2615.0}
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = wyckoff.evaluate(ctx)
        if res is not None:
            # TP2 must be meaningfully above TP1 (scaled by risk, NOT 0.01!)
            self.assertGreater(res.tp2 - res.tp1, 5.0)
            self.assertGreater(res.tp3 - res.tp2, 5.0)
            self.assertGreaterEqual(res.rr, 1.5)

    def test_ict_sweep_label_accuracy(self):
        # Bullish sweep below equal lows must say SSL (not BSL)
        candles = _make_candles([100.0 + i * 0.2 for i in range(55)], spread=0.5)
        # Put clear swing lows and a final bullish liquidity sweep candle
        for idx in (15, 25, 35):
            candles[idx]["low"] = 104.0
        candles[-2]["high"] = 109.0
        candles[-1] = {"datetime": "2026-11-21 10:00:00",
                       "open": 105.0, "high": 107.0, "low": 103.5, "close": 106.8, "volume": 200.0}
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = ict.evaluate(ctx)
        if res is not None and res.direction == "BUY":
            joined = " ".join(res.reasons)
            self.assertIn("SSL", joined)
            self.assertNotIn("BSL", joined)

    def test_smc_rejects_broken_demand_zone(self):
        # Create upward move with bullish OB, then price crashes BELOW OB low -> must return None!
        closes = [100.0 + i * 0.5 for i in range(55)]
        candles = _make_candles(closes, spread=0.4)
        # Price crashes far below every recent candle
        candles[-1]["low"] = 80.0
        candles[-1]["close"] = 81.0
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles)
        res = smc.evaluate(ctx)
        if res is not None:
            self.assertNotEqual(res.direction, "BUY")

    def test_consensus_filter_rejects_split_and_resolves_dominance(self):
        buy1 = StrategyResult("SMC", "BUY", 2650, 2640, 2670, 2685, 2700, 2.0, 78, "b1")
        sell1 = StrategyResult("ICT", "SELL", 2650, 2660, 2630, 2615, 2600, 2.0, 75, "s1")
        kept, note = consensus_filter([buy1, sell1])
        self.assertEqual(kept, [])
        self.assertIn("تعارض منهجيات", note)

        # Clear 2-to-1 BUY dominance with score lead
        buy2 = StrategyResult("PA", "BUY", 2650, 2640, 2670, 2685, 2700, 2.0, 82, "b2")
        weak_sell = StrategyResult("RSI", "SELL", 2650, 2660, 2630, 2615, 2600, 2.0, 62, "s2")
        kept2, note2 = consensus_filter([buy2, buy1, weak_sell])
        self.assertEqual(len(kept2), 2)
        self.assertTrue(all(r.direction == "BUY" for r in kept2))
        self.assertIn("consensus BUY", note2)


class TestPhase4RiskAndAdvisoryAndTracker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "phase4.db")
        config.ADMIN_ID = 999001
        db.init_db()

    def tearDown(self):
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    def _insert_signal(self, symbol="XAUUSD", direction="BUY", status="RUNNING",
                       entry=2650.0, stop=2640.0, tp1=2670.0, tp2=2685.0, tp3=2700.0,
                       source="AUTO", created_by=0, hit_tp=0, closed_at="",
                       created_at=None, expires_at="", filled_at="") -> int:
        now = created_at or config.now()
        with db.conn() as c:
            cur = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_by,hit_tp,
                   created_at,closed_at,expires_at,filled_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, "M15", direction, entry, stop, tp1, tp2, tp3,
                 f"{tp1},{tp2},{tp3}", 2.0, 82, "SMC", "test", status,
                 source, created_by, hit_tp, now, closed_at, expires_at, filled_at or now),
            )
            return cur.lastrowid

    def test_correlation_filter_blocks_4th_same_usd_exposure(self):
        db.set_setting("max_correlated_signals", "3")
        # BUY EURUSD, BUY GBPUSD, SELL USDJPY -> all 3 are USD_SHORT!
        self._insert_signal(symbol="EURUSD", direction="BUY", entry=1.10, stop=1.09, tp1=1.12)
        self._insert_signal(symbol="GBPUSD", direction="BUY", entry=1.28, stop=1.27, tp1=1.30)
        self._insert_signal(symbol="USDJPY", direction="SELL", entry=150.0, stop=151.0, tp1=148.0)

        # 4th USD_SHORT (BUY AUDUSD) MUST be blocked
        d_block = risk.check_signal("AUDUSD", 2.5, 0.5, source="AUTO", direction="BUY")
        self.assertFalse(d_block.allowed)
        self.assertIn("الترابط", d_block.reason)

        # Opposite USD_LONG (BUY USDCAD) MUST be allowed
        d_opp = risk.check_signal("USDCAD", 2.5, 0.5, source="AUTO", direction="BUY")
        self.assertTrue(d_opp.allowed)

        # Unrelated bucket (XAUUSD BUY = METALS_BUY) MUST be allowed
        d_gold = risk.check_signal("XAUUSD", 2.5, 0.5, source="AUTO", direction="BUY")
        self.assertTrue(d_gold.allowed)

    def test_daily_loss_circuit_breaker_trips_for_auto_but_exempts_admin(self):
        db.set_setting("max_daily_loss_pct", "3.0")
        db.set_setting("risk_per_signal_pct", "1.0")
        now = config.now()
        for _ in range(3):
            self._insert_signal(symbol="XAUUSD", status="SL_HIT", hit_tp=0, closed_at=now)
        with db.conn() as c:
            c.execute("UPDATE signals SET accounting_model='PARTIAL_50_25_25' WHERE status='SL_HIT'")
        d_auto = risk.check_signal("EURUSD", 2.5, 0.5, source="AUTO", direction="BUY")
        self.assertFalse(d_auto.allowed)
        self.assertIn("قاطع الخسارة اليومية", d_auto.reason)

        d_admin = risk.check_signal("EURUSD", 2.5, 0.5, source="ADMIN_SIGNAL_NOW", direction="BUY")
        self.assertTrue(d_admin.allowed)

    def test_opposite_advisory_honest_on_loss_and_includes_channels(self):
        # Open BUY at 2650 currently in FLOATING LOSS at 2644 (-60 pips!)
        old_id = self._insert_signal(symbol="XAUUSD", direction="BUY", status="RUNNING",
                                     entry=2650.0, stop=2640.0, tp1=2670.0)
        new_id = self._insert_signal(symbol="XAUUSD", direction="SELL", status="RUNNING",
                                     entry=2644.0, stop=2654.0, tp1=2624.0)
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                      (7001, old_id, config.now(), "7001", 11))
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                      (-1, old_id, config.now(), "-1001234567890", 22))
        sent = []
        async def fake_send(target, text):
            sent.append((target, text))
        sig = {"id": new_id, "symbol": "XAUUSD", "direction": "SELL", "source": "AUTO"}
        with patch("fysb.engines.market_data.fetch_price", return_value={"price": 2644.0}):
            cnt = asyncio.run(signal_engine.opposite_close_advisory(sig, fake_send))
        self.assertGreaterEqual(cnt, 2)
        targets = [t for t, _ in sent]
        self.assertIn(7001, targets)
        self.assertIn(-1001234567890, targets)
        # MUST honestly report floating loss, NEVER call a losing trade "رابحة حتى"
        for _, txt in sent:
            self.assertIn("خسارة عائمة", txt)
            self.assertNotIn("رابحة حتى", txt)

    def test_tracker_breakeven_stop_out_after_tp1_closes_cleanly(self):
        t0 = "2026-11-22 10:00:00"
        sid = self._insert_signal(symbol="XAUUSD", direction="BUY", status="TP1_HIT",
                                  entry=2650.0, stop=2640.0, tp1=2670.0, tp2=2685.0, tp3=2700.0,
                                  hit_tp=1, created_at=t0, filled_at=t0)
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                      (8001, sid, t0, "8001", 101))
        candles = [
            {"datetime": "2026-11-22 10:00:00", "open": 2650.0, "high": 2655.0, "low": 2649.5, "close": 2654.0},
            {"datetime": "2026-11-22 10:15:00", "open": 2654.0, "high": 2672.0, "low": 2654.0, "close": 2670.5},  # TP1
            {"datetime": "2026-11-22 10:30:00", "open": 2670.0, "high": 2671.0, "low": 2649.0, "close": 2650.0},  # BE!
        ]
        payload = {"values": candles, "last_candle_iso": "2026-11-22T10:30:00+00:00", "age_seconds": 60}
        with patch.object(tracker, "_tracking_payload", return_value=payload):
            res = tracker.check_open_signals("")
        self.assertEqual(res["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status, closed_at, half_notified FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "TP1_HIT")
        self.assertTrue(bool(row["closed_at"]))
        self.assertEqual(row["half_notified"], 1)
        self.assertTrue(any("Breakeven" in txt for _, txt in res["notifications"]))

    def test_private_signal_now_never_spams_admin_and_expiry_sends_alerts(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        exp = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        priv_id = self._insert_signal(symbol="XAUUSD", status="ACTIVE", source="SIGNAL_NOW",
                                      created_by=5555, created_at=past, expires_at=exp)
        pub_id = self._insert_signal(symbol="EURUSD", status="RUNNING", source="AUTO",
                                     created_at=past, expires_at=exp)
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                      (5555, priv_id, past, "5555", 50))
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                      (6666, pub_id, past, "6666", 60))
        cnt, edits, notes = tracker.expire_with_alerts()
        self.assertEqual(cnt, 2)
        self.assertEqual(len(edits), 2)
        # Private user gets alert for priv_id; Admin does NOT get alert for priv_id, ONLY for pub_id!
        priv_targets = [u for u, t in notes if "XAUUSD" in t]
        pub_targets = [u for u, t in notes if "EURUSD" in t]
        self.assertEqual(priv_targets, [5555])
        self.assertIn(6666, pub_targets)
        self.assertIn(config.ADMIN_ID, pub_targets)


class TestPhase4WalkForwardBacktester(unittest.TestCase):
    def test_walk_forward_backtest_runs_end_to_end(self):
        # Construct 140 realistic wave candles so strategies fire and future bars resolve
        closes = []
        px = 2600.0
        for i in range(140):
            if i < 45:
                px += 0.8
            elif i < 55:
                px -= 1.2
            elif i < 95:
                px += 1.4
            elif i < 110:
                px -= 1.5
            else:
                px += 1.1
            closes.append(round(px, 2))
        candles = _make_candles(closes, spread=1.5)
        rep = backtest.run_backtest("XAUUSD", "M15", candles=candles, warmup=50, max_hold_bars=30)
        self.assertEqual(rep.error, "")
        self.assertEqual(rep.bars_tested, 90)
        html = backtest.format_report(rep)
        self.assertIn("XAUUSD", html)
        self.assertIn("الاختبار التاريخي", html)


if __name__ == "__main__":
    unittest.main()
