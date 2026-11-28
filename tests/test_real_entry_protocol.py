"""REAL-ENTRY PROTOCOL — the «الإشارة ترسل بعد المرور من نقطة الدخول» bug.

User verdict (v6.7.0, signal #5): a BUY XAUUSD was published with entry
3,586.04 while the market stood at 3,614.90 (+288.6 pips ABOVE the entry —
49.6% of the whole entry→TP1 leg already consumed) and the card still printed
«🟢 نشطة — بانتظار التنفيذ» with a 1:2.00 R:R the market no longer offered.
Then the SAME unfilled signal celebrated «💧 نصف الطريق نحو TP1».

Pinned shut here:
  1. PUBLISH GATE — a setup whose entry→TP1 leg is >25% consumed (pending
     side) or whose entry was blown through toward the stop (passed side) is
     a relic: REJECTED with STALE_ENTRY, never stored, never delivered.
  2. BIRTH TRUTH — an at-market/already-crossed entry is born RUNNING with
     filled_at + birth quote; «بانتظار التنفيذ» is impossible for an
     executed trade from the very first card.
  3. LIFECYCLE DISCIPLINE — an UNFILLED signal earns nothing: no TP banking,
     no halfway celebration; reaching TP1 without ever touching the entry
     KILLS the idea (CANCELLED — «فات الدخول», zero result credited).
  4. FILL ANCHOR — targets are evaluated strictly from filled_at onward, so
     a pre-fill visit to TP1/half can never be banked after a late fill.
  5. LEGACY SALVAGE — pre-protocol at-market births (the stuck #3 class) are
     proven filled from the first tracked candle's open within a small band.
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import tests.helpers  # noqa: F401  — MUST precede fysb: pins DOTENV_PATH to an isolated temp SQLite env
from fysb import db, config
from fysb.engines import tracker
from fysb.engines import signal_engine
from fysb.engines.entry_state import (CHASE_MAX_RATIO, birth_pulse_line,
                                      birth_status, entry_geometry, is_stale_entry)

ADMIN = 9001


def _candles(start_utc: datetime, n: int, hi: float, lo: float, open_px: float = None):
    out = []
    o = open_px if open_px is not None else (hi + lo) / 2
    for i in range(n):
        t = start_utc + timedelta(minutes=5 * i)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": o, "high": hi, "low": lo, "close": (hi + lo) / 2,
                    "volume": 1000 + i})
    return out


def _payload(values, interval="5min"):
    return {"symbol": "XAUUSD", "interval": interval, "values": values,
            "fetched_at": config.now(),
            "last_candle_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "age_seconds": 30.0, "source": "LIVE"}


def _insert_signal(direction="BUY", entry=3586.04, stop=3556.95,
                   tp1=3644.24, tp2=3660.07, tp3=3719.55,
                   minutes_ago=120, status="ACTIVE", filled_at="", live_px=0.0):
    with db.conn() as c:
        cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                     targets,rr,score,strategy,reason,status,source,created_by,created_at,
                     data_source,data_time,strategy_scores,expires_at,entry_zone_lo,entry_zone_hi,
                     filled_at,live_px)
                     VALUES('XAUUSD',?,'M15',?,?,?,?,?,'t',2.0,95,'CRT','ctx',?,'AUTO',0,?,
                     'LIVE','t','{}',?,'',0.0,?,?)""",
                      (direction, entry, stop, tp1, tp2, tp3, status,
                       (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                       filled_at, live_px))
        return cur.lastrowid


