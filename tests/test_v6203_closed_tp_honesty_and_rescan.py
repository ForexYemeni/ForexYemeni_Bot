"""v6.20.3 Regression Tests — Closed TP1/TP2 Profit Card Honesty, Live-Quote BE
Close, Open-Signal Filtering & Immediate Rescan After Close."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fysb import config, db
from fysb.engines import market_data, signal_engine, tracker
from fysb.engines.strategies import StrategyResult
from fysb.handlers import user_pages
from fysb.services import reports
from fysb.ui.cards import render_signal_card


class TestV6203ClosedTpHonestyAndRescan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "v6203.db")
        config.ADMIN_ID = 999001
        db.init_db()

    def tearDown(self):
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    def test_exact_owner_xauusd_tp1_be_close_card_shows_closed_profit_not_negative_drift(self):
        """Reproduces exact live owner report: XAUUSD BUY @ 3625.52, TP1 @ 3655.24,
        price drops to 3583.40 -> MUST close at BE, render CLOSED PROFIT badge,
        NEVER show 'سعر السوق الآن -421.2 pips', and vanish from live signals."""
        t0 = "2026-11-24 11:01:32"
        with db.conn() as c:
            sid = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,hit_tp,pips_result,
                   created_at,filled_at,closed_at)
                   VALUES('XAUUSD','M15','BUY',3625.52,3610.66,3655.24,3670.10,3692.40,
                   '3655.24,3670.10,3692.40',2.0,95,'ICT','test','TP1_HIT','AUTO',1,297.2,
                   ?,?, '')""",
                (t0, t0),
            ).lastrowid
            c.execute(
                "INSERT INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id) VALUES(?,?,?,?,?)",
                (88001, sid, t0, "88001", 701),
            )

        # Even if the 5m candle window starts AFTER TP1 was hit and only sees price at 3583.40
        candles = [
            {"datetime": "2026-11-24 15:15:00", "open": 3590.0, "high": 3592.0, "low": 3582.0, "close": 3585.0},
            {"datetime": "2026-11-24 15:20:00", "open": 3585.0, "high": 3586.0, "low": 3581.0, "close": 3583.40},
        ]
        payload = {"values": candles, "last_candle_iso": "2026-11-24T15:20:00+00:00", "age_seconds": 60}
        with patch.object(tracker, "_tracking_payload", return_value=payload):
            res = tracker.check_open_signals("")

        self.assertEqual(res["stats"]["updated"], 1)
        with db.conn() as c:
            sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
        self.assertEqual(sig["status"], "TP1_HIT")
        self.assertTrue(bool(sig["closed_at"]))

        # Edited card MUST say مغلقة على ربح and NEVER show negative floating drift!
        self.assertTrue(len(res["edits"]) >= 1)
        edited_card = res["edits"][0][3]
        self.assertIn("مغلقة على ربح", edited_card)
        self.assertIn("مغلقة نهائياً على ربح محقق", edited_card)
        self.assertNotIn("سعر السوق الآن", edited_card)
        self.assertNotIn("-421", edited_card)
        self.assertNotIn("تحقق الهدف الأول — مأمونة", edited_card)

        # Direct render_signal_card also clearly shows CLOSED profit + وقت الإغلاق
        direct = render_signal_card(sig, user_view=True)
        self.assertIn("مغلقة على ربح", direct)
        self.assertIn("وقت الإغلاق", direct)

        # MUST NO LONGER appear as an open/live signal in user signals or reports!
        live = user_pages._live_signals_page(["XAUUSD"], ["M15"], ["ICT"], 60)
        self.assertEqual(live, [])
        demo_open = user_pages._open_signals_rows()
        self.assertEqual(demo_open, [])
        self.assertEqual(reports.dashboard()["signals"]["open"], 0)

    def test_closed_tp1_signal_does_not_block_new_signal_for_36_hours(self):
        """Once a trade closed >15 min ago, engine MUST resume searching and
        issue a new setup on the same symbol+direction+TF instead of DUPLICATE."""
        old_created = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        old_closed = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        with db.conn() as c:
            c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,hit_tp,pips_result,
                   created_at,filled_at,closed_at)
                   VALUES('XAUUSD','M15','BUY',3625.52,3610.66,3655.24,3670.10,3692.40,
                   '3655.24,3670.10,3692.40',2.0,95,'ICT','old','TP1_HIT','AUTO',1,297.2,
                   ?,?,?)""",
                (old_created, old_created, old_closed),
            )

        base = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
        vals = [
            {"datetime": (base + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": 3559.0 + 0.4 * i, "high": 3562.0 + 0.4 * i,
             "low": 3558.0 + 0.4 * i, "close": 3560.0 + 0.4 * i, "volume": 100.0}
            for i in range(60)
        ]
        last_px = vals[-1]["close"]
        payload = {"values": vals, "last_candle_iso": "2026-11-24T15:20:00+00:00", "age_seconds": 30}
        cand = StrategyResult("ICT", "BUY", last_px, last_px - 13.0, last_px + 26.0, last_px + 39.0, last_px + 58.5, 2.0, 88, ["fresh setup"])
        with patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "is_stale", return_value=False), \
             patch.object(market_data, "market_state", return_value=True), \
             patch.object(market_data, "fetch_price", return_value={"price": last_px}), \
             patch.object(signal_engine, "evaluate_all", return_value=[cand]):
            out = signal_engine.generate_signal("XAUUSD", "M15")

        self.assertEqual(out["status"], "SIGNAL_CREATED")
        self.assertIsNotNone(out["signal_id"])


if __name__ == "__main__":
    unittest.main()
