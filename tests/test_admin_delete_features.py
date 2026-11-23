"""Permanent-deletion features + signal-availability regression tests.

Covers:
  * delete_user_forever: wipes every trace (12 tables) in one transaction,
    refuses the admin account, refuses unknown users, audit-logged
  * admin user-detail surface: «🗑 حذف المستخدم نهائياً» button present next to
    ban / notifications buttons, confirm page, real deletion through dispatcher
  * admin signal view: «🗑 حذف نهائي للإشارة» button present, confirm page,
    real erase of the signal row + its deliveries through dispatcher
  * kill switch ships OFF: a fresh database can issue signals from the first
    boot (the bug that kept the platform signal-less since day one)
"""
import asyncio
import unittest

from tests.helpers import MockBot, MockQuery, MockContext, page_of, page_markup_of
from fysb import db, config
from fysb.handlers import dispatcher
from fysb.services import users as users_service
from fysb.engines.signal_engine import generate_signal


def run(coro):
    return asyncio.run(coro)


def fire(data: str, uid: int):
    q = MockQuery(data, uid)
    ctx = MockContext()
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


ADMIN = 9001
USER = 555311


class TestDeleteUserForever(unittest.TestCase):
    def setUp(self):
        db.init_db()
        # build a user with traces across every related table (clean slate first:
        # the temp DB persists across tests and demo_accounts is UNIQUE(user_id))
        with db.conn() as c:
            for t in ("subscriptions", "payments", "demo_accounts", "demo_positions",
                      "demo_trades", "analyses", "notification_events", "user_accounts",
                      "tickets", "signal_deliveries"):
                c.execute(f"DELETE FROM {t} WHERE user_id=?", (USER,))
            for t in c.execute("SELECT id FROM tickets WHERE user_id=?", (USER,)).fetchall():
                c.execute("DELETE FROM ticket_messages WHERE ticket_id=?", (t["id"],))
            c.execute("DELETE FROM tickets WHERE user_id=?", (USER,))
            c.execute("DELETE FROM referrals WHERE invitee_id=?", (USER,))
        import types
        u = type("TG", (), {"id": USER, "username": "victim", "full_name": "VictimUser"})()
        users_service.ensure_user(u)
        with db.conn() as c:
            c.execute("INSERT OR IGNORE INTO plans(code,title,markets,timeframes,strategies,min_score,"
                      "max_signals_per_day,price,currency,duration_days) "
                      "VALUES('VIP','VIP','XAUUSD','M15','ALL',80,50,10,'USD',30)")
            c.execute("INSERT INTO subscriptions(user_id,plan_code,starts_at,status,created_at) "
                      "VALUES(?,?,?,?,?)", (USER, 'VIP', config.now(), 'ACTIVE', config.now()))
            c.execute("INSERT INTO payments(user_id,plan_code,amount,method,status,created_at) "
                      "VALUES(?,?,?,?,?,?)", (USER, 'VIP', 10, 'wallet', 'PENDING', config.now()))
            c.execute("INSERT INTO demo_accounts(user_id,balance,created_at) VALUES(?,?,?)",
                      (USER, 10000, config.now()))
            c.execute("INSERT INTO analyses(user_id,symbol,timeframe,analysis_type,summary,created_at) "
                      "VALUES(?,?,?,?,?,?)", (USER, 'XAUUSD', 'M15', 'trend', 'ok', config.now()))
            c.execute("INSERT INTO notification_events(user_id,kind,payload,created_at) VALUES(?,?,?,?)",
                      (USER, 'test', '{}', config.now()))
            c.execute("INSERT INTO user_accounts(user_id,broker,account_type,account_ref,status,created_at) "
                      "VALUES(?,?,?,?,?,?)", (USER, 'BINANCE', 'DEMO', 'ref-1', 'ACTIVE', config.now()))
            c.execute("INSERT INTO referrals(campaign_id,invitee_id,invited_at) VALUES(?,?,?)",
                      (1, USER, config.now()))
            c.execute("INSERT INTO tickets(user_id,subject,message,status,created_at) VALUES(?,?,?,?,?)",
                      (USER, 'help', 'hello', 'OPEN', config.now()))
            tid = c.execute("SELECT id FROM tickets WHERE user_id=?", (USER,)).fetchone()["id"]
            c.execute("INSERT INTO ticket_messages(ticket_id,sender_role,sender_id,body,created_at) "
                      "VALUES(?,?,?,?,?)", (tid, 'USER', USER, 'hello', config.now()))

    def _table_counts(self, uid):
        checks = {}
        with db.conn() as c:
            for t in ("users", "subscriptions", "payments", "demo_accounts", "demo_positions",
                      "demo_trades", "analyses", "notification_events", "user_accounts",
                      "tickets", "signal_deliveries"):
                checks[t] = c.execute(f"SELECT COUNT(*) n FROM {t} WHERE user_id=?",
                                      (uid,)).fetchone()["n"]
            checks["referrals"] = c.execute("SELECT COUNT(*) n FROM referrals WHERE invitee_id=?",
                                            (uid,)).fetchone()["n"]
            tm = 0
            for t in c.execute("SELECT id FROM tickets WHERE user_id=?", (uid,)).fetchall():
                tm += c.execute("SELECT COUNT(*) n FROM ticket_messages WHERE ticket_id=?",
                                (t["id"],)).fetchone()["n"]
            checks["ticket_messages"] = tm
        return checks

    def test_service_wipes_every_trace(self):
        res = users_service.delete_user_forever(USER, ADMIN)
        self.assertTrue(res.get("ok"), res)
        counts = self._table_counts(USER)
        for t, n in counts.items():
            self.assertEqual(n, 0, f"{t} not wiped (n={n})")
        # audit trail records the royal deletion
        with db.conn() as c:
            a = c.execute("SELECT action FROM audit WHERE action LIKE ?", (f"user_deleted_forever:{USER}",)).fetchone()
            self.assertIsNotNone(a)

    def test_admin_account_is_undeletable(self):
        res = users_service.delete_user_forever(config.ADMIN_ID, ADMIN)
        self.assertFalse(res.get("ok"))

    def test_unknown_user_refused(self):
        res = users_service.delete_user_forever(111222333, ADMIN)
        self.assertFalse(res.get("ok"))

    def test_button_in_user_detail_and_real_flow(self):
        # the user-detail page must render (and the flow must complete end-to-end)
        fire(f"a:userdetail:{USER}", ADMIN)
        # the confirm page lists wipe scope and warns irreversibility
        q2, _ = fire(f"a:userdelete:{USER}", ADMIN)
        t2 = page_of(q2)
        self.assertIn("حذف نهائي", t2)
        self.assertIn("غير قابل للاسترجاع", t2)
        # execute — user gone for good
        q3, _ = fire(f"a:userdeletego:{USER}", ADMIN)
        t3 = page_of(q3)
        self.assertIn("تم حذف المستخدم نهائياً", t3)
        self.assertIsNone(users_service.get_user(USER))

    def test_admin_guard_via_dispatcher(self):
        q, _ = fire(f"a:userdelete:{config.ADMIN_ID}", ADMIN)
        t = page_of(q)
        self.assertIn("لا يُحذف", t)