class TestEntryGeometry(unittest.TestCase):
    """BUY/SELL sides of the shared Real-Entry geometry."""

    def test_buy_pending_side_and_ratio(self):
        # price ABOVE entry = waiting for the retrace down (signal #5 case)
        geo = entry_geometry("BUY", 3586.04, 3556.95, 3644.24, 3614.90)
        self.assertEqual(geo["side"], "pending")
        self.assertAlmostEqual(geo["consumed"], 28.86 / 58.20, places=6)

    def test_buy_passed_side(self):
        geo = entry_geometry("BUY", 3586.04, 3556.95, 3644.24, 3584.00)
        self.assertEqual(geo["side"], "passed")
        self.assertAlmostEqual(geo["consumed"], 2.04 / 29.09, places=6)

    def test_sell_pending_side(self):
        # price BELOW a sell entry = waiting for the retrace UP (limit semantics)
        geo = entry_geometry("SELL", 3581.60, 3592.91, 3558.97, 3575.00)
        self.assertEqual(geo["side"], "pending")
        self.assertAlmostEqual(geo["consumed"], 6.60 / 22.63, places=6)

    def test_sell_passed_side(self):
        # price ABOVE a sell entry = fill territory, consumed toward the stop
        geo = entry_geometry("SELL", 3581.60, 3592.91, 3558.97, 3585.00)
        self.assertEqual(geo["side"], "passed")
        self.assertAlmostEqual(geo["consumed"], 3.40 / 11.31, places=6)

    def test_birth_status_directions(self):
        self.assertEqual(birth_status("BUY", 3586.04, 3586.04), "RUNNING")
        self.assertEqual(birth_status("BUY", 3586.04, 3580.00), "RUNNING")
        self.assertEqual(birth_status("BUY", 3586.04, 3590.00), "ACTIVE")
        self.assertEqual(birth_status("SELL", 3581.60, 3581.60), "RUNNING")
        self.assertEqual(birth_status("SELL", 3581.60, 3585.00), "RUNNING")
        self.assertEqual(birth_status("SELL", 3581.60, 3575.00), "ACTIVE")


class TestStaleGate(unittest.TestCase):
    def test_signal5_scenario_is_stale(self):
        """The EXACT user report: chase 288.6 pips of a 582-pip TP leg."""
        stale, detail = is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, 3614.90)
        self.assertTrue(stale)
        self.assertIn("50%", detail)

    def test_at_market_is_fresh(self):
        stale, _ = is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, 3584.00)
        self.assertFalse(stale)

    def test_small_pending_chase_is_fresh(self):
        stale, _ = is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, 3592.00)
        self.assertFalse(stale)

    def test_blown_through_toward_stop_is_stale(self):
        # price already below entry, 2/3 of the way to the stop
        stale, _ = is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, 3567.00)
        self.assertTrue(stale)

    def test_exact_ratio_threshold(self):
        # 25% of the TP leg = 14.55 — just below/above the line
        px = 3586.04 + CHASE_MAX_RATIO * (3644.24 - 3586.04) - 0.01
        self.assertFalse(is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, px)[0])
        px += 0.30   # 3 pips beyond the line (floor 8 pips already exceeded)
        self.assertTrue(is_stale_entry("XAUUSD", "BUY", 3586.04, 3556.95, 3644.24, px)[0])


class _FakeCandidate:
    def __init__(self, direction="BUY", entry=3586.04, stop=3556.95,
                 tp1=3644.24, tp2=3660.07, tp3=3719.55):
        self.direction = direction
        self.entry = entry
        self.stop = stop
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp3 = tp3
        self.rr = 2.0
        self.score = 95
        self.code = "CRT"
        self.reasons = ["CRT range test"]
        self._summary = "CRT range test"

    def summary(self):
        return self._summary


class TestPublishGate(unittest.TestCase):
    """generate_signal must refuse stale setups and stamp birth truth."""

    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM engine_runs")
        self.payload = _payload(tests.helpers.uptrend_candles(80), interval="15min")

    def _run(self, live_px: float):
        with patch.object(signal_engine.market_data, "fetch_candles", return_value=self.payload), \
             patch.object(signal_engine.market_data, "fetch_price", return_value={"price": live_px}), \
             patch.object(signal_engine.market_data, "market_state", return_value=True), \
             patch.object(signal_engine, "evaluate_all", return_value=[_FakeCandidate()]), \
             patch.object(signal_engine.risk, "check_signal",
                          return_value=SimpleNamespace(allowed=True, reason="")):
            return signal_engine.generate_signal("XAUUSD", force=True)

    def _row(self):
        with db.conn() as c:
            return c.execute("SELECT * FROM signals").fetchone()

    def test_stale_setup_refused_and_never_stored(self):
        res = self._run(3614.90)   # the exact signal-#5 birth quote
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("stale", res["reason"])
        self.assertIsNone(res["signal_id"])
        with db.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"], 0)
            runs = c.execute("SELECT result FROM engine_runs WHERE result='STALE_ENTRY'").fetchall()
        self.assertTrue(runs)

    def test_at_market_birth_is_running_with_filled_at(self):
        res = self._run(3584.00)   # price already crossed the BUY entry
        self.assertEqual(res["status"], "SIGNAL_CREATED")
        row = self._row()
        self.assertEqual(row["status"], "RUNNING")
        self.assertTrue(row["filled_at"])
        self.assertAlmostEqual(row["live_px"], 3584.00, places=6)

    def test_pending_birth_is_active_and_honest(self):
        res = self._run(3592.00)   # modest retrace pending — a real limit plan
        self.assertEqual(res["status"], "SIGNAL_CREATED")
        row = self._row()
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["filled_at"], "")
        self.assertAlmostEqual(row["live_px"], 3592.00, places=6)


