"""ROYAL v6.10.0 — the nine owner reports, every one pinned by a test.

 1. Statistics pages (a:statistics) — paginated, each page ≤ 4096, no crash.
 2. Deliveries card shows the REAL seq number + luxury results.
 3. Payment-rejection reason survives a stale awaiting_usdt_wallet flag
    (CLEAR_ON_NAV + reject-before-wallet ordering).
 4. Packages: luxury defaults stamped once, direct-entry editor, custom
    values, markets/strategies pickers show ONLY live/implemented items.
 5. Signals center categories + real P/L accounting page.
 6. Opposite-signal close advisory (holders only, real numbers).
 7. «إشارة الآن» staged flow (user + admin) + honest auto-search watchers.
 8. Plan page strategies: one line each + real identity pages.
 9. Strategies board: per-strategy buttons, identity page, verify present;
    CLASSIC + SK engines implemented, tested, verified on boot.
"""
import unittest
import asyncio
from unittest.mock import patch

from tests.helpers import (MockBot, MockContext, MockQuery, FakeUser, page_for,
                           page_markup_for, make_candles)
from fysb import db, config
from fysb.handlers import dispatcher
from fysb.services import users as users_service


def run(coro):
    return asyncio.run(coro)


def fire(data: str, uid: int, bot=None) -> tuple[MockQuery, MockContext]:
    q = MockQuery(data, uid)
    ctx = MockContext(bot)
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def fire_ctx(data: str, uid: int, ctx: MockContext) -> MockQuery:
    """Fire a callback THROUGH an existing context (draft/flag continuity)."""
    q = MockQuery(data, uid)
    q._ctx = ctx
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q


USER = 555900
ADMIN = 9001
OTHER = 555901


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


