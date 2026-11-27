"""Phase 1 Security Hardening, Strategy Cleanup, Boot Persistence & /update Tests (v6.12.0)."""
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fysb import config, db
from fysb.handlers import background
from fysb.services import chain_verify, payments, secrets, strategy_stats


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point SQLite to a fresh temp DB for each test."""
    db_file = tmp_path / "phase1_test.db"
    monkeypatch.setattr(config, "DB_FILE", db_file)
    monkeypatch.setattr(config, "USE_PG", False)
    db._settings_cache.clear()
    db.init_db(snapshot_before_structural=False)
    yield db_file
    db._settings_cache.clear()


# =============================================================================
# 1. Strategy catalog cleanup (ONLY the 13 real implemented strategies)
# =============================================================================
def test_only_18_real_strategies_exist(isolated_db):
    with db.conn() as c:
        rows = c.execute("SELECT code, verified, enabled FROM strategy_catalog ORDER BY code").fetchall()
    codes = {r["code"] for r in rows}
    expected = set(db.IMPLEMENTED_STRATEGIES)
    assert len(expected) == 18
    assert codes == expected
    assert all(r["verified"] == 1 and r["enabled"] == 1 for r in rows)
    assert set(strategy_stats.BASELINE_ORDER) == expected
    for fake in ("ELLIOTT_WAVE", "VOLUME_PROFILE", "VWAP", "ATR_BREAKOUT", "SUPERTREND"):
        assert fake not in codes
        assert fake not in strategy_stats.BASELINE_ORDER


def test_init_db_purges_legacy_placeholder_strategies_and_user_prefs(isolated_db):
    with db.conn() as c:
        c.execute(
            "INSERT INTO strategy_catalog(code,title,description,enabled,verified,created_at,updated_at) "
            "VALUES('ELLIOTT_WAVE','Elliott','placeholder',0,0,?,?)",
            (config.now(), config.now()),
        )
        c.execute(
            "INSERT INTO users(user_id,full_name,strategy,joined_at) VALUES(99001,'Tester','ELLIOTT_WAVE',?)",
            (config.now(),),
        )
    logs = db.init_db(snapshot_before_structural=False)
    assert any("purged 1" in ln for ln in logs)
    with db.conn() as c:
        assert c.execute("SELECT 1 FROM strategy_catalog WHERE code='ELLIOTT_WAVE'").fetchone() is None
        u = c.execute("SELECT strategy FROM users WHERE user_id=99001").fetchone()
        assert u["strategy"] == ""


# =============================================================================
# 2. init_db() boot-override fix (admin toggles persist across restarts)
# =============================================================================
def test_init_db_preserves_admin_catalog_toggles(isolated_db):
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET enabled=1 WHERE symbol='EURUSD'")
        c.execute("UPDATE strategy_catalog SET enabled=0 WHERE code='ICT'")
        c.execute("UPDATE timeframe_catalog SET enabled=0 WHERE code='M5'")

    # Simulate bot restart
    db.init_db(snapshot_before_structural=False)

    with db.conn() as c:
        assert c.execute("SELECT enabled FROM market_catalog WHERE symbol='EURUSD'").fetchone()["enabled"] == 1
        assert c.execute("SELECT enabled FROM strategy_catalog WHERE code='ICT'").fetchone()["enabled"] == 0
        assert c.execute("SELECT enabled FROM timeframe_catalog WHERE code='M5'").fetchone()["enabled"] == 0


# =============================================================================
# 3. On-chain USDT verification exploits blocked
# =============================================================================
def test_bep20_decimals_18_blocks_penny_exploit():
    """$0.0001 on BEP20 (1e14 raw wei) MUST be rejected; $19 (19e18 wei) passes."""
    assert chain_verify.EVM_CHAINS["BEP20"]["decimals"] == 18
    wallet = "0x1111111111111111111111111111111111111111"
    txid_0x = "0x" + "ab" * 32
    assert chain_verify.valid_txid(txid_0x, "BEP20") is True
    assert chain_verify.valid_txid(txid_0x, "TRC20") is False

    def make_rpc(raw_amount: int, latest_block: int = 100, tx_block: int = 90, block_ts: int = 2_000_000_000):
        def fake_rpc(rpc, method, params, timeout):
            if method == "eth_getTransactionReceipt":
                assert params[0] == txid_0x.lower()
                return {
                    "status": "0x1",
                    "blockNumber": hex(tx_block),
                    "logs": [{
                        "address": chain_verify.EVM_CHAINS["BEP20"]["contract"],
                        "topics": [
                            chain_verify.TRANSFER_TOPIC,
                            "0x" + "0" * 64,
                            chain_verify._wallet_topic(wallet),
                        ],
                        "data": hex(raw_amount),
                    }],
                }
            if method == "eth_blockNumber":
                return hex(latest_block)
            if method == "eth_getBlockByNumber":
                return {"timestamp": hex(block_ts)}
            raise AssertionError(method)
        return fake_rpc

    # Exploit: $0.0001 in 18-decimal BEP20 USDT (previously decoded as 100,000,000 USDT!)
    with patch.object(chain_verify, "_rpc_call", side_effect=make_rpc(10**14)):
        res = chain_verify.verify_evm_usdt(txid_0x, wallet, 19.0, "BEP20")
    assert res["ok"] is False
    assert "أقل من المطلوب" in res["reason"]

    # Insufficient confirmations (2/6) -> rejected
    with patch.object(chain_verify, "_rpc_call", side_effect=make_rpc(19 * 10**18, latest_block=91, tx_block=90)):
        res = chain_verify.verify_evm_usdt(txid_0x, wallet, 19.0, "BEP20")
    assert res["ok"] is False
    assert "تأكيداتها" in res["reason"]

    # Historical replay (block_ts < min_timestamp) -> rejected
    with patch.object(chain_verify, "_rpc_call", side_effect=make_rpc(19 * 10**18, block_ts=1_700_000_000)):
        res = chain_verify.verify_evm_usdt(txid_0x, wallet, 19.0, "BEP20", min_timestamp=1_800_000_000)
    assert res["ok"] is False
    assert "قديمة" in res["reason"]

    # Genuine $19 BEP20 USDT with 11 confirmations & fresh timestamp -> PASSES
    with patch.object(chain_verify, "_rpc_call", side_effect=make_rpc(19 * 10**18, block_ts=1_900_000_000)):
        res = chain_verify.verify_evm_usdt(txid_0x, wallet, 19.0, "BEP20", min_timestamp=1_800_000_000)
    assert res["ok"] is True
    assert abs(res["amount"] - 19.0) < 1e-6


def test_trc20_blocks_sub_dollar_exploit_and_contract_spoofing_and_old_tx():
    import httpx as _httpx
    wallet = "TVALIDRECEIVERWALLET12345678901234"
    txid = "cd" * 32

    def mock_get(payload):
        return patch.object(
            _httpx, "get",
            return_value=type("R", (), {
                "status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: payload,
            })(),
        )

    # 1. $0.01 (10_000 raw units) MUST NOT be misread as $10,000 USDT!
    penny_payload = {
        "data": [{
            "event_name": "Transfer",
            "block_timestamp": 1_900_000_000_000,
            "contract_address": chain_verify.USDT_TRC20_CONTRACT,
            "result": {"from": "TFROM000000000000000000000000000", "to": wallet, "value": "10000"},
        }],
        "success": True,
    }
    with mock_get(penny_payload):
        res = chain_verify.verify_trc20_usdt(txid, wallet, 19.0)
    assert res["ok"] is False
    assert "0.01 USDT" in res["reason"]

    # 2. Mixed-contract spoofing: real USDT event to OTHER wallet + FAKE token $99 to OUR wallet
    spoof_payload = {
        "data": [
            {
                "event_name": "Transfer",
                "block_timestamp": 1_900_000_000_000,
                "contract_address": chain_verify.USDT_TRC20_CONTRACT,
                "result": {"from": "TFROM", "to": "TOTHERWALLET1234567890123456789012", "value": "1"},
            },
            {
                "event_name": "Transfer",
                "block_timestamp": 1_900_000_000_000,
                "contract_address": "TFAKECONTRACT123456789012345678901",
                "result": {"from": "TFROM", "to": wallet, "value": "99000000"},
            },
        ],
        "success": True,
    }
    with mock_get(spoof_payload):
        res = chain_verify.verify_trc20_usdt(txid, wallet, 19.0)
    assert res["ok"] is False

    # 3. Old-TX replay blocked via min_timestamp
    old_payload = {
        "data": [{
            "event_name": "Transfer",
            "block_timestamp": 1_700_000_000_000,
            "contract_address": chain_verify.USDT_TRC20_CONTRACT,
            "result": {"from": "TFROM", "to": wallet, "value": "20000000"},
        }],
        "success": True,
    }
    with mock_get(old_payload):
        res = chain_verify.verify_trc20_usdt(txid, wallet, 19.0, min_timestamp=1_800_000_000.0)
    assert res["ok"] is False
    assert "قديمة" in res["reason"]


def test_txid_canonical_case_and_0x_uniqueness(isolated_db):
    p1 = payments.create_request(101, "PRO", 19.0, "USDT")
    p2 = payments.create_request(102, "PRO", 19.0, "USDT")
    raw_tx = "0x" + "AbCdEf01" * 8
    r1 = payments.attach_txid(p1, 101, raw_tx, "BEP20")
    assert r1["ok"] is True

    # Same TXID in lowercase without 0x -> blocked!
    assert payments.txid_used(raw_tx[2:].lower()) == p1
    assert payments.txid_used(raw_tx.upper()) == p1
    r2 = payments.attach_txid(p2, 102, raw_tx[2:].upper(), "BEP20")
    assert r2["ok"] is False


# =============================================================================
# 4. Broker secrets encryption at rest (ENC1) + automatic migration
# =============================================================================
def test_secrets_encrypt_decrypt_and_tamper_detection(monkeypatch):
    monkeypatch.setenv("BROKER_SECRET_KEY", "test-master-secret-key-32-chars-minimum!")
    payload = {"api_key": "K_LIVE_999", "api_secret": "S_LIVE_888", "account_id": "ACC-7"}
    enc1 = secrets.encrypt_json(payload)
    enc2 = secrets.encrypt_json(payload)
    assert secrets.is_encrypted(enc1)
    assert enc1 != enc2  # random salt & nonce
    assert "K_LIVE_999" not in enc1
    assert secrets.decrypt_json(enc1) == payload

    # Flip one char in ciphertext -> HMAC verification MUST fail!
    tampered = enc1[:-1] + ("A" if enc1[-1] != "A" else "B")
    with pytest.raises(ValueError):
        secrets.decrypt_json(tampered)


def test_init_db_auto_encrypts_legacy_plaintext_brokers(isolated_db, monkeypatch):
    monkeypatch.setenv("BROKER_SECRET_KEY", "test-migration-secret-key-at-least-16")
    legacy = json.dumps({"api_key": "OLD_PLAIN_KEY", "api_secret": "OLD_PLAIN_SECRET"})
    with db.conn() as c:
        c.execute(
            "INSERT INTO broker_adapters(key,title,status,enabled,config_json,created_at,updated_at) "
            "VALUES('BINANCE','Legacy','CONFIGURED',0,?,?,?)",
            (legacy, config.now(), config.now()),
        )
    db.init_db(snapshot_before_structural=False)
    with db.conn() as c:
        stored = c.execute("SELECT config_json FROM broker_adapters WHERE title='Legacy'").fetchone()["config_json"]
    assert secrets.is_encrypted(stored)
    assert "OLD_PLAIN_KEY" not in stored
    assert secrets.decrypt_json(stored)["api_key"] == "OLD_PLAIN_KEY"


# =============================================================================
# 5. /update lock release (Linux os.execve safety) + SHA-256 verification
# =============================================================================
def test_update_restart_releases_lock_and_execve_reacquire_succeeds(tmp_path, monkeypatch):
    import updater
    lf = tmp_path / "fysb.lock"
    monkeypatch.setattr(background, "LOCK_FILE", lf)
    background._held_lock_paths.clear()

    lock = background.SingleInstanceLock()
    assert lock.acquire() is True
    assert lf.exists()

    # _prepare_for_restart() must remove our lock before os.execve
    updater._prepare_for_restart()
    assert not lf.exists()

    # Even when upgrading from a pre-v6.12.0 process (which left fysb.lock
    # behind with our PID + current /proc start_tick), a freshly exec'd process
    # (empty _held_lock_paths) MUST take over cleanly instead of deadlocking!
    tick = background._proc_start_tick(os.getpid())
    lf.write_text(f"{os.getpid()}:{tick}")
    background._held_lock_paths.clear()
    new_lock = background.SingleInstanceLock()
    assert new_lock.acquire() is True
    new_lock.release()


def test_updater_verifies_sha256_and_blocks_tampered_zip(tmp_path, monkeypatch):
    import updater

    # Build a valid minimal update zip
    src_zip = tmp_path / "good.zip"
    with zipfile.ZipFile(src_zip, "w") as z:
        z.writestr("main.py", "print('ok')\n")
        z.writestr("fysb/config.py", "VERSION = '9.9.9'\n")
    good_sha = updater.sha256_file(src_zip)
    bad_sha = "0" * 64

    dest = tmp_path / "install_dir"
    dest.mkdir()

    def fake_download(url, target, timeout=180):
        Path(target).write_bytes(src_zip.read_bytes())
        return Path(target)

    # 1. Wrong SHA-256 -> MUST abort without extracting anything!
    monkeypatch.setattr(updater, "latest_release", lambda: {
        "tag": "v9.9.9", "url": "https://example.invalid/pkg.zip",
        "name": "ForexYemeni1.zip", "sha256": bad_sha,
    })
    monkeypatch.setattr(updater, "_download", fake_download)
    res_bad = updater.self_update(dest_root=dest)
    assert res_bad["status"] == "error"
    assert "SHA-256" in res_bad["detail"]
    assert not (dest / "main.py").exists()

    # 2. Matching SHA-256 -> installs cleanly!
    monkeypatch.setattr(updater, "latest_release", lambda: {
        "tag": "v9.9.9", "url": "https://example.invalid/pkg.zip",
        "name": "ForexYemeni1.zip", "sha256": good_sha,
    })
    res_ok = updater.self_update(dest_root=dest)
    assert res_ok["status"] == "updated"
    assert res_ok.get("sha256_verified") is True
    assert (dest / "main.py").exists()
