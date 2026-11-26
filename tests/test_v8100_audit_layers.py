"""Post-audit acceptance: real local transactions, offline transport doubles.

These are software safety properties, not live broker/performance validation.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import patch

import pytest
import tests.helpers
from tests.helpers import uptrend_candles
from fysb import config, db
from fysb.engines import accounting, backtest, issuance, market_data, news_guard, quality_gate, risk, signal_engine, tracker
from fysb.engines.strategies.base import StrategyResult
from fysb.services import analytics, auto_bridge, digest, strategy_stats, subscriptions, true_analysis


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "audit.db"))
    db.init_db()
    db.set_setting("global_kill_switch", "0")
    db.set_setting("news_guard_enabled", "0")
    db.set_setting("min_rr", "1.0")
    strategy_stats.invalidate_leaderboard_cache()
    yield
    news_guard.reset_cache()
    strategy_stats.invalidate_leaderboard_cache()


def insert_signal(**kw):
    row = dict(symbol="XAUUSD", direction="BUY", timeframe="M15", entry=100., stop=99.,
               tp1=102., tp2=103., tp3=104.5, rr=2., score=85, strategy="ICT", source="AUTO",
               status="RUNNING", hit_tp=0, created_at=config.now(),
               filled_at="2026-11-26T08:00:00+00:00", closed_at="", private_user_id=0,
               pips_result=0., accounting_model=accounting.PARTIAL_MODEL)
    row.update(kw)
    with db.tx() as c:
        row["id"] = c.execute("INSERT INTO signals("+",".join(row)+") VALUES("+",".join("?" for _ in row)+")", tuple(row.values())).lastrowid
    return row


def bar(minute, high, low, close):
    return dict(datetime=f"2026-11-26 08:{minute:02}:00", open=close, high=high, low=low, close=close)


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("level", [1, 2])
def test_tracker_batch_and_incremental_polling_agree_on_be(mirror, level):
    sig = dict(symbol="XAUUSD", direction="BUY", entry=100., stop=90., tp1=120., tp2=130., tp3=145.,
               hit_tp=0, filled_at="2026-11-26T08:00:00Z")
    walk = [bar(0, 121 if level == 1 else 131, 101, 120), bar(5, 146, 99, 101)]
    if mirror:
        sig.update(direction="SELL", stop=110., tp1=80., tp2=70., tp3=55.)
        walk = [{**c, "high": 200-c["low"], "low": 200-c["high"], "close": 200-c["close"], "open": 200-c["open"]} for c in walk]
    assert tracker._evaluate_signal(sig, walk[:1]) == f"TP{level}_HIT"
    assert tracker._evaluate_signal(sig, walk) == f"TP{level}_BE"
    assert tracker._evaluate_signal({**sig, "hit_tp": level}, walk) == f"TP{level}_BE"


def test_tracker_persists_partial_model_and_closes_once(isolated):
    sig = insert_signal()
    candles = [bar(0, 102.5, 100.1, 102), bar(5, 104.7, 99.9, 100)]
    event = tracker._evaluate_signal(sig, candles)
    assert event == "TP1_BE"
    payload = dict(values=candles, last_candle_iso=candles[-1]["datetime"])
    with patch.object(tracker, "_alert_targets", return_value=[]):
        _, pips, _ = tracker._apply_transition(sig, event, payload, 100.)
        assert pips == 10.  # .5 * 2R * 10 risk-pips, NOT 20p full TP1
        assert tracker._apply_transition(sig, event, payload, 100.) == ([], 0., [])
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM signals WHERE id=?", (sig["id"],)).fetchone())
    assert row["closed_at"] and row["hit_tp"] == 1 and row["pips_result"] == 10.


@pytest.mark.parametrize("n", [100, 101, 102, 103, 104])
def test_htf_buckets_do_not_repaint_old_history_or_disguise_ltf(n):
    cs = uptrend_candles(108)
    short = quality_gate.resample_htf(cs[:n], 4)
    longer = quality_gate.resample_htf(cs, 4)
    assert short == longer[:len(short)]
    assert short[0]["datetime"].endswith("08:00:00")
    assert len(quality_gate.resample_htf(cs[:12], 4)) == 3  # never 12 LTF bars
    sliding = quality_gate.resample_htf(cs[1:n], 4)
    assert sliding == short[1:]


def test_htf_omits_bucket_with_missing_constituent():
    cs = uptrend_candles(16)
    del cs[5]
    out = quality_gate.resample_htf(cs, 4)
    assert len(out) == 3
    assert not any(c["datetime"].endswith("09:00:00") for c in out)


def test_tracking_refuses_truncated_history_instead_of_inventing_path(isolated):
    payload = dict(values=[bar(5, 110, 101, 105)], last_candle_iso="2026-11-26 08:05:00", age_seconds=0)
    with patch.object(market_data, "fetch_candles", return_value=payload), patch.object(market_data, "is_stale", return_value=False):
        with pytest.raises(RuntimeError, match="incomplete"):
            tracker._tracking_payload("XAUUSD", "", "2026-11-26T08:00:00+00:00")


def analysis_doubles(monkeypatch):
    def fetch(symbol, tf, size=150):
        cs = uptrend_candles(90, base={"M5": 100, "M15": 200, "M30": 300, "H1": 400, "H4": 500}.get(tf, 600))
        return dict(values=cs, last_candle_iso=cs[-1]["datetime"], source="TEST"), None
    monkeypatch.setattr(analytics, "_fetch", fetch)
    monkeypatch.setattr(analytics, "analytics_enabled", lambda: True)
    monkeypatch.setattr(analytics, "quota_state", lambda uid: dict(quota=100, used=0, remaining=100))
    monkeypatch.setattr(subscriptions, "plan_for", lambda uid: dict(markets="XAUUSD", timeframes="M5,M15,M30,H1", strategies="ICT"))
    monkeypatch.setattr(analytics, "_build_real_confirmation_spec", lambda **kw: {})
    monkeypatch.setattr(market_data, "is_stale", lambda p: False)
    monkeypatch.setattr(market_data, "market_state", lambda *a: True)


def test_analytics_multiframe_keeps_chart_prices_context_and_entitlement_consistent(isolated, monkeypatch):
    analysis_doubles(monkeypatch)
    seen = []
    monkeypatch.setattr(analytics, "evaluate_all", lambda ctx, allowed: seen.append((ctx, allowed)) or [])
    result = analytics.run_analysis(42, "XAUUSD", "ALL", strategy="ALL")
    assert result["ok"], result
    ctx, allowed = seen[0]
    assert ctx.timeframe == "H1" and ctx.candles[0]["open"] == 400
    assert ctx.bias_candles[0]["open"] == 500
    chart = result["analysis"]["chart_data"]
    assert chart["tf"] == ctx.timeframe and chart["candles"] == ctx.candles[-95:]
    assert allowed == ["ICT"]
    with db.tx() as c:
        c.execute("UPDATE strategy_catalog SET enabled=0")
    assert signal_engine._allowed_strategies("XAUUSD", "M15") == []
    seen.clear()
    assert analytics.run_analysis(42, "XAUUSD", "ALL", strategy="ALL")["ok"]
    assert seen[0][1] == []
    assert not analytics.run_analysis(42, "XAUUSD", "M15", strategy="ICT")["ok"]


def test_analytics_strips_forming_bar_before_computing_indicators(isolated, monkeypatch):
    from fysb.engines import analysis
    cs = uptrend_candles(80)
    future = datetime.now(timezone.utc)+timedelta(minutes=5)
    cs[-1].update(datetime=future.isoformat(), high=9999., close=9998.)
    monkeypatch.setattr(analytics, "_fetch", lambda *a: (dict(values=cs), None))
    frame = analytics._one_frame_reading("XAUUSD", "M15", None)
    assert frame["candles"] == cs[:-1]
    assert frame["e9"] == analysis.ema([c["close"] for c in cs[:-1]], 9)
    assert frame["close"] != 9998.


@pytest.mark.parametrize("source", ["SIGNAL_NOW", "TRUE_ANALYSIS_INSTANT", "TRUE_ANALYSIS_CONFIRMED", "AUTO"])
def test_private_outcomes_and_open_runners_never_pollute_public_stats(isolated, source):
    insert_signal(source=source, private_user_id=42, status="TP1_HIT", hit_tp=1, pips_result=20.)
    insert_signal(status="TP2_HIT", hit_tp=2, pips_result=30., created_at=(datetime.now(timezone.utc)+timedelta(microseconds=1)).isoformat())  # public OPEN runner
    strategy_stats.invalidate_leaderboard_cache()
    assert strategy_stats.leaderboard() == []
    d = digest.build_digest_data()
    assert d["wins"] == 0 and d["decided"] == 0
    assert d["open"] == 1
    assert risk.check_signal("XAUUSD", 2, .1).metrics["open_signals"] == 1


def test_private_radar_does_not_alert_admin_without_owner_delivery(isolated):
    sig = insert_signal(source="TRUE_ANALYSIS_CONFIRMED", private_user_id=42)
    with db.tx() as c:
        c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)", (sig["id"], 42, config.now()))
    with patch.object(signal_engine, "admin_accepts_market", return_value=True):
        assert tracker._alert_targets(sig, "private") == [(42, "private")]


@pytest.mark.parametrize("hit,expected", [(1, 1.), (2, 1.75), (3, 2.875)])
def test_new_accounting_is_shared_by_stats_risk_digest_and_tracker(isolated, hit, expected):
    sig = insert_signal(hit_tp=hit, status=f"TP{hit}_HIT", closed_at=config.now(), created_at=config.now())
    assert accounting.modeled_r(sig) == expected
    board = strategy_stats.leaderboard()
    assert board[0]["avg_r"] == expected
    assert digest._calc_r_multiple(sig) == expected
    assert risk.daily_signal_pnl_pct()[0] == round(expected, 2)


def test_legacy_stored_values_are_not_silently_rewritten(isolated):
    sig = insert_signal(status="TP1_HIT", hit_tp=1, pips_result=20., accounting_model="LEGACY_LEVEL", closed_at=config.now())
    assert accounting.modeled_r(sig) == 2.
    strategy_stats.leaderboard()
    digest.build_digest_data()
    with db.conn() as c:
        assert c.execute("SELECT pips_result FROM signals WHERE id=?", (sig["id"],)).fetchone()["pips_result"] == 20.


def test_losing_periods_are_not_hidden_from_scheduled_public_reports():
    assert digest.channel_broadcast_allowed(dict(decided=10, net_r=-8, profit_factor=.2))[0]


@pytest.mark.parametrize("direction,lo,hi", [("BUY", 101, 105), ("SELL", 95, 99)])
def test_backtest_never_fills_untouched_entry_with_tolerance(direction, lo, hi):
    cand = StrategyResult("ICT", direction, 100, 90 if direction == "BUY" else 110,
                          120 if direction == "BUY" else 80, 130 if direction == "BUY" else 70,
                          145 if direction == "BUY" else 55, 2, 80)
    future = [dict(open=(hi+lo)/2, high=hi, low=lo, close=(hi+lo)/2)]
    assert backtest._simulate_trade("XAUUSD", cand, future, 0, spread_pips=0).outcome == "UNFILLED"


@pytest.mark.parametrize("defect", ["unknown", "stale", "proxy", "future"])
def test_execution_quote_cannot_use_http_fetch_time_as_market_time(isolated, monkeypatch, defect):
    quote = dict(price=100., provider="fixture", fetched_at=config.now(), observed_at=config.now())
    if defect == "unknown": quote.pop("observed_at")
    elif defect == "stale": quote["observed_at"] = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    elif defect == "proxy": quote["is_proxy"] = True
    elif defect == "future": quote["observed_at"] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    monkeypatch.setattr(market_data, "fetch_price", lambda *a: quote)
    assert not market_data.execution_quote("XAUUSD")["ok"]


def test_missing_news_calendar_is_unknown_and_blocks_issuance(isolated, monkeypatch):
    db.set_setting("news_guard_enabled", "1")
    monkeypatch.setattr(news_guard, "fetch_events", lambda: [])
    monkeypatch.setattr(news_guard, "last_feed_error", lambda: "offline")
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert blocked and event["health"] == "UNKNOWN"


@pytest.mark.parametrize("defect", ["kill", "risk", "quarantine", "disabled"])
def test_shared_issuance_policy_applies_to_free_and_radar(isolated, monkeypatch, defect):
    if defect == "kill": db.set_setting("global_kill_switch", "1")
    elif defect == "risk": db.set_setting("min_rr", "5")
    elif defect == "disabled":
        with db.tx() as c: c.execute("UPDATE strategy_catalog SET enabled=0 WHERE code='ICT'")
    elif defect == "quarantine":
        from fysb.engines import intelligence
        monkeypatch.setattr(intelligence, "strategy_quarantine_status", lambda *a: dict(quarantined=True, reason="quarantine"))
    for source in ("AUTO_FREE", "TRUE_ANALYSIS_CONFIRMED"):
        assert not issuance.policy("XAUUSD", "ICT", "BUY", 2., .1, source=source, user_id=42)[0]


def test_radar_historical_confirmation_is_pending_not_execution_receipt(isolated, monkeypatch):
    monkeypatch.setattr(market_data, "execution_quote", lambda *a: dict(ok=True, price=100.))
    sig = true_analysis._create_private_signal(uid=42, symbol="XAUUSD", tf="M15", strategy="ICT", direction="BUY",
        plan_levels=dict(entry=100., sl=99.5, tp1=101., tp2=101.5, tp3=102.25, rr=2.), score=85,
        reason="confirmation fixture", source="TRUE_ANALYSIS_CONFIRMED", data_time="2026-01-01")
    assert sig is not None and sig["status"] == "ACTIVE" and not sig["filled_at"]
    assert sig["created_at"] != "2026-01-01"


def test_serialized_risk_check_and_insert_cannot_overbook_cap(isolated):
    db.set_setting("max_open_signals", "1")
    def issue(i):
        with issuance.serialized() as c:
            ok = risk.check_signal("XAUUSD", 2., .1).allowed
            if ok:
                c.execute("INSERT INTO signals(symbol,direction,entry,stop,tp1,tp2,tp3,rr,status,source,created_at) VALUES('XAUUSD','BUY',100,99,102,103,104.5,2,'ACTIVE','AUTO',?)", (config.now(),))
            return ok
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(issue, range(2))) == [False, True]


def bridge_fixture(monkeypatch):
    with db.tx() as c:
        cid = c.execute("INSERT INTO bridge_connections(user_id,platform,account_mode,enabled,max_open_trades,created_at,updated_at) VALUES(42,'tradelocker','DEMO',1,3,?,?)", (config.now(), config.now())).lastrowid
    monkeypatch.setattr(auto_bridge, "user_can_copy", lambda *a: (True, "fixture"))
    monkeypatch.setattr(auto_bridge, "_daily_dd_breached", lambda *a: (False, 0.))
    monkeypatch.setattr(auto_bridge.vault, "decrypt_json", lambda *a: dict(email="fixture", password="not-a-credential"))
    with db.tx() as c:
        c.execute("UPDATE bridge_connections SET creds_enc='fixture' WHERE id=?", (cid,))
    monkeypatch.setattr(market_data, "execution_quote", lambda *a: dict(ok=True, price=100.))
    return cid


@pytest.mark.parametrize("defect", ["pending", "private", "free", "undelivered", "closed", "kill"])
def test_bridge_rejects_ineligible_signals_before_broker_call(isolated, monkeypatch, defect):
    bridge_fixture(monkeypatch)
    sig = insert_signal()
    if defect == "pending": changes = dict(status="ACTIVE", filled_at="")
    elif defect == "private": changes = dict(private_user_id=42)
    elif defect == "free": changes = dict(source="AUTO_FREE")
    elif defect == "closed": changes = dict(closed_at=config.now(), status="TP3_HIT")
    else: changes = {}
    with db.tx() as c:
        for key, value in changes.items(): c.execute(f"UPDATE signals SET {key}=? WHERE id=?", (value, sig["id"]))
        if defect != "undelivered":
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)", (sig["id"], 42, config.now()))
    if defect == "kill": db.set_setting("global_kill_switch", "1")
    with patch.object(auto_bridge, "_tradelocker_place_order") as broker:
        assert auto_bridge.dispatch_signal_to_bridges(sig)["queued"] == 0
        broker.assert_not_called()


def test_bridge_reserves_before_network_and_never_retries_ambiguous_timeout(isolated, monkeypatch):
    cid = bridge_fixture(monkeypatch)
    sig = insert_signal()
    with db.tx() as c:
        c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)", (sig["id"], 42, config.now()))
    calls = []
    def broker(*a, **kw):
        with db.conn() as c:
            row = c.execute("SELECT status FROM bridge_orders WHERE connection_id=? AND signal_id=?", (cid, sig["id"])).fetchone()
        assert row and row["status"] == "RESERVING"
        calls.append(1)
        raise TimeoutError("broker might have accepted")
    monkeypatch.setattr(auto_bridge, "_tradelocker_place_order", broker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: auto_bridge.dispatch_signal_to_bridges(sig), range(2)))
    assert len(calls) == 1
    auto_bridge.dispatch_signal_to_bridges(sig)
    assert len(calls) == 1
    with db.conn() as c:
        assert c.execute("SELECT status FROM bridge_orders").fetchone()["status"] == "UNKNOWN"


def test_additive_migration_preserves_legacy_signal_and_local_settings(isolated):
    sig = insert_signal(accounting_model="LEGACY_LEVEL", pips_result=20., closed_at=config.now(), status="TP1_HIT", hit_tp=1)
    db.set_setting("audit_preservation_sentinel", "keep")
    db.init_db()
    with db.conn() as c:
        saved = dict(c.execute("SELECT * FROM signals WHERE id=?", (sig["id"],)).fetchone())
    assert saved["accounting_model"] == "LEGACY_LEVEL" and saved["pips_result"] == 20.
    assert db.get_setting("audit_preservation_sentinel") == "keep"


@pytest.mark.parametrize("offset", [0, 5, 14])
def test_confirmed_candles_never_closes_a_future_or_forming_single_bar(offset):
    from fysb.engines.analysis import confirmed_candles
    now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    cs = [dict(datetime=(now+timedelta(minutes=offset)).isoformat(), open=100, high=101, low=99, close=100)]
    assert confirmed_candles(cs, "M15", now) == []


def test_gold_proxy_metadata_cannot_be_hidden_by_live_source(isolated):
    cs = uptrend_candles(50)
    payload = market_data._assemble("XAUUSD", "15min", cs, "LIVE", False, None, "XAUUSD", "venue-gold")
    assert payload["source"] == "LIVE" and payload["is_proxy"]
    assert payload["provider"] == "venue-gold" and payload["instrument_kind"] == "PROXY"
