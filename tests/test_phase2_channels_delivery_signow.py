"""Phase 2 Tests: Channels, Unified Delivery, New-User Defaults, SignalView Refs,
Signal-Now Privacy, Global Cap Isolation & Auto-Search Watcher (v6.14.0)."""
import asyncio
from unittest.mock import patch

import pytest

from fysb import config, db
from fysb.engines import risk, signal_engine, tracker
from fysb.handlers import admin_channels, dispatcher, inputs, signal_now
from fysb.services import subscriptions as subs
from fysb.services import users as users_service
from tests.helpers import FakeUser, MockBot, MockContext, MockQuery, page_for

ADMIN = 9001
USER1 = 600101
USER2 = 600102


@pytest.fixture
def phase2_db(tmp_path, monkeypatch):
    db_file = tmp_path / "phase2.db"
    monkeypatch.setattr(config, "DB_FILE", db_file)
    monkeypatch.setattr(config, "USE_PG", False)
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN)
    db._settings_cache.clear()
    db.init_db(snapshot_before_structural=False)
    users_service.ensure_user(FakeUser(USER1, "UserOne"))
    users_service.ensure_user(FakeUser(USER2, "UserTwo"))
    # Give both users VIP so plan entitlements cover EURUSD/XAUUSD/H1/M15/ICT
    subs.grant_subscription(USER1, "VIP", source="MANUAL", actor=ADMIN)
    subs.grant_subscription(USER2, "VIP", source="MANUAL", actor=ADMIN)
    yield
    db._settings_cache.clear()


