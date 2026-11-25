"""Phase 6 (v6.16.0) regression & contract suite:
  1. Performance: touch_and_get / touch 60s in-memory write throttle,
     subscriptions.plans_for_users O(1) batch plan resolution,
     signal_engine.emit_to_users card-first delivery + offloaded auto_execute,
     tracker._card_refresh_edits batch account loading & channel safety.
  2. Versioned /update + Instant Rollback:
     updater.normalize_tag, self_update(target_tag=...) downgrade/reinstall,
     automatic pre-update rollback snapshot (never stores .env/.db),
     updater.rollback_update offline restore, /update v6.14.1 / list / rollback,
     and admin panel a:updates / a:updver / a:updrollback callbacks.
"""
import asyncio
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockMessage, MockQuery, page_of
import updater
from fysb import config, db
from fysb.engines import demo as demo_engine
from fysb.engines import signal_engine, tracker
from fysb.handlers import dispatcher
from fysb.handlers.common import update_command
from fysb.services import subscriptions as subs_service
from fysb.services import users as users_service

ADMIN_ID = 9001


def _fake_user(uid: int):
    return type("U", (), {"id": uid, "username": f"p6u{uid}", "full_name": f"P6 User {uid}"})()


def _build_pkg(path: pathlib.Path, version: str):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ForexYemeni_Bot/main.py", f"# main {version}\n")
        z.writestr("ForexYemeni_Bot/fysb/__init__.py", f"__version__ = '{version}'\n")
        z.writestr("ForexYemeni_Bot/fysb/config.py", f"VERSION = '{version}'\n")


def _fire(data: str, uid: int = ADMIN_ID):
    q = MockQuery(data, uid)
    ctx = MockContext()
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


class TestPhase6TouchThrottle(unittest.TestCase):
    uid = 880101

    def setUp(self):
        db.init_db()
        users_service.reset_touch_cache()
        users_service.ensure_user(_fake_user(self.uid))

    def test_touch_and_get_throttles_writes_within_window(self):
        users_service.reset_touch_cache()
        orig_conn = db.conn
        updates = {"n": 0}

        class _CountingConn:
            def __init__(self, inner):
                self._inner = inner

            def __enter__(self):
                self._c = self._inner.__enter__()
                return self

            def __exit__(self, *a):
                return self._inner.__exit__(*a)

            def execute(self, sql, params=()):
                if str(sql).strip().upper().startswith("UPDATE USERS SET LAST_SEEN"):
                    updates["n"] += 1
                return self._c.execute(sql, params)

        with patch.object(db, "conn", side_effect=lambda: _CountingConn(orig_conn())):
            u1 = users_service.touch_and_get(self.uid)
            u2 = users_service.touch_and_get(self.uid)
            u3 = users_service.touch_and_get(self.uid)
            users_service.touch(self.uid)
        self.assertIsNotNone(u1)
        self.assertEqual(u2["user_id"], self.uid)
        self.assertEqual(u3["user_id"], self.uid)
        # Only the FIRST call wrote last_seen; calls 2, 3, and touch() were throttled!
        self.assertEqual(updates["n"], 1)


