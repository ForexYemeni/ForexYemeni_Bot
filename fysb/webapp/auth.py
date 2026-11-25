"""Official Telegram WebApp `initData` HMAC-SHA256 verification (v7.0.0).

Implements the exact Telegram Bot API specification:
  secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token.encode())
  check_hash = HMAC_SHA256(key=secret_key, msg=sorted_data_check_string.encode()).hexdigest()

Security guarantees:
  * Constant-time hash comparison (`hmac.compare_digest`).
  * Replay protection via `auth_date` freshness window (default 24h).
  * Strict server-side Admin check (`user_id == config.ADMIN_ID`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

from fysb import config


def build_init_data(user_dict: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Helper to sign a valid Telegram `initData` string (useful for tests & diagnostics)."""
    ts = int(auth_date if auth_date is not None else time.time())
    pairs = {
        "auth_date": str(ts),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":"), ensure_ascii=False),
    }
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    h = hmac.new(secret, check_str.encode("utf-8"), hashlib.sha256).hexdigest()
    pairs["hash"] = h
    return urllib.parse.urlencode(pairs)


def verify_init_data(
    init_data: str,
    bot_token: str | None = None,
    *,
    max_age_seconds: int = 86400,
    now_ts: int | None = None,
) -> dict | None:
    """Verify Telegram WebApp `initData` and return authenticated principal dict, or None."""
    raw = (init_data or "").strip()
    tok = (bot_token if bot_token is not None else config.BOT_TOKEN) or ""
    if not raw or not tok:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        return None

    # Freshness check (anti-replay)
    try:
        auth_date = int(parsed.get("auth_date") or "0")
    except ValueError:
        return None
    now_i = int(now_ts if now_ts is not None else time.time())
    if auth_date <= 0 or (now_i - auth_date) > max_age_seconds or (auth_date - now_i) > 300:
        return None

    check_str = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", tok.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check_str.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    user_raw = parsed.get("user") or ""
    if not user_raw:
        return None
    try:
        user_obj = json.loads(user_raw)
        uid = int(user_obj["id"])
    except Exception:
        return None

    return {
        "user_id": uid,
        "user": user_obj,
        "auth_date": auth_date,
        "is_admin": bool(config.ADMIN_ID and uid == int(config.ADMIN_ID)),
    }
