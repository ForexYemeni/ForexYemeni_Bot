"""Authenticated encryption for stored secrets (broker API keys/secrets).

Pure Python standard-library implementation (zero native C/Rust dependencies,
runs identically on Linux, Termux, Windows, and hosted bot panels):
  * Master secret comes from BROKER_SECRET_KEY in .env (recommended). When not
    configured yet, falls back to an HKDF-style domain-separated derivation
    from BOT_TOKEN + ADMIN_ID so secrets are STILL encrypted at rest rather
    than stored in plaintext.
  * Per-record random 16-byte salt + 16-byte nonce.
  * PBKDF2-HMAC-SHA256 (100,000 rounds) derives independent 32-byte encryption
    and 32-byte MAC keys.
  * SHA-256 CTR-mode keystream + Encrypt-then-MAC (HMAC-SHA256) verified in
    constant time (hmac.compare_digest) before decryption.
  * Wire format: "ENC1:" + urlsafe_b64(salt[16] || nonce[16] || mac[32] || ct).

Legacy migration:
  * decrypt_json() transparently reads pre-v6.12.0 plaintext JSON dicts so
    existing rows never break on upgrade; db.init_db() re-encrypts them in
    place on boot.
"""
import base64
import hashlib
import hmac
import json
import os

from fysb import config

_PREFIX = "ENC1:"
_VERSION = b"FYSB-ENC-V1"
_SALT_LEN = 16
_NONCE_LEN = 16
_MAC_LEN = 32
_PBKDF2_ITERS = 100_000


def has_dedicated_key() -> bool:
    """True when BROKER_SECRET_KEY is explicitly configured in the environment."""
    return len((os.getenv("BROKER_SECRET_KEY") or "").strip()) >= 16


def _master_material() -> bytes:
    """Resolve the master key material (never logged, never stored in DB)."""
    dedicated = (os.getenv("BROKER_SECRET_KEY") or "").strip()
    if dedicated:
        return ("dedicated:" + dedicated).encode("utf-8")
    # Safe domain-separated fallback so secrets are never written in plaintext
    # even before the admin sets BROKER_SECRET_KEY in .env.
    tok = (config.BOT_TOKEN or "fysb-fallback-token").strip()
    aid = str(config.ADMIN_ID or 0)
    return hashlib.sha256(f"fysb-broker-secret-v1|{aid}|{tok}".encode("utf-8")).digest()


def _derive_keys(salt: bytes) -> tuple[bytes, bytes]:
    dk = hashlib.pbkdf2_hmac("sha256", _master_material(), salt, _PBKDF2_ITERS, dklen=64)
    return dk[:32], dk[32:]


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            enc_key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_text(plaintext: str) -> str:
    """Encrypt UTF-8 text into an authenticated ENC1 envelope."""
    pt = (plaintext or "").encode("utf-8")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    enc_key, mac_key = _derive_keys(salt)
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    mac = hmac.new(mac_key, _VERSION + salt + nonce + ct, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(salt + nonce + mac + ct).decode("ascii")
    return _PREFIX + blob


def decrypt_text(token: str) -> str:
    """Verify MAC and decrypt an ENC1 envelope. Raises ValueError on tamper."""
    if not is_encrypted(token):
        raise ValueError("not an ENC1 encrypted envelope")
    raw = base64.urlsafe_b64decode(token[len(_PREFIX):].encode("ascii"))
    min_len = _SALT_LEN + _NONCE_LEN + _MAC_LEN
    if len(raw) < min_len:
        raise ValueError("corrupted encrypted envelope")
    salt = raw[:_SALT_LEN]
    nonce = raw[_SALT_LEN:_SALT_LEN + _NONCE_LEN]
    mac = raw[_SALT_LEN + _NONCE_LEN:min_len]
    ct = raw[min_len:]
    enc_key, mac_key = _derive_keys(salt)
    expected = hmac.new(mac_key, _VERSION + salt + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("encrypted envelope authentication failed (wrong key or tampered)")
    ks = _keystream(enc_key, nonce, len(ct))
    pt = bytes(a ^ b for a, b in zip(ct, ks))
    return pt.decode("utf-8")


def encrypt_json(obj: dict) -> str:
    """Serialize dict and encrypt into an authenticated ENC1 envelope."""
    return encrypt_text(json.dumps(obj or {}, ensure_ascii=False, separators=(",", ":")))


def decrypt_json(blob: str) -> dict:
    """Decrypt ENC1 JSON envelope (or read legacy plaintext JSON cleanly)."""
    if not blob:
        return {}
    if is_encrypted(blob):
        return json.loads(decrypt_text(blob))
    # Backward-compatible read for pre-v6.12.0 plaintext rows
    data = json.loads(blob)
    return data if isinstance(data, dict) else {}
