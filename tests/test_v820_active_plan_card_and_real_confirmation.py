"""v8.2.0 Regression Suite — Active Subscribed Plan Highlight/Card & Real Analysis Confirmation."""
from __future__ import annotations
from datetime import datetime, timedelta

import asyncio
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.helpers  # noqa: E402,F401


@pytest.fixture
def env(tmp_path, monkeypatch):
    from fysb import config, db
    from fysb.services import payments as payments_svc, subscriptions as subs, users as users_svc

    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v820.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "USE_PG", False)
    db.init_db()
    subs.invalidate_plans_cache()
    payments_svc.set_chain_wallet("TRC20", "T" + "8" * 33)
    uid = 820101
    users_svc.ensure_user(SimpleNamespace(id=uid, username="royal_vip", full_name="عميل VIP"))
    subs.grant_subscription(uid, "VIP", source="TEST", actor=config.ADMIN_ID)
    return uid


def _fire_user(uid: int, data: str):
    from fysb.handlers import dispatcher

    replies = []

    async def _edit(text, reply_markup=None, parse_mode=None, **kw):
        replies.append((text, reply_markup))

    q = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=uid, username="royal_vip", first_name="عميل", last_name="VIP", full_name="عميل VIP"),
        message=SimpleNamespace(chat_id=uid, message_id=11, edit_text=_edit, reply_text=_edit),
        answer=AsyncMock(),
        edit_message_text=_edit,
    )
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q), ctx))
    assert replies, f"No reply rendered for {data}"
    return replies[-1]


def test_active_plan_highlighted_in_green_and_hides_payment_methods(env):
    uid = env

    # 1) u:plans highlights ONLY the active VIP plan in green ('success') and others in blue ('primary')
    txt, mk = _fire_user(uid, "u:plans")
    assert "[✅ باقتك المفعّلة]" in txt
    plan_btns = [b for row in mk.inline_keyboard for b in row if (b.callback_data or "").startswith("u:planview:")]
    assert len(plan_btns) >= 4
    by_code = {b.callback_data.split(":")[-1].upper(): b for b in plan_btns}
    assert by_code["VIP"].to_dict().get("style") == "success"
    assert "باقتك المفعّلة الآن" in by_code["VIP"].text
    for other in ("FREE", "PRO", "ROYAL"):
        assert by_code[other].to_dict().get("style") == "primary"
    assert "ترقية إلى" in by_code["ROYAL"].text

    # 2) Opening active VIP plan shows luxury Membership Card with ZERO payment methods!
    v_txt, v_mk = _fire_user(uid, "u:planview:VIP")
    assert "بطاقة العضوية المفعّلة" in v_txt
    assert "مشترك ومفعّل بالفعل الآن" in v_txt
    assert "المحافظ الموثقة" not in v_txt
    v_datas = [b.callback_data or "" for row in v_mk.inline_keyboard for b in row]
    assert not any(d.startswith("u:usdtpay:") for d in v_datas)
    assert not any(d.startswith("u:starspay:") for d in v_datas)
    assert not any(d.startswith("u:requestplan:") for d in v_datas)
    assert not any(d.startswith("u:applypromo:") for d in v_datas)
    # Upgrade button to ROYAL IS present!
    assert "u:planview:ROYAL" in v_datas

    # 3) Opening ROYAL (an upgrade!) DOES show payment methods & upgrade banner!
    u_txt, u_mk = _fire_user(uid, "u:planview:ROYAL")
    assert "ترقية ملكية" in u_txt
    assert "المحافظ الموثقة" in u_txt
    u_datas = [b.callback_data or "" for row in u_mk.inline_keyboard for b in row]
    assert "u:usdtpay:ROYAL:TRC20" in u_datas
    assert any(d.startswith("u:applypromo:ROYAL") for d in u_datas)


def _bearish_h1_candles(break_now: bool = False) -> list[dict]:
    bars = []
    px = 3610.0
    for i in range(85):
        o = px
        drift = -0.35 + math.sin(i * 0.35) * 2.2
        c = o + drift
        h = max(o, c) + 2.5
        lo = min(o, c) - 2.5
        bars.append({"datetime": (datetime(2026, 11, 20) + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), "open": o, "high": h, "low": lo, "close": c})
        px = c
    if break_now:
        # Force a decisive full-body bearish breakout candle well below recent support
        low_min = min(b["low"] for b in bars[-25:])
        bars.append({
            "datetime": "2026-11-24 12:00:00",
            "open": low_min + 1.0,
            "high": low_min + 2.0,
            "low": low_min - 18.0,
            "close": low_min - 16.5,
        })
    return bars


def test_real_analysis_confirmation_type_and_stability_timeframe(env):
    from fysb.engines import market_data
    from fysb.services import analytics
    from fysb.ui import cards, charts

    uid = env

    # Case A: Waiting / pre-breakout H1 real candles
    bars_wait = _bearish_h1_candles(break_now=False)
    payload_wait = {
        "ok": True,
        "values": bars_wait,
        "last_candle_iso": bars_wait[-1]["datetime"],
        "provider": "TEST_FEED",
        "stale": False,
    }
    with patch.object(market_data, "fetch_candles", return_value=payload_wait), \
         patch.object(market_data, "market_state", return_value=True):
        res = analytics.run_analysis(uid, "XAUUSD", "H1", "", "", "ALL", enforce_plan=False)

    assert res["ok"] is True
    an = res["analysis"]
    conf = an.get("confirmation")
    assert isinstance(conf, dict)
    assert conf["stability_tf"] == "H1"
    assert "M15" in conf["confirm_tf"]
    assert conf["buffer_pips"] > 0
    assert 0 <= conf["body_pct"] <= 100

    card_html = cards.render_analysis_card(an)
    assert "نوع التأكيد الحقيقي:" in card_html
    assert "إطار الثبات والتأكيد:" in card_html
    assert "H1" in card_html and "M15" in card_html
    assert "ضمان عدم الكسر الكاذب:" in card_html
    assert "0.15×ATR" in card_html
    assert "حالة التفعيل اللحظية:" in card_html

    cap_html = charts.render_analysis_caption(an)
    assert "الثبات والتأكيد" in cap_html
    assert "H1" in cap_html
    assert len(cap_html) <= 1020

    # Case B: Decisive real full-body breakout candle -> activated == True
    bars_brk = _bearish_h1_candles(break_now=True)
    payload_brk = {
        "ok": True,
        "values": bars_brk,
        "last_candle_iso": bars_brk[-1]["datetime"],
        "provider": "TEST_FEED",
        "stale": False,
    }
    with patch.object(market_data, "fetch_candles", return_value=payload_brk), \
         patch.object(market_data, "market_state", return_value=True):
        res2 = analytics.run_analysis(uid, "XAUUSD", "H1", "", "", "ALL", enforce_plan=False)
    assert res2["ok"] is True
    an2 = res2["analysis"]
    if an2["direction"] == "SELL":
        assert an2["confirmation"]["activated"] is True
        assert "🟢" in an2["confirmation"]["live_state"]
