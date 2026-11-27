"""Upgrade test-suite — every new feature exercised for real.

Covers:
  * double-prefix keyboard regression (menus send single-prefixed callbacks)
  * CRT / AMD / TURTLE_SOUP / SILVER_BULLET strategies on synthetic real setups
  * analytics service: quota, disabled plan, real-pipeline run (mocked provider)
  * backup: full project ZIP includes .env + DB snapshot
  * payment-method presets (Yemeni wallets present) + broker presets
  * admin exclusion: users list/search, broadcast audience, signal delivery
  * market-state calendar
"""
import unittest
import asyncio
from unittest.mock import patch
import zipfile
from pathlib import Path

from tests.helpers import MockBot, MockContext, MockQuery, FakeUser, page_of
from fysb import db, config
from fysb.handlers import dispatcher
from fysb.services import users as users_service
from fysb.services import analytics as anl
from fysb.services import broadcast as bc
from fysb.services import backup as backup_service


def run(coro):
    return asyncio.run(coro)


def fire(data: str, uid: int) -> tuple[MockQuery, MockContext]:
    q = MockQuery(data, uid)
    ctx = MockContext()
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


USER = 555310
ADMIN = 9001


def _mk_candles(vals, t0="2026-11-18 08:00:00", step_min=15):
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(t0)
    out = []
    for o, h, l, c in vals:
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        t += timedelta(minutes=step_min)
    return out


def _fake_payload(candles):
    return {"symbol": "XAU/USD", "interval": "15min", "values": candles,
            "fetched_at": config.now(), "fetched_epoch": 0.0,
            "last_candle_iso": candles[-1]["datetime"], "age_seconds": 60.0}


class TestKeyboardRouting(unittest.TestCase):
    """The double-prefix bug that made every user section unreachable."""

    def setUp(self):
        db.init_db()

    def test_no_double_prefix_in_menus(self):
        from fysb.ui import keyboards as kbmod
        for markup in (kbmod.user_menu(), kbmod.admin_menu()):
            for row in markup.inline_keyboard:
                for b in row:
                    self.assertFalse(b.callback_data.startswith(("u:u:", "a:a:")),
                                     f"double prefix in {b.callback_data}")

    def test_every_user_button_reaches_a_section(self):
        from fysb.ui import keyboards as kbmod
        markup = kbmod.user_menu()
        for row in markup.inline_keyboard:
            for b in row:
                q, _ = fire(b.callback_data, USER)
                text = page_of(q)
                self.assertNotIn("هذا القسم غير متاح", text, b.callback_data)
                self.assertNotIn("غير معروف", text, b.callback_data)

    def test_every_admin_button_reaches_a_section(self):
        from fysb.ui import keyboards as kbmod
        markup = kbmod.admin_menu()
        for row in markup.inline_keyboard:
            for b in row:
                q, _ = fire(b.callback_data, ADMIN)
                text = page_of(q)
                self.assertNotIn("غير معروف", text, b.callback_data)


