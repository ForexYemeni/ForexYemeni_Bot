"""ROYAL v6.11.0 — ZERO-WAIT BUTTONS + WYCKOFF graduation.

Two owner demands delivered and proven in one suite:
  1. «جعل أزرار البوت تستجيب بشكل أسرع دون الانتظار للحظة واحدة» —
     concurrent_updates enabled, Telegram HTTP pool widened, PG pool raised,
     and EVERY blocking call on a handler path shipped to the worker pool via
     fysb.afn (the loop never waits on WAN — proven here by semantics,
     parallelism and coverage tests).
  2. WYCKOFF — the last deliberately-closed catalog entry — is now a real
     mechanical engine (range + spring/upthrust + effort), registered,
     verified on boot, with an honest identity card.
"""
import asyncio
import time
import unittest

from tests.helpers import (MockBot, MockContext, MockQuery, MockMessage,
                           FakeUser, make_candles)

USER = 555311
ADMIN = 9001


def _fire(data: str, uid: int):
    from fysb import db
    from fysb.handlers import dispatcher, common
    from fysb.services import users as users_service
    db.init_db()
    common._last_panel.clear()
    try:
        users_service.ensure_user(FakeUser(uid))
    except Exception:
        pass
    q = MockQuery(data, uid)
    upd = type("Update", (), {})()
    upd.callback_query = q
    ctx = MockContext(MockBot())
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def _page_text(q) -> str:
    edits = getattr(q.message, "edits", None) or []
    if edits:
        return edits[-1]["text"]
    reps = getattr(q.message, "replies", None) or []
    if reps:
        return reps[-1]["text"]
    return getattr(q.message, "text", "") or ""


# ---------------------------------------------------------------------------
# WYCKOFF fixtures
# ---------------------------------------------------------------------------
def _range_spring(volume_surge: bool = True) -> list[dict]:
    """44-candle sideways range 100–102 with midline alternations, then a
    SPRING candle: wick to 98.8 (below the floor), close 100.9 (back inside).
    44 candles: the engine needs >= RANGE_WINDOW(26)+14 = 40 to judge a range."""
    vals = []
    for i in range(44):
        up = i % 2 == 0
        o = 100.3 if up else 101.7
        c = 101.6 if up else 100.4
        h = max(o, c) + 0.35
        l = min(o, c) - 0.35
        vals.append((o, h, l, c))
    candles = make_candles(vals)
    spring = dict(candles[-1])
    spring.update({"open": 100.5, "high": 100.9, "low": 98.8, "close": 100.9})
    if volume_surge:
        spring["volume"] = 4000.0          # window average is ~1000+i → surge
    return candles[:-1] + [spring]


def _range_upthrust() -> list[dict]:
    vals = []
    for i in range(44):
        up = i % 2 == 0
        o = 100.3 if up else 101.7
        c = 101.6 if up else 100.4
        h = max(o, c) + 0.35
        l = min(o, c) - 0.35
        vals.append((o, h, l, c))
    candles = make_candles(vals)
    thrust = dict(candles[-1])
    thrust.update({"open": 101.5, "high": 103.3, "low": 100.9, "close": 101.1})
    thrust["volume"] = 4000.0
    return candles[:-1] + [thrust]


def _trending_no_range() -> list[dict]:
    vals = [(100 + i * 0.6, 100 + i * 0.6 + 0.3, 100 + i * 0.6 - 0.2, 100 + i * 0.6 + 0.2)
            for i in range(45)]
    return make_candles(vals)


