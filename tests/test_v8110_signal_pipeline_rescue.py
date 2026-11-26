"""v8.11.0 — Signal-pipeline rescue suite (P1..P6 acceptance).

Software safety properties against the LOCAL test DB and monkeypatched
providers — no live network, no live Telegram, no performance claims:
  * P2: a STALE provider success no longer halts the candle failover chain;
    the freshest stale payload is an honest last resort and is never cached.
  * P3: the news shield can no longer act as a global kill switch on refresh
    failures — usable cached events keep serving; UNKNOWN applies only to a
    genuine data void (or cache older than the admin max-age), with an
    explicit admin fail-open switch for the void case.
  * P4: engine-silence watchdog — blocked-cycle card content + rate limiting
    + daily disabled-engine reminder bookkeeping.
  * P5: the hidden risk ceiling is visible (counts vs caps in one snapshot).
  * P1: the admin gate-diagnostics page renders with real gate states.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import tests.helpers  # noqa: F401  (isolated env FIRST)
from fysb import config, db
from fysb.engines import market_data as md
from fysb.engines import news_guard
from fysb.engines import risk


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "rescue.db"))
    db.init_db()
    db.set_setting("global_kill_switch", "0")
    db.set_setting("news_guard_enabled", "1")
    db.set_setting("news_guard_fail_open", "0")
    db.set_setting("news_guard_cache_max_hours", "48")
    md._cache.clear()
    md._fail_streak.clear()
    news_guard.reset_cache()
    yield
    news_guard.reset_cache()
    config.invalidate_market_key_cache()
    md._cache.clear()
    md._fail_streak.clear()


def _hour_candles(count: int, last_open_minutes_ago: float) -> list[dict]:
    """Regular 1h candles whose LAST candle opened `last_open_minutes_ago` ago."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=last_open_minutes_ago) - timedelta(hours=count - 1)
    out = []
    for i in range(count):
        t = start + timedelta(hours=i)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": 100.0 + i,
                    "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i})
    return out


def _event_in(minutes_from_now: int) -> dict:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return {"title": "NonFarm Payrolls", "currency": "USD", "impact": "High",
            "dt_utc": dt.isoformat(), "forecast": "180K", "previous": "150K"}


# ---------------------------------------------------------------- P2: chain traversal
def test_fresh_alternative_wins_after_stale_success(isolated, monkeypatch):
    monkeypatch.setattr(md, "staleness_limit", lambda interval: 10800)
    monkeypatch.setattr(md, "_fetch_yahoo_candles",
                        lambda sym, iv, out: _hour_candles(40, last_open_minutes_ago=21 * 60))
    monkeypatch.setattr(config, "has_market_api", lambda: True)
    monkeypatch.setattr(config, "market_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(md, "_fetch_td_candles",
                        lambda sym, iv, key, out: _hour_candles(40, last_open_minutes_ago=90))

    payload = md.fetch_candles("XAUUSD", "1h", use_cache=False)
    assert payload["provider"] == "twelvedata"
    assert not md.is_stale(payload)


def test_stale_fallback_is_freshest_and_never_cached(isolated, monkeypatch):
    monkeypatch.setattr(md, "staleness_limit", lambda interval: 10800)
    monkeypatch.setattr(md, "_fetch_yahoo_candles",
                        lambda sym, iv, out: _hour_candles(40, last_open_minutes_ago=21 * 60))
    monkeypatch.setattr(config, "has_market_api", lambda: True)
    monkeypatch.setattr(config, "market_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(md, "_fetch_td_candles",
                        lambda sym, iv, key, out: _hour_candles(40, last_open_minutes_ago=5 * 60))

    payload = md.fetch_candles("XAUUSD", "1h", use_cache=True)
    assert md.is_stale(payload)                       # honest staleness preserved
    assert payload["provider"] == "twelvedata"        # freshest stale wins (2h < 20h)
    # last-resort stale evidence is NEVER cached as fresh:
    assert (("XAUUSD", "1h", 120) not in md._cache)


def test_total_failure_without_any_payload_still_raises(isolated, monkeypatch):
    monkeypatch.setattr(md, "staleness_limit", lambda interval: 10800)

    def _boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(md, "_fetch_yahoo_candles", _boom)
    monkeypatch.setattr(md, "_fetch_venue_gold_candles", _boom)
    monkeypatch.setattr(config, "has_market_api", lambda: True)
    monkeypatch.setattr(config, "market_api_key", lambda: "TESTKEY")
    monkeypatch.setattr(md, "_fetch_td_candles", _boom)
    with pytest.raises(RuntimeError):
        md.fetch_candles("XAUUSD", "1h", use_cache=False)


# ---------------------------------------------------------------- P3: news shield
def test_news_usable_cache_keeps_serving_when_refresh_fails(isolated):
    ev = _event_in(10)
    news_guard.set_cached_events([ev], ts=time.time())
    news_guard._CACHE["error"] = "HTTPError: 403"     # refresh failing
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert blocked and event.get("health") != "UNKNOWN" and event["title"] == "NonFarm Payrolls"


