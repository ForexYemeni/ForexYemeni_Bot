"""v6.20.7 regression tests:
1. Backtest format_report() displays ALL executed strategies (no [:6] truncation),
   total evaluated strategy count (13), and silent strategies transparently.
2. Linked channels default to ALL markets/timeframes/strategies, support 1-tap
   a:chanall enablement, and cleanly toggle ALL in markets/timeframes UI.
3. End-to-end auto signal delivery + live-card editing + push alerts for linked
   channels (including numeric chat_id normalization, signature preservation,
   admin_track_now live edits + expiry alerts, and admin_engine_now broadcasting).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fysb import config, db
from fysb.engines.backtest import BacktestReport, format_report


def test_backtest_report_shows_all_13_strategies_without_truncation():
    all_strats = [
        "ict", "smc", "price_action", "crt", "amd", "turtle_soup",
        "silver_bullet", "fibonacci", "trend", "ichimoku", "classic", "sk", "wyckoff",
    ]
    by_strat = {
        code: {"trades": 2, "wins": 1, "win_rate_pct": 50.0, "net_r": 0.75}
        for code in all_strats[:10]
    }
    rep = BacktestReport(
        symbol="XAUUSD",
        timeframe="M15",
        bars_tested=750,
        strategies_used=all_strats,
        total_signals=25,
        filled_trades=20,
        unfilled=5,
        wins=10,
        losses=10,
        win_rate_pct=50.0,
        net_r=7.5,
        expectancy_r=0.375,
        profit_factor=1.75,
        max_drawdown_r=3.0,
        by_strategy=by_strat,
    )
    html = format_report(rep)
    # Every single one of the 10 active strategies must appear (no [:6] cap!)
    for code in all_strats[:10]:
        assert f"<b>{code}</b>" in html, f"Strategy {code} was truncated from report"
    # Total evaluated count (13) and active ratio (10/13) are shown
    assert "<b>13</b>" in html
    assert "(10/13)" in html
    # Remaining 3 silent strategies are listed transparently
    for silent in all_strats[10:]:
        assert silent in html


def test_new_channel_defaults_all_and_matches_every_market_tf_strategy():
    from fysb.handlers import admin_channels

    cid = admin_channels.add_channel("-1001998877665", "VIP Royal Channel", "channel")
    ch = admin_channels.get_channel(cid)
    assert ch["markets"] == "ALL"
    assert ch["timeframes"] == "ALL"
    assert ch["strategies"] == "ALL"
    assert ch["enabled"] == 1
    assert ch["auto_signals"] == 1

    # Matches Gold, Forex, Crypto, Indices across any TF & strategy
    for sym, tf, strat in [
        ("XAUUSD", "M15", "ict"),
        ("EURUSD", "H4", "smc"),
        ("BTCUSDT", "H1", "trend"),
        ("US30", "M5", "wyckoff"),
    ]:
        assert admin_channels._channel_matches_signal(
            ch, {"symbol": sym, "timeframe": tf, "strategy": strat}
        )


def test_channel_enable_all_and_feat_toggle():
    from fysb.handlers import admin_channels

    cid = admin_channels.add_channel("-1001112223334", "Legacy Gold Channel", "channel")
    admin_channels.update_channel(
        cid, enabled=0, auto_signals=0, markets="XAUUSD", timeframes="M15", strategies="ict"
    )
    bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock())
    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=config.ADMIN_ID, message_id=50,
            edit_text=AsyncMock(), reply_text=AsyncMock(),
        ),
        from_user=SimpleNamespace(id=config.ADMIN_ID, full_name="Admin"),
        answer=AsyncMock(),
    )
    ctx = SimpleNamespace(user_data={}, bot=bot)

    asyncio.run(admin_channels.admin_chan_enable_all(q, config.ADMIN_ID, ctx, bot, cid))
    ch = admin_channels.get_channel(cid)
    assert ch["enabled"] == 1
    assert ch["auto_signals"] == 1
    assert ch["markets"] == "ALL"
    assert ch["timeframes"] == "ALL"
    assert ch["strategies"] == "ALL"

    # Toggle specific market replaces ALL cleanly
    asyncio.run(admin_channels.admin_chan_feat_set(q, config.ADMIN_ID, ctx, bot, cid, "markets", "EURUSD"))
    assert admin_channels.get_channel(cid)["markets"] == "EURUSD"

    # Toggle ALL back clears specific selection
    asyncio.run(admin_channels.admin_chan_feat_set(q, config.ADMIN_ID, ctx, bot, cid, "markets", "ALL"))
    assert admin_channels.get_channel(cid)["markets"] == "ALL"

    # Helper expands ALL safely (never returns literal "ALL")
    resolved_tfs = admin_channels._resolve_channel_tfs(admin_channels.get_channel(cid))
    assert "ALL" not in resolved_tfs and "M15" in resolved_tfs
    resolved_mkts = asyncio.run(admin_channels._resolve_channel_markets(admin_channels.get_channel(cid)))
    assert "ALL" not in resolved_mkts and len(resolved_mkts) >= 1


def test_channel_auto_delivery_live_card_edit_and_numeric_chat_id():
    from fysb.engines import signal_engine, tracker
    from fysb.handlers import admin_channels, background

    cid = admin_channels.add_channel("-1001555666777", "Signals Live Track", "channel")
    # Insert a realistic open BUY signal on EURUSD H1 (non-Gold!)
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               rr,strategy,score,reason,status,source,created_at)
               VALUES('EURUSD','H1','BUY',1.1000,1.0950,1.1050,1.1100,1.1150,
               2.0,'ict',86,'test','ACTIVE','auto',?)""",
            (config.now(),),
        )
        sid = cur.lastrowid
        sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

    sent_calls = []
    edited_calls = []

    async def fake_send(chat_id, text, **kwargs):
        sent_calls.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=9001)

    async def fake_edit(text=None, chat_id=None, message_id=None, **kwargs):
        edited_calls.append((chat_id, message_id, text))
        return SimpleNamespace(message_id=message_id)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=fake_send),
                          edit_message_text=AsyncMock(side_effect=fake_edit))

    async def bot_send(uid, text):
        return await fake_send(uid, text)

    bot_send._bot = bot

    stats = asyncio.run(signal_engine.emit_to_users(sig, bot_send, bot=bot, include_channels=True))
    assert stats["channels_sent"] >= 1
    assert any(c_id == -1001555666777 for c_id, _ in sent_calls)

    # Delivery ref was pinned with negative channel user_id and real message_id
    refs = tracker._delivery_refs(sid)
    chan_refs = [r for r in refs if r["user_id"] == -cid]
    assert len(chan_refs) == 1
    assert chan_refs[0]["chat_id"] == "-1001555666777"
    assert int(chan_refs[0]["message_id"]) == 9001

    # Live-card edit preserves BOT_SIGNATURE footer for channel posts
    edits = tracker._card_refresh_edits(sig, 1.1025, chan_refs)
    assert len(edits) == 1
    _u, edit_cid, edit_mid, edit_txt = edits[0]
    assert edit_cid == "-1001555666777"
    assert edit_mid == 9001
    assert config.BOT_SIGNATURE in edit_txt

    # _safe_send and _safe_edit normalize numeric string chat_id to int
    assert asyncio.run(background._safe_send(bot, "-1001555666777", "🔔 TP1")) is True
    assert asyncio.run(background._safe_edit(bot, "-1001555666777", 9001, edit_txt)) is True
    assert sent_calls[-1][0] == -1001555666777
    assert edited_calls[-1][0] == -1001555666777
    assert edited_calls[-1][1] == 9001


