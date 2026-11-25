"""v7.0.1 Hotfix regression tests:
  1. `_build_signal_chart_png` calls `market_data.fetch_candles` with its REAL signature
     (prov_sym, parse_interval(tf), api_key=..., outputsize=72) and reads `payload["values"]`.
  2. Pure-Python stdlib PNG fallback renders real 960x560 candlestick PNG charts even when
     Pillow is NOT installed on the host.
  3. Mini App `/api/candles` calls `market_data.fetch_candles` properly and returns OHLC bars.
"""
import io
import json
import pytest

from fysb import config, db
from fysb.engines import market_data
from fysb.handlers import user_pages
from fysb.ui import charts
from fysb.webapp import auth as wa_auth
from fysb.webapp import server as wa_server


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    p = tmp_path / "hotfix701.db"
    monkeypatch.setattr(config, "DB_FILE", p)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", "123456:TESTTOKEN_HOTFIX")
    monkeypatch.setattr(config, "ADMIN_ID", 990001)
    db.init_db()
    return tmp_path


def _fake_bars(n: int = 35):
    out = []
    px = 2650.0
    for i in range(n):
        o = px
        c = o + (1.2 if i % 2 == 0 else -0.6)
        out.append({
            "datetime": f"2026-11-24T{(i // 4):02d}:{(i % 4) * 15:02d}:00Z",
            "open": round(o, 2),
            "high": round(max(o, c) + 0.8, 2),
            "low": round(min(o, c) - 0.7, 2),
            "close": round(c, 2),
        })
        px = c
    return out


def test_build_signal_chart_png_uses_real_fetch_candles_signature(isolated_db, monkeypatch):
    seen = []

    def fake_fetch(symbol, interval, api_key="", outputsize=120, use_cache=True):
        seen.append((symbol, interval, outputsize))
        return {"values": _fake_bars(), "source": "TEST", "last_candle_iso": "2026-11-24T08:00:00Z"}

    monkeypatch.setattr(market_data, "fetch_candles", fake_fetch)

    sig = {
        "id": 11, "symbol": "XAUUSD", "timeframe": "H1", "direction": "BUY",
        "strategy": "ICT", "score": 88, "rr": 2.6,
        "entry": 2655.0, "stop": 2645.0, "tp1": 2667.0, "tp2": 2680.0, "tp3": 2695.0,
        "status": "RUNNING", "pips_result": 0.0,
    }
    png, is_proof = user_pages._build_signal_chart_png(sig, force_proof=False)
    assert is_proof is False
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert seen == [("XAU/USD", "1h", 72)]


def test_stdlib_png_fallback_when_pillow_unavailable(isolated_db, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(charts, "PIL_AVAILABLE", False)
    assert charts.charts_enabled() is True

    sig = {
        "id": 12, "symbol": "XAUUSD", "timeframe": "M15", "direction": "SELL",
        "strategy": "SMC", "score": 85, "rr": 2.4,
        "entry": 2675.0, "stop": 2685.0, "tp1": 2663.0, "tp2": 2650.0, "tp3": 2635.0,
        "status": "TP3_HIT", "pips_result": 400.0,
    }
    bars = _fake_bars(30)
    png_live = charts.render_signal_chart(sig, bars)
    assert png_live is not None and png_live[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(io.BytesIO(png_live))
    assert im.size == (960, 560)

    png_proof = charts.render_proof_chart(sig, bars)
    assert png_proof is not None and png_proof[:8] == b"\x89PNG\r\n\x1a\n"
    im2 = Image.open(io.BytesIO(png_proof))
    assert im2.size == (960, 560)


def test_webapp_api_candles_uses_real_fetch_candles_signature(isolated_db, monkeypatch):
    def fake_fetch(symbol, interval, api_key="", outputsize=120, use_cache=True):
        assert symbol == "XAU/USD"
        assert interval == "1h"
        assert outputsize == 80
        return {"values": _fake_bars(20), "source": "TEST"}

    monkeypatch.setattr(market_data, "fetch_candles", fake_fetch)
    hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": 990001}, "123456:TESTTOKEN_HOTFIX")}
    st, _, raw = wa_server.dispatch_request("GET", "/api/candles?symbol=XAUUSD&tf=H1", hdr)
    assert st == 200
    data = json.loads(raw)
    assert data["ok"] is True
    assert len(data["candles"]) == 20
