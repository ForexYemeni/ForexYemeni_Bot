"""v6.28.0 Phase 5 tests (#31): 100% Free Crypto Exchange API Integration
(Binance, Bybit v5, OKX v5) — ENC1 encryption at rest, Testnet-first,
No-Withdraw enforcement, Admin master toggle, Kill Switch & order execution."""
import pytest

from fysb import config, db
from fysb.services import crypto_exchange as cex


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    p = tmp_path / "phase5.db"
    monkeypatch.setattr(config, "DB_FILE", p)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    db.init_db()
    return tmp_path


def test_version_schema_and_encrypted_storage(isolated_db):
    assert tuple(int(x) for x in config.VERSION.split(".")) >= (6, 28, 0)
    assert cex.is_enabled() is True
    uid = 750001
    secret_val = "SUPER_SECRET_KEY_99887766"
    saved = cex.save_exchange_key(uid, "BINANCE", "BINANCE_KEY_12345678", secret_val, max_trade_usd=150.0)
    assert saved["testnet"] is True  # Testnet-first default
    assert saved["masked_key"] == "BINA••••5678"

    # Verify ENC1 ciphertext in DB — plaintext secret MUST NOT exist anywhere in DB!
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM user_exchange_keys WHERE user_id=? AND exchange='BINANCE'", (uid,)).fetchone())
    assert row["creds_enc"].startswith("ENC1:")
    assert secret_val not in row["creds_enc"]
    assert "BINANCE_KEY_12345678" not in row["creds_enc"]

    # list_user_keys never leaks creds_enc
    listed = cex.list_user_keys(uid)
    assert len(listed) == 1
    assert "creds_enc" not in listed[0]
    assert listed[0]["masked_key"] == "BINA••••5678"

    # OKX requires passphrase
    with pytest.raises(ValueError):
        cex.save_exchange_key(uid, "OKX", "OKX_KEY_123456", "OKX_SECRET_123456", passphrase="")
    okx = cex.save_exchange_key(uid, "OKX", "OKX_KEY_123456", "OKX_SECRET_123456", passphrase="my-okx-pass")
    assert okx["exchange"] == "OKX" and okx["testnet"] is True


def test_symbol_mapping_and_hmac_signatures(isolated_db):
    assert cex.is_crypto_symbol("BTCUSD") is True
    assert cex.is_crypto_symbol("XAUUSD") is False
    assert cex.exchange_symbol("BINANCE", "BTCUSD") == "BTCUSDT"
    assert cex.exchange_symbol("BYBIT", "ETHUSD") == "ETHUSDT"
    assert cex.exchange_symbol("OKX", "SOLUSD") == "SOL-USDT"
    assert cex.exchange_symbol("BINANCE", "XAUUSD") is None

    # Deterministic official signing helpers
    assert len(cex.sign_binance("timestamp=1700000000000", "sec")) == 64
    assert len(cex.sign_bybit("1700000000000", "key", "5000", "", "sec")) == 64
    assert len(cex.sign_okx("2026-11-24T00:00:00.000Z", "GET", "/api/v5/account/balance", "", "sec")) > 20