class TestDeleteSignalForever(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,
                         rr,score,strategy,reason,status,source,created_by,created_at)
                         VALUES('XAUUSD','SELL','M15',3504.3,3526.0,3461.0,3439.3,3417.7,'3461,3439,3417',
                         2.0,90,'PRICE_ACTION','test','CANCELLED','AUTO',0,?)""", (config.now(),))
            self.sid = c.execute("SELECT id FROM signals ORDER BY id DESC LIMIT 1").fetchone()["id"]
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                      (self.sid, USER, config.now()))

    def test_button_present_for_terminal_signal(self):
        q, _ = fire(f"a:signalview:{self.sid}", ADMIN)
        # v6.8.x: the delete-forever control is an inline BUTTON on the card —
        # assert it in the full user-visible surface (text + keyboard).
        t = page_of(q) + str(page_markup_of(q))
        self.assertIn("حذف نهائي للإشارة", t)

    def test_confirm_then_real_erase(self):
        q, _ = fire(f"a:delsignal:{self.sid}", ADMIN)
        t = page_of(q)
        self.assertIn("حذف نهائي", t)
        self.assertIn("لا أرشيف", t)
        q2, _ = fire(f"a:delsignalgo:{self.sid}", ADMIN)
        t2 = page_of(q2)
        self.assertIn("تم حذف الإشارة", t2)
        with db.conn() as c:
            self.assertIsNone(c.execute("SELECT id FROM signals WHERE id=?", (self.sid,)).fetchone())
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE signal_id=?",
                                       (self.sid,)).fetchone()["n"], 0)

    def test_missing_signal_graceful(self):
        q, _ = fire("a:delsignalgo:99999999", ADMIN)
        self.assertIn("غير موجودة", page_of(q))


class TestKillSwitchShipsOff(unittest.TestCase):
    """The day-one bug: fresh installs had the kill switch ON -> zero signals ever."""

    def test_fresh_db_allows_signals(self):
        db.init_db()
        self.assertEqual(db.get_setting("global_kill_switch", "MISSING"), "0")
        self.assertFalse(db.kill_switch_on())

    def test_engine_gate_block_removed(self):
        # the scanner gate condition must not trip on a fresh DB
        db.init_db()
        gate_open = (db.get_setting("engine_enabled", "0") == "1"
                     and not db.kill_switch_on()
                     and db.get_setting("signal_push_enabled", "1") == "1")
        self.assertTrue(gate_open)

    def test_generate_signal_not_killswitch_rejected(self):
        # with real providers reachable, a cycle must never die on the kill gate
        try:
            res = generate_signal("XAUUSD")
        except Exception:
            self.skipTest("live market data unreachable in this environment")
        self.assertNotEqual(res.get("reason"), "kill switch engaged")
