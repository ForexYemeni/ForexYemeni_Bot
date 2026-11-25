"""v6.26.0 Phase 3 tests: Free local Pillow charts (#13/#14) + Risk Profile,
1-Tap Lot Calculator, Daily Drawdown Guard & Auto Trading Journal (#21-#24)."""
import asyncio
import io
import pytest

from fysb import config, db
from fysb.services import risk_journal, user_trade
from fysb.ui import charts, keyboards as kb


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    p = tmp_path / "phase3.db"
    monkeypatch.setattr(config, "DB_FILE", p)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    db.init_db()
    return tmp_path


def _sample_candles(n: int = 55, base: float = 2650.0, step: float = 0.6) -> list[dict]:
    out = []
    px = base
    for i in range(n):
        o = px
        delta = step if i % 3 != 0 else -step * 0.55
        c = o + delta
        hi = max(o, c) + 0.9
        lo = min(o, c) - 0.8
        out.append({
            "datetime": f"2026-11-24T{(i // 4):02d}:{(i % 4) * 15:02d}:00Z",
            "open": round(o, 2),
            "high": round(hi, 2),
            "low": round(lo, 2),
            "close": round(c, 2),
            "volume": 1200 + i * 15,
        })
        px = c
    return out


def _sample_signal(direction: str = "BUY", status: str = "RUNNING", pips_result: float = 0.0) -> dict:
    if direction == "BUY":
        return {
            "id": 77,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "direction": "BUY",
            "strategy": "ICT",
            "score": 88,
            "entry": 2655.0,
            "stop": 2645.0,
            "tp1": 2667.0,
            "tp2": 2680.0,
            "tp3": 2695.0,
            "rr": 2.5,
            "status": status,
            "pips_result": pips_result,
            "created_at": config.now(),
        }
    return {
        "id": 78,
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "direction": "SELL",
        "strategy": "SMC",
        "score": 84,
        "entry": 2675.0,
        "stop": 2685.0,
        "tp1": 2663.0,
        "tp2": 2650.0,
        "tp3": 2635.0,
        "status": status,
        "pips_result": pips_result,
        "created_at": config.now(),
    }


def _insert_signal(sig: dict) -> int:
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,strategy,score,entry,stop,tp1,tp2,tp3,rr,
               status,pips_result,source,created_at,closed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig["symbol"], sig["timeframe"], sig["direction"], sig["strategy"],
                sig["score"], sig["entry"], sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"], 2.5,
                sig["status"], sig.get("pips_result", 0.0), "AUTO",
                sig["created_at"], config.now() if sig["status"] in ("TP2_HIT", "TP3_HIT", "SL_HIT") else "",
            ),
        )
        return int(cur.lastrowid)


def test_version_and_phase3_schema(isolated_db):
    assert config.VERSION == "6.26.0"
    assert charts.charts_enabled() is True
    with db.conn() as c:
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "user_risk_profiles" in tables
    assert "trade_journal" in tables


