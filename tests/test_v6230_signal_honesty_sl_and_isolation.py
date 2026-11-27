"""v6.23.0 — Regression & Guarantee Suite:
1. Signed SL/TP pips on signal cards (SL always -X.X pips, TP1..3 always +X.X pips).
2. Strict SL/TP directional geometry + Max-SL ATR/% institutional cap (blocks 888-pip Gold H1 stops)
   + price_action spike-wick stop cap.
3. 100% Honest Delivered-Only User Signal Archive + Strict Post-Join Isolation +
   Zero Pre-Join/Undelivered Tracking Alerts + 100% Automatic Live Delivery for Brand-New Users.
"""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fysb import config, db, instruments
from fysb.engines import signal_engine, tracker
from fysb.engines.strategies import price_action
from fysb.engines.strategies.base import Ctx
from fysb.handlers import dispatcher
from fysb.services import signal_archive, users
from fysb.ui.cards import render_signal_card

ADMIN = 990001
NEW_USER = 990020
OLD_USER = 990021


def _fire(cb_data: str, uid: int):
    captured = {"text": "", "kb": None}

    async def _edit(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _reply(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _answer(*a, **kw):
        pass

    msg = SimpleNamespace(chat_id=uid, message_id=1, edit_text=_edit, reply_text=_reply, chat=SimpleNamespace(id=uid))
    q = SimpleNamespace(
        data=cb_data,
        from_user=SimpleNamespace(id=uid, username=f"u{uid}", first_name="User", full_name="User"),
        message=msg,
        answer=_answer,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user, effective_chat=SimpleNamespace(id=uid))
    ctx = SimpleNamespace(user_data={}, bot_data={}, bot=SimpleNamespace())
    asyncio.run(dispatcher.buttons(upd, ctx))
    datas = []
    if captured["kb"] and getattr(captured["kb"], "inline_keyboard", None):
        for row in captured["kb"].inline_keyboard:
            for b in row:
                if getattr(b, "callback_data", None):
                    datas.append(b.callback_data)
    return captured["text"], datas


class TestV6230SignalHonestySlAndIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "v6230.db")
        config.ADMIN_ID = ADMIN
        db.init_db()

    def tearDown(self):
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # 1. Signed SL / TP pips — NEVER '+' on Stop Loss!
    # ------------------------------------------------------------------
    def test_signed_pips_and_signal_card_sl_always_negative(self):
        # SELL trade (Gold): SL above entry => negative pips; TPs below entry => positive pips
        self.assertAlmostEqual(instruments.signed_pips("XAUUSD", "SELL", 3565.71, 3580.71), -150.0, places=1)
        self.assertAlmostEqual(instruments.signed_pips("XAUUSD", "SELL", 3565.71, 3535.71), +300.0, places=1)

        # BUY trade (Gold): SL below entry => negative pips; TPs above entry => positive pips
        self.assertAlmostEqual(instruments.signed_pips("XAUUSD", "BUY", 3565.71, 3550.71), -150.0, places=1)
        self.assertAlmostEqual(instruments.signed_pips("XAUUSD", "BUY", 3565.71, 3595.71), +300.0, places=1)

        sig_sell = {
            "id": 101,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "direction": "SELL",
            "entry": 3565.71,
            "stop": 3580.71,
            "tp1": 3535.71,
            "tp2": 3520.71,
            "tp3": 3498.21,
            "rr": 2.0,
            "score": 88,
            "strategy": "PRICE_ACTION",
            "status": "ACTIVE",
            "reason": "test",
            "created_at": config.now(),
            "data_time": config.now(),
            "pips_result": 0.0,
            "hit_tp": 0,
            "closed_at": "",
            "filled_at": "",
            "live_px": None,
        }
        card_sell = render_signal_card(sig_sell, user_view=True)
        self.assertIn("وقف الخسارة SL:</b> <code>3,580.71</code>  <i>(-150.0 pips)</i>", card_sell)
        self.assertNotIn("وقف الخسارة SL:</b> <code>3,580.71</code>  <i>(+150.0 pips)</i>", card_sell)
        self.assertIn("الهدف الأول TP1:</b> <code>3,535.71</code>  <i>(+300.0 pips)</i>", card_sell)
        self.assertIn("الهدف الثاني TP2:</b> <code>3,520.71</code>  <i>(+450.0 pips)</i>", card_sell)
        self.assertIn("الهدف الثالث TP3:</b> <code>3,498.21</code>  <i>(+675.0 pips)</i>", card_sell)

        sig_buy = dict(sig_sell, direction="BUY", stop=3550.71, tp1=3595.71, tp2=3610.71, tp3=3633.21)
        card_buy = render_signal_card(sig_buy, user_view=True)
        self.assertIn("وقف الخسارة SL:</b> <code>3,550.71</code>  <i>(-150.0 pips)</i>", card_buy)
        self.assertIn("الهدف الأول TP1:</b> <code>3,595.71</code>  <i>(+300.0 pips)</i>", card_buy)

    # ------------------------------------------------------------------
    # 2. Institutional Geometry & Max-Stop Guard + price_action Spike Cap
    # ------------------------------------------------------------------
    def test_geometry_gate_rejects_inverted_and_888pip_gold_stop(self):
        # The exact live bug reported by the owner: 888.7-pip H1 Gold SL ($88.87)
        ok, reason = signal_engine.validate_signal_geometry(
            "XAUUSD", "H1", "SELL",
            entry=3565.71, stop=3654.58,
            tp1=3387.97, tp2=3299.10, tp3=3165.79,
            atr_val=15.0,
        )
        self.assertFalse(ok)
        # v8.13.4: the reason is the Arabic disclosure («سقف البنيوي» + تكيف التقلب)
        self.assertTrue("ATR" in reason or "max" in reason or "سقف" in reason)

        # Even if ATR wasn't passed (0), H1 % cap (1.50% = $53.48) STILL blocks the $88.87 stop!
        ok2, reason2 = signal_engine.validate_signal_geometry(
            "XAUUSD", "H1", "SELL",
            entry=3565.71, stop=3654.58,
            tp1=3387.97, tp2=3299.10, tp3=3165.79,
            atr_val=0.0,
        )
        self.assertFalse(ok2)
        # v8.13.4: Arabic disclosure instead of the English "max" text
        self.assertIn("سقف H1 البنيوي", reason2)

        # Inverted BUY stop (SL above entry) -> REJECTED
        ok_inv_b, _ = signal_engine.validate_signal_geometry(
            "XAUUSD", "M15", "BUY",
            entry=3565.0, stop=3580.0, tp1=3595.0, tp2=3610.0, tp3=3625.0,
            atr_val=10.0,
        )
        self.assertFalse(ok_inv_b)

        # Inverted SELL stop (SL below entry) -> REJECTED
        ok_inv_s, _ = signal_engine.validate_signal_geometry(
            "XAUUSD", "M15", "SELL",
            entry=3565.0, stop=3550.0, tp1=3535.0, tp2=3520.0, tp3=3505.0,
            atr_val=10.0,
        )
        self.assertFalse(ok_inv_s)

        # Valid institutional Gold H1 SELL -> ACCEPTED
        ok_good, _ = signal_engine.validate_signal_geometry(
            "XAUUSD", "H1", "SELL",
            entry=3565.71, stop=3585.71,
            tp1=3525.71, tp2=3505.71, tp3=3475.71,
            atr_val=12.0,
        )
        self.assertTrue(ok_good)

    def test_price_action_caps_abnormal_spike_wick_stop(self):
        # Build 40 bearish H1 candles with a single freak $100 news wick 3 bars before close
        candles = []
        px = 3650.0
        for i in range(40):
            o = px
            c = px - 2.0
            h = o + 1.5
            l = c - 1.5
            if i == 36:
                h = o + 100.0  # freak spike wick
            if i == 39:
                # Clear bearish engulfing conviction candle
                o = c + 4.0
                c = c - 4.0
                h = o + 1.0
                l = c - 1.0
            candles.append({"open": o, "high": h, "low": l, "close": c, "datetime": f"2026-11-24 {i:02d}:00:00"})
            px = c
        ctx = Ctx(symbol="XAUUSD", timeframe="H1", candles=candles, bias_candles=candles)
        res = price_action.evaluate(ctx)
        if res is not None:
            # Stop MUST be capped — nowhere near the $100 freak wick!
            self.assertLess(abs(res.entry - res.stop), 50.0)
            self.assertGreater(res.stop, res.entry if res.direction == "SELL" else 0)

    # ------------------------------------------------------------------
    # 3. Post-Join Isolation + Delivered-Only Honesty + Auto Live Delivery
    # ------------------------------------------------------------------
    def test_new_user_isolation_no_prejoin_leaks_and_automatic_live_delivery(self):
        now_dt = datetime.now(timezone.utc)
        t_old_user = (now_dt - timedelta(hours=5)).isoformat()
        t_pre_join_sig = (now_dt - timedelta(hours=2)).isoformat()
        t_new_user = (now_dt - timedelta(minutes=5)).isoformat()
        t_live_sig = now_dt.isoformat()

        # Register OLD_USER (5h ago)
        users.ensure_user(SimpleNamespace(id=OLD_USER, username="old", first_name="Old", full_name="Old"))
        with db.conn() as c:
            c.execute("UPDATE users SET joined_at=? WHERE user_id=?", (t_old_user, OLD_USER))

        # Insert pre-join signal S_OLD (2h ago, ACTIVE, XAUUSD M15 CLASSIC score=90)
        with db.conn() as c:
            sid_old = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_by,created_at)
                   VALUES('XAUUSD','M15','BUY',3550.0,3535.0,3580.0,3595.0,3617.5,
                   '3580,3595,3617.5',2.0,90,'CLASSIC','prejoin','ACTIVE','AUTO',0,?)""",
                (t_pre_join_sig,),
            ).lastrowid
            c.execute(
                "INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(?,?,?)",
                (OLD_USER, sid_old, t_pre_join_sig),
            )

        # Register brand-new user NEW_USER (5m ago) — ZERO manual settings touched!
        users.ensure_user(SimpleNamespace(id=NEW_USER, username="fresh", first_name="Fresh", full_name="Fresh"))
        u_new = users.get_user(NEW_USER)
        self.assertEqual(int(u_new["notifications"]), 1)  # 100% auto-enabled by default
        with db.conn() as c:
            c.execute("UPDATE users SET joined_at=? WHERE user_id=?", (t_new_user, NEW_USER))

        # A) Brand-new user opens u:signals / open / dates / demopick -> NEVER sees S_OLD!
        t_txt, t_datas = _fire("u:signals", NEW_USER)
        self.assertNotIn(f"u:signalview:{sid_old}", t_datas)
        _, open_datas = _fire("u:sigday:open:0", NEW_USER)
        self.assertNotIn(f"u:signalview:{sid_old}", open_datas)
        _, demo_datas = _fire("u:demopick", NEW_USER)
        self.assertNotIn(f"u:demolots:{sid_old}", demo_datas)

        # B) Direct callback u:signalview:<sid_old> is BLOCKED & never subscribes NEW_USER to tracking!
        v_txt, _ = _fire(f"u:signalview:{sid_old}", NEW_USER)
        self.assertIn("لم تُرسل لحسابك", v_txt)
        with db.conn() as c:
            row = c.execute(
                "SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?",
                (NEW_USER, sid_old),
            ).fetchone()
        self.assertIsNone(row)

        # C) Tracker alert targets for S_OLD NEVER include NEW_USER!
        with db.conn() as c:
            sig_old_row = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid_old,)).fetchone())
        targets_old = [t[0] for t in tracker._alert_targets(sig_old_row, "TP1 hit")]
        self.assertIn(OLD_USER, targets_old)
        self.assertNotIn(NEW_USER, targets_old)

        # D) Re-running emit_to_users on S_OLD STILL refuses to send S_OLD to NEW_USER!
        sent_to = []

        async def _fake_send(chat_id, text, **kw):
            sent_to.append((int(chat_id), text))
            return SimpleNamespace(chat_id=chat_id, message_id=777)

        asyncio.run(signal_engine.emit_to_users(sig_old_row, _fake_send, include_channels=False))
        self.assertNotIn(NEW_USER, [u for u, _ in sent_to])

        # E) Now a brand-new live signal S_LIVE is born AFTER NEW_USER joined ->
        #    NEW_USER receives it 100% AUTOMATICALLY with ZERO setup!
        sent_to.clear()
        with db.conn() as c:
            sid_live = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_by,created_at)
                   VALUES('XAUUSD','M15','SELL',3565.0,3580.0,3535.0,3520.0,3497.5,
                   '3535,3520,3497.5',2.0,90,'CLASSIC','live','ACTIVE','AUTO',0,?)""",
                (t_live_sig,),
            ).lastrowid
            sig_live_row = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid_live,)).fetchone())

        res = asyncio.run(signal_engine.emit_to_users(sig_live_row, _fake_send, include_channels=False))
        self.assertIn(NEW_USER, [u for u, _ in sent_to])
        self.assertGreaterEqual(res["sent"], 1)

        # Verify card sent to NEW_USER has negative SL pips (-150.0 pips) and positive TP1 (+300.0 pips)
        new_user_cards = [txt for u, txt in sent_to if u == NEW_USER]
        self.assertTrue(any("(-150.0 pips)" in c and "(+300.0 pips)" in c for c in new_user_cards))

        # S_LIVE now appears in NEW_USER's u:signals AND tracker targets include NEW_USER!
        _, t_datas_after = _fire("u:signals", NEW_USER)
        self.assertIn(f"u:signalview:{sid_live}", t_datas_after)
        self.assertNotIn(f"u:signalview:{sid_old}", t_datas_after)
        targets_live = [t[0] for t in tracker._alert_targets(sig_live_row, "TP1 hit")]
        self.assertIn(NEW_USER, targets_live)

        # F) An undelivered post-join signal (e.g. quota already consumed) NEVER appears in NEW_USER's u:signals!
        with db.conn() as c:
            sid_undeliv = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_by,created_at)
                   VALUES('XAUUSD','M15','BUY',3570.0,3555.0,3600.0,3615.0,3637.5,
                   '3600,3615,3637.5',2.0,92,'CLASSIC','undeliv','ACTIVE','AUTO',0,?)""",
                (t_live_sig,),
            ).lastrowid
        _, t_datas_undeliv = _fire("u:signals", NEW_USER)
        self.assertNotIn(f"u:signalview:{sid_undeliv}", t_datas_undeliv)


if __name__ == "__main__":
    unittest.main()