class TestPhase6BatchPlansAndOffload(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")
            c.execute("DELETE FROM demo_positions")
            c.execute("DELETE FROM demo_accounts")
            c.execute("DELETE FROM subscriptions WHERE user_id IN (880201, 880202, 880203)")
            c.execute("DELETE FROM users WHERE user_id IN (880201, 880202, 880203)")
            c.execute("UPDATE market_catalog SET enabled=1, verified=1 WHERE symbol='XAUUSD'")
        for uid in (880201, 880202, 880203):
            users_service.ensure_user(_fake_user(uid))

    def test_plans_for_users_matches_plan_for(self):
        subs_service.grant_subscription(880201, "VIP", source="ADMIN", actor=ADMIN_ID, override_days=30)
        # Expired subscription on 880202 -> must fall back to FREE
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with db.conn() as c:
            c.execute("""INSERT INTO subscriptions(user_id,plan_code,starts_at,ends_at,status,created_at,source)
                         VALUES(880202,'VIP',?,?,'ACTIVE',?,'ADMIN')""", (past, past, past))
        batch = subs_service.plans_for_users([880201, 880202, 880203])
        self.assertEqual(batch[880201]["code"], "VIP")
        self.assertEqual(batch[880202]["code"], "FREE")
        self.assertEqual(batch[880203]["code"], "FREE")

    def test_emit_to_users_delivers_all_cards_before_offloaded_auto_execute(self):
        # Enable auto_trade for 880201 and 880202
        for uid in (880201, 880202):
            subs_service.grant_subscription(uid, "VIP", source="ADMIN", actor=ADMIN_ID, override_days=30)
            demo_engine.set_balance(uid, 10000.0)
            demo_engine.set_auto_trade(uid, True)
        with db.conn() as c:
            cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at)
                         VALUES('XAUUSD','BUY','M15',3500,3480,3540,3560,3580,'t',2.0,95,'SMC','x',
                         'RUNNING','AUTO',0,?,'LIVE','t','{}','')""", (config.now(),))
            sid = cur.lastrowid
            sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

        events = []

        async def fake_send(uid, text):
            kind = "EXEC" if "نُفّذت الصفقة تلقائياً" in text else "CARD"
            events.append((kind, uid))
            return type("M", (), {"message_id": len(events), "chat_id": uid})()

        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 3500.0, "fetched_at": config.now(), "source": "LIVE"}):
            stats = asyncio.run(signal_engine.emit_to_users(sig, fake_send, include_channels=False))

        self.assertGreaterEqual(stats["sent"], 2)
        self.assertEqual(stats["auto_executed"], 2)
        card_indices = [i for i, (k, u) in enumerate(events) if k == "CARD" and u in (880201, 880202)]
        exec_indices = [i for i, (k, u) in enumerate(events) if k == "EXEC" and u in (880201, 880202)]
        self.assertEqual(len(card_indices), 2)
        self.assertEqual(len(exec_indices), 2)
        # Critical-path guarantee: ALL signal cards arrived BEFORE any offloaded auto_execute card!
        self.assertLess(max(card_indices), min(exec_indices))

    def test_tracker_card_refresh_edits_handles_users_and_channels_in_batch(self):
        sig = {"id": 77, "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 3500.0, "stop": 3480.0, "tp1": 3540.0, "tp2": 3560.0, "tp3": 3580.0,
               "rr": 2.0, "score": 90, "strategy": "SMC", "status": "RUNNING", "reason": "ok",
               "created_at": config.now(), "data_time": "2026-11-24 12:00:00", "pips_result": 0.0}
        refs = [
            {"user_id": 880201, "chat_id": "880201", "message_id": 11},
            {"user_id": -1, "chat_id": "-100123456", "message_id": 22},
        ]
        edits = tracker._card_refresh_edits(sig, 3510.0, refs)
        self.assertEqual(len(edits), 2)
        # User card has personal block; channel card does NOT leak a personal demo block!
        self.assertIn("خطتك الشخصية", edits[0][3])
        self.assertNotIn("خطتك الشخصية", edits[1][3])


class TestPhase6VersionedUpdateAndRollback(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="fysb_vupd_"))
        self.dest = self.tmp / "app"
        self.dest.mkdir()
        # Seed a live running app at v6.16.0 with protected .env & DB
        (self.dest / "main.py").write_text("# main 6.16.0\n", encoding="utf-8")
        (self.dest / "fysb").mkdir()
        (self.dest / "fysb" / "config.py").write_text("VERSION = '6.16.0'\n", encoding="utf-8")
        (self.dest / ".env").write_text("BOT_TOKEN=KEEP_ME_SECRET\n", encoding="utf-8")
        (self.dest / "database").mkdir()
        (self.dest / "database" / "bot.db").write_text("LIVE_DB", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalize_tag(self):
        self.assertEqual(updater.normalize_tag("6.14.1"), "v6.14.1")
        self.assertEqual(updater.normalize_tag("v6.14.1"), "v6.14.1")
        self.assertEqual(updater.normalize_tag("V6.15.0"), "v6.15.0")
        self.assertEqual(updater.normalize_tag("../evil"), "")
        self.assertEqual(updater.normalize_tag("not-a-ver"), "")

    def test_versioned_downgrade_saves_snapshot_and_rollback_restores_offline(self):
        pkg_old = self.tmp / "v6.14.1.zip"
        _build_pkg(pkg_old, "6.14.1")
        sha_old = updater.sha256_file(pkg_old)

        def fake_dl(url, dpath, timeout=updater.TIMEOUT_ZIP):
            pathlib.Path(dpath).write_bytes(pkg_old.read_bytes())
            return pathlib.Path(dpath)

        with patch.object(updater, "release_by_tag",
                          return_value={"tag": "v6.14.1", "url": "http://x/old.zip",
                                        "name": "ForexYemeni1.zip", "sha256": sha_old}), \
             patch.object(updater, "_download", side_effect=fake_dl):
            res = updater.self_update(dest_root=self.dest, target_tag="6.14.1", force=True)

        self.assertEqual(res["status"], "updated", res)
        self.assertEqual(res["direction"], "downgrade")
        self.assertTrue(res.get("sha256_verified"))
        self.assertTrue(res.get("rollback_backup"))
        self.assertIn("6.14.1", (self.dest / "fysb" / "config.py").read_text(encoding="utf-8"))
        # Protected files untouched
        self.assertEqual((self.dest / ".env").read_text(encoding="utf-8"), "BOT_TOKEN=KEEP_ME_SECRET\n")
        self.assertEqual((self.dest / "database" / "bot.db").read_text(encoding="utf-8"), "LIVE_DB")

        # Verify rollback snapshot does NOT contain .env or bot.db
        snaps = updater.list_rollback_snapshots(self.dest)
        self.assertEqual(len(snaps), 1)
        with zipfile.ZipFile(snaps[0]) as z:
            names = z.namelist()
        self.assertFalse(any(n.endswith(".env") or n.endswith(".db") for n in names))

        # Now test offline rollback back to original 6.16.0 code!
        rb = updater.rollback_update(dest_root=self.dest)
        self.assertEqual(rb["status"], "rolled_back", rb)
        self.assertIn("6.16.0", (self.dest / "fysb" / "config.py").read_text(encoding="utf-8"))
        self.assertEqual((self.dest / ".env").read_text(encoding="utf-8"), "BOT_TOKEN=KEEP_ME_SECRET\n")

    def test_update_command_version_arg_and_list_and_rollback(self):
        class _Upd:
            def __init__(self, uid):
                self.effective_user = type("U", (), {"id": uid})()
                self.effective_message = MockMessage()

        # 1) /update v6.14.1 -> calls self_update(None, "v6.14.1", True) & restarts
        upd1 = _Upd(ADMIN_ID)
        ctx1 = type("C", (), {"args": ["v6.14.1"]})()
        restarted = {"n": 0}

        async def _no_sleep(_s):
            return None

        with patch.object(updater, "self_update",
                          return_value={"status": "updated", "local": config.VERSION,
                                        "remote": "v6.14.1", "files": 42,
                                        "direction": "downgrade", "sha256_verified": True,
                                        "rollback_backup": "update_rollback_v6.16.0.zip"}) as m_upd, \
             patch.object(updater, "restart", side_effect=lambda: restarted.__setitem__("n", restarted["n"] + 1)), \
             patch("asyncio.sleep", side_effect=_no_sleep):
            asyncio.run(update_command(upd1, ctx1))
        m_upd.assert_called_once_with(None, "v6.14.1", True)
        self.assertEqual(restarted["n"], 1)
        self.assertIn("الرجوع وتثبيت الإصدار", upd1.effective_message.edits[-1]["text"])

        # 2) /update list -> shows available releases & buttons
        upd2 = _Upd(ADMIN_ID)
        ctx2 = type("C", (), {"args": ["list"]})()
        with patch.object(updater, "list_releases",
                          return_value=[{"tag": "v6.16.0", "url": "http://x", "name": "ForexYemeni1.zip"},
                                        {"tag": "v6.14.1", "url": "http://y", "name": "ForexYemeni1.zip"}]), \
             patch.object(updater, "list_rollback_snapshots", return_value=[]):
            asyncio.run(update_command(upd2, ctx2))
        edited = upd2.effective_message.edits[-1]["text"]
        self.assertIn("/update v6.14.1", edited)

        # 3) /update rollback -> calls rollback_update & restarts
        upd3 = _Upd(ADMIN_ID)
        ctx3 = type("C", (), {"args": ["rollback"]})()
        with patch.object(updater, "rollback_update",
                          return_value={"status": "rolled_back", "local": config.VERSION,
                                        "restored_version": "6.14.1", "files": 40}), \
             patch.object(updater, "restart", side_effect=lambda: restarted.__setitem__("n", restarted["n"] + 1)), \
             patch("asyncio.sleep", side_effect=_no_sleep):
            asyncio.run(update_command(upd3, ctx3))
        self.assertEqual(restarted["n"], 2)
        self.assertIn("تم التراجع بنجاح", upd3.effective_message.edits[-1]["text"])

    def test_admin_panel_updates_and_confirm_callbacks(self):
        with patch.object(updater, "list_releases",
                          return_value=[{"tag": "v6.16.0", "url": "http://x", "name": "ForexYemeni1.zip"},
                                        {"tag": "v6.14.1", "url": "http://y", "name": "ForexYemeni1.zip"}]), \
             patch.object(updater, "list_rollback_snapshots", return_value=[]):
            q1, _ = _fire("a:updates")
        self.assertIn("إدارة التحديثات والإصدارات", page_of(q1))
        q2, _ = _fire("a:updver:v6.14.1")
        self.assertIn("v6.14.1", page_of(q2))
        self.assertIn("تأكيد", page_of(q2))


if __name__ == "__main__":
    unittest.main()
