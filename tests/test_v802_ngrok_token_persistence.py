"""v8.0.2 Regression Suite — Persistent Ngrok/Tunnel Tokens Across Updates & Clean Restart.

Verifies:
  1. Saving Ngrok token + static domain persists to ALL 3 layers (DB + tunnel_tokens.json + .env).
  2. Tokens auto-recover & self-heal DB after a full DB reset or process restart.
  3. Clearing tokens wipes all 3 layers cleanly.
  4. `updater.safe_extract()` NEVER overwrites `.env`, `tunnel_tokens.json`, `.tunnel.pid`, or `database/`.
  5. `updater._prepare_for_restart()` invokes `tunnel.terminate_tunnel_sync()` (fixing the
     broken `_STATE.get('proc')` bug that left orphan ngrok agents causing ERR_NGROK_108).
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

import updater
from fysb import config, db
from fysb.webapp import tunnel as tun


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    dbf = tmp_path / "v802.db"
    env_f = tmp_path / ".env"
    env_f.write_text("BOT_TOKEN=123:ABC\nADMIN_ID=1\n", encoding="utf-8")
    monkeypatch.setattr(config, "DB_FILE", dbf)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "USE_PG", False)
    monkeypatch.setattr(config, "_DOTENV_FILE", env_f)
    monkeypatch.setenv("DOTENV_PATH", str(env_f))
    for k in ("NGROK_AUTHTOKEN", "NGROK_DOMAIN", "CLOUDFLARE_TUNNEL_TOKEN", "CF_TUNNEL_TOKEN", "PINGGY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    db.init_db()
    return tmp_path, env_f


def test_01_save_ngrok_token_persists_to_db_json_and_dotenv_and_survives_db_reset(isolated_env, monkeypatch):
    tmp_path, env_f = isolated_env
    tok = "2xTestNgrokAuthtokenVeryLong1234567890"
    dom = "royal-bot.ngrok-free.dev"

    tun.save_tunnel_token("ngrok_authtoken", tok)
    tun.save_tunnel_token("ngrok_domain", dom)

    # 1) Stored in DB
    assert db.get_setting("ngrok_authtoken", "") == tok
    assert db.get_setting("ngrok_domain", "") == dom

    # 2) Stored in .env (preserved across /update!)
    env_txt = env_f.read_text(encoding="utf-8")
    assert f"NGROK_AUTHTOKEN={tok}" in env_txt
    assert f"NGROK_DOMAIN={dom}" in env_txt
    assert "BOT_TOKEN=123:ABC" in env_txt  # untouched!

    # 3) Stored in tunnel_tokens.json
    tok_json = tmp_path / "tunnel_tokens.json"
    assert tok_json.is_file() and tok in tok_json.read_text(encoding="utf-8")

    # 4) Simulate full DB wipe + fresh restart without env vars -> recovers from tunnel_tokens.json & heals DB!
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    monkeypatch.delenv("NGROK_DOMAIN", raising=False)
    db.set_setting("ngrok_authtoken", "")
    db.set_setting("ngrok_domain", "")
    assert tun.get_ngrok_token() == tok
    assert tun.get_ngrok_domain() == dom
    # Self-healed DB!
    assert db.get_setting("ngrok_authtoken", "") == tok
    assert db.get_setting("ngrok_domain", "") == dom


def test_02_clear_tunnel_tokens_wipes_all_three_layers(isolated_env):
    tmp_path, env_f = isolated_env
    tun.save_tunnel_token("ngrok_authtoken", "2xTempTokenToClear1234567890")
    tun.save_tunnel_token("ngrok_domain", "temp.ngrok-free.dev")
    assert tun.get_ngrok_token()

    tun.clear_tunnel_tokens()
    assert tun.get_ngrok_token() == ""
    assert tun.get_ngrok_domain() == ""
    assert db.get_setting("ngrok_authtoken", "") == ""
    assert "NGROK_AUTHTOKEN" not in env_f.read_text(encoding="utf-8")
    assert "NGROK_AUTHTOKEN" not in os.environ


def test_03_updater_preserves_tunnel_tokens_and_cleanly_terminates_tunnel(tmp_path):
    assert "tunnel_tokens.json" in updater.PRESERVE_EXACT
    assert ".tunnel.pid" in updater.PRESERVE_EXACT
    assert ".env" in updater.PRESERVE_EXACT
    assert "database" in updater.PRESERVE_DIRS

    # Build a fake update zip trying to overwrite .env, tunnel_tokens.json, and database/forexyemeni.db
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / ".env").write_text("NGROK_AUTHTOKEN=KEEP_ME\n", encoding="utf-8")
    (dest / "tunnel_tokens.json").write_text('{"ngrok_authtoken":"KEEP_ME"}', encoding="utf-8")
    (dest / "database").mkdir()
    (dest / "database" / "forexyemeni.db").write_bytes(b"SQLITE_KEEP")

    zpath = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("main.py", "# updated")
        z.writestr("fysb/config.py", "VERSION='8.0.2'")
        z.writestr(".env", "WIPED=1")
        z.writestr("tunnel_tokens.json", "{}")
        z.writestr("database/forexyemeni.db", "WIPED")

    updater.safe_extract(zpath, dest)
    assert (dest / ".env").read_text(encoding="utf-8") == "NGROK_AUTHTOKEN=KEEP_ME\n"
    assert "KEEP_ME" in (dest / "tunnel_tokens.json").read_text(encoding="utf-8")
    assert (dest / "database" / "forexyemeni.db").read_bytes() == b"SQLITE_KEEP"

    # Verify _prepare_for_restart calls terminate_tunnel_sync() instead of broken _STATE.get('proc')
    called = []
    with patch.object(tun, "terminate_tunnel_sync", side_effect=lambda wait_sec=2.5: called.append(wait_sec)):
        updater._prepare_for_restart()
    assert len(called) == 1


def test_04_main_py_ignores_edited_messages_and_version_802():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "~filters.UpdateType.EDITED_MESSAGE" in src
    assert config.VERSION == "8.0.2"
