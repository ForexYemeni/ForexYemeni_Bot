"""Responsiveness contracts: bounded query counts and no delivery DB I/O on the loop.

These are local/offline tests, not a claim about Telegram or production latency.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

import tests.helpers  # noqa: F401 — establish the isolated offline environment first
from fysb import config, db
from fysb.engines import signal_engine, tracker
from fysb.handlers import common, dispatcher
from fysb.services import broadcast, partners, reports, subscriptions
from fysb.webapp import server


def test_package_version_matches_runtime_version():
    from fysb import __version__
    assert __version__ == config.VERSION


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "responsiveness.db")
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()
    subscriptions.invalidate_plans_cache()
    with db.conn() as c:
        c.execute("INSERT INTO users(user_id,joined_at,full_name) VALUES(?,?,?)",
                  (1001, config.now(), "Trader"))
    return tmp_path


def insert_signal(sid, symbol="XAUUSD", status="RUNNING", closed_at=""):
    with db.conn() as c:
        c.execute("""INSERT INTO signals
            (id,symbol,direction,entry,stop,tp1,tp2,tp3,rr,score,strategy,status,created_at,closed_at)
            VALUES(?,?,'BUY',2600,2590,2610,2620,2630,3,95,'SMC',?,?,?)""",
                  (sid, symbol, status, config.now(), closed_at))
        return dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())


def watch_queries(monkeypatch):
    queries = []
    real = db.conn

    def connect():
        c = real()
        c.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
        return c

    monkeypatch.setattr(db, "conn", connect)
    return queries


def warm_settings():
    for key in ("engine_enabled", "global_kill_switch", "maintenance", "live_enabled", "news_guard_enabled"):
        db.get_setting(key)


def test_reports_empty_tables_keep_zero_values(fresh_db, monkeypatch):
    warm_settings()
    queries = watch_queries(monkeypatch)
    d = reports.dashboard()
    assert d["users"] == {"total": 1, "active_7d": 0, "new_7d": 1, "blocked": 0}
    assert set(d["signals"].values()) == {0}
    assert d["payments"] == {"pending": 0, "approved": 0, "rejected": 0, "approved_amount": 0}
    assert len(queries) == 8


def test_reports_aggregates_preserve_status_and_money_rules(fresh_db):
    for sid, status, closed in ((10, "RUNNING", ""), (20, "TP1_HIT", "closed"),
                                (30, "TP3_HIT", "closed"), (40, "SL_HIT", "closed"),
                                (50, "CLOSED_WIN", "closed"), (60, "EXPIRED", ""),
                                (70, "CANCELLED", "")):
        insert_signal(sid, status=status, closed_at=closed)
    with db.conn() as c:
        c.execute("INSERT INTO users(user_id,joined_at,is_blocked) VALUES(?,?,1)",
                  (config.ADMIN_ID, config.now()))
        for status, amount in (("PENDING", 100), ("APPROVED", 12.5), ("APPROVED", 7.25), ("REJECTED", 500)):
            c.execute("INSERT INTO payments(user_id,plan_code,amount,status,created_at) VALUES(1001,'VIP',?,?,?)",
                      (amount, status, config.now()))
        for status, plan in (("ACTIVE", "FREE"), ("ACTIVE", "VIP"), ("EXPIRED", "VIP"), ("REPLACED", "VIP")):
            c.execute("INSERT INTO subscriptions(user_id,plan_code,starts_at,status,created_at) VALUES(1001,?,?,?,?)",
                      (plan, config.now(), status, config.now()))
    d = reports.dashboard()
    assert d["users"]["total"] == 1  # admin stays excluded
    assert d["subscriptions"] == {"free": 1, "paid": 1, "expired": 2}
    assert d["payments"] == {"pending": 1, "approved": 2, "rejected": 1, "approved_amount": 19.75}
    assert d["signals"] == {"total": 7, "open": 1, "completed": 3,
                             "cancelled": 1, "expired": 1, "wins": 1, "sl_hits": 1}


def test_admin_header_needs_only_two_counter_queries(fresh_db, monkeypatch):
    warm_settings()
    insert_signal(10)
    queries = watch_queries(monkeypatch)
    d = common._admin_home_snapshot()
    assert d["users"] == 1
    assert d["open_sigs"] == 1
    assert len(queries) == 2


@pytest.mark.parametrize("limit", [1, 20, 60])
def test_admin_signal_numbering_is_global_without_per_row_queries(fresh_db, monkeypatch, limit):
    for sid, symbol in ((10, "XAUUSD"), (30, "EURUSD"), (50, "XAUUSD")):
        insert_signal(sid, symbol)
    queries = watch_queries(monkeypatch)
    result = server._query_admin_signals(symbol="XAUUSD", limit=limit)
    rows = result["signals"]
    assert [(r["id"], r["seq"]) for r in rows] == [(50, 3), (10, 1)][:limit]
    # Main list + two batch counters; creator ids are absent in this fixture.
    assert len(queries) == 3
    assert not any("WHERE id<=" in sql for sql in queries)
    assert all("_seq" not in row for row in rows)
    # Deleted/gapped ids must not become the displayed sequential number.
    assert rows[0]["seq"] == tracker.seq_of(rows[0]["id"])


def test_partner_financial_aggregates_keep_all_status_semantics(fresh_db):
    now = config.now()
    with db.conn() as c:
        pid = c.execute("INSERT INTO channel_partners(user_id,campaign_code,created_at,updated_at) VALUES(1001,'speed',?,?)",
                        (now, now)).lastrowid
        cid = c.execute("INSERT INTO ref_campaigns(code,title,created_at,updated_at) VALUES('speed','Speed',?,?)",
                        (now, now)).lastrowid
        for uid, converted in ((2001, 0), (2002, 1)):
            c.execute("INSERT INTO referrals(campaign_id,invitee_id,invited_at,converted) VALUES(?,?,?,?)",
                      (cid, uid, now, converted))
        for status, gross, commission in (("EARNED", 100, 25), ("PAID", 60, 15), ("VOID", 40, 10)):
            c.execute("""INSERT INTO partner_commissions
                (partner_id,buyer_user_id,plan_code,gross_amount,commission_amount,status,created_at)
                VALUES(?,1001,'VIP',?,?,?,?)""", (pid, gross, commission, status, now))
    d = partners.partner_stats(pid)
    assert d["invited"] == 2 and d["converted"] == 1 and d["conv_rate_pct"] == 50
    assert d["gross_sales"] == 200  # includes every status, just like the original
    assert d["earned_pending"] == 25 and d["paid_out"] == 15 and d["total_earned"] == 40


def test_broadcast_database_operations_never_run_on_event_loop(fresh_db, monkeypatch):
    bid = broadcast.create(config.ADMIN_ID, "all", "", "Speed")
    loop_thread = threading.get_ident()
    real = db.conn

    def connect():
        assert threading.get_ident() != loop_thread, "broadcast blocks all button handlers"
        return real()

    sent = []

    async def send(uid, text, **kwargs):
        sent.append(uid)

    monkeypatch.setattr(db, "conn", connect)
    res = asyncio.run(broadcast.run_broadcast(bid, SimpleNamespace(send_message=send), [1001]))
    assert res == {"sent": 1, "failed": 0, "aborted": False}
    assert sent == [1001]


def test_signal_delivery_database_operations_never_run_on_event_loop(fresh_db, monkeypatch):
    sig = insert_signal(10)
    sig["strategy"] = "TREND"  # eligible for the seeded FREE plan
    loop_thread = threading.get_ident()
    real = db.conn

    def connect():
        assert threading.get_ident() != loop_thread, "signal delivery blocks all button handlers"
        return real()

    # An assertion inside personal_block is intentionally swallowed by that
    # service, so record violations as well as raising to cover that path.
    violations = []

    def checked_connect():
        if threading.get_ident() == loop_thread:
            violations.append(True)
        return connect()

    async def send(uid, text, **kwargs):
        return SimpleNamespace(message_id=50, chat_id=uid)

    monkeypatch.setattr(db, "conn", checked_connect)
    res = asyncio.run(signal_engine.emit_to_users(sig, send, include_channels=False))
    assert not violations
    assert res["sent"] == 1 and res["failed"] == 0


def test_broadcast_repeat_taps_claim_once(fresh_db):
    bid = broadcast.create(config.ADMIN_ID, "all", "", "Speed")
    sent = []

    async def send(uid, text, **kwargs):
        sent.append(uid)
        await asyncio.sleep(0)

    async def run():
        bot = SimpleNamespace(send_message=send)
        return await asyncio.gather(broadcast.run_broadcast(bid, bot, [1001]),
                                    broadcast.run_broadcast(bid, bot, [1001]))

    results = asyncio.run(run())
    assert len(sent) == 1
    assert sum("error" in result for result in results) == 1
    assert broadcast.get(bid)["status"] == "DONE"
    assert "error" in asyncio.run(broadcast.run_broadcast(bid, None, [1001]))
    assert bid not in broadcast.ABORTS


def test_abort_during_background_claim_still_prevents_sends(fresh_db, monkeypatch):
    bid = broadcast.create(config.ADMIN_ID, "all", "", "Speed")
    real = broadcast._claim_broadcast

    def claim(*args):
        # Simulate pressing abort while the worker thread is awaiting DB I/O.
        broadcast.abort(bid)
        return real(*args)

    monkeypatch.setattr(broadcast, "_claim_broadcast", claim)
    res = asyncio.run(broadcast.run_broadcast(bid, None, [1001]))
    assert res == {"sent": 0, "failed": 0, "aborted": True}
    assert broadcast.get(bid)["status"] == "ABORTED"


def test_callback_acknowledgement_does_not_wait_for_slow_route(monkeypatch):
    async def run():
        ack = asyncio.Event()
        release = asyncio.Event()

        async def answer():
            ack.set()

        async def route(*args):
            await release.wait()

        monkeypatch.setattr(dispatcher, "_route_user", route)
        q = SimpleNamespace(data="u:home", from_user=SimpleNamespace(id=1001), answer=answer)
        task = asyncio.create_task(dispatcher.buttons(SimpleNamespace(callback_query=q), SimpleNamespace(bot=None)))
        try:
            await asyncio.wait_for(ack.wait(), 1)
            assert not task.done()
        finally:
            release.set()
            await task

    asyncio.run(run())


@pytest.mark.parametrize("page,expected_queries", [("home", 2), ("dashboard", 4)])
def test_user_pages_batch_usage_without_cross_user_leaks(fresh_db, monkeypatch, page, expected_queries):
    from fysb.handlers import user_pages

    insert_signal(10)
    insert_signal(20)
    now = config.now()
    with db.conn() as c:
        u = dict(c.execute("SELECT * FROM users WHERE user_id=1001").fetchone())
        for uid, sid, delivered_at in ((1001, 10, now), (1001, 20, "2000-01-01"), (2002, 10, now)):
            c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(?,?,?)",
                      (uid, sid, delivered_at))
        for uid in (1001, 2002):
            c.execute("INSERT INTO analyses(user_id,symbol,timeframe,created_at) VALUES(?,'XAUUSD','M15',?)",
                      (uid, now))
        for uid, status in ((1001, "WATCHING_STABILITY"), (1001, "EXPIRED"), (2002, "WATCHING_CONFIRM")):
            c.execute("""INSERT INTO analysis_monitors
                (user_id,symbol,timeframe,direction,stability_tf,confirm_tf,status,created_at,expires_at,updated_at)
                VALUES(?,'XAUUSD','M15','BUY','M15','M5',?,?,?,?)""",
                      (uid, status, now, "2099-01-01", now))
    subscriptions.plan_for(1001)  # warm the existing plan cache, not a new cache
    db.get_setting("free_daily_signals")
    db.get_setting("trading_mode")
    queries = watch_queries(monkeypatch)
    if page == "home":
        _, _, state, _, ta = user_pages._home_snapshot(1001, u)
    else:
        d = user_pages._dashboard_snapshot(1001, "XAUUSD", u)
        state, ta = d["state"], d["ta"]
        assert d["counts"][3:] == (2, 1)  # total deliveries and today's analyses
    assert state["used"] == 1
    assert ta["radars"] == 1
    assert len(queries) == expected_queries


def test_failed_signal_send_releases_quota_off_event_loop(fresh_db, monkeypatch):
    sig = insert_signal(10)
    sig["strategy"] = "TREND"
    loop_thread = threading.get_ident()
    real = db.conn
    violations = []

    def connect():
        if threading.get_ident() == loop_thread:
            violations.append(True)
        return real()

    async def send(*args, **kwargs):
        raise RuntimeError("simulated blocked bot")

    monkeypatch.setattr(db, "conn", connect)
    result = asyncio.run(signal_engine.emit_to_users(sig, send, include_channels=False))
    assert not violations
    assert result["failed"] == 1 and result["sent"] == 0
    with real() as c:
        assert c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=1001").fetchone()["n"] == 0


def test_opposite_advisory_database_operations_stay_off_event_loop(fresh_db, monkeypatch):
    old = insert_signal(10)
    sig = dict(old, id=20, direction="SELL")
    with db.conn() as c:
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(1001,10,?)", (config.now(),))
    loop_thread = threading.get_ident()
    real = db.conn
    violations = []

    def connect():
        if threading.get_ident() == loop_thread:
            violations.append(True)
        return real()

    monkeypatch.setattr(db, "conn", connect)
    monkeypatch.setattr(signal_engine.market_data, "fetch_price", lambda *a: {"price": 2605})
    sent = []

    async def send(uid, text):
        sent.append((uid, text))

    count = asyncio.run(signal_engine.opposite_close_advisory(sig, send))
    assert not violations
    assert count >= 1
    assert sent[0][0] == 1001
    assert "#1" in sent[0][1]
