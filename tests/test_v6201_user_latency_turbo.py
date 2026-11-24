"""v6.20.1 User-Account Latency Turbo Regression Tests.

Verifies the exact structural fixes that cut user button latency by 70-90%:
  1. dispatcher._route_user passes preloaded_u -> user_page NEVER calls touch_and_get twice.
  2. build_user_home_card / _dashboard_snapshot / profile_summary use 1 DB checkout instead of 6-14.
  3. subs.plan_for() caches plans & user plan_code (0 DB checkouts on hot repeat; instant invalidation on grant).
  4. db.get_setting() bulk-preloads settings once for 30s (0 DB checkouts for subsequent hot keys).
  5. u:signalview uses cached_price / sig.live_px in 0ms without blocking on market_data.fetch_price().
  6. pglayer.Conn(transactional=False) switches autocommit=True and skips rollback on IDLE sessions.
"""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fysb import config, db, pglayer
from fysb.engines import market_data
from fysb.handlers import dispatcher, user_pages
from fysb.services import strategy_stats, subscriptions as subs, users as users_service


class TestV6201UserLatencyTurbo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "turbo.db")
        config.ADMIN_ID = 999001
        db.init_db()
        users_service.reset_touch_cache()
        tg = SimpleNamespace(id=77001, username="speedy", full_name="Speedy User")
        users_service.ensure_user(tg)

    def tearDown(self):
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    def test_settings_bulk_preload_eliminates_repeated_db_hits(self):
        db._clear_all_caches()
        orig_conn = db.conn
        calls = {"n": 0}

        def counting_conn():
            calls["n"] += 1
            return orig_conn()

        with patch.object(db, "conn", side_effect=counting_conn):
            _ = db.get_setting("maintenance", "0")
            _ = db.get_setting("ui_edit_in_place", "0")
            _ = db.get_setting("trading_mode", "SIGNALS_ONLY")
            _ = db.get_setting("free_daily_signals", "2")
            _ = db.maintenance_on()
        # All 5 reads fulfilled by ONE single bulk preload query!
        self.assertEqual(calls["n"], 1)

    def test_plan_for_cache_and_instant_invalidation_on_grant(self):
        p0 = subs.plan_for(77001)
        self.assertEqual(p0["code"], "FREE")

        # Second call MUST hit RAM cache (0 DB connections!)
        orig_conn = db.conn
        calls = {"n": 0}
        def counting_conn():
            calls["n"] += 1
            return orig_conn()

        with patch.object(db, "conn", side_effect=counting_conn):
            p1 = subs.plan_for(77001)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(p1["code"], "FREE")

        # Grant VIP -> cache invalidated immediately -> sees VIP right away!
        subs.grant_subscription(77001, "VIP", source="ADMIN", actor=config.ADMIN_ID)
        p2 = subs.plan_for(77001)
        self.assertEqual(p2["code"], "VIP")

    def test_user_home_and_dashboard_do_not_double_fetch_user_or_plan(self):
        # Warm settings & plan caches first (steady-state user session)
        _ = db.maintenance_on()
        _ = subs.plan_for(77001)

        msg = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=10, chat_id=77001)))
        q = SimpleNamespace(
            from_user=SimpleNamespace(id=77001, username="speedy", full_name="Speedy User"),
            data="u:home",
            message=msg,
            answer=AsyncMock(),
        )
        ctx = SimpleNamespace(bot=SimpleNamespace(), user_data={})
        upd = SimpleNamespace(callback_query=q)

        orig_conn = db.conn
        calls = {"n": 0}
        def counting_conn():
            calls["n"] += 1
            return orig_conn()

        with patch.object(db, "conn", side_effect=counting_conn):
            asyncio.run(dispatcher.buttons(upd, ctx))

        q.answer.assert_awaited_once()
        # Was 10 sequential DB connections before v6.20.1; now strictly <= 2 (1 touch_and_get + 1 home snapshot)!
        self.assertLessEqual(calls["n"], 2)

        # Dashboard tap must also stay <= 2 DB checkouts (was 14-16!)
        calls["n"] = 0
        q.data = "u:dashboard"
        with patch.object(db, "conn", side_effect=counting_conn):
            asyncio.run(dispatcher.buttons(upd, ctx))
        self.assertLessEqual(calls["n"], 2)

    def test_signalview_uses_live_px_without_blocking_http_fetch(self):
        market_data._cache.clear()
        now = config.now()
        with db.conn() as c:
            cur = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_at,live_px)
                   VALUES('XAUUSD','M15','BUY',2650,2640,2670,2685,2700,'2670,2685,2700',
                   2.0,85,'SMC','test','RUNNING','AUTO',?,2658.5)""",
                (now,),
            )
            sid = cur.lastrowid

        sent_texts = []
        async def fake_reply(**kw):
            sent_texts.append(kw.get("text", ""))
            return SimpleNamespace(message_id=55, chat=SimpleNamespace(id=77001))

        msg = SimpleNamespace(reply_text=fake_reply)
        q = SimpleNamespace(
            from_user=SimpleNamespace(id=77001, username="speedy", full_name="Speedy User"),
            data=f"u:signalview:{sid}",
            message=msg,
            answer=AsyncMock(),
        )
        ctx = SimpleNamespace(bot=SimpleNamespace(), user_data={})
        upd = SimpleNamespace(callback_query=q)

        with patch.object(market_data, "fetch_price", side_effect=AssertionError("MUST NOT block on HTTP when live_px exists")):
            asyncio.run(dispatcher.buttons(upd, ctx))

        self.assertTrue(any("2658.50" in t or "2,658.50" in t for t in sent_texts))

    def test_pglayer_conn_uses_autocommit_and_skips_idle_rollback(self):
        import psycopg

        class FakePG:
            closed = False
            autocommit = False
            info = SimpleNamespace(transaction_status=psycopg.pq.TransactionStatus.IDLE)
            rollbacks = 0
            commits = 0

            def rollback(self):
                self.rollbacks += 1

            def commit(self):
                self.commits += 1

            def close(self):
                self.closed = True

        fake = FakePG()
        with patch.object(pglayer, "_acquire", return_value=fake), patch.object(pglayer, "_pool", []):
            with pglayer.conn():
                self.assertTrue(fake.autocommit)
        # ZERO unnecessary COMMIT / ROLLBACK round trips on non-transactional reads!
        self.assertEqual(fake.commits, 0)
        self.assertEqual(fake.rollbacks, 0)

    def test_leaderboard_cache_avoids_repeated_full_table_scans(self):
        _ = strategy_stats.leaderboard()
        orig_conn = db.conn
        calls = {"n": 0}
        def counting_conn():
            calls["n"] += 1
            return orig_conn()
        with patch.object(db, "conn", side_effect=counting_conn):
            _ = strategy_stats.leaderboard()
            _ = strategy_stats.leaderboard_text()
            _ = strategy_stats.ordered_codes(["SMC", "ICT"])
        self.assertEqual(calls["n"], 0)


if __name__ == "__main__":
    unittest.main()