def test_news_cached_within_max_age_still_blocks(isolated):
    ev = _event_in(-5)  # 5 minutes ago — still inside the ±30min buffer
    news_guard.set_cached_events([ev], ts=time.time() - 24 * 3600)
    news_guard._CACHE["error"] = "FeedUnavailable"
    health = news_guard.feed_health()
    assert health["state"] == "CACHED"
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert blocked and event.get("health") != "UNKNOWN" and event["title"] == "NonFarm Payrolls"


def test_news_data_void_blocks_by_default(isolated, monkeypatch):
    monkeypatch.setattr(news_guard, "fetch_events", lambda: [])
    news_guard._CACHE["error"] = "FeedUnavailable"
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert blocked and event["health"] == "UNKNOWN"


def test_news_empty_feed_from_successful_refresh_is_quiet_week_not_void(isolated, monkeypatch):
    """v8.4.0/v8.10.0 contract: a SUCCESSFUL refresh returning zero events is
    truthful 'no High-impact events scheduled' — never a blocking void."""
    monkeypatch.setattr(news_guard, "fetch_events", lambda: [])
    news_guard.set_cached_events([], ts=time.time())   # fresh success, empty feed
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert (blocked, event) == (False, None)


def test_news_data_void_fail_open_is_admin_opt_in(isolated, monkeypatch):
    monkeypatch.setattr(news_guard, "fetch_events", lambda: [])
    news_guard._CACHE["error"] = "FeedUnavailable"
    db.set_setting("news_guard_fail_open", "1")
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert (blocked, event) == (False, None)


def test_news_cache_beyond_max_age_is_a_void(isolated):
    ev = _event_in(10)
    news_guard.set_cached_events([ev], ts=time.time() - 100 * 3600)
    news_guard._CACHE["error"] = "FeedUnavailable"
    assert news_guard.feed_health()["state"] == "UNKNOWN"
    blocked, event = news_guard.check_news_block("XAUUSD")
    assert blocked and event["health"] == "UNKNOWN"


def test_news_admin_cache_age_setting_clamped(isolated):
    db.set_setting("news_guard_cache_max_hours", "9999")
    assert news_guard.max_cache_age_seconds() == 168 * 3600
    db.set_setting("news_guard_cache_max_hours", "0")
    assert news_guard.max_cache_age_seconds() == 6 * 3600


# ---------------------------------------------------------------- P5: hidden ceiling
def _insert_open_signal(symbol="XAUUSD", status="RUNNING", source="AUTO",
                        private_user_id=0, created_at=None):
    from fysb.engines import accounting
    row = dict(symbol=symbol, direction="BUY", timeframe="M15", entry=100., stop=99.,
               tp1=102., tp2=103., tp3=104.5, rr=2., score=85, strategy="ICT", source=source,
               status=status, hit_tp=0, created_at=created_at or config.now(),
               filled_at="2026-11-26T08:00:00+00:00", closed_at="",
               private_user_id=private_user_id, pips_result=0.,
               accounting_model=accounting.PARTIAL_MODEL)
    with db.tx() as c:
        cols = ",".join(row)
        row["id"] = c.execute(
            f"INSERT INTO signals({cols}) VALUES({','.join('?' for _ in row)})",
            tuple(row.values())).lastrowid
    return row


def test_risk_snapshot_counts_only_public_auto_rows(isolated):
    for i in range(12):
        _insert_open_signal(symbol="XAUUSD" if i < 4 else "EURUSD")
    _insert_open_signal(private_user_id=5)            # private — excluded
    _insert_open_signal(source="SIGNAL_NOW", private_user_id=0)  # private-now — excluded
    snap = risk.open_caps_snapshot()
    assert snap["open"] == 12 and snap["ceiling_full"] is True
    assert snap["worst_symbol"] == "EURUSD" and snap["worst_symbol_open"] == 8
    assert snap["oldest_open_hours"] >= 0


def test_risk_snapshot_empty_and_flags(isolated):
    snap = risk.open_caps_snapshot()
    assert snap["open"] == 0 and not snap["ceiling_full"]
    assert snap["daily_breached"] is False


# ---------------------------------------------------------------- P4: silence watchdog
def _blocked_results(n=4):
    return [{"symbol": "XAUUSD", "tf": "H1", "status": "REJECTED",
             "reason": "data stale (age 942min)"} for _ in range(n)]


def test_silence_card_contains_real_reasons_and_gates(isolated):
    from fysb.handlers import background
    card = background._silence_card(_blocked_results())
    assert "data stale (age 942min)" in card
    assert "الماسح الآلي" in card
    assert "KEYED" in card  # codename only — vendor names never in cards
    assert "942" in card or "stale" in card


