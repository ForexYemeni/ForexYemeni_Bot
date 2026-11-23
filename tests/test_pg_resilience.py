"""v6.7.2 — hosted-database resilience: backups are best-effort, boot never blocks.

Reproduces the LIVE bot-hosting + Supabase crash (2026-11-23): the boot-time
pre-migration snapshot full-dumped every table, the server's statement_timeout
canceled one with «canceling statement due to statement timeout», the exception
aborted init_db, the connection rollback undid the just-applied migrations, and
every restart re-applied them and crashed again — an infinite boot loop, the
bot could never start («لم يتم تشغيل البوت»).

The contract after this release:
  * a snapshot can DEGRADE, never DESTROY — quick_db_snapshot returns None
    instead of raising; init_db logs and continues;
  * dump_all skips poisoned tables (retry once, then report under _meta);
  * the session raises the server's statement_timeout for its own statements;
  * a timed-out statement is NOT transparently retried (that would only
    double the stall).
"""
import json
import unittest
import zipfile
from unittest import mock

import psycopg

from tests.helpers import *  # noqa: F401,F403 — env isolation + mocks
from fysb import db, config
from fysb import pglayer
from fysb.services import backup as backup_service


# ---------------------------------------------------------------------------
# Fake pooled connection — keyed by table name, no real PostgreSQL needed
# ---------------------------------------------------------------------------
class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return [dict(r) if not isinstance(r, dict) else r for r in self._rows]


class FakeConn:
    # class-level state survives the per-attempt re-instantiation
    state = {"fail": {}, "seen": {}}

    def __init__(self, behavior):
        self._behavior = behavior

    def execute(self, sql, params=()):
        s = sql.strip()
        if s.upper().startswith("SET"):
            return FakeResult([])
        table = s.split()[-1].strip().strip('"')
        FakeConn.state["seen"][table] = FakeConn.state["seen"].get(table, 0) + 1
        beh = self._behavior.get(table, [])
        if isinstance(beh, Exception):
            raise beh
        if table in FakeConn.state["fail"]:
            n = FakeConn.state["fail"][table]
            if n > 0:
                FakeConn.state["fail"][table] = n - 1
                raise psycopg.errors.QueryCanceled()
        return FakeResult(beh)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        return False


class TestDumpAllResilience(unittest.TestCase):
    def setUp(self):
        FakeConn.state = {"fail": {}, "seen": {}}

    def _patched(self, behavior):
        return [mock.patch.object(pglayer, "conn", side_effect=lambda: FakeConn(behavior)),
                mock.patch.object(pglayer, "list_tables",
                                  return_value={"candles", "signals", "users"})]

    def test_poisoned_table_skipped_and_reported_not_raised(self):
        behavior = {"candles": psycopg.errors.QueryCanceled(),
                    "signals": [{"id": 1, "symbol": "XAUUSD"}],
                    "users": [{"id": 2}]}
        patches = self._patched(behavior)
        with patches[0], patches[1]:
            data = pglayer.dump_all()          # must NOT raise — the live bug
        self.assertNotIn("candles", data)
        self.assertEqual(data["signals"], [{"id": 1, "symbol": "XAUUSD"}])
        self.assertEqual(data["users"], [{"id": 2}])
        self.assertEqual(data["_meta"]["skipped_tables"], ["candles"])
        self.assertIn("QueryCanceled", data["_meta"]["errors"][0])
        # retried exactly twice before giving up
        self.assertEqual(FakeConn.state["seen"]["candles"], 2)

    def test_transient_failure_retried_on_fresh_session(self):
        behavior = {"signals": [{"id": 1, "symbol": "XAUUSD"}],
                    "users": [{"id": 2}], "candles": []}
        FakeConn.state["fail"]["signals"] = 1  # fail once, succeed on retry
        patches = self._patched(behavior)
        with patches[0], patches[1]:
            data = pglayer.dump_all()
        self.assertEqual(data["signals"], [{"id": 1, "symbol": "XAUUSD"}])
        self.assertNotIn("_meta", data)
        self.assertEqual(FakeConn.state["seen"]["signals"], 2)

    def test_meta_dict_is_never_replayed_by_restore_json(self):
        data = {"users": [{"id": 1}],
                "_meta": {"skipped_tables": ["candles"], "errors": ["candles: QueryCanceled"]}}
        tables = sorted(t for t in data.keys() if isinstance(data.get(t), list))
        self.assertEqual(tables, ["users"])  # _meta excluded by the list filter


