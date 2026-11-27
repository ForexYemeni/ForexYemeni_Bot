"""v8.14.0 tests — guaranteed fresh page at boot + real-spot gold candles.

The owner's two outstanding problems, fixed root-first:
  1. the 46 signals survived because the fresh page waited for a button press;
     now the owner's documented decision EXECUTES deterministically on the
     first boot after v8.14.0 (once, stamped, notices queued for Telegram).
  2. gold died entirely («تعذر جلب» + no chart) when the futures venue and the
     keyed provider both failed — because fx-ref had NO XAUUSD mapping. Now
     XAUUSD=X gives the chain a REAL SPOT candle source (no offset needed).
"""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import config, db
from fysb.engines import market_data as md
from fysb.engines import tracker


@pytest.fixture()
def seeded_db():
    db.init_db()
    with db.conn() as c:
        for t in ("signals", "signal_deliveries", "trade_journal",
                  "exchange_orders", "bridge_orders", "engine_runs"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM settings WHERE key IN ('fresh_page_executed',"
                  "'fresh_page_notice_holders','fresh_page_notice_pending')")
    yield


def _mk_sig(status="RUNNING", user=7001, entry=3673.0):
    with db.conn() as c:
        sid = c.execute("""INSERT INTO signals(symbol,timeframe,strategy,direction,entry,stop,tp1,tp2,tp3,
                           rr,score,status,source,created_at,filled_at,closed_at,expires_at)
                           VALUES('XAUUSD','15min','AMD','SELL',?,3684.8,3660,3650,3640,
                           2.5,88,?,'ENGINE','2026-11-27 09:00:00','2026-11-27 09:00:00','','')""",
                        (entry, status)).lastrowid
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(?,?,?)",
                  (user, sid, config.now()))
        return sid


def _boot_fresh_page():
    import main
    main._fresh_page_on_boot()


# ---------------------------------------------------------------------------
# boot-time wipe: deterministic, once, holders queued
# ---------------------------------------------------------------------------

def test_boot_fresh_page_wipes_once_and_stamps(seeded_db):
    s1 = _mk_sig("RUNNING", user=7001, entry=3673.0)
    s2 = _mk_sig("ACTIVE", user=7002, entry=3674.0)
    _boot_fresh_page()
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) n FROM signal_deliveries").fetchone()["n"] == 0
    assert db.get_setting("fresh_page_executed") == "1"
    assert db.get_setting("fresh_page_notice_pending") == "1"
    holders = json.loads(db.get_setting("fresh_page_notice_holders"))
    assert set(holders) == {7001, 7002, config.ADMIN_ID}   # captured BEFORE the wipe
    assert s1 and s2


def test_boot_fresh_page_never_runs_twice(seeded_db):
    _mk_sig("RUNNING")
    _boot_fresh_page()
    assert db.get_setting("fresh_page_executed") == "1"
    # second boot: stamp blocks — even a new signal survives
    s = _mk_sig("ACTIVE", user=8001)
    _boot_fresh_page()
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"] == 1
        assert c.execute("SELECT id FROM signals WHERE id=?", (s,)).fetchone()


# ---------------------------------------------------------------------------
# gold: real-spot fx-ref source in the chain
# ---------------------------------------------------------------------------

def test_xauusd_maps_to_real_spot_yahoo():
    assert md._yahoo_symbol_for("XAUUSD") == "XAUUSD=X"


def test_gold_chain_includes_fx_ref_before_keyed():
    assert md._CANDLE_CHAINS["gold"] == ["venue-gold", "fx-ref", "twelvedata"]


def _spot_gold_candles(n=60, base=3665.0):
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    return [{"datetime": (end - timedelta(minutes=15 * (n - i))).strftime("%Y-%m-%d %H:%M:%S"),
             "open": base, "high": base + 1.0, "low": base - 1.0, "close": base,
             "volume": 100} for i in range(n)]


def test_gold_survives_via_fx_ref_when_venue_and_keyed_fail():
    # venue-gold: raw futures with NO calibration at all → honest refusal;
    # fx-ref (XAUUSD=X): true spot candles → the chain SURVIVES at spot level
    md._cache.clear()
    md._fail_streak.clear()
    calib = config.DB_FILE.parent / "spot_calibration.json"
    if calib.exists():
        calib.unlink()
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref", "twelvedata"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[RuntimeError("yahoo GC=F down"), _spot_gold_candles()]), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md, "_fetch_td_candles", side_effect=RuntimeError("td down")), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    # fx-ref IS spot — the close is the spot level itself, no offset fiction
    assert float(p["values"][-1]["close"]) == pytest.approx(3665.0)
    assert p["source"] == "LIVE"
    if calib.exists():
        calib.unlink()