def test_no_withdraw_policy_enforcement(isolated_db, monkeypatch):
    uid = 750002
    cex.save_exchange_key(uid, "BYBIT", "BYBIT_KEY_123456", "BYBIT_SEC_123456", testnet=True)

    # Mock Bybit query-api returning Withdraw permission -> MUST BE BLOCKED!
    def fake_http_withdraw(url, **kwargs):
        if "query-api" in url:
            return {"retCode": 0, "result": {"permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}}}
        return {}

    monkeypatch.setattr(cex, "_http_json", fake_http_withdraw)
    res = cex.verify_connection(uid, "BYBIT")
    assert res["ok"] is False
    assert res.get("withdraw_violation") is True

    # Execution with blocked key MUST be refused even without HTTP call
    sig = {"id": 10, "symbol": "BTCUSD", "direction": "BUY", "entry": 90000.0, "stop": 88000.0, "tp1": 93000.0}
    ex_res = cex.execute_crypto_signal(uid, "BYBIT", sig)
    assert ex_res["ok"] is False
    assert "السحب" in ex_res["error"]


def test_verify_and_execute_all_three_exchanges_and_guards(isolated_db, monkeypatch):
    uid = 750003
    cex.save_exchange_key(uid, "BINANCE", "BIN_KEY_12345678", "BIN_SEC_12345678", testnet=True, max_trade_usd=50.0)
    cex.save_exchange_key(uid, "BYBIT", "BYB_KEY_12345678", "BYB_SEC_12345678", testnet=True, max_trade_usd=75.0)
    cex.save_exchange_key(uid, "OKX", "OKX_KEY_12345678", "OKX_SEC_12345678", passphrase="pass", testnet=True, max_trade_usd=60.0)

    def fake_http(url, method="GET", headers=None, body=None, timeout=10.0):
        if "binance.vision/api/v3/account" in url:
            return {"balances": [{"asset": "USDT", "free": "1250.50"}]}
        if "binance.vision/api/v3/order" in url:
            return {"orderId": 99881, "status": "FILLED"}
        if "bybit.com/v5/user/query-api" in url:
            return {"retCode": 0, "result": {"permissions": {"Spot": ["SpotTrade"], "Wallet": []}}}
        if "bybit.com/v5/account/wallet-balance" in url:
            return {"retCode": 0, "result": {"list": [{"totalAvailableBalance": "880.25"}]}}
        if "bybit.com/v5/order/create" in url:
            return {"retCode": 0, "result": {"orderId": "BYB-551"}}
        if "okx.com/api/v5/account/balance" in url:
            assert headers.get("x-simulated-trading") == "1"  # Testnet demo header verified!
            return {"code": "0", "data": [{"details": [{"availBal": "420.00"}]}]}
        if "okx.com/api/v5/trade/order" in url:
            assert headers.get("x-simulated-trading") == "1"
            return {"code": "0", "data": [{"ordId": "OKX-771"}]}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(cex, "_http_json", fake_http)

    # Verify all 3 exchanges
    v_bin = cex.verify_connection(uid, "BINANCE")
    assert v_bin["ok"] is True and v_bin["usdt_balance"] == 1250.5
    v_byb = cex.verify_connection(uid, "BYBIT")
    assert v_byb["ok"] is True and v_byb["usdt_balance"] == 880.25
    v_okx = cex.verify_connection(uid, "OKX")
    assert v_okx["ok"] is True and v_okx["usdt_balance"] == 420.0

    # Execute BTCUSD signal across all 3 exchanges (respecting per-exchange max_trade_usd cap!)
    btc_sig = {"id": 42, "symbol": "BTCUSD", "direction": "BUY", "entry": 90000.0, "stop": 88500.0, "tp1": 92500.0}
    e_bin = cex.execute_crypto_signal(uid, "BINANCE", btc_sig, notional_usd=500.0)
    assert e_bin["ok"] is True and e_bin["trade_usd"] == 50.0 and e_bin["order_id"] == "99881"

    e_byb = cex.execute_crypto_signal(uid, "BYBIT", btc_sig)
    assert e_byb["ok"] is True and e_byb["trade_usd"] == 75.0 and e_byb["order_id"] == "BYB-551"

    e_okx = cex.execute_crypto_signal(uid, "OKX", btc_sig)
    assert e_okx["ok"] is True and e_okx["symbol"] == "BTC-USDT" and e_okx["order_id"] == "OKX-771"

    # Non-crypto signal (Gold) cleanly refused
    gold_sig = {"id": 43, "symbol": "XAUUSD", "direction": "BUY", "entry": 2650.0, "stop": 2640.0, "tp1": 2670.0}
    e_gold = cex.execute_crypto_signal(uid, "BINANCE", gold_sig)
    assert e_gold["ok"] is False

    # Kill Switch blocks all execution immediately
    db.set_setting("global_kill_switch", "1")
    e_ks = cex.execute_crypto_signal(uid, "BINANCE", btc_sig)
    assert e_ks["ok"] is False
    db.set_setting("global_kill_switch", "0")

    # Admin master toggle disables Crypto API completely
    cex.set_enabled(False)
    assert cex.is_enabled() is False
    assert cex.execute_crypto_signal(uid, "BINANCE", btc_sig)["ok"] is False
    cex.set_enabled(True)

    # Order audit history & admin overview
    orders = cex.recent_orders(uid, 10)
    assert len(orders) == 3
    ov = cex.admin_overview()
    assert ov["total_keys"] == 3 and ov["orders_ok"] == 3
    assert "Binance" in cex.render_user_exchange_card(uid)
    assert "ENC1" in cex.render_admin_crypto_api_card()