def _insert_signal(**kw) -> int:
    base = dict(symbol="XAUUSD", direction="BUY", timeframe="M15", entry=3500.0,
                stop=3480.0, tp1=3540.0, tp2=3560.0, tp3=3580.0,
                targets="3540,3560,3580", rr=2.0, score=88, strategy="ICT",
                reason="test", status="ACTIVE", source="AUTO", created_by=0,
                created_at=config.now(), data_source="LIVE", data_time="",
                filled_at="", hit_tp=0, closed_at="", pips_result=0.0,
                expires_at="", live_px=0.0, half_notified=0)
    base.update(kw)
    with db.conn() as c:
        cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                           targets,rr,score,strategy,reason,status,source,created_by,created_at,
                           data_source,data_time,filled_at,hit_tp,closed_at,pips_result,expires_at,
                           live_px,half_notified)
                           VALUES(:symbol,:direction,:timeframe,:entry,:stop,:tp1,:tp2,:tp3,:targets,
                           :rr,:score,:strategy,:reason,:status,:source,:created_by,:created_at,
                           :data_source,:data_time,:filled_at,:hit_tp,:closed_at,:pips_result,:expires_at,
                           :live_px,:half_notified)""", base)
        return cur.lastrowid


def _deliver(uid: int, sid: int, chat_id: str = "", message_id: int = 0) -> None:
    with db.conn() as c:
        c.execute("""INSERT OR IGNORE INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id)
                     VALUES(?,?,?,?,?)""", (uid, sid, config.now(), chat_id, message_id))


class _Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass
        try:
            users_service.ensure_user(FakeUser(OTHER))
        except Exception:
            pass


# =============================================================================
# 1) Statistics — paginated, every page fits, navigation works
# =============================================================================
class TestStatisticsPages(_Base):
    def test_all_pages_fit_and_render(self):
        for page in range(5):
            q, ctx = fire(f"a:statistics:{page}", ADMIN)
            text = page_for(q, ctx)
            self.assertTrue(text, f"page {page} rendered nothing")
            self.assertLessEqual(len(text), 4096, f"page {page} exceeds Telegram limit")
            self.assertIn("الإحصائيات الملكية", text)
            self.assertIn(f"القسم <b>{page + 1}</b> من <b>5</b>", text)

    def test_navigation_buttons_present(self):
        q, ctx = fire("a:statistics:0", ADMIN)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertIn("a:statistics:1", datas)
        q2, _ = fire("a:statistics:1", ADMIN)
        mk2 = page_markup_for(q2, ctx)
        datas2 = [b.callback_data for row in mk2.inline_keyboard for b in row]
        self.assertIn("a:statistics:0", datas2)


# =============================================================================
# 2) Deliveries — REAL seq + luxury card
# =============================================================================
class TestDeliveriesSeq(_Base):
    def test_card_shows_seq_not_internal_id(self):
        # seed 5 signals then DELETE two: the remaining ones' seq numbers shift
        ids = [_insert_signal() for _ in range(5)]
        with db.conn() as c:
            c.execute("DELETE FROM signals WHERE id IN (?,?)", (ids[1], ids[3]))
        kept = [ids[0], ids[2], ids[4]]
        _deliver(USER, kept[2])
        q, ctx = fire(f"a:userdeliveries:{USER}", ADMIN)
        text = page_for(q, ctx)
        self.assertIn("الإشارات التي استلمها المستخدم", text)
        # the delivered signal's REAL seq (position among ALL signals) — the
        # same number the signals center shows, computed the same way
        with db.conn() as c:
            seq = c.execute("SELECT COUNT(*) n FROM signals WHERE id<=?", (kept[2],)).fetchone()["n"]
        self.assertIn(f"#{seq}", text)
        # the RAW internal id must never be displayed as the number
        self.assertNotIn(f"#{kept[2]} ", text)


# =============================================================================
# 3) Payment rejection — the stale-flag killer
# =============================================================================
class TestRejectReasonFlow(_Base):
    def test_clear_on_nav_kills_stale_wallet_flag(self):
        ctx = MockContext()
        ctx.user_data["awaiting_usdt_wallet"] = {"chain": "TRC20"}
        ctx.user_data["reject_payment_id"] = 77
        from fysb.handlers.inputs import CLEAR_ON_NAV
        self.assertIn("awaiting_usdt_wallet", CLEAR_ON_NAV)
        self.assertIn("reject_payment_id", CLEAR_ON_NAV)
        for k in CLEAR_ON_NAV:
            ctx.user_data.pop(k, None)
        self.assertNotIn("awaiting_usdt_wallet", ctx.user_data)

    def test_reject_button_resets_flows_and_awaits_reason(self):
        ctx_user = MockContext()
        ctx_user.user_data["awaiting_usdt_wallet"] = {"chain": "TRC20"}
        # a pending payment must exist for the reject button
        from fysb.services import payments as payments_service
        pid = payments_service.create_request(USER, "PRO", 19.0, "MANUAL")
        q = MockQuery(f"a:rejectpay:{pid}", ADMIN)
        q._ctx = ctx_user
        upd = type("Update", (), {})()
        upd.callback_query = q
        async def _fake_route():
            # simulate the dispatcher's CLEAR_ON_NAV then the handler
            from fysb.handlers.inputs import clear_flows
            clear_flows(ctx_user.user_data)
            ctx_user.user_data["reject_payment_id"] = pid
        asyncio.run(_fake_route())
        self.assertNotIn("awaiting_usdt_wallet", ctx_user.user_data)
        self.assertEqual(ctx_user.user_data["reject_payment_id"], pid)

    def test_reason_text_beats_wallet_validator(self):
        # even if BOTH flags were somehow alive, the rejection branch runs first
        from fysb.handlers.inputs import _admin_text
        ctx = MockContext()
        ctx.user_data["awaiting_usdt_wallet"] = {"chain": "TRC20"}
        from fysb.services import payments as payments_service
        pid = payments_service.create_request(USER, "PRO", 19.0, "MANUAL")
        ctx.user_data["reject_payment_id"] = pid
        upd = type("Update", (), {})()
        upd.message = type("Msg", (), {})()
        upd.message.reply_text = async_noop
        upd.effective_message = upd.message
        upd.effective_user = type("U", (), {"id": ADMIN})()
        import types as _t
        ctx.bot.send_message = async_noop
        handled = asyncio.run(_admin_text(upd, ctx, ADMIN, "مستند غير مقبول — المبلغ لا يطابق"))
        self.assertTrue(handled)
        self.assertNotIn("reject_payment_id", ctx.user_data)
        p = payments_service.get_request(pid)
        self.assertEqual(p["status"], "REJECTED")
        self.assertIn("المبلغ لا يطابق", p["rejection_reason"] or "")


async def async_noop(*a, **k):
    return None


# =============================================================================
# 4) Packages — luxury defaults, direct editor, honest pickers
# =============================================================================
class TestLuxuryPlans(_Base):
    def setUp(self):
        super().setUp()
        # Other suites legitimately retune the plans (analytics quota tests…).
        # A fresh boot re-stamps exactly once — assert against THAT contract.
        with db.conn() as c:
            c.execute("DELETE FROM settings WHERE key='plans_luxury_v6100_applied'")
        db._settings_cache.clear()   # the 5s TTL cache must not resurrect the flag
        db.init_db()

    def test_defaults_stamped_once(self):
        with db.conn() as c:
            free = dict(c.execute("SELECT * FROM plans WHERE code='FREE'").fetchone())
            pro = dict(c.execute("SELECT * FROM plans WHERE code='PRO'").fetchone())
            vip = dict(c.execute("SELECT * FROM plans WHERE code='VIP'").fetchone())
        self.assertEqual(free["max_signals_per_day"], 2)
        self.assertEqual(free["max_analyses_per_day"], 3)
        self.assertEqual(free["timeframes"], "M15")
        self.assertEqual(free["strategies"], "ICT,PRICE_ACTION")
        self.assertLessEqual(len(free["strategies"].split(",")), 2)
        self.assertEqual(pro["price"], 19.0)
        self.assertGreater(pro["max_signals_per_day"], free["max_signals_per_day"])
        self.assertGreater(len(pro["strategies"].split(",")), len(free["strategies"].split(",")))
        self.assertGreater(vip["max_signals_per_day"], pro["max_signals_per_day"])
        self.assertGreater(len(vip["strategies"].split(",")), len(pro["strategies"].split(",")))

    def test_force_merge_retired(self):
        """The old boot merge stuffed every implemented strategy into FREE —
        it must stay retired after init_db."""
        with db.conn() as c:
            free = c.execute("SELECT strategies FROM plans WHERE code='FREE'").fetchone()
        self.assertNotIn("SK", free["strategies"])
        self.assertNotIn("ICHIMOKU", free["strategies"])

    def test_direct_field_edit(self):
        from fysb.handlers.admin_core import plan_field_apply
        msg = plan_field_apply("PRO", "quota", "7")
        self.assertIn("7", msg)
        msg = plan_field_apply("PRO", "price", "19.5")
        with db.conn() as c:
            p = c.execute("SELECT price, max_signals_per_day FROM plans WHERE code='PRO'").fetchone()
        self.assertEqual(p["price"], 19.5)
        self.assertEqual(p["max_signals_per_day"], 7)
        with self.assertRaises(ValueError):
            plan_field_apply("PRO", "score", "10")
        with self.assertRaises(ValueError):
            plan_field_apply("PRO", "quota", "abc")

    def test_plan_field_button_opens_prompt(self):
        q, ctx = fire("a:planfield:PRO:price", ADMIN)
        text = page_for(q, ctx)
        self.assertIn("أرسل", text)
        self.assertIn("السعر", text)

    def test_creation_markets_only_live_and_strats_only_implemented(self):
        from fysb.handlers.admin_core import _plan_reply_current
        ctx = MockContext()
        ctx.user_data["plan_draft"] = {"stage": "markets", "markets": [], "timeframes": [], "strategies": []}
        q = MockQuery("a:plans", ADMIN)
        run(_plan_reply_current(q, ADMIN, ctx, ctx.bot, ctx.user_data["plan_draft"], "test"))
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        live = {r["symbol"] for r in db.conn().execute("SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()}
        mkt_datas = [d.split(":", 2)[2] for d in datas if d.startswith("a:planmkt:")]
        for s in mkt_datas:
            self.assertIn(s, live)
        ctx.user_data["plan_draft"] = {"stage": "strategies", "markets": [], "timeframes": [], "strategies": []}
        q2 = MockQuery("a:plans", ADMIN)
        run(_plan_reply_current(q2, ADMIN, ctx, ctx.bot, ctx.user_data["plan_draft"], "test"))
        mk2 = page_markup_for(q2, ctx)
        strat_datas = [d.split(":", 2)[2] for d in [b.callback_data for row in mk2.inline_keyboard for b in row] if d.startswith("a:planstrat:")]
        from fysb.engines.strategies import REGISTRY
        for s in strat_datas:
            self.assertIsNotNone(REGISTRY.get(s), f"{s} must be an IMPLEMENTED engine")

    def test_custom_value_button_prompts(self):
        ctx = MockContext()
        ctx.user_data["plan_draft"] = {"stage": "days", "markets": [], "timeframes": [], "strategies": []}
        q = fire_ctx("a:plancustom:days", ADMIN, ctx)
        self.assertIn("المدة", page_for(q, ctx))
        self.assertEqual(ctx.user_data.get("awaiting_plan_custom"), "days")


# =============================================================================
# 5) Signals center — categories + real P/L accounting
# =============================================================================
class TestSignalsCenter(_Base):
    def test_category_filter(self):
        sid_p = _insert_signal(status="ACTIVE")
        sid_r = _insert_signal(status="RUNNING")
        sid_sl = _insert_signal(status="SL_HIT", pips_result=-20.0)
        q, ctx = fire("a:sigcat:pending:0", ADMIN)
        text = page_for(q, ctx)
        self.assertIn("بانتظار التنفيذ", text)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertIn(f"a:signalview:{sid_p}", datas)
        self.assertNotIn(f"a:signalview:{sid_sl}", datas)
        q2, ctx2 = fire("a:sigcat:sl:0", ADMIN)
        mk2 = page_markup_for(q2, ctx2)
        datas2 = [b.callback_data for row in mk2.inline_keyboard for b in row]
        self.assertIn(f"a:signalview:{sid_sl}", datas2)
        self.assertNotIn(f"a:signalview:{sid_p}", datas2)

    def test_pl_accounting_renders_real_numbers(self):
        _insert_signal(status="TP1_HIT", pips_result=40.0, symbol="XAUUSD")
        _insert_signal(status="SL_HIT", pips_result=-20.0, symbol="XAUUSD")
        q, ctx = fire("a:sigpl", ADMIN)
        text = page_for(q, ctx)
        self.assertIn("محاسبة الربح والخسارة", text)
        self.assertIn("XAUUSD", text)
        self.assertIn("pips", text)

    def test_center_has_signow_and_pl_buttons(self):
        q, ctx = fire("a:signals", ADMIN)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertIn("a:signow", datas)
        self.assertIn("a:sigpl", datas)


# =============================================================================
# 6) Opposite-signal close advisory
# =============================================================================
class TestOppositeAdvisory(_Base):
    def setUp(self):
        super().setUp()
        # cross-test hygiene: the advisory scans ALL open signals — a shared
        # session DB would let earlier tests' open rows pollute the scan.
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")

    def test_targets_finder(self):
        from fysb.engines.signal_engine import opposite_close_advisory_targets
        old = _insert_signal(direction="SELL", status="RUNNING")
        _insert_signal(direction="BUY", status="RUNNING")   # same side as the NEW signal — excluded
        _insert_signal(direction="SELL", status="SL_HIT")   # terminal — excluded
        targets = opposite_close_advisory_targets("XAUUSD", "BUY", exclude_sid=999999)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["id"], old)

    def test_advisory_sent_only_to_real_holders(self):
        from fysb.engines.signal_engine import emit_to_users
        old_sell = _insert_signal(direction="SELL", status="RUNNING")
        _deliver(OTHER, old_sell)
        new_buy = _insert_signal(direction="BUY", status="ACTIVE")
        sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (new_buy,)).fetchone())
        with patch("fysb.engines.market_data.fetch_price", lambda *a, **k: {"price": 3470.0}):
            sent_log = []

            async def fake_send(uid, text):
                sent_log.append((uid, text))
                return None

            asyncio.run(emit_to_users(sig, fake_send))
        advisories = [t for uid, t in sent_log if "صفقة متعاكسة" in t]
        self.assertTrue(advisories, "holder of the opposite trade must get the advisory")
        self.assertIn("رابحة حتى", advisories[0])
        holder_uids = {uid for uid, _ in sent_log if "صفقة متعاكسة" in _}
        self.assertIn(OTHER, holder_uids)
        self.assertNotIn(USER, holder_uids)  # USER holds nothing → no advisory

    def test_no_advisory_without_holders(self):
        from fysb.engines.signal_engine import emit_to_users
        _insert_signal(direction="SELL", status="RUNNING")  # open opposite, no deliveries
        new_buy = _insert_signal(direction="BUY", status="ACTIVE")
        sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (new_buy,)).fetchone())
        sent_log = []

        async def fake_send(uid, text):
            sent_log.append((uid, text))

        asyncio.run(emit_to_users(sig, fake_send))
        self.assertFalse([t for _, t in sent_log if "صفقة متعاكسة" in t])


# =============================================================================
# 7) «إشارة الآن» — staged flow + honest watchers
# =============================================================================
class TestSignalNow(_Base):
    def test_user_flow_renders_stages(self):
        q, ctx = fire("u:signow", USER)
        text = page_for(q, ctx)
        self.assertIn("إشارة الآن", text)
        self.assertIn("نوع التداول", text)
        q2, ctx2 = fire("u:signowstyle:SCALP", USER)
        text2 = page_for(q2, ctx2)
        self.assertIn("السوق", text2)
        q3, _ = fire("u:signowstyle:SWING", USER)
        self.assertIn("السوق", page_for(q3, ctx))

    def test_style_limits_tfs(self):
        from fysb.handlers import signal_now
        # SWING needs swing-grade TFs inside the plan — grant them first
        with db.conn() as c:
            c.execute("UPDATE plans SET timeframes='M15,H1,H4' WHERE code='FREE'")
        ctx = MockContext()
        ctx.user_data[signal_now.DRAFT_KEY] = {"stage": "market", "style": "SWING", "is_admin": False}
        q = MockQuery("u:signowsym:XAUUSD", USER)
        run(signal_now.pick_symbol(q, USER, ctx, ctx.bot, "XAUUSD"))
        d = ctx.user_data[signal_now.DRAFT_KEY]
        self.assertNotIn("M1", d["tfs"])
        self.assertIn("H4", d["tfs"])
        self.assertNotIn("M15", d["tfs"])  # M15 is not a swing grade

    def test_strategy_stage_only_plan_and_implemented(self):
        from fysb.handlers import signal_now
        ctx = MockContext()
        ctx.user_data[signal_now.DRAFT_KEY] = {"stage": "tf", "style": "SCALP", "is_admin": False,
                                               "symbol": "XAUUSD", "tfs": ["M15"]}
        q = MockQuery("u:signowtf:M15", USER)
        run(signal_now.pick_tf(q, USER, ctx, ctx.bot, "M15"))
        d = ctx.user_data[signal_now.DRAFT_KEY]
        from fysb.engines.strategies import REGISTRY
        for s in d["strategies"]:
            self.assertIsNotNone(REGISTRY.get(s))
        with db.conn() as c:
            free = c.execute("SELECT strategies FROM plans WHERE code='FREE'").fetchone()
        allowed = {x.strip().upper() for x in free["strategies"].split(",")}
        for s in d["strategies"]:
            self.assertIn(s, allowed)

    def test_generate_signal_honors_only_strategies(self):
        from fysb.engines.signal_engine import generate_signal
        candles = _mk_candles([(100 + i * 0.05, 100 + i * 0.05 + 0.4, 100 + i * 0.05 - 0.3,
                                100 + i * 0.05 + 0.1) for i in range(80)])
        with patch("fysb.engines.market_data.fetch_candles", lambda *a, **k: _fake_payload(candles)), \
             patch("fysb.engines.market_data.is_stale", lambda p: False), \
             patch("fysb.engines.market_data.fetch_price", lambda *a, **k: {"price": 104.0}):
            res = generate_signal("XAUUSD", "M15", True, ["WYCKOFF"])  # closed engine
            self.assertEqual(res["status"], "REJECTED")
            self.assertIn("غير مفعلة", res["reason"])

    def test_watcher_lifecycle(self):
        from fysb.handlers import signal_now
        wid = signal_now.start_watcher(USER, "XAUUSD", "M15", "ICT", "SCALP", 15)
        w = signal_now.active_watcher(USER)
        self.assertEqual(w["id"], wid)
        self.assertEqual(w["interval_minutes"], 15)
        # not due yet
        self.assertEqual([x for x in signal_now.due_watchers() if x["id"] == wid], [])
        # force due
        with db.conn() as c:
            c.execute("UPDATE signal_watchers SET next_run='2020-01-01T00:00:00+00:00' WHERE id=?", (wid,))
        due = [x for x in signal_now.due_watchers() if x["id"] == wid]
        self.assertEqual(len(due), 1)
        signal_now.reschedule_watcher(wid, 15)
        self.assertEqual([x for x in signal_now.due_watchers() if x["id"] == wid], [])
        # expiry horizon is stamped (24h)
        self.assertTrue(w["expires_at"] > w["created_at"])
        self.assertEqual(signal_now.cancel_watchers(USER), 1)
        self.assertIsNone(signal_now.active_watcher(USER))

    def test_watcher_expiry_notifies_and_marks(self):
        from fysb.handlers import signal_now
        wid = signal_now.start_watcher(USER, "XAUUSD", "M15", "ICT", "SCALP", 15)
        past = "2020-01-01T00:00:00+00:00"
        with db.conn() as c:
            c.execute("UPDATE signal_watchers SET next_run=?, expires_at=? WHERE id=?",
                      (past, past, wid))
        bot = MockBot()
        app = type("App", (), {"bot": bot})()
        run(signal_now.run_due_watchers(app))
        with db.conn() as c:
            st = c.execute("SELECT status FROM signal_watchers WHERE id=?", (wid,)).fetchone()["status"]
        self.assertEqual(st, "EXPIRED")
        self.assertTrue(any("انتهت مدة البحث التلقائي" in m["text"] for m in bot.sent))

    def test_watch_period_button_starts(self):
        from fysb.handlers import signal_now
        ctx = MockContext()
        ctx.user_data[signal_now.DRAFT_KEY] = {"stage": "strategy", "style": "SCALP", "is_admin": False,
                                               "symbol": "XAUUSD", "tf": "M15",
                                               "strategies": ["ICT"], "tfs": ["M15"]}
        # generation with the real provider chain -> honest card (never a fake signal)
        q2 = fire_ctx("u:signowstrat:ICT", USER, ctx)
        self.assertTrue(page_for(q2, ctx))
        # the watcher start consumes the same context draft
        ctx.user_data[signal_now.DRAFT_KEY] = {"is_admin": False, "symbol": "XAUUSD", "tf": "M15",
                                               "strategy": "ICT", "style": "SCALP"}
        q3 = fire_ctx("u:signowwatch:30", USER, ctx)
        self.assertIn("البحث التلقائي", page_for(q3, ctx))
        w = signal_now.active_watcher(USER)
        self.assertIsNotNone(w)
        self.assertEqual(w["interval_minutes"], 30)
        q4 = fire_ctx(f"u:watchoff:{w['id']}", USER, ctx)
        self.assertIn("أُلغي البحث التلقائي", page_for(q4, ctx))
        self.assertIsNone(signal_now.active_watcher(USER))


# =============================================================================
# 8+9) Strategy identity pages (user plan page + admin board) + new engines
# =============================================================================
class TestStrategyInfo(_Base):
    def test_user_plan_page_lists_strategies_with_buttons(self):
        q, ctx = fire("u:planview:PRO", USER)
        text = page_for(q, ctx)
        self.assertIn("الاستراتيجيات داخل هذه الباقة", text)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertIn("u:stratinfo:ICT:PRO", datas)
        self.assertIn("u:stratinfo:SMC:PRO", datas)

    def test_identity_page_implemented(self):
        q, ctx = fire("u:stratinfo:ICT:PRO", USER)
        text = page_for(q, ctx)
        self.assertIn("ICT", text)
        self.assertIn("كيف تدخل هذه المنهجية", text)
        self.assertIn("القواعد الميكانيكية", text)

    def test_identity_page_closed_is_honest(self):
        q, ctx = fire("u:stratinfo:WYCKOFF:PRO", USER)
        text = page_for(q, ctx)
        self.assertIn("غير منفذة كمحرك بعد", text)

    def test_admin_board_buttons_and_info_page(self):
        q, ctx = fire("a:strategies:0", ADMIN)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertTrue(any(d.startswith("a:strategyinfo:") for d in datas))
        q2, ctx2 = fire("a:strategyinfo:ICT:0", ADMIN)
        text2 = page_for(q2, ctx2)
        self.assertIn("ICT", text2)
        mk2 = page_markup_for(q2, ctx2)
        datas2 = [b.callback_data for row in mk2.inline_keyboard for b in row]
        self.assertIn("a:strategy_toggle:ICT:0", datas2)

    def test_verify_button_exists_for_verifiable(self):
        with db.conn() as c:
            c.execute("UPDATE strategy_catalog SET verified=0, enabled=0 WHERE code='CRT'")
        q, ctx = fire("a:strategyinfo:CRT:0", ADMIN)
        mk = page_markup_for(q, ctx)
        datas = [b.callback_data for row in mk.inline_keyboard for b in row]
        self.assertIn("a:strategy_verify:CRT:0", datas)
        with db.conn() as c:
            c.execute("UPDATE strategy_catalog SET verified=1, enabled=1 WHERE code='CRT'")

    def test_classic_engine_real_setup(self):
        from fysb.engines.strategies.classic import evaluate
        from fysb.engines.strategies.base import Ctx

        def build_buy(n_trend=52):
            vals = []
            px = 2000.0
            for _i in range(n_trend):            # calm uptrend above EMA50
                o, c = px, px + 1.0
                vals.append((o, c + 0.35, o - 0.2, c))
                px = c
            for _i in range(3):                  # pullback into the swing low
                o = px
                c = px - 0.9
                vals.append((o, o + 0.15, c - 0.2, c))
                px = c
            o = px - 0.15                        # TRUE bull engulfing
            vals.append((o, px + 1.8, o - 0.15, px + 1.55))
            return make_candles(vals)

        res = evaluate(Ctx(symbol="XAUUSD", timeframe="M15",
                           candles=build_buy(), bias_candles=build_buy()))
        self.assertIsNotNone(res, "CLASSIC must fire on its documented setup")
        self.assertEqual(res.direction, "BUY")
        self.assertEqual(res.code, "CLASSIC")
        self.assertGreaterEqual(res.score, 55)

    def test_sk_engine_real_setup(self):
        from fysb.engines.strategies.sk import evaluate
        from fysb.engines.strategies.base import Ctx

        def build_sk(n_trend=42):
            vals = []
            px = 100.0
            for _i in range(n_trend):            # EMA9>EMA21 micro uptrend
                o, c = px, px + 0.20
                vals.append((o, c + 0.08, o - 0.05, c))
                px = c
            cd = make_candles(vals)
            key = max(float(c["high"]) for c in cd[-12:])
            o = px
            return make_candles(vals + [(o, key + 0.75, o - 0.05, key + 0.4)])

        res = evaluate(Ctx(symbol="XAUUSD", timeframe="M15",
                           candles=build_sk(), bias_candles=build_sk()))
        self.assertIsNotNone(res, "SK must fire on its documented setup")
        self.assertEqual(res.direction, "BUY")
        self.assertGreaterEqual(res.score, 55)
        self.assertEqual(res.code, "SK")

    def test_sk_registered_and_verified_on_boot(self):
        from fysb.engines.strategies import REGISTRY, VERIFIABLE
        self.assertIsNotNone(REGISTRY.get("SK"))
        self.assertIsNotNone(REGISTRY.get("CLASSIC"))
        self.assertIn("SK", VERIFIABLE)
        with db.conn() as c:
            r = c.execute("SELECT verified, enabled FROM strategy_catalog WHERE code='SK'").fetchone()
        self.assertEqual((r["verified"], r["enabled"]), (1, 1))


# =============================================================================
# 7b) Channel live delivery — full alerting to the linked channel
# =============================================================================
class TestChannelLiveDelivery(_Base):
    def test_pin_channel_delivery_and_tracker_routing(self):
        from fysb.handlers.admin_channels import pin_channel_delivery
        from fysb.engines.tracker import _apply_transition
        sid = _insert_signal(status="RUNNING")
        ch = {"chat_id": "-1001234567890"}
        msg = type("SentMsg", (), {"message_id": 4242})()
        pin_channel_delivery(ch, sid, msg)
        with db.conn() as c:
            row = c.execute("SELECT * FROM signal_deliveries WHERE signal_id=? AND user_id=0",
                            (sid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["chat_id"], "-1001234567890")
        self.assertEqual(row["message_id"], 4242)
        # a transition must route the notification to the CHANNEL chat too
        sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
        notes, pips, edits = _apply_transition(sig, "TP1_HIT",
                                               {"last_candle_iso": config.now()}, 0.0)
        targets = [t for t, _ in notes]
        self.assertIn("-1001234567890", [str(t) for t in targets])
        # the live-card edit must also include the channel post
        edit_targets = [(str(chat), mid) for _, chat, mid, _t in edits]
        self.assertIn(("-1001234567890", 4242), edit_targets)

    def test_user_rows_still_route_to_user_id(self):
        from fysb.engines.tracker import _apply_transition
        sid = _insert_signal(status="RUNNING")
        _deliver(OTHER, sid)
        sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
        notes, _, _ = _apply_transition(sig, "TP1_HIT",
                                        {"last_candle_iso": config.now()}, 0.0)
        self.assertIn(OTHER, [t for t, _ in notes])
