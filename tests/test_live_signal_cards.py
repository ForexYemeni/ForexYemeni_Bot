"""LIVE SIGNAL CARDS — the user's ORIGINAL message is the board.

User complaints this suite pins shut (v6.7.0):
  1. «الإشارة مازالت تظهر نشطة بانتظار التنفيذ» — the transition fired and the
     💧 halfway advisory arrived, yet the FIRST message the user received kept
     its emission-day text forever. Root: deliveries stored no chat/message
     refs, so nothing could ever be edited. Fix: refs are pinned at delivery
     and every transition re-renders that exact card via edit_message_text.
  2. «📡 إشارة #3 XAUUSD: RUNNING (+0.0 pips)» — the admin notice showed a raw
     machine token and a meaningless zero while the trade was ~+110 pips in
     profit. Fix: Arabic status + price at activation + signed drift pips.
  3. Signed pips everywhere: a SELL trading below entry shows +pips; above
     entry shows -pips. An absolute distance can never fake a profit.
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import tests.helpers  # noqa: F401  — MUST precede fysb: pins DOTENV_PATH to an isolated temp SQLite env
from fysb import db, config
from fysb.engines import tracker
from fysb.engines.signal_engine import record_delivery_refs


ADMIN = 9001


def _candles(start_utc: datetime, n: int, hi: float, lo: float):
    out = []
    for i in range(n):
        t = start_utc + timedelta(minutes=5 * i)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": (hi + lo) / 2, "high": hi, "low": lo,
                    "close": (hi + lo) / 2, "volume": 1000 + i})
    return out


class _Msg:
    """Minimal stand-in for a Telegram Message object."""

    def __init__(self, chat_id: int, message_id: int):
        self.chat_id = chat_id
        self.message_id = message_id


class LiveCardBase(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM engine_runs")
            cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                         VALUES('XAUUSD','SELL','M15',3581.60,3592.91,3558.97,3547.66,3530.69,
                         '3558.97,3547.66,3530.69',2.0,95,'SMC','ctx','ACTIVE','AUTO',0,?,
                         'LIVE','2026-11-22 20:00:00','{}',?,'',0.0)""",
                      ((datetime.now(timezone.utc) - timedelta(minutes=55)).isoformat(),
                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()))
            self.sid = cur.lastrowid
            # delivery WITH editable refs — the card the user actually holds
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at,chat_id,message_id)"
                      " VALUES(?,?,?,?,?)", (self.sid, 555001, config.now(), "555000", 777))
        self._admin_patcher = patch.object(config, "ADMIN_ID", ADMIN)
        self._admin_patcher.start()
        self.addCleanup(self._admin_patcher.stop)

    def _payload(self, hi: float, lo: float) -> dict:
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        return {"symbol": "XAUUSD", "interval": "5min",
                "values": _candles(start, 40, hi, lo),
                "fetched_at": config.now(),
                "last_candle_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "age_seconds": 30.0, "source": "LIVE"}


class TestTransitionEditsOriginalCard(LiveCardBase):
    def test_entry_cross_edits_the_held_card(self):
        """SELL 3581.60, candles close 3578.50 → RUNNING + the original card is
        re-rendered with the new status and a live price line."""
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        self.assertEqual(len(result["edits"]), 1)
        uid, chat_id, message_id, text = result["edits"][0]
        self.assertEqual((uid, chat_id, message_id), (555001, "555000", 777))
        self.assertIn("نشطة — قيد التنفيذ في السوق", text)   # new status on the card
        self.assertNotIn("بانتظار التنفيذ", text)            # old status GONE
        self.assertIn("سعر السوق الآن", text)                # live pulse line
        self.assertIn("3,578.50", text)                      # the actual close
        self.assertIn("+31.0 pips", text)                    # (3581.60-3578.50)/0.1 in-favour
        with db.conn() as c:
            row = c.execute("SELECT status,live_px FROM signals WHERE id=?", (self.sid,)).fetchone()
        self.assertEqual(row["status"], "RUNNING")
        self.assertAlmostEqual(row["live_px"], 3578.50, places=6)

    def test_admin_notice_arabic_with_real_drift(self):
        """The admin must NEVER again see «RUNNING (+0.0 pips)» while the trade
        is in profit — Arabic label + the true drift from entry."""
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3570.0)):
            result = tracker.check_open_signals("")
        admin_texts = [t for uid, t in result["notifications"] if uid == ADMIN]
        # this pass fires BOTH the activation notice AND the halfway advisory —
        # filter for the activation one
        running_admin = [t for t in admin_texts if "قيد التنفيذ" in t]
        self.assertEqual(len(running_admin), 1)
        self.assertNotIn("RUNNING", running_admin[0])
        self.assertNotIn("+0.0 pips", running_admin[0])
        self.assertIn("3,576.00", running_admin[0])
        self.assertIn("+56.0 pips", running_admin[0])    # (3581.60-3576.0)/0.1

    def test_deliveries_without_refs_never_break(self):
        """Legacy rows (pre-refs) simply produce no edit — no crash, no spam."""
        with db.conn() as c:
            c.execute("UPDATE signal_deliveries SET chat_id='', message_id=0")
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        self.assertEqual(result["edits"], [])
        # DB state still advanced honestly
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "RUNNING")

    def test_tp1_hit_edits_card_with_banked_result(self):
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")
        with db.conn() as c:
            c.execute("UPDATE signals SET half_notified=1")
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3560.0, 3550.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        self.assertEqual(len(result["edits"]), 1)
        text = result["edits"][0][3]
        self.assertIn("تحقق الهدف الأول", text)
        self.assertIn("+226.3 pips", text)               # (3581.60-3558.97)/0.1
        self.assertNotIn("بانتظار التنفيذ", text)