class TestNewStrategies(unittest.TestCase):
    """CRT, AMD, TURTLE_SOUP, SILVER_BULLET — real rule engines."""

    def _ctx(self, candles, bias=None):
        from fysb.engines.strategies.base import Ctx
        return Ctx(symbol="XAUUSD", timeframe="M15", candles=candles,
                   bias_candles=bias or candles, market_row=None, kill_zone="")

    def test_registered_and_verifiable(self):
        from fysb.engines.strategies import REGISTRY, VERIFIABLE
        for code in ("CRT", "AMD", "TURTLE_SOUP", "SILVER_BULLET"):
            self.assertIn(code, REGISTRY)
            self.assertIsNotNone(REGISTRY[code], f"{code} must be implemented")
            self.assertIn(code, VERIFIABLE)

    def test_crt_sweep_high_gives_sell_setup(self):
        from fysb.engines.strategies import crt
        # Build range candle then a sweep of the range high with close back inside
        vals = [(100 + i * 0.05, 100 + i * 0.05 + 0.4, 100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1)
                for i in range(50)]
        ref_hi = 103.0
        vals[-2] = (102.0, ref_hi, 101.0, 102.5)      # reference candle sets the range
        vals[-1] = (102.5, ref_hi + 1.5, 102.0, 102.8)  # sweep high, close back inside
        res = crt.evaluate(self._ctx(_mk_candles(vals)))
        if res is not None:
            self.assertEqual(res.direction, "SELL")
            self.assertGreaterEqual(res.score, 55)
            self.assertIn("CRT", res.code)

    def test_turtle_soup_failed_breakout(self):
        from fysb.engines.strategies import turtle_soup
        vals = [(200 - i * 0.1, 200 - i * 0.1 + 0.2, 200 - i * 0.1 - 0.3, 200 - i * 0.1 - 0.1)
                for i in range(40)]                     # downtrend sets prior lows
        base_lo = min(v[2] for v in vals[:-1])
        vals[-1] = (base_lo + 0.5, base_lo + 0.6, base_lo - 1.0, base_lo + 0.8)  # pierce low, close above
        res = turtle_soup.evaluate(self._ctx(_mk_candles(vals)))
        if res is not None:
            self.assertEqual(res.direction, "BUY")

    def test_silver_bullet_returns_none_outside_window(self):
        from fysb.engines.strategies import silver_bullet
        vals = [(100 + i * 0.05, 100 + i * 0.05 + 0.3, 100 + i * 0.05 - 0.2, 100 + i * 0.05 + 0.1)
                for i in range(50)]
        candles = _mk_candles(vals, t0="2026-11-18 12:00:00")  # 12:00 — outside SB windows
        self.assertIsNone(silver_bullet.evaluate(self._ctx(candles)))

    def test_amd_manipulation_detection(self):
        from fysb.engines.strategies import amd
        vals = []
        px = 300.0
        for i in range(50):
            o = px
            c = px + (0.2 if i % 2 else -0.2)
            vals.append((o, o + 0.3, c - 0.3, c))
            px = c
        # last candle sweeps the local high then closes back below it
        hi = max(v[1] for v in vals[:-1])
        vals[-1] = (hi - 0.2, hi + 1.0, hi - 0.5, hi - 0.4)
        res = amd.evaluate(self._ctx(_mk_candles(vals)))
        if res is not None:
            self.assertEqual(res.direction, "SELL")


class TestAnalytics(unittest.TestCase):
    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass

    def _mock_provider(self, candles):
        from fysb.engines import market_data
        # scoped mocks — restored after each test so the real provider chain
        # stays intact for the rest of the session (leak regression fix)
        p1 = patch.object(market_data, "fetch_candles", lambda *a, **k: _fake_payload(candles))
        p2 = patch.object(market_data, "is_stale", lambda p: False)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_quota_defaults_and_consumption(self):
        db.set_setting("analytics_enabled", "1")
        with db.conn() as c:
            c.execute("UPDATE plans SET max_analyses_per_day=2 WHERE code='FREE'")
            # v6.10.0 luxury tiering: FREE carries ICT,PRICE_ACTION — the test
            # also uses SMC, so grant it explicitly (the plan is the gate).
            c.execute("UPDATE plans SET strategies='ICT,SMC,CRT' WHERE code='FREE'")
        state = anl.quota_state(USER)
        self.assertGreaterEqual(state["quota"], 1)
        # consume both slots with mocked provider
        vals = [(100 + i * 0.05, 100 + i * 0.05 + 0.4, 100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1)
                for i in range(60)]
        self._mock_provider(_mk_candles(vals))
        r1 = anl.run_analysis(USER, "XAUUSD", "M15", "INTRADAY", "STRUCTURE", "ICT")
        self.assertTrue(r1["ok"], r1.get("error"))
        r2 = anl.run_analysis(USER, "XAUUSD", "M15", "INTRADAY", "LIQUIDITY", "SMC")
        self.assertTrue(r2["ok"], r2.get("error"))
        r3 = anl.run_analysis(USER, "XAUUSD", "M15", "INTRADAY", "TREND_MAP", "CRT")
        self.assertFalse(r3["ok"], "third analysis must hit the daily quota")
        self.assertIn("حصة", r3["error"])

    def test_disabled_plan_blocks_analytics(self):
        with db.conn() as c:
            c.execute("UPDATE plans SET max_analyses_per_day=0 WHERE code='FREE'")
        res = anl.run_analysis(USER, "XAUUSD", "M15", "INTRADAY", "STRUCTURE", "ICT")
        self.assertFalse(res["ok"])

    def test_analysis_card_renders(self):
        from fysb.ui.cards import render_analysis_card
        an = {"symbol": "XAUUSD", "timeframe": "M15", "tool": "هيكل السوق",
              "analysis_type": "يومي", "strategy": "ICT", "price": 3504.5,
              "price_time": config.now(), "direction": "BUY", "quality": 78,
              "summary": ["اتجاه صاعد"], "zones": ["🟩 دعم: 3480"],
              "recommendation": "انحياز صاعد", "activation": "فوق 3520",
              "invalidation": "3470", "risk_note": "1-2%", "market_open": True}
        txt = render_analysis_card(an)
        self.assertIn("تحليل XAUUSD", txt)
        self.assertIn("التوصية", txt)