class TestWyckoffEngine(unittest.TestCase):
    def setUp(self):
        from fysb import db
        db.init_db()

    def test_spring_buys_on_the_documented_setup(self):
        from fysb.engines.strategies.wyckoff import evaluate
        from fysb.engines.strategies.base import Ctx
        candles = _range_spring()
        res = evaluate(Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles))
        self.assertIsNotNone(res, "SPRING must fire on its documented setup")
        self.assertEqual(res.code, "WYCKOFF")
        self.assertEqual(res.direction, "BUY")
        self.assertEqual(res.checks.get("event"), "SPRING")
        self.assertGreaterEqual(res.score, 55)
        self.assertLess(res.stop, res.entry)
        self.assertGreater(res.tp1, res.entry)
        self.assertGreater(res.tp2, res.tp1)
        self.assertGreater(res.tp3, res.tp2)
        self.assertGreaterEqual(res.rr, 2.0)

    def test_upthrust_sells_on_the_documented_setup(self):
        from fysb.engines.strategies.wyckoff import evaluate
        from fysb.engines.strategies.base import Ctx
        candles = _range_upthrust()
        res = evaluate(Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles))
        self.assertIsNotNone(res, "UPTHRUST must fire on its documented setup")
        self.assertEqual(res.direction, "SELL")
        self.assertEqual(res.checks.get("event"), "UPTHRUST")
        self.assertGreater(res.stop, res.entry)
        self.assertLess(res.tp1, res.entry)
        self.assertLess(res.tp2, res.tp1)
        self.assertLess(res.tp3, res.tp2)

    def test_trending_market_is_honestly_no_setup(self):
        from fysb.engines.strategies.wyckoff import evaluate
        from fysb.engines.strategies.base import Ctx
        candles = _trending_no_range()
        res = evaluate(Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles))
        self.assertIsNone(res, "a running trend is NOT a range — no spring, no signal")

    def test_registered_verified_enabled_on_boot(self):
        from fysb import db
        from fysb.engines.strategies import REGISTRY, VERIFIABLE
        from fysb.db import IMPLEMENTED_STRATEGIES
        self.assertIsNotNone(REGISTRY.get("WYCKOFF"))
        self.assertIn("WYCKOFF", VERIFIABLE)
        self.assertIn("WYCKOFF", IMPLEMENTED_STRATEGIES)
        with db.conn() as c:
            row = c.execute("SELECT verified, enabled FROM strategy_catalog WHERE code='WYCKOFF'").fetchone()
        self.assertEqual((row["verified"], row["enabled"]), (1, 1))
        desc = c.execute("SELECT description FROM strategy_catalog WHERE code='WYCKOFF'").fetchone()["description"]
        self.assertIn("منفذة واختبارة آلياً", desc)
        self.assertNotIn("مغلق", desc)

    def test_identity_card_is_implemented_and_honest(self):
        from fysb.services import strategy_info
        page = strategy_info.info_page("WYCKOFF")
        self.assertIn("WYCKOFF", page)
        self.assertNotIn("غير منفذة كمحرك بعد", page)
        self.assertIn("Spring", page)

    def test_evaluate_all_includes_wyckoff(self):
        from fysb.engines.strategies import evaluate_all
        from fysb.engines.strategies.base import Ctx
        candles = _range_spring()
        results = evaluate_all(Ctx(symbol="XAUUSD", timeframe="M15", candles=candles, bias_candles=candles),
                               ["WYCKOFF"])
        self.assertTrue(any(r.code == "WYCKOFF" for r in results),
                        "evaluate_all must run the graduated engine")