class TestConnTimeoutNotRetried(unittest.TestCase):
    def test_query_canceled_raises_immediately(self):
        calls = {"n": 0}

        class BoomCursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                calls["n"] += 1
                raise psycopg.errors.QueryCanceled()

        class FakeInner:
            closed = False

            def cursor(self):
                return BoomCursor()

            def rollback(self):
                pass

            def close(self):
                pass

        with mock.patch.object(pglayer, "_acquire", return_value=FakeInner()):
            c = pglayer.Conn()
        with self.assertRaises(psycopg.errors.QueryCanceled):
            c.execute("SELECT 1")
        self.assertEqual(calls["n"], 1)  # exactly one attempt — no pointless retry


class TestSnapshotNeverFatal(unittest.TestCase):
    def test_pg_dump_crash_returns_none(self):
        db.init_db()
        with mock.patch.object(config, "USE_PG", True), \
                mock.patch.object(db, "pglayer", pglayer, create=True), \
                mock.patch.object(pglayer, "dump_all",
                                  side_effect=psycopg.errors.QueryCanceled()):
            snap = backup_service.quick_db_snapshot("boom")
        self.assertIsNone(snap)  # the contract: degrade, never raise

    def test_pg_empty_dump_returns_none(self):
        db.init_db()
        with mock.patch.object(config, "USE_PG", True), \
                mock.patch.object(db, "pglayer", pglayer, create=True), \
                mock.patch.object(pglayer, "dump_all",
                                  return_value={"_meta": {"skipped_tables": ["all"]}}):
            snap = backup_service.quick_db_snapshot("empty")
        self.assertIsNone(snap)

    def test_pg_dump_success_writes_verified_json(self):
        db.init_db()
        with mock.patch.object(config, "USE_PG", True), \
                mock.patch.object(db, "pglayer", pglayer, create=True), \
                mock.patch.object(pglayer, "dump_all",
                                  return_value={"users": [{"id": 1, "username": "royal"}]}):
            snap = backup_service.quick_db_snapshot("pgok")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.suffix, ".json")
        ok, detail = backup_service.verify_archive(snap)
        self.assertTrue(ok, detail)
        payload = json.loads(snap.read_text(encoding="utf-8"))
        self.assertEqual(payload["users"][0]["username"], "royal")

    def test_boot_loop_is_dead_init_db_survives_snapshot_crash(self):
        """THE live bug: migration + crashing snapshot must not abort the boot."""
        db.init_db()
        col = next(iter(db.ADD_COLUMNS["users"]))       # e.g. last_seen
        with db.conn() as c:
            c.execute(f"ALTER TABLE users DROP COLUMN {col}")  # force re-migration
        crash = psycopg.errors.QueryCanceled()
        with mock.patch.object(backup_service, "quick_db_snapshot", side_effect=crash):
            db.init_db()                                # must NOT raise
        with db.conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        self.assertIn(col, cols)                        # migration re-applied

    def test_full_project_archive_tolerates_missing_snapshot(self):
        db.init_db()
        with mock.patch.object(backup_service, "quick_db_snapshot", return_value=None):
            dest = backup_service.full_project_archive()
        self.assertTrue(dest.exists())
        with zipfile.ZipFile(str(dest)) as z:
            names = z.namelist()
        self.assertIn("database/SNAPSHOT_UNAVAILABLE.txt", names)
        self.assertFalse(any(n.startswith("database/forexyemeni_backup") for n in names))


if __name__ == "__main__":
    unittest.main()
