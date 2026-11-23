"""Signal lifecycle regression suite — the «نشطة للأبد» bug.

Root cause (fixed here): tracker compared provider candle datetimes
("YYYY-MM-DD HH:MM:SS", space separator) against created_at[:19] from
config.now() ("YYYY-MM-DDTHH:MM:SS", 'T' separator). Since ' ' < 'T' in ASCII,
EVERY candle was filtered out — no signal was ever evaluated, SL/TP never
recorded, signals stayed ACTIVE forever.

Covers:
  * _utc_cutoff normalization (T/Z/offset/space/empty forms)
  * tracker evaluates candles AFTER a 'T'-format created_at (regression)
  * tracker still ignores candles BEFORE creation (no fabricated outcomes)
  * expiry sweep also retires legacy rows with EMPTY expires_at
  * bulk purge of CANCELLED/EXPIRED signals through the dispatcher
  * analysis/signal cards carry NO educational disclaimer (paid luxury copy)
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.helpers import MockBot, MockQuery, MockContext
from fysb import db, config
from fysb.engines import tracker
from fysb.handlers import dispatcher
from fysb.ui import cards


ADMIN = 9001


def fire(data: str, uid: int):
    q = MockQuery(data, uid)
    ctx = MockContext()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def _candles(start_utc: datetime, n: int, hi: float, lo: float):
    """Space-separated provider-style candle datetimes."""
    out = []
    for i in range(n):
        t = start_utc + timedelta(minutes=5 * i)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": (hi + lo) / 2, "high": hi, "low": lo,
                    "close": (hi + lo) / 2, "volume": 1000 + i})
    return out


class TestUtcCutoff(unittest.TestCase):
    def test_iso_t_offset(self):
        self.assertEqual(tracker._utc_cutoff("2026-11-23T00:41:33.123456+00:00"),
                         "2026-11-23 00:41:33")

    def test_iso_z(self):
        self.assertEqual(tracker._utc_cutoff("2026-11-23T00:41:33Z"),
                         "2026-11-23 00:41:33")

    def test_space_format_passthrough(self):
        self.assertEqual(tracker._utc_cutoff("2026-11-23 00:41:33"),
                         "2026-11-23 00:41:33")

    def test_non_utc_converted(self):
        # +03:00 (Mecca) 03:41 == 00:41 UTC
        self.assertEqual(tracker._utc_cutoff("2026-11-23T03:41:00+03:00"),
                         "2026-11-23 00:41:00")

    def test_empty_is_safe_min(self):
        self.assertEqual(tracker._utc_cutoff(""), "0000-01-01 00:00:00")
        self.assertEqual(tracker._utc_cutoff(None), "0000-01-01 00:00:00")


class TestTrackerEvaluationRegression(unittest.TestCase):
    """The exact user scenario: SELL XAUUSD created yesterday (T-format), price
    rallied far beyond the stop — the tracker MUST record SL_HIT now."""

    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM engine_runs")
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                         VALUES('XAUUSD','SELL','M15',3581.60,3592.91,3558.97,3547.66,3530.69,
                         '3558.97,3547.66,3530.69',2.0,95,'SMC','ctx test','ACTIVE','AUTO',0,?,
                         'LIVE','2026-11-22 20:00:00','{}',?,'',0.0)""",
                      ((datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()))

    def _payload(self):
        # candles starting 19h ago (after creation 20h ago): SL 3592.91 breached
        start = datetime.now(timezone.utc) - timedelta(hours=19)
        return {"symbol": "XAUUSD", "interval": "5min",
                "values": _candles(start, 60, 3607.0, 3570.0),
                "fetched_at": config.now(), "last_candle_iso":
                    (datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S"),
                "age_seconds": 30.0, "source": "LIVE"}

    def test_sl_hit_recorded_from_t_format_created_at(self):
        payload = self._payload()
        with patch.object(tracker.market_data, "fetch_candles", return_value=payload):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["checked"], 1)
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals").fetchone()
        self.assertEqual(row["status"], "SL_HIT")
        self.assertTrue(row["pips_result"] < 0)          # SELL stopped out
        # admin informed — as a REAL message in notifications (Arabic label),
        # not the dead admin_notice string no loop ever sent
        admin_lines = [t for uid, t in result["notifications"] if uid == ADMIN]
        self.assertTrue(admin_lines)
        self.assertIn("ضرب وقف الخسارة", admin_lines[0])
        self.assertNotIn("SL_HIT (", admin_lines[0])

    def test_candles_before_creation_ignored(self):
        # all candles BEFORE creation -> nothing evaluated, status unchanged
        start = datetime.now(timezone.utc) - timedelta(hours=30)
        payload = {"symbol": "XAUUSD", "interval": "5min",
                   "values": _candles(start, 60, 3607.0, 3570.0),
                   "fetched_at": config.now(), "last_candle_iso": start.strftime("%Y-%m-%d %H:%M:%S"),
                   "age_seconds": 30.0, "source": "LIVE"}
        with patch.object(tracker.market_data, "fetch_candles", return_value=payload):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 0)
        self.assertEqual(result["stats"]["skipped_stale"], 1)
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "ACTIVE")

    def test_tp1_recorded_for_buy(self):
        with db.conn() as c:
            c.execute("UPDATE signals SET direction='BUY', stop=3570.0, tp1=3604.0, tp2=3626.0, tp3=3648.0")
        start = datetime.now(timezone.utc) - timedelta(hours=19)
        payload = {"symbol": "XAUUSD", "interval": "5min",
                   "values": _candles(start, 40, 3610.0, 3575.0),
                   "fetched_at": config.now(),
                   "last_candle_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                   "age_seconds": 30.0, "source": "LIVE"}
        with patch.object(tracker.market_data, "fetch_candles", return_value=payload):
            result = tracker.check_open_signals("")
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals").fetchone()
        self.assertEqual(row["status"], "TP1_HIT")
        self.assertTrue(row["pips_result"] > 0)

    def test_sl_before_tp_is_sl_hit(self):
        """Chronology honesty: stop touched FIRST -> SL_HIT even if a target is
        crossed later (a dead trade is never re-branded as a winner)."""
        sig = {"id": 9, "symbol": "XAUUSD", "direction": "SELL", "timeframe": "M15",
               "entry": 3581.60, "stop": 3592.91, "tp1": 3558.97, "tp2": 3547.66, "tp3": 3530.69,
               "rr": 2.0, "score": 95, "status": "ACTIVE", "hit_tp": 0}
        base = datetime(2026, 11, 22, 12, 0, 0)
        c1 = {"datetime": "2026-11-22 12:00:00", "open": 3580.0, "high": 3596.0, "low": 3575.0, "close": 3590.0}  # SL
        c2 = {"datetime": "2026-11-22 12:05:00", "open": 3550.0, "high": 3556.0, "low": 3540.0, "close": 3545.0}  # TP1+TP2 later
        self.assertEqual(tracker._evaluate_signal(sig, [c1, c2]), "SL_HIT")

    def test_tp_first_keeps_best_progress(self):
        """Target banked first -> later stop touch does not downgrade (best-progress)."""
        sig = {"id": 9, "symbol": "XAUUSD", "direction": "SELL", "timeframe": "M15",
               "entry": 3581.60, "stop": 3592.91, "tp1": 3558.97, "tp2": 3547.66, "tp3": 3530.69,
               "rr": 2.0, "score": 95, "status": "ACTIVE", "hit_tp": 0}
        c1 = {"datetime": "2026-11-22 12:00:00", "open": 3555.0, "high": 3560.0, "low": 3540.0, "close": 3542.0}  # TP1+TP2
        c2 = {"datetime": "2026-11-22 12:05:00", "open": 3570.0, "high": 3595.0, "low": 3568.0, "close": 3590.0}  # SL later
        self.assertEqual(tracker._evaluate_signal(sig, [c1, c2]), "TP2_HIT")


class TestLegacyExpirySweep(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")

    def test_empty_expires_at_old_signal_expires(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with db.conn() as c:
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                         VALUES('XAUUSD','SELL','M15',3581.6,3592.91,3558.97,3547.66,3530.69,
                         't',2.0,90,'SMC','x','ACTIVE','AUTO',0,?,'LIVE','t','{}','',0.0,0.0)""", (old,))
        n = tracker.expire_stale_signals()
        self.assertEqual(n, 1)
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "EXPIRED")

    def test_fresh_signal_survives_sweep(self):
        with db.conn() as c:
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                         VALUES('XAUUSD','SELL','M15',3581.6,3592.91,3558.97,3547.66,3530.69,
                         't',2.0,90,'SMC','x','ACTIVE','AUTO',0,?,'LIVE','t','{}','',0.0,0.0)""",
                      (config.now(),))
        n = tracker.expire_stale_signals()
        self.assertEqual(n, 0)
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "ACTIVE")


class TestBulkPurgeThroughDispatcher(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
        # one CANCELLED + one EXPIRED + one ACTIVE (must survive)
        ids = []
        for status in ("CANCELLED", "EXPIRED", "ACTIVE"):
            with db.conn() as c:
                cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                             targets,rr,score,strategy,reason,status,source,created_by,created_at,
                             data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi)
                             VALUES('XAUUSD','SELL','M15',3581.6,3592.91,3558.97,3547.66,3530.69,
                             't',2.0,90,'SMC','x',?,'AUTO',0,?,'LIVE','t','{}','',0.0,0.0)""",
                          (status, config.now()))
                ids.append(cur.lastrowid)
        with db.conn() as c:
            for sid in ids:
                c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                          (sid, 555999, config.now()))

    def test_purge_confirm_then_delete(self):
        q, _ = fire("a:signalpurge", ADMIN)
        body = str(q.message.replies)
        self.assertIn("تنظيف", body)
        self.assertIn("2", body)
        q2, _ = fire("a:signalpurgego", ADMIN)
        self.assertIn("تم التنظيف", str(q2.message.replies))
        with db.conn() as c:
            rows = c.execute("SELECT id,status FROM signals ORDER BY id").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "ACTIVE")
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM signal_deliveries").fetchone()["n"], 1)

    def test_purge_when_clean(self):
        with db.conn() as c:
            c.execute("DELETE FROM signals WHERE status IN ('CANCELLED','EXPIRED')")
        q, _ = fire("a:signalpurge", ADMIN)
        self.assertIn("نظيف", str(q.message.replies))


class NoDisclaimerCards(unittest.TestCase):
    """Paid royal product: no educational/informational disclaimers on cards."""

    def test_signal_card_has_no_disclaimer(self):
        sig = {"id": 1, "symbol": "XAUUSD", "direction": "SELL", "timeframe": "M15",
               "entry": 3581.6, "stop": 3592.91, "tp1": 3558.97, "tp2": 3547.66, "tp3": 3530.69,
               "rr": 2.0, "score": 95, "strategy": "SMC", "reason": "ctx", "status": "ACTIVE",
               "created_at": config.now(), "data_time": "2026-11-22 23:45:00", "pips_result": 0}
        text = cards.render_signal_card(sig, user_view=True)
        self.assertNotIn("تحليل تعليمي", text)
        self.assertNotIn("ليس نصيحة استثمارية", text)
        self.assertNotIn("لأغراض المعلومات فقط", text)

    def test_analysis_card_has_no_disclaimer(self):
        an = {"symbol": "XAUUSD", "timeframe": "M15", "price": 3584.1, "price_time": "2026-11-22 23:45:00",
              "direction": "SELL", "quality": 95, "summary": ["قراءة"], "zones": ["منطقة"],
              "activation": "تفعيل", "invalidation": "3592", "risk_note": "التزم بإدارة المخاطر"}
        text = cards.render_analysis_card(an)
        self.assertNotIn("تحليل تعليمي", text)
        self.assertNotIn("ليس أمر تنفيذ", text)
        self.assertIn("إدارة المخاطر", text)


class TestEntryFillActivation(unittest.TestCase):
    """The exact user-reported bug: price traded through the entry (SELL 3581.60,
    market at 3575) yet the card stayed «🟢 نشطة — بانتظار التنفيذ» forever.

    Fix: candle-confirmed entry fill transitions ACTIVE -> RUNNING («نشطة — قيد
    التنفيذ»); RUNNING signals keep tracking toward TP/SL like before."""

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
                      ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()))
            self.sid = cur.lastrowid

    def _payload(self, hi: float, lo: float):
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        return {"symbol": "XAUUSD", "interval": "5min",
                "values": _candles(start, 40, hi, lo),
                "fetched_at": config.now(), "last_candle_iso":
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "age_seconds": 30.0, "source": "LIVE"}

    def test_entry_crossed_becomes_running(self):
        """SELL entry 3581.60, price trades down to 3575 (entry crossed, TP1 NOT
        reached) -> status must become RUNNING, not stay «بانتظار التنفيذ»."""
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals").fetchone()
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["pips_result"], 0.0)        # not a result yet — trade is live
        self.assertTrue(result["notifications"])         # receivers notified once

    def test_running_not_reannounced_on_second_pass(self):
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 0)  # no duplicate activation
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "RUNNING")

    def test_entry_never_touched_stays_active(self):
        """SELL whose entry was never traded (price below entry from the start,
        above TP1) honestly stays ACTIVE — no fabricated execution."""
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3580.0, 3576.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 0)
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT status FROM signals").fetchone()["status"], "ACTIVE")

    def test_running_then_tp1_hits(self):
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")
        with db.conn() as c:
            c.execute("UPDATE signals SET half_notified=1")
        # later window reaches TP1 3558.97
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3560.0, 3550.0)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals").fetchone()
        self.assertEqual(row["status"], "TP1_HIT")
        self.assertTrue(row["pips_result"] > 0)

    def test_running_notification_is_luxury_arabic(self):
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                      (self.sid, 555001, config.now()))
        payload = self._payload(3582.0, 3575.0)
        with patch.object(tracker.market_data, "fetch_candles", return_value=payload):
            result = tracker.check_open_signals("")
        user_text = result["notifications"][0][1]     # deliveries first, admin note last
        self.assertIn("قيد التنفيذ", user_text)
        self.assertIn("السعر عند التفعيل", user_text)
        self.assertIn("pips", user_text)

    def test_running_row_tracked_by_expiry_sweep(self):
        with patch.object(tracker.market_data, "fetch_candles", return_value=self._payload(3582.0, 3575.0)):
            tracker.check_open_signals("")
        old = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with db.conn() as c:
            c.execute("UPDATE signals SET created_at=?, expires_at=''", (old,))
        self.assertEqual(tracker.expire_stale_signals(), 1)  # RUNNING rows expire too


class PlainTextSignalButtons(unittest.TestCase):
    """Telegram inline buttons are PLAIN TEXT — HTML tags rendered literally as
    «رموز برمجية» in the admin signals list (user screenshot bug)."""

    def test_compact_button_has_no_html_tags(self):
        sig = {"id": 4, "symbol": "ETHUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 3000.0, "stop": 3010.0, "tp1": 3020.0, "tp2": 3040.0, "tp3": 3060.0,
               "rr": 2.0, "score": 92, "strategy": "CRT", "status": "ACTIVE"}
        text = cards.render_signal_compact(sig)
        for tag in ("<b>", "</b>", "<code>", "</code>", "<i>", "</i>", "<u>", "</u>"):
            self.assertNotIn(tag, text)
        self.assertIn("ETHUSD", text)
        self.assertIn("#4", text)

    def test_compact_button_all_statuses(self):
        sig = {"id": 1, "symbol": "XAUUSD", "direction": "SELL", "timeframe": "M15",
               "entry": 3581.6, "stop": 3592.9, "tp1": 3558.9, "tp2": 3547.6, "tp3": 3530.6,
               "rr": 2.0, "score": 95, "strategy": "SMC", "status": "RUNNING"}
        text = cards.render_signal_compact(sig)
        self.assertNotIn("<b>", text)
        self.assertNotIn("</b>", text)
        self.assertIn("قيد التنفيذ", text)
        self.assertIn("المال الذكي", text)


if __name__ == "__main__":
    unittest.main()