def test_pillow_signal_chart_and_proof_chart_png(isolated_db):
    from PIL import Image
    candles = _sample_candles()

    png_buy = charts.render_signal_chart(_sample_signal("BUY"), candles)
    assert png_buy is not None and png_buy[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(io.BytesIO(png_buy))
    assert im.size == (1200, 680)

    png_sell = charts.render_signal_chart(_sample_signal("SELL"), candles)
    assert png_sell is not None and png_sell[:8] == b"\x89PNG\r\n\x1a\n"

    # Proof chart (#14) for TP3_HIT and SL_HIT
    proof_win = charts.render_proof_chart(_sample_signal("BUY", status="TP3_HIT", pips_result=400.0), candles)
    assert proof_win is not None and proof_win[:8] == b"\x89PNG\r\n\x1a\n"
    im2 = Image.open(io.BytesIO(proof_win))
    assert im2.size == (1200, 680)

    proof_loss = charts.render_proof_chart(_sample_signal("SELL", status="SL_HIT", pips_result=-100.0), candles)
    assert proof_loss is not None and proof_loss[:8] == b"\x89PNG\r\n\x1a\n"

    # Honest behavior: when candles are empty, never fabricate fake bars
    png_empty = charts.render_signal_chart(_sample_signal("BUY"), [])
    assert png_empty is None

    # Toggle off/on
    db.set_setting("charts_enabled", "0")
    assert charts.charts_enabled() is False
    db.set_setting("charts_enabled", "1")
    assert charts.charts_enabled() is True


def test_risk_profile_and_lot_calculator(isolated_db):
    uid = 900101
    prof = risk_journal.get_risk_profile(uid)
    assert prof["balance"] == 1000.0
    assert prof["risk_pct"] == 1.0
    assert prof["max_daily_dd_pct"] == 3.0

    # Update profile and clamp extreme inputs cleanly
    prof2 = risk_journal.update_risk_profile(uid, balance=5000.0, risk_pct=2.0, leverage=200, max_daily_dd_pct=4.0)
    assert prof2["balance"] == 5000.0
    assert prof2["risk_pct"] == 2.0
    assert prof2["leverage"] == 200
    assert prof2["max_daily_dd_pct"] == 4.0

    sig = _sample_signal("BUY")
    calc = risk_journal.calculate_signal_lot(uid, sig)
    assert calc["risk_usd"] == 100.0  # 2% of $5,000
    assert calc["lots"] > 0
    assert len(calc["tp_profits"]) == 3
    assert calc["tp_profits"][2]["usd"] > calc["tp_profits"][1]["usd"] > calc["tp_profits"][0]["usd"] > 0

    card = risk_journal.render_lot_calculator_card(uid, sig)
    assert "حاسبة اللوت" in card
    assert "XAUUSD" in card

    # Compact snippet auto-injected into personal_block (#22)
    snip = risk_journal.compact_risk_snippet(uid, sig)
    assert "lot" in snip
    pblock = user_trade.personal_block(sig, uid)
    assert snip in pblock


def test_auto_trading_journal_and_daily_drawdown_guard(isolated_db):
    uid = 900202
    risk_journal.update_risk_profile(uid, balance=1000.0, risk_pct=2.0, max_daily_dd_pct=3.0)

    # 1) Create two RUNNING signals, log TAKEN, then close them as SL_HIT (breach 3% DD guard!)
    s_loss1 = _sample_signal("SELL", status="RUNNING", pips_result=0.0)
    s_loss1["id"] = _insert_signal(s_loss1)
    s_loss2 = _sample_signal("SELL", status="RUNNING", pips_result=0.0)
    s_loss2["id"] = _insert_signal(s_loss2)
    s_skip = _sample_signal("BUY", status="RUNNING", pips_result=0.0)
    s_skip["id"] = _insert_signal(s_skip)

    r1 = risk_journal.log_journal_action(uid, s_loss1, "TAKEN")
    assert r1["action"] == "TAKEN" and r1["lots"] > 0
    risk_journal.log_journal_action(uid, s_loss2, "TAKEN")
    risk_journal.log_journal_action(uid, s_skip, "SKIPPED")

    # Simulate tracker closing both taken signals as SL_HIT (-100 pips each => -2% * 2 = -4% > 3%)
    with db.conn() as c:
        c.execute(
            "UPDATE signals SET status='SL_HIT', pips_result=-100.0, closed_at=? WHERE id IN (?,?)",
            (config.now(), s_loss1["id"], s_loss2["id"]),
        )

    synced = risk_journal.sync_user_journal(uid)
    assert synced == 2

    dd = risk_journal.daily_drawdown_status(uid)
    assert dd["trades_closed_today"] == 2
    assert dd["today_loss_pct"] >= 3.0
    assert dd["breached"] is True

    rcard = risk_journal.render_risk_profile_card(uid)
    assert "4.00%" in rcard
    assert "🚨" in rcard

    # Now add a big winning TP3 trade and verify journal stats & profit factor
    s_win = _sample_signal("BUY", status="TP3_HIT", pips_result=400.0)
    s_win["id"] = _insert_signal(s_win)
    risk_journal.log_journal_action(uid, s_win, "TAKEN")

    summary = risk_journal.journal_summary(uid, days=30)
    assert summary["taken_count"] == 3
    assert summary["skipped_count"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 2
    assert summary["win_rate"] == 33.3
    assert summary["net_pips"] == 200.0
    assert summary["net_usd"] > 0

    jcard = risk_journal.render_journal_card(uid, days=30)
    assert "مفكرة التداول التلقائية" in jcard
    assert "33.3%" in jcard


def test_signal_action_keyboard_and_emit_compatibility(isolated_db):
    markup = kb.signal_action_keyboard(55)
    assert len(markup.inline_keyboard) == 2
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert cbs == ["u:lotcalc:55", "u:sigchart:55", "u:jtake:55", "u:jskip:55"]

    # Ensure emit_to_users passes reply_markup when bot_send accepts it
    from fysb.engines import signal_engine
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(user_id,username,full_name,joined_at,notifications,is_blocked) VALUES(?,?,?,?,1,0)",
            (555001, "tester", "Test", config.now()),
        )
    sig = _sample_signal("BUY", status="ACTIVE")
    sig["strategy"] = "CLASSIC"
    sid = _insert_signal(sig)
    with db.conn() as c:
        sig_row = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

    captured = []

    async def bot_send_with_kb(uid, text, reply_markup=None):
        captured.append((uid, text, reply_markup))
        return None

    res = asyncio.run(signal_engine.emit_to_users(sig_row, bot_send_with_kb, include_channels=False))
    assert res["sent"] == 1
    assert len(captured) == 1
    assert captured[0][2] is not None
    assert captured[0][2].inline_keyboard[0][0].callback_data == f"u:lotcalc:{sid}"
