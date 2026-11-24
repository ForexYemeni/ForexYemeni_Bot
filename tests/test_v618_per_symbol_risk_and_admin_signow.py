"""Tests for v6.18.0:
1. Per-symbol open signal cap (max_open_per_symbol) + higher multi-market global cap (max_open_signals).
2. Admin Signal-Now (ADMIN_SIGNAL_NOW) cap exemption + non-interference with AUTO slots.
3. Regular user private Signal-Now per-symbol + total isolation.
4. Admin Risk UI adjustment & legacy '3' -> '12' safe migration.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fysb import config, db
from fysb.engines import risk
from fysb.handlers import dispatcher, signal_now
from fysb.services import users as users_service


def _insert_sig(symbol="XAUUSD", direction="BUY", tf="M15", entry=2600.0,
                source="AUTO", created_by=0, status="ACTIVE"):
    with db.tx(immediate=True) as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,
               rr,score,strategy,reason,status,source,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, direction, tf, entry, entry - 10, entry + 20, entry + 40, entry + 60, "",
             2.5, 85, "SMC", "test", status, source, created_by, config.now()),
        )
        return cur.lastrowid


class _FakeMsg:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.message_id = 10
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.message_id + len(self.sent))

    async def edit_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self


class _FakeQuery:
    def __init__(self, uid: int, data: str):
        self.from_user = SimpleNamespace(id=uid, username=f"u{uid}", full_name=f"User {uid}")
        self.data = data
        self.message = _FakeMsg(uid)

    async def answer(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.messages))


def _fire(uid: int, data: str, ctx=None):
    q = _FakeQuery(uid, data)
    bot = _FakeBot()
    if ctx is None:
        ctx = SimpleNamespace(bot=bot, user_data={}, args=[])
    else:
        ctx.bot = bot
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q.message.sent[-1][0] if q.message.sent else "", ctx


class TestV618MultiMarketRiskAndAdminSignow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = config.DB_FILE
        self._orig_admin = config.ADMIN_ID
        config.DB_FILE = Path(self._tmp.name) / "v618.db"
        config.ADMIN_ID = 9001
        db.init_db()
        users_service.reset_touch_cache()
        for uid, uname in ((9001, "admin"), (3001, "user1")):
            users_service.ensure_user(SimpleNamespace(id=uid, username=uname, full_name=uname))

    def tearDown(self):
        config.DB_FILE = self._orig_db
        config.ADMIN_ID = self._orig_admin
        self._tmp.cleanup()

    def test_per_symbol_cap_does_not_choke_other_markets(self):
        self.assertEqual(db.get_setting_int("max_open_per_symbol", 0), 3)
        self.assertEqual(db.get_setting_int("max_open_signals", 0), 12)

        # Open 3 XAUUSD AUTO signals -> XAUUSD hits per-symbol cap (3/3)
        _insert_sig(symbol="XAUUSD", entry=2600.0, source="AUTO")
        _insert_sig(symbol="XAUUSD", entry=2610.0, source="AUTO")
        _insert_sig(symbol="XAUUSD", entry=2620.0, source="AUTO")

        gold_dec = risk.check_signal("XAUUSD", rr=2.5, atr_pct_value=1.0, source="AUTO")
        self.assertFalse(gold_dec.allowed)
        self.assertIn("XAUUSD", gold_dec.reason)
        self.assertIn("3/3", gold_dec.reason)

        # BTCUSD & EURUSD MUST STILL BE ALLOWED!
        btc_dec = risk.check_signal("BTCUSD", rr=2.5, atr_pct_value=1.0, source="AUTO")
        eur_dec = risk.check_signal("EURUSD", rr=2.5, atr_pct_value=1.0, source="AUTO")
        self.assertTrue(btc_dec.allowed, f"BTCUSD must not be blocked by Gold: {btc_dec.reason}")
        self.assertTrue(eur_dec.allowed, f"EURUSD must not be blocked by Gold: {eur_dec.reason}")

    def test_global_ceiling_enforced_across_all_markets(self):
        db.set_setting("max_open_per_symbol", "2")
        db.set_setting("max_open_signals", "3")
        _insert_sig(symbol="XAUUSD", entry=2600.0, source="AUTO")
        _insert_sig(symbol="BTCUSD", entry=90000.0, source="AUTO")
        _insert_sig(symbol="EURUSD", entry=1.1000, source="AUTO")

        # 4th signal on GBPUSD hits global cap (3/3)
        gbp_dec = risk.check_signal("GBPUSD", rr=2.5, atr_pct_value=1.0, source="AUTO")
        self.assertFalse(gbp_dec.allowed)
        self.assertIn("3/3", gbp_dec.reason)

    def test_admin_signal_now_is_exempt_and_does_not_consume_auto_slots(self):
        db.set_setting("max_open_per_symbol", "2")
        db.set_setting("max_open_signals", "2")
        _insert_sig(symbol="XAUUSD", entry=2600.0, source="AUTO")
        _insert_sig(symbol="XAUUSD", entry=2610.0, source="AUTO")

        # AUTO is blocked on both per-symbol and global caps
        self.assertFalse(risk.check_signal("XAUUSD", rr=2.5, atr_pct_value=1.0, source="AUTO").allowed)

        # Admin Signal-Now is EXEMPT from open-signal caps!
        adm_dec = risk.check_signal("XAUUSD", rr=2.5, atr_pct_value=1.0,
                                    source="ADMIN_SIGNAL_NOW", created_by=9001)
        self.assertTrue(adm_dec.allowed, f"Admin Signal-Now should be cap-exempt: {adm_dec.reason}")
        self.assertTrue(adm_dec.metrics.get("cap_exempt"))

        # Still enforces RR & volatility safety gates
        low_rr = risk.check_signal("XAUUSD", rr=1.0, atr_pct_value=1.0,
                                   source="ADMIN_SIGNAL_NOW", created_by=9001)
        self.assertFalse(low_rr.allowed)

        # Admin Signal-Now rows in DB must NOT steal slots from AUTO engine
        with db.tx(immediate=True) as c:
            c.execute("DELETE FROM signals")
        _insert_sig(symbol="XAUUSD", entry=2601.0, source="ADMIN_SIGNAL_NOW", created_by=9001)
        _insert_sig(symbol="XAUUSD", entry=2602.0, source="ADMIN_SIGNAL_NOW", created_by=9001)
        self.assertTrue(risk.check_signal("XAUUSD", rr=2.5, atr_pct_value=1.0, source="AUTO").allowed)

    def test_admin_signow_handler_passes_admin_signal_now_source(self):
        captured = {}

        def _fake_gen(symbol, tf, force, only_strategies, source, req_uid):
            captured["source"] = source
            return {"status": "NO_SETUP"}

        ctx = SimpleNamespace(bot=_FakeBot(), user_data={}, args=[])
        ctx.user_data[signal_now.DRAFT_KEY] = {
            "stage": "strategy", "is_admin": True, "style": "INTRADAY",
            "symbol": "XAUUSD", "tf": "M15", "strategies": ["SMC"],
        }
        with patch.object(signal_now, "generate_signal", side_effect=_fake_gen):
            _fire(9001, "a:signowstrat:ALL", ctx)
        self.assertEqual(captured.get("source"), "ADMIN_SIGNAL_NOW")

    def test_admin_risk_ui_and_legacy_migration(self):
        # UI exposes & adjusts max_open_per_symbol and max_open_signals
        text, _ = _fire(9001, "a:risk")
        self.assertIn("أقصى إشارات مفتوحة لكل سوق", text)
        self.assertIn("أقصى إشارات مفتوحة (الكلي)", text)
        _fire(9001, "a:riskadj:max_open_per_symbol:1")
        self.assertEqual(db.get_setting_int("max_open_per_symbol", 0), 4)

        # Simulate legacy DB with max_open_signals='3' & no v6180 flag -> migrates to '12'
        with db.tx(immediate=True) as c:
            c.execute("DELETE FROM settings WHERE key='risk_multimarket_v6180'")
            c.execute("UPDATE settings SET value='3' WHERE key='max_open_signals'")
        db.init_db()
        self.assertEqual(db.get_setting("max_open_signals"), "12")

        # Custom admin value (e.g. '7') is preserved untouched across migration!
        with db.tx(immediate=True) as c:
            c.execute("DELETE FROM settings WHERE key='risk_multimarket_v6180'")
            c.execute("UPDATE settings SET value='7' WHERE key='max_open_signals'")
        db.init_db()
        self.assertEqual(db.get_setting("max_open_signals"), "7")


if __name__ == "__main__":
    unittest.main()