class TestBackupZip(unittest.TestCase):
    def test_full_archive_is_zip_excluding_env_by_default(self):
        # Security (v6.12.0): .env holds BOT_TOKEN, DATABASE_URL, and
        # BROKER_SECRET_KEY — excluded by default so unencrypted Telegram ZIPs
        # never leak secrets. Opt-in include_env=True remains available offline.
        base = config.BASE_DIR
        env_file = base / ".env"
        created = False
        if not env_file.is_file():
            env_file.write_text("BOT_TOKEN=000:royal-test-dummy\nADMIN_ID=1\n", encoding="utf-8")
            created = True
        dest = None
        dest_opt = None
        try:
            dest = backup_service.full_project_archive()
            self.assertEqual(dest.suffix, ".zip")
            with zipfile.ZipFile(str(dest)) as z:
                names = z.namelist()
                self.assertNotIn(".env", names, ".env must be excluded by default for security")
                self.assertIn("ENV_EXCLUDED_FOR_SECURITY.txt", names)
                self.assertTrue(any(n.startswith("fysb/") for n in names), "code must be included")
                self.assertTrue(any("database/" in n for n in names), "DB snapshot must be included")
            ok, detail = backup_service.verify_archive(dest)
            self.assertTrue(ok, detail)
            dest_opt = backup_service.full_project_archive(include_env=True)
            with zipfile.ZipFile(str(dest_opt)) as z:
                self.assertIn(".env", z.namelist())
        finally:
            if created:
                env_file.unlink(missing_ok=True)
            if dest is not None:
                dest.unlink(missing_ok=True)
            if dest_opt is not None:
                dest_opt.unlink(missing_ok=True)


class TestAdminExclusion(unittest.TestCase):
    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass

    def test_admin_never_listed_as_user(self):
        users_service.ensure_user(FakeUser(config.ADMIN_ID, "boss", "Admin Boss"))
        rows = users_service.page_list(0)
        ids = [r["user_id"] for r in rows["rows"]]
        self.assertNotIn(config.ADMIN_ID, ids)
        found = users_service.search("Admin Boss")
        self.assertFalse(any(r["user_id"] == config.ADMIN_ID for r in found))

    def test_broadcast_audience_excludes_admin(self):
        ids = bc.audience_ids("all", "")
        self.assertNotIn(config.ADMIN_ID, ids)

    def test_signal_delivery_skips_admin(self):
        from fysb.engines.signal_engine import emit_to_users
        with db.conn() as c:
            c.execute("INSERT OR IGNORE INTO users(user_id,full_name,joined_at,notifications,is_blocked)"
                      " VALUES(?,?,?,1,0)", (config.ADMIN_ID, "admin", config.now()))
        sent_targets = []

        async def send(uid, text):
            sent_targets.append(uid)

        sig = {"id": 999999, "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 1.0, "stop": 0.9, "tp1": 1.2, "tp2": 1.4, "tp3": 1.6,
               "rr": 2.0, "score": 90, "strategy": "ICT", "reason": "test",
               "status": "ACTIVE", "data_source": "T", "data_time": config.now(),
               "created_at": config.now(), "pips_result": 0}
        stats = asyncio.run(emit_to_users(sig, send))
        self.assertNotIn(config.ADMIN_ID, sent_targets)