def test_admin_track_now_applies_channel_live_edits_and_expiry_alerts(monkeypatch):
    from fysb.engines import tracker
    from fysb.handlers import admin_trading

    monkeypatch.setattr(
        tracker,
        "check_open_signals",
        lambda _k: {
            "stats": {"checked": 2, "updated": 1, "skipped_stale": 0, "errors": 0},
            "notifications": [("-1001555666777", "🎯 TP1 hit")],
            "edits": [(-1, "-1001555666777", 9001, "LIVE UPDATED CARD")],
        },
    )
    monkeypatch.setattr(
        tracker,
        "expire_with_alerts",
        lambda: (
            1,
            [(-1, "-1001555666777", 9002, "EXPIRED CARD")],
            [("-1001555666777", "⏳ Signal expired")],
        ),
    )

    sends = []
    edits = []

    async def fake_send(chat_id, text, **kwargs):
        sends.append((chat_id, text))

    async def fake_edit(text=None, chat_id=None, message_id=None, **kwargs):
        edits.append((chat_id, message_id, text))

    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=fake_send),
        edit_message_text=AsyncMock(side_effect=fake_edit),
    )
    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=config.ADMIN_ID, message_id=77,
            edit_text=AsyncMock(), reply_text=AsyncMock(),
        ),
        from_user=SimpleNamespace(id=config.ADMIN_ID, full_name="Admin"),
        answer=AsyncMock(),
    )
    ctx = SimpleNamespace(user_data={}, bot=bot)

    asyncio.run(admin_trading.admin_track_now(q, config.ADMIN_ID, ctx, bot))

    # Both live update edit AND expiry edit reached the linked channel as int chat_id!
    assert (-1001555666777, 9001, "LIVE UPDATED CARD") in edits
    assert (-1001555666777, 9002, "EXPIRED CARD") in edits
    # Both TP1 notification AND expiry notification reached the linked channel!
    assert (-1001555666777, "🎯 TP1 hit") in sends
    assert (-1001555666777, "⏳ Signal expired") in sends


def test_admin_engine_now_broadcasts_created_signal_to_channels(monkeypatch):
    from fysb.engines import signal_engine
    from fysb.handlers import admin_channels, admin_trading

    admin_channels.add_channel("-1001888999000", "Auto Broadcast Chan", "channel")
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               rr,strategy,score,reason,status,source,created_at)
               VALUES('XAUUSD','M15','BUY',2650.0,2640.0,2660.0,2670.0,2685.0,
               2.0,'ict',88,'engine_now','ACTIVE','auto',?)""",
            (config.now(),),
        )
        sid = cur.lastrowid

    monkeypatch.setattr(admin_trading, "_enabled_verified_market_dicts", lambda: [{"symbol": "XAUUSD"}])
    monkeypatch.setattr(
        signal_engine,
        "generate_signal",
        lambda sym: {"status": "SIGNAL_CREATED", "signal_id": sid, "score": 88},
    )

    chan_msgs = []

    async def fake_send(chat_id, text, **kwargs):
        chan_msgs.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=4444)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=fake_send))
    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=config.ADMIN_ID, message_id=88,
            edit_text=AsyncMock(), reply_text=AsyncMock(),
        ),
        from_user=SimpleNamespace(id=config.ADMIN_ID, full_name="Admin"),
        answer=AsyncMock(),
    )
    ctx = SimpleNamespace(user_data={}, bot=bot)

    asyncio.run(admin_trading.admin_engine_now(q, config.ADMIN_ID, ctx, bot))

    assert any(c_id == -1001888999000 for c_id, _ in chan_msgs)
