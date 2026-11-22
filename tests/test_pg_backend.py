"""Live PostgreSQL engine test — gated by FY_TEST_DATABASE_URL.

CI never sets FY_TEST_DATABASE_URL, so every test here SKIPS and the suite
stays green on SQLite. To run the live checks locally against the real
Supabase/PostgreSQL:

    FY_TEST_DATABASE_URL="postgresql://..." python -m pytest tests/test_pg_backend.py -v

The gate uses a dedicated variable (NOT DATABASE_URL) because helpers.py
deliberately neutralizes DATABASE_URL to keep the 189 SQLite tests isolated
from any inherited/ancestor .env values (load_dotenv pollution).
"""
import os
import unittest

from fysb import config, db

_LIVE_URL = os.getenv("FY_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(_LIVE_URL, "FY_TEST_DATABASE_URL not set — PostgreSQL live test skipped")
class TestPostgresLive(unittest.TestCase):
    def setUp(self):
        from fysb import pglayer
        self._old_url = config.DATABASE_URL
        self._old_flag = config.USE_PG
        config.DATABASE_URL = _LIVE_URL
        config.USE_PG = True
        db.pglayer = pglayer
        self.addCleanup(self._detach)

    def _detach(self):
        db.pglayer.close_pool()
        config.DATABASE_URL = self._old_url
        config.USE_PG = self._old_flag

    def test_engine_is_pg_and_healthy(self):
        self.assertTrue(config.USE_PG)
        ok, detail = db.healthy()
        self.assertTrue(ok, detail)

    def test_schema_and_seeds(self):
        db.init_db()
        with db.conn() as c:
            plans = c.execute("SELECT code FROM plans").fetchall()
            self.assertGreaterEqual(len(plans), 3)
            self.assertIn("VIP", {p[0] for p in plans})

    def test_lastrowid_and_row_semantics(self):
        with db.conn() as c:
            cur = c.execute(
                "INSERT INTO engine_runs(symbol,timeframe,result,created_at) VALUES(?,?,?,?)",
                ("PGTEST", "M15", "smoke", config.now()))
            rid = cur.lastrowid
            self.assertIsInstance(rid, int)
            row = c.execute("SELECT id,symbol FROM engine_runs WHERE id=?", (rid,)).fetchone()
            self.assertEqual(row[0], rid)                    # integer access
            self.assertEqual(dict(row)["symbol"], "PGTEST")  # dict() conversion
            c.execute("DELETE FROM engine_runs WHERE id=?", (rid,))

    def test_insert_or_ignore_idempotent(self):
        with db.conn() as c:
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('pgtest_key','a')")
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('pgtest_key','b')")
            v = c.execute("SELECT value FROM settings WHERE key='pgtest_key'").fetchone()
            self.assertEqual(v["value"], "a")
            c.execute("DELETE FROM settings WHERE key='pgtest_key'")


if __name__ == "__main__":
    unittest.main()