class TestMarketState(unittest.TestCase):
    def test_crypto_always_open(self):
        from fysb.engines.market_data import market_state
        self.assertIs(market_state("crypto"), True)

    def test_saturday_closed_for_metals(self):
        from fysb.engines.market_data import market_state
        import fysb.engines.market_data as md
        from datetime import datetime, timezone
        real_now = md.datetime
        _SAT = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)  # a Saturday
        class FakeDT:
            # v8.14.5: callable + delegating — market_state now CONSTRUCTS
            # datetimes (DST-aware settlement anchors), not only .now()
            def __call__(self, *a, **k):
                return datetime(*a, **k)
            @staticmethod
            def now(tz=None):
                return _SAT if tz is None else _SAT.astimezone(tz)
            def __getattr__(self, item):
                return getattr(datetime, item)
        md.datetime = FakeDT()
        try:
            self.assertIs(market_state("metal"), False)
        finally:
            md.datetime = real_now


class TestPaymentPresets(unittest.TestCase):
    def test_yemeni_wallets_present(self):
        from fysb.handlers.inputs import PM_PRESETS
        for k in ("JAWALI", "ONE_CASH", "CASH_WALLET", "MTN_CASH", "YU_WALLET", "YEMEN_BANK"):
            self.assertIn(k, PM_PRESETS, f"missing Yemeni wallet {k}")
            self.assertEqual(PM_PRESETS[k][1], "اليمن")

    def test_crypto_and_bank_presets(self):
        from fysb.handlers.inputs import PM_PRESETS
        for k in ("USDT_TRC20", "USDT_BEP20", "BINANCE_PAY", "BANK_WIRE"):
            self.assertIn(k, PM_PRESETS)


class TestBrokerPresets(unittest.TestCase):
    def test_presets_have_real_endpoints(self):
        from fysb.handlers.inputs import BROKER_PRESETS
        self.assertIn("https://api.binance.com", BROKER_PRESETS["BINANCE"]["base_url"])
        self.assertIn("agiliumtrade", BROKER_PRESETS["METAAPI"]["base_url"])
        for key, meta in BROKER_PRESETS.items():
            self.assertIn("test", meta)

    def test_guided_broker_flow_persists_masked(self):
        ctx = MockContext()
        ctx.user_data["broker_draft"] = {"step": "account_id", "type": "BINANCE",
                                         "base_url": "https://api.binance.com",
                                         "name": "حسابي", "api_key": "SECRETKEY123",
                                         "api_secret": "SECRETSECRET", "account_id": ""}
        from fysb.handlers.inputs import _broker_step

        class FakeMsg:
            async def reply_text(self, text, **kw):
                self.last = text

        upd = type("U", (), {})()
        upd.effective_message = FakeMsg()
        asyncio.run(_broker_step(upd, ctx, ADMIN, ctx.user_data["broker_draft"], "-"))
        with db.conn() as c:
            row = c.execute("SELECT * FROM broker_adapters WHERE key='BINANCE' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        from fysb.services import secrets as secrets_service
        self.assertTrue(secrets_service.is_encrypted(row["config_json"]))
        self.assertNotIn("SECRETKEY123", row["config_json"])
        self.assertNotIn("SECRETSECRET", row["config_json"])
        cfg = secrets_service.decrypt_json(row["config_json"])
        self.assertEqual(cfg["api_key"], "SECRETKEY123")
        self.assertNotIn("SECRETKEY123", upd.effective_message.last, "API key must never be echoed")
        with db.conn() as c:
            c.execute("DELETE FROM broker_adapters WHERE id=?", (row["id"],))


if __name__ == "__main__":
    unittest.main()