def _insert_sig(symbol="XAUUSD", direction="BUY", tf="M15", strategy="ICT",
                score=85, status="RUNNING", source="AUTO", created_by=0) -> int:
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
               strategy,reason,status,source,created_by,created_at,filled_at,live_px)
               VALUES(?,?,?,2600.0,2590.0,2620.0,2630.0,2640.0,'2620,2630,2640',2.0,?,?,?,?,?,?,?, ?,2600.0)""",
            (symbol, direction, tf, score, strategy, "test", status, source, created_by, config.now(), config.now()),
        )
        return cur.lastrowid


# =============================================================================
# 1. Multi-channel pinning + tracker routing (no user_id=0 collision!)
# =============================================================================
def test_multi_channel_pinning_and_tracker_alerts(phase2_db):
    ch1_id = admin_channels.add_channel("-1001111111", "Gold Channel 1", "channel")
    ch2_id = admin_channels.add_channel("-1002222222", "Gold Channel 2", "channel")
    ch1 = admin_channels.get_channel(ch1_id)
    ch2 = admin_channels.get_channel(ch2_id)
    sid = _insert_sig(status="RUNNING")

    admin_channels.pin_channel_delivery(ch1, sid, type("M", (), {"message_id": 101})())
    admin_channels.pin_channel_delivery(ch2, sid, type("M", (), {"message_id": 202})())

    with db.conn() as c:
        rows = c.execute("SELECT user_id, chat_id, message_id FROM signal_deliveries WHERE signal_id=? ORDER BY user_id",
                         (sid,)).fetchall()
    assert len(rows) == 2
    assert {(r["chat_id"], r["message_id"]) for r in rows} == {("-1001111111", 101), ("-1002222222", 202)}

    sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
    notes, _, edits = tracker._apply_transition(sig, "TP1_HIT", {"last_candle_iso": config.now()}, 0.0)
    note_targets = {str(t) for t, _ in notes}
    edit_targets = {(str(chat), mid) for _, chat, mid, _ in edits}
    assert "-1001111111" in note_targets and "-1002222222" in note_targets
    assert ("-1001111111", 101) in edit_targets and ("-1002222222", 202) in edit_targets


# =============================================================================
# 2. Unified emit_to_users -> delivers to BOTH users AND matching channels
# =============================================================================
def test_emit_to_users_broadcasts_to_enabled_channels_without_duplicates(phase2_db):
    c1 = admin_channels.add_channel("-1003333333", "Active Gold", "channel")
    c2 = admin_channels.add_channel("-1004444444", "Disabled Channel", "channel")
    admin_channels.update_channel(c1, enabled=1, auto_signals=1, markets="XAU/USD", timeframes="M15", strategies="ALL")
    admin_channels.update_channel(c2, enabled=0, auto_signals=1, markets="XAUUSD", timeframes="M15", strategies="ALL")

    sid = _insert_sig(symbol="XAUUSD", tf="M15", strategy="ICT", score=86)
    sig = dict(db.conn().execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
    bot = MockBot()

    stats = asyncio.run(signal_engine.emit_to_users(sig, inputs.bot_sender_from(bot)))
    assert stats["sent"] == 2
    assert stats["channels_sent"] == 1
    sent_chats = {str(m["chat_id"]) for m in bot.sent}
    assert str(USER1) in sent_chats and str(USER2) in sent_chats
    assert "-1003333333" in sent_chats
    assert "-1004444444" not in sent_chats

    # Second call to emit_to_channels for the same signal must NOT double-post!
    ch_stats2 = asyncio.run(admin_channels.emit_to_channels(sig, bot))
    assert ch_stats2["channels_sent"] == 0


# =============================================================================
# 3. New user open defaults + slash-normalized exact symbol matching
# =============================================================================
def test_new_user_defaults_open_and_slash_normalization(phase2_db):
    u1 = users_service.get_user(USER1)
    assert u1["market"] == ""
    assert u1["timeframe"] == ""
    assert u1["strategy"] == ""
    assert u1["min_score"] == 0

    # New user receives EURUSD H1 score=74 immediately (previously silently dropped by XAUUSD/M15/80!)
    assert signal_engine.passes_personal_filters(u1, "EUR/USD", "H1", "ICT", 74) is True

    # Exact token match: "EUR" must NOT match "EURUSD", while "XAU/USD" matches "XAUUSD"
    u_xau = dict(u1, market="XAU/USD")
    assert signal_engine.passes_personal_filters(u_xau, "XAUUSD", "H1", "ICT", 74) is True
    u_eur = dict(u1, market="EUR")
    assert signal_engine.passes_personal_filters(u_eur, "EURUSD", "H1", "ICT", 74) is False


# =============================================================================
# 4. u:signalview pins chat_id + message_id for live tracker edits
# =============================================================================
def test_signalview_pins_delivery_refs(phase2_db):
    sid = _insert_sig(symbol="XAUUSD", status="RUNNING")
    q = MockQuery(f"u:signalview:{sid}", USER1)
    ctx = MockContext(MockBot())
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))

    with db.conn() as c:
        row = c.execute(
            "SELECT chat_id, message_id FROM signal_deliveries WHERE user_id=? AND signal_id=?",
            (USER1, sid),
        ).fetchone()
    assert row is not None
    assert str(row["chat_id"]) == str(q.message.chat_id)
    assert int(row["message_id"]) == int(q.message.message_id)


# =============================================================================
# 5. Auto-Search Watcher works seamlessly after NO_SETUP (DRAFT_KEY preserved!)
# =============================================================================
def test_signow_preserves_draft_on_no_setup_so_watcher_starts(phase2_db):
    ctx = MockContext(MockBot())
    ctx.user_data[signal_now.DRAFT_KEY] = {
        "stage": "strategy", "style": "SCALP", "is_admin": False,
        "symbol": "XAUUSD", "tf": "M15", "strategies": ["ICT"], "tfs": ["M15"],
    }

    def fire_ctx(data: str):
        q = MockQuery(data, USER1)
        upd = type("Update", (), {})()
        upd.callback_query = q
        asyncio.run(dispatcher.buttons(upd, ctx))
        return q

    with patch.object(signal_now, "generate_signal", return_value={"status": "NO_SETUP", "reason": "no setup"}):
        q1 = fire_ctx("u:signowstrat:ICT")
    assert "البحث التلقائي" in page_for(q1, ctx)
    # DRAFT_KEY must STILL contain strategy='ICT' (previously popped and lost!)
    assert ctx.user_data.get(signal_now.DRAFT_KEY, {}).get("strategy") == "ICT"

    # Clicking watcher period works directly WITHOUT any manual re-injection!
    q2 = fire_ctx("u:signowwatch:30")
    assert "البحث التلقائي الملكي مفعّل" in page_for(q2, ctx)
    w = signal_now.active_watcher(USER1)
    assert w is not None
    assert w["symbol"] == "XAUUSD" and w["strategy"] == "ICT" and w["interval_minutes"] == 30


# =============================================================================
# 6. Signal-Now privacy + global max_open_signals isolation
# =============================================================================
def test_signow_is_private_to_requester_and_isolated_from_global_cap(phase2_db):
    # Create an enabled channel + USER2 (both would receive an AUTO broadcast)
    c1 = admin_channels.add_channel("-1005555555", "Public Channel", "channel")
    admin_channels.update_channel(c1, enabled=1, auto_signals=1, markets="XAUUSD", timeframes="M15", strategies="ALL")

    priv_sid = _insert_sig(symbol="XAUUSD", tf="M15", strategy="ICT", source="SIGNAL_NOW", created_by=USER1)
    bot = MockBot()
    ctx = MockContext(bot)
    ctx.user_data[signal_now.DRAFT_KEY] = {
        "stage": "strategy", "style": "SCALP", "is_admin": False,
        "symbol": "XAUUSD", "tf": "M15", "strategies": ["ICT"], "tfs": ["M15"],
    }
    q = MockQuery("u:signowstrat:ICT", USER1)
    upd = type("Update", (), {})()
    upd.callback_query = q

    with patch.object(signal_now, "generate_signal",
                      return_value={"status": "SIGNAL_CREATED", "signal_id": priv_sid, "score": 88}):
        asyncio.run(dispatcher.buttons(upd, ctx))

    sent_targets = {str(m["chat_id"]) for m in bot.sent}
    assert str(USER1) in sent_targets, "requester MUST receive their own private signal"
    assert str(USER2) not in sent_targets, "private Signal-Now MUST NOT broadcast to other users"
    assert "-1005555555" not in sent_targets, "private Signal-Now MUST NOT broadcast to public channels"

    # Global cap isolation: 3 open SIGNAL_NOW rows must NOT block AUTO signal generation!
    db.set_setting("max_open_signals", "3")
    _insert_sig(symbol="EURUSD", source="SIGNAL_NOW", created_by=USER1)
    _insert_sig(symbol="GBPUSD", source="SIGNAL_NOW", created_by=USER2)
    auto_dec = risk.check_signal("XAUUSD", rr=2.5, atr_pct_value=1.0, source="AUTO")
    assert auto_dec.allowed is True, f"private SIGNAL_NOW rows must not block global AUTO engine: {auto_dec.reason}"