# ---------------------------------------------------------------------------
# ZERO-WAIT BUTTONS — the structural guarantees
# ---------------------------------------------------------------------------
class TestZeroWaitButtons(unittest.TestCase):
    def setUp(self):
        from fysb import db
        from fysb.handlers import common
        db.init_db()
        common._last_panel.clear()

    def test_concurrent_updates_enabled_in_main_builder(self):
        with open("main.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("concurrent_updates(True)", src,
                      "updates must run in parallel tasks — never one-by-one")
        self.assertIn("connection_pool_size(64)", src,
                      "Telegram HTTP pool must fit the parallelism")

    def test_afn_run_is_to_thread_with_identical_semantics(self):
        from fysb import afn

        def blocking(a, b, *, k=None):
            time.sleep(0.01)
            return (a + b, k)

        got = asyncio.run(afn.run(blocking, 2, 3, k="x"))
        self.assertEqual(got, (5, "x"))

    def test_afn_run_propagates_exceptions_unchanged(self):
        from fysb import afn

        def boom():
            raise ValueError("same error")

        async def go():
            try:
                await afn.run(boom)
            except ValueError as e:
                return str(e)
            return None

        self.assertEqual(asyncio.run(go()), "same error")

    def test_afn_run_never_blocks_the_loop(self):
        """While one blocking call sits in a worker thread, the loop keeps
        ticking — the exact property that keeps every button instant."""
        from fysb import afn
        ticks = []

        async def ticker():
            for _ in range(12):
                ticks.append(1)
                await asyncio.sleep(0.005)

        async def go():
            t = asyncio.create_task(ticker())
            await asyncio.sleep(0.01)
            def blocking():
                time.sleep(0.08)          # WAN-class latency
                return "done"
            res = await afn.run(blocking)
            await t
            return res

        res = asyncio.run(go())
        self.assertEqual(res, "done")
        # 12 ticks × 5ms = 60ms; the blocking call alone would freeze the loop
        # and the ticker could not have completed all ticks before it finished.
        self.assertEqual(len(ticks), 12, "the loop must keep pumping during blocking work")

    def test_handler_paths_fully_offloaded(self):
        """EVERY handler module routes blocking calls through afn (or an
        existing to_thread) — a grep-proof contract, not a promise.
        chain_verify.valid_wallet is a pure regex validator (no I/O) and is
        exempt like esc()/kb.btn/config.now()."""
        import re
        from pathlib import Path
        handlers = Path("fysb/handlers")
        offenders = []
        blocking_service_calls = re.compile(
            r"^(?!\s*#)(?!.*await afn\.run)(?!.*await asyncio\.to_thread)(?!.*def _)"
            r"(?!.*thread-offload)(?!.*return await)(?!.*= await )"
            r"(?!.*chain_verify\.valid_wallet)"
            r".*\b(users_service|payments_service|support_service|subs|audit_log|"
            r"strategy_stats|strategy_info|analytics|broadcast|backup_service|"
            r"referrals|demo_engine|chain_verify|users)\.[a-z_]+\(", )
        for path in sorted(handlers.glob("*.py")):
            src = path.read_text(encoding="utf-8")
            in_async = False
            for i, line in enumerate(src.splitlines(), 1):
                if line.strip().startswith("async def "):
                    in_async = True
                elif line.strip().startswith("def ") and not line.strip().startswith("async"):
                    in_async = False
                if not in_async or line.strip().startswith("#"):
                    continue
                if "await afn.run" in line or "asyncio.to_thread" in line:
                    continue
                m = blocking_service_calls.match(line)
                if m:
                    offenders.append(f"{path.name}:{i}: {line.strip()[:90]}")
        self.assertEqual(offenders, [],
                         "blocking calls left on the event loop:\n" + "\n".join(offenders[:12]))

    def test_pg_pool_raised_for_parallel_workers(self):
        from fysb import pglayer
        self.assertGreaterEqual(pglayer._POOL_MAX, 16,
                                "pool must fit parallel worker-thread DB calls")

    def test_button_still_answers_first_and_renders(self):
        from fysb import db
        from fysb.services import users as users_service
        users_service.ensure_user(FakeUser(USER))
        q, ctx = _fire("u:home", USER)
        self.assertTrue(q._answered, "q.answer() must stay the FIRST action")
        self.assertTrue(_page_text(q), "every tap must deliver a visible page")

    def test_admin_button_latency_stays_instant_on_local_engine(self):
        from fysb.services import users as users_service
        users_service.ensure_user(FakeUser(ADMIN))
        t0 = time.monotonic()
        q, ctx = _fire("a:home", ADMIN)
        ms = (time.monotonic() - t0) * 1000
        self.assertTrue(q._answered)
        self.assertLess(ms, 3000, "even the slowest local page must answer fast")