def test_silence_alert_rate_limited(isolated, monkeypatch):
    from fysb.handlers import background
    sent = []

    async def _send(text, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(config, "ADMIN_ID", 9001)
    monkeypatch.setattr(background, "_safe_send",
                        lambda bot, chat_id, text, **kw: _send(text))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=lambda *a, **k: None))
    first = asyncio.run(background._maybe_silence_alert(app, _blocked_results()))
    second = asyncio.run(background._maybe_silence_alert(app, _blocked_results()))
    assert first is True and len(sent) == 1
    assert second is False and len(sent) == 1   # rate-limited inside the window
    db.set_setting("engine_last_silence_alert", "0")
    third = asyncio.run(background._maybe_silence_alert(app, _blocked_results()))
    assert third is True and len(sent) == 2


def test_engine_disabled_notice_once_per_day(isolated, monkeypatch):
    from fysb.handlers import background
    sent = []

    async def _send(text, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(config, "ADMIN_ID", 9001)
    monkeypatch.setattr(background, "_safe_send",
                        lambda bot, chat_id, text, **kw: _send(text))
    db.set_setting("engine_enabled", "0")
    asyncio.run(background._engine_disabled_daily_notice(SimpleNamespace(bot=None)))
    asyncio.run(background._engine_disabled_daily_notice(SimpleNamespace(bot=None)))
    assert len(sent) == 1                       # one gentle daily reminder only
    db.set_setting("engine_enabled", "1")
    asyncio.run(background._engine_disabled_daily_notice(SimpleNamespace(bot=None)))
    assert len(sent) == 1                       # no reminder while engine is ON


# ---------------------------------------------------------------- Admin panel key
def test_twelvedata_key_panel_side_encrypted_and_used(isolated):
    from fysb.handlers import admin_system
    raw = "A1b2C3d4E5f6G7h8I9j0K1l2"   # 24 alnum chars
    admin_system._td_key_store_sync(raw)
    config.invalidate_market_key_cache()
    assert config.market_api_key() == raw
    assert config.market_api_key_source() == "panel"
    # encrypted at rest — the raw key must never appear in the settings table:
    with db.conn() as c:
        blob = c.execute("SELECT value FROM settings WHERE key='twelvedata_api_key_enc'").fetchone()["value"]
    assert raw not in blob and blob.startswith("ENC1:")
    admin_system._td_key_clear_sync()
    config.invalidate_market_key_cache()
    assert config.market_api_key() == ""
    assert config.market_api_key_source() == ""


def test_env_key_takes_priority_over_panel(isolated):
    from fysb.handlers import admin_system
    admin_system._td_key_store_sync("Panel0Key0Panel0Key0AB")
    config.invalidate_market_key_cache()
    monkey_env = "Env0Key0Env0Key0Env0Key0AB"
    original = config._MARKET_KEY_RAW
    config._MARKET_KEY_RAW = monkey_env
    try:
        assert config.market_api_key() == monkey_env
        assert config.market_api_key_source() == "env"
    finally:
        config._MARKET_KEY_RAW = original
        config.invalidate_market_key_cache()


# ---------------------------------------------------------------- P1: diagnostics page
class _FakeMsg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


class _FakeQuery:
    def __init__(self):
        self.from_user = SimpleNamespace(id=9001)
        self.message = _FakeMsg()

    async def answer(self):
        return True


def test_diagnostics_page_renders_gate_truth(isolated):
    from fysb.handlers import admin_trading
    db.set_setting("engine_enabled", "1")
    _insert_open_signal()
    news_guard.set_cached_events([_event_in(10)], ts=time.time())
    q = _FakeQuery()
    asyncio.run(admin_trading.admin_engine_diagnostics(q, 9001, None, None))
    assert q.message.sent, "diagnostics page must render"
    body = q.message.sent[-1]
    assert "تشخيص بوابات الإشارات" in body
    assert "XAUUSD" in body
    assert "1/12" in body or "1/" in body  # open ceiling counters visible


def test_probe_reports_honest_verdict(isolated, monkeypatch):
    from fysb.handlers import admin_trading
    monkeypatch.setattr(md, "staleness_limit", lambda interval: 10800)
    monkeypatch.setattr(md, "fetch_price",
                        lambda sym, api_key="", use_cache=True: {"price": 4213.5})
    monkeypatch.setattr(md, "fetch_candles",
                        lambda sym, iv, api_key="", outputsize=120, use_cache=True:
                        {"values": _hour_candles(40, last_open_minutes_ago=21 * 60),
                         "age_seconds": 20 * 3600, "last_candle_iso": "2026-11-26 03:00:00"})
    out = admin_trading._probe_market_data_sync()
    assert "4,213.50" in out and "⚠️" in out     # honest stale verdict, real numbers


def test_news_admin_page_renders_with_health_and_controls(isolated):
    from fysb.handlers import admin_trading
    news_guard.set_cached_events([_event_in(15)], ts=time.time())  # fresh — no live fetch
    q = _FakeQuery()
    asyncio.run(admin_trading.admin_news_guard(q, 9001, None, None))
    body = q.message.sent[-1]
    assert "صحة التقويم" in body
    assert "حي ومحدّث" in body