class TestLivePulseRefresh(LiveCardBase):
    def test_first_pulse_edits_then_quiet_until_meaningful_move(self):
        """Already-RUNNING signal: an unchanged price does NOT re-edit; a ≥3-pip
        travel (0.3 USD on gold) re-renders the card with the fresh close."""
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")               # ACTIVE -> RUNNING (+pulse, px 3578.50)
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.5)):
            result = tracker.check_open_signals("")      # close 3578.75 → delta 2.5 pips < 3 → silence
        self.assertEqual(result["stats"]["refreshed"], 0)
        self.assertEqual(result["edits"], [])
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3574.0)):
            result = tracker.check_open_signals("")      # close 3578.00 → delta 5 pips ≥ 3 → pulse
        self.assertEqual(result["stats"]["refreshed"], 1)
        self.assertEqual(len(result["edits"]), 1)
        self.assertIn("3,578.00", result["edits"][0][3])
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3574.0)):
            result = tracker.check_open_signals("")      # same price → silence again
        self.assertEqual(result["stats"]["refreshed"], 0)
        self.assertEqual(result["edits"], [])

    def test_refresh_updates_data_time(self):
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")
        iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload = self._payload(3582.0, 3574.0)
        payload["last_candle_iso"] = iso
        with patch.object(tracker.market_data, "fetch_candles", return_value=payload):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["refreshed"], 1)
        with db.conn() as c:
            row = c.execute("SELECT data_time,live_px FROM signals WHERE id=?", (self.sid,)).fetchone()
        self.assertEqual(row["data_time"], iso)
        self.assertAlmostEqual(row["live_px"], 3578.0, places=6)


class TestSignedDrift(unittest.TestCase):
    def test_sell_below_entry_is_profit(self):
        self.assertEqual(tracker.drift_pips("XAUUSD", "SELL", 3581.60, 3570.60), 110.0)

    def test_sell_above_entry_is_loss(self):
        self.assertEqual(tracker.drift_pips("XAUUSD", "SELL", 3581.60, 3585.60), -40.0)

    def test_buy_above_entry_is_profit(self):
        self.assertEqual(tracker.drift_pips("XAUUSD", "BUY", 3581.60, 3592.60), 110.0)

    def test_buy_below_entry_is_loss(self):
        self.assertEqual(tracker.drift_pips("XAUUSD", "BUY", 3581.60, 3577.60), -40.0)


class TestDeliveryRefsPinning(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                         VALUES('XAUUSD','BUY','M15',3500.0,3480.0,3540.0,3560.0,3580.0,
                         '3540.0,3560.0,3580.0',2.0,90,'ICT','ctx','ACTIVE','AUTO',0,?,
                         'LIVE','2026-11-22 20:00:00','{}',?,'',0.0)""",
                      (config.now(),
                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()))
            self.sid = cur.lastrowid
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                      (self.sid, 555002, config.now()))

    def test_refs_pinned_on_delivery(self):
        record_delivery_refs(555002, self.sid, _Msg(-100222, 4242))
        with db.conn() as c:
            row = c.execute("SELECT chat_id,message_id FROM signal_deliveries").fetchone()
        self.assertEqual(row["chat_id"], "-100222")
        self.assertEqual(row["message_id"], 4242)

    def test_none_message_is_a_safe_noop(self):
        record_delivery_refs(555002, self.sid, None)
        with db.conn() as c:
            row = c.execute("SELECT chat_id,message_id FROM signal_deliveries").fetchone()
        self.assertEqual(row["chat_id"], "")
        self.assertEqual(row["message_id"], 0)

    def test_message_without_chat_id_is_a_safe_noop(self):
        record_delivery_refs(555002, self.sid, object())   # no chat_id/message_id attrs
        with db.conn() as c:
            row = c.execute("SELECT chat_id,message_id FROM signal_deliveries").fetchone()
        self.assertEqual(row["chat_id"], "")


class TestExpiryCardEdits(LiveCardBase):
    def test_expired_signal_edits_its_own_card(self):
        """An expired signal's original message must stop claiming
        «بانتظار التنفيذ» — the held card is re-rendered as EXPIRED."""
        old = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with db.conn() as c:
            c.execute("UPDATE signals SET created_at=?, expires_at='' WHERE id=?",
                      (old, self.sid))
        expired, edits = tracker.expire_with_card_edits()
        self.assertEqual(expired, 1)
        self.assertEqual(len(edits), 1)
        uid, chat_id, message_id, text = edits[0]
        self.assertEqual((uid, chat_id, message_id), (555001, "555000", 777))
        self.assertIn("منتهية الصلاحية", text)
        self.assertNotIn("بانتظار التنفيذ", text)
        self.assertNotIn("سعر السوق الآن", text)   # expiry makes no price claims
        with db.conn() as c:
            row = c.execute("SELECT status,closed_at FROM signals WHERE id=?", (self.sid,)).fetchone()
        self.assertEqual(row["status"], "EXPIRED")
        self.assertTrue(row["closed_at"])

    def test_fresh_signal_never_expires(self):
        expired, edits = tracker.expire_with_card_edits()
        self.assertEqual((expired, edits), (0, []))


class TestSchemaUpgrade(unittest.TestCase):
    def test_additive_columns_exist(self):
        db.init_db()
        with db.conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(signal_deliveries)").fetchall()}
            sig_cols = {r[1] for r in c.execute("PRAGMA table_info(signals)").fetchall()}
        self.assertIn("chat_id", cols)
        self.assertIn("message_id", cols)
        self.assertIn("live_px", sig_cols)


if __name__ == "__main__":
    unittest.main()