class TestLifecycleDiscipline(unittest.TestCase):
    """Unfilled signals earn nothing; filled ones anchor at filled_at."""

    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM engine_runs")

    def test_pending_new_protocol_signal_stays_active(self):
        """Post-epoch pending row: no false activation from the first candle's
        open, no TP banking, no halfway — honest «بانتظار التنفيذ»."""
        sid = _insert_signal(minutes_ago=0)
        vals = _candles(datetime.now(timezone.utc) - timedelta(minutes=10), 4,
                        3610.0, 3595.0, open_px=3602.5)   # never touches 3586.04
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 0)
        with db.conn() as c:
            row = c.execute("SELECT status,half_notified,filled_at FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["half_notified"], 0)
        self.assertEqual(row["filled_at"], "")

    def test_missed_setup_is_cancelled_not_a_winner(self):
        """TP1 reached without EVER touching the entry → CANCELLED (zero result),
        never TP1_HIT — an unentered trade cannot win."""
        sid = _insert_signal(minutes_ago=0)
        vals = _candles(datetime.now(timezone.utc) - timedelta(minutes=10), 4,
                        3650.0, 3590.0, open_px=3620.0)   # hi >= tp1, lo > entry
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")
        self.assertEqual(row["pips_result"], 0.0)
        admin_notes = [t for uid, t in result["notifications"] if uid == ADMIN]
        self.assertTrue(any("فات الدخول" in t for t in admin_notes))

    def test_prefill_tp_visit_never_banked_after_late_fill(self):
        """Price visited TP1 BEFORE the retrace filled → RUNNING (not TP1_HIT);
        only post-fill candles may bank targets."""
        sid = _insert_signal(minutes_ago=54)
        start = datetime.now(timezone.utc) - timedelta(minutes=55)
        vals = _candles(start, 1, 3650.0, 3600.0, open_px=3620.0)      # TP visit, no entry touch
        vals += _candles(start + timedelta(minutes=5), 3, 3605.0, 3595.0, open_px=3600.0)
        vals += _candles(start + timedelta(minutes=20), 1, 3590.5, 3580.0,
                         open_px=3586.0)                               # THE fill candle
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,filled_at FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "RUNNING")          # NOT TP1_HIT
        self.assertEqual(row["filled_at"], vals[-1]["datetime"])
        # post-fill candle touches TP1 → now it banks, from the fill onward
        vals2 = vals + _candles(start + timedelta(minutes=25), 1, 3650.0, 3590.0,
                                open_px=3590.0)
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals2)):
            tracker.check_open_signals("")
        with db.conn() as c:
            row = c.execute("SELECT status,pips_result FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "TP1_HIT")
        self.assertGreater(row["pips_result"], 0)

    def test_halfway_never_fires_before_fill_but_after(self):
        """Pre-fill visit to the half level is silence; the SAME visit after the
        fill fires the advisory exactly once."""
        sid = _insert_signal(minutes_ago=34)   # half = 3615.14
        start = datetime.now(timezone.utc) - timedelta(minutes=35)
        vals = _candles(start, 1, 3620.0, 3600.0, open_px=3610.0)      # half visit, no fill
        vals += _candles(start + timedelta(minutes=5), 2, 3605.0, 3595.0, open_px=3600.0)
        vals += _candles(start + timedelta(minutes=15), 1, 3590.5, 3580.0,
                         open_px=3586.0)                               # fill candle
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            tracker.check_open_signals("")
        with db.conn() as c:
            row = c.execute("SELECT status,half_notified FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["half_notified"], 0)           # pre-fill visit ignored
        vals2 = vals + _candles(start + timedelta(minutes=20), 1, 3625.0, 3590.0,
                                open_px=3590.0)             # post-fill half visit
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals2)):
            result = tracker.check_open_signals("")
        admin_notes = [t for uid, t in result["notifications"] if uid == ADMIN]
        self.assertTrue(any("نصف الطريق" in t for t in admin_notes))
        with db.conn() as c:
            row = c.execute("SELECT half_notified FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["half_notified"], 1)


class TestLegacySalvage(unittest.TestCase):
    """The stuck #3 class: pre-protocol at-market SELL birth, price left the
    entry forever — first tracked candle's open proves the fill."""

    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM engine_runs")
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('real_entry_protocol_since',?)",
                      (config.now(),))

    def test_legacy_at_market_sell_activated(self):
        sid = _insert_signal(direction="SELL", entry=3581.60, stop=3592.91,
                             tp1=3558.97, tp2=3547.66, tp3=3530.69, minutes_ago=55)
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at,chat_id,message_id)"
                      " VALUES(?,?,?,?,?)", (sid, 555123, config.now(), "555000", 888))
        # open 3581.2 sits INSIDE the at-market band; high never reaches 3581.60
        vals = _candles(datetime.now(timezone.utc) - timedelta(minutes=60), 4,
                        3581.4, 3577.2, open_px=3581.2)
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 1)
        with db.conn() as c:
            row = c.execute("SELECT status,filled_at FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["filled_at"], vals[1]["datetime"])
        admin_notes = [t for uid, t in result["notifications"] if uid == ADMIN]
        self.assertTrue(any("قيد التنفيذ" in t for t in admin_notes))

    def test_legacy_pending_retrace_row_untouched(self):
        """A genuine pre-protocol pending row (open far on the waiting side)
        must NOT be branded RUNNING by the salvage heuristic."""
        sid = _insert_signal(direction="SELL", entry=3581.60, stop=3592.91,
                             tp1=3558.97, tp2=3547.66, tp3=3530.69, minutes_ago=55)
        vals = _candles(datetime.now(timezone.utc) - timedelta(minutes=60), 4,
                        3580.0, 3576.0, open_px=3578.0)   # 36 pips below entry
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            result = tracker.check_open_signals("")
        self.assertEqual(result["stats"]["updated"], 0)
        with db.conn() as c:
            row = c.execute("SELECT status,filled_at FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["filled_at"], "")

    def test_post_protocol_row_never_heuristic_activated(self):
        """A post-epoch ACTIVE row whose first candle sits inside the band is
        still pending — the heuristic is legacy-only by design."""
        sid = _insert_signal(direction="SELL", entry=3581.60, stop=3592.91,
                             tp1=3558.97, tp2=3547.66, tp3=3530.69, minutes_ago=0)
        vals = _candles(datetime.now(timezone.utc) - timedelta(minutes=10), 4,
                        3581.4, 3576.0, open_px=3581.2)
        with patch.object(tracker.market_data, "fetch_candles", return_value=_payload(vals)):
            tracker.check_open_signals("")
        with db.conn() as c:
            row = c.execute("SELECT status FROM signals WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row["status"], "ACTIVE")


class TestBirthPulse(unittest.TestCase):
    """The delivered card states the market truth at birth — from the stored
    birth quote, with zero extra network calls."""

    def test_running_birth_pulse(self):
        sig = {"symbol": "XAUUSD", "direction": "BUY", "entry": 3586.04,
               "status": "RUNNING", "live_px": 3584.00}
        line = birth_pulse_line(sig)
        self.assertIn("سعر السوق عند الإصدار", line)
        self.assertIn("قيد التنفيذ", line)
        self.assertIn("-20.4 pips", line)   # long at 3586.04, market 3584 → underwater, honest

    def test_pending_birth_pulse(self):
        sig = {"symbol": "XAUUSD", "direction": "BUY", "entry": 3586.04,
               "status": "ACTIVE", "live_px": 3614.90}
        line = birth_pulse_line(sig)
        self.assertIn("بانتظار وصول السعر لمنطقة الدخول", line)
        self.assertIn("288.6 pips", line)

    def test_no_quote_no_pulse(self):
        self.assertEqual(birth_pulse_line({"symbol": "XAUUSD", "direction": "BUY",
                                           "entry": 3586.04, "status": "ACTIVE",
                                           "live_px": 0}), "")


class TestSchemaUpgrade(unittest.TestCase):
    def test_filled_at_column_and_protocol_epoch(self):
        db.init_db()
        with db.conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(signals)").fetchall()}
            epoch = c.execute("SELECT value FROM settings WHERE key='real_entry_protocol_since'").fetchone()
        self.assertIn("filled_at", cols)
        self.assertTrue(epoch and epoch["value"])


if __name__ == "__main__":
    unittest.main()
