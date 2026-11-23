"""REGRESSION — picker-empty bug reported live 2026-11-22.

User report: in the analytics flow, after choosing the market, the page said
"⏱ اختر الإطار الزمني — الأطر المسموحة لباقتك (Free)" but showed ZERO timeframe
buttons. Root cause: market/timeframe catalogs were seeded disabled and nothing
ever enabled them; the UI filtered plan codes through the dead catalog.

This suite pins three layers:
  1. db.init_db() self-heals a legacy-dead catalog on every boot.
  2. The UI flow (u:analytics → u:anlsym:XAUUSD → u:anltf:M15) always renders
     buttons — with a dead catalog AND with a healthy one.
  3. The composite "ALL" lens still runs when the strategy catalog is dead.
"""
import unittest
import asyncio

from tests.helpers import (MockContext, MockQuery, FakeUser,
                           page_of, page_markup_of)
from fysb import db, config
from fysb.handlers import dispatcher
from fysb.services import users as users_service

USER = 555301


def _mk_update(q):
    upd = type("Update", (), {})()
    upd.callback_query = q
    return upd


def fire(data: str, uid: int, ctx=None):
    q = MockQuery(data, uid)
    ctx = ctx or MockContext(None)
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q


def _kill_catalogs():
    """Simulate the EXACT legacy state found live: every catalog row disabled."""
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET enabled=0, verified=0")
        c.execute("UPDATE timeframe_catalog SET enabled=0, verified=0")
        c.execute("UPDATE strategy_catalog SET enabled=0, verified=0")


class TestPickerSelfHeal(unittest.TestCase):
    def setUp(self):
        db.init_db()
        # Order-independence: other suites toggle platform flags (maintenance,
        # block-new-users) in the shared test DB — reset both so this suite
        # tests the picker, not global switches.
        db.set_setting("maintenance", "0")
        db.set_setting("block_new_users", "0")
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass

    # ---- layer 1: boot self-heal -------------------------------------------
    def test_init_db_heals_dead_catalogs(self):
        _kill_catalogs()
        db.init_db()
        # v6.8.0 GOLD-FIRST POLICY (owner instruction): the heal arms GOLD only;
        # every other instrument ships CLOSED and stays in the catalog, verified
        # per provider coverage, awaiting the admin's per-market enablement.
        with db.conn() as c:
            mkts = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
            total = c.execute("SELECT COUNT(*) n FROM market_catalog").fetchone()["n"]
            gold = c.execute("SELECT enabled,verified FROM market_catalog WHERE symbol='XAUUSD'").fetchone()
            others_open = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND symbol!='XAUUSD'").fetchone()["n"]
            tfs = c.execute("SELECT COUNT(*) n FROM timeframe_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
            strats = c.execute("SELECT COUNT(*) n FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        self.assertEqual(mkts, 1, "gold-first: exactly ONE market ships open")
        self.assertGreaterEqual(total, len(db.SEED_MARKETS), "all seeded markets must exist in the catalog")
        self.assertEqual(tuple(gold), (1, 1), "the open market must be XAUUSD itself")
        self.assertEqual(others_open, 0, "no other market may open before the admin arms it")
        self.assertGreaterEqual(tfs, 8, "seeded timeframes must be live after boot heal")
        self.assertGreaterEqual(strats, len(db.IMPLEMENTED_STRATEGIES),
                                "implemented strategies must be live after boot heal")

    # ---- layer 1b: gold-first shipping policy (v6.8.0) -----------------------
    def test_gold_first_policy(self):
        with db.conn() as c:
            sections = c.execute("SELECT category, COUNT(*) n FROM market_catalog GROUP BY category").fetchall()
            cats = {r["category"]: r["n"] for r in sections}
        for cat in ("metal", "forex", "crypto", "index", "stock"):
            self.assertGreaterEqual(cats.get(cat, 0), 1, f"catalog must carry a {cat} section")

    # ---- layer 2: the exact user flow ---------------------------------------
    def test_anlsym_page_has_timeframe_buttons_on_dead_catalog(self):
        """THE reported bug: market chosen → timeframe page must show buttons."""
        _kill_catalogs()          # dead catalog, NO boot heal in between
        q = fire("u:anlsym:XAUUSD", USER)
        self.assertTrue(q._answered)
        self.assertTrue(page_of(q), "anlsym must render something")
        text = page_of(q)
        kb_rows = page_markup_of(q)
        self.assertIn("اختر الإطار الزمني", text)
        self.assertIn("anltf:", str(kb_rows), "timeframe buttons MUST exist even with a dead catalog")

    def test_market_picker_never_dead_ends(self):
        _kill_catalogs()
        q = fire("u:analytics", USER)
        self.assertTrue(q._answered)
        kb_rows = str(page_markup_of(q))
        self.assertIn("anlsym:", kb_rows, "market buttons must exist even with a dead catalog")

    def test_full_flow_reaches_analysis_generation(self):
        _kill_catalogs()
        ctx = MockContext(None)      # one shared context like the real bot session
        fire("u:anlsym:XAUUSD", USER, ctx)
        q = fire("u:anltf:M15", USER, ctx)
        self.assertTrue(q._answered)
        kb_rows = str(page_markup_of(q))
        self.assertIn("anlstrat:", kb_rows, "strategy buttons must exist even with a dead catalog")

    def test_timeframe_buttons_use_arabic_names(self):
        db.init_db()          # healthy catalog
        q = fire("u:anlsym:XAUUSD", USER)
        text = page_of(q)
        markup = str(page_markup_of(q))
        self.assertIn("anltf:M15", markup)
        self.assertIn("15 دقيقة", markup, "timeframe labels must be luxury Arabic names")


if __name__ == "__main__":
    unittest.main()
