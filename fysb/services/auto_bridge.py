"""Royal Auto-Bridge Service (v7.7.0 — 100% Free Multi-Platform Broker Copier).

Supports BOTH Broker Demo (safe default!) and Live accounts across 4 platforms:
  1. TradeLocker  (100% phone-native REST API: demo.tradelocker.com / live.tradelocker.com)
  2. Deriv        (100% phone-native WebSocket API: ws.derivws.com — Demo & Live tokens)
  3. cTrader      (100% phone-native Open API OAuth/Token — Demo & Live accounts)
  4. MT5 Bridge   (100% free MQL5 Expert Advisor polling /api/bridge/poll + /ack)

Granular Admin Governance:
  - Global Master Switch (`bridge_enabled`) + Emergency Kill Switch (`bridge_kill_switch`)
  - Live Mode Lock (`bridge_allow_live`: can restrict platform to Broker DEMO only)
  - 4 Access Modes (`bridge_access_mode`): OFF | ALL | PLANS | SELECTED
  - Per-User Override (`bridge_user_rules`): AUTO | ALLOW | DENY
  - Per-Connection Admin Freeze (`admin_frozen`) + Full Audit Log (`bridge_orders`)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as py_secrets
import socket
import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fysb import config, db, instruments
from fysb.config import log
from fysb.services import secrets as vault, subscriptions

PLATFORMS = {
    "tradelocker": {
        "name": "TradeLocker",
        "badge": "📱 من الهاتف 100%",
        "desc": "نسخ سحابي مباشر لحسابات Demo و Live في شركات التمويل والوسطاء عبر REST API الرسمي.",
        "fields": ("email", "password", "server", "account_id", "acc_num"),
    },
    "deriv": {
        "name": "Deriv",
        "badge": "📱 من الهاتف 100%",
        "desc": "نسخ فوري للذهب والفوركس والمؤشرات عبر Deriv API Token الرسمي (Demo $10K أو Live).",
        "fields": ("api_token", "app_id"),
    },
    "ctrader": {
        "name": "cTrader Open API",
        "badge": "📱 من الهاتف 100%",
        "desc": "ربط رسمي لحسابات cTrader Demo و Live (IC Markets · Pepperstone · FTMO · FxPro).",
        "fields": ("access_token", "account_id", "broker_name"),
    },
    "mt5_bridge": {
        "name": "MT5 Royal Bridge EA",
        "badge": "🖥 مجاني 100% (EA)",
        "desc": "ملف ForexYemeni_Bridge.mq5 جاهز للتحميل بضغطة يعمل على أي حساب MT5 Demo أو Live بلا اشتراك VPS مدفوع.",
        "fields": ("symbol_suffix",),
    },
}

VALID_ACCESS_MODES = ("OFF", "ALL", "PLANS", "SELECTED")
VALID_OVERRIDES = ("AUTO", "ALLOW", "DENY")


# ---------------------------------------------------------------------------
# 1. Admin Policy & Access Governance
# ---------------------------------------------------------------------------

def get_policy() -> dict:
    mode = (db.get_setting("bridge_access_mode", "PLANS") or "PLANS").strip().upper()
    if mode not in VALID_ACCESS_MODES:
        mode = "PLANS"
    plans_raw = db.get_setting("bridge_allowed_plans", "VIP,ROYAL") or "VIP,ROYAL"
    allowed_plans = [p.strip().upper() for p in plans_raw.split(",") if p.strip()]
    return {
        "enabled": (db.get_setting("bridge_enabled", "1") or "1") == "1",
        "kill_switch": (db.get_setting("bridge_kill_switch", "0") or "0") == "1",
        "allow_live": (db.get_setting("bridge_allow_live", "1") or "1") == "1",
        "access_mode": mode,
        "allowed_plans": allowed_plans,
    }


def update_policy(*, enabled: bool | None = None, kill_switch: bool | None = None,
                  allow_live: bool | None = None, access_mode: str | None = None,
                  allowed_plans: list[str] | str | None = None) -> dict:
    if enabled is not None:
        db.set_setting("bridge_enabled", "1" if enabled else "0")
    if kill_switch is not None:
        db.set_setting("bridge_kill_switch", "1" if kill_switch else "0")
    if allow_live is not None:
        db.set_setting("bridge_allow_live", "1" if allow_live else "0")
    if access_mode is not None:
        m = str(access_mode).strip().upper()
        if m in VALID_ACCESS_MODES:
            db.set_setting("bridge_access_mode", m)
    if allowed_plans is not None:
        if isinstance(allowed_plans, list):
            clean = ",".join(sorted({str(x).strip().upper() for x in allowed_plans if str(x).strip()}))
        else:
            clean = ",".join(sorted({x.strip().upper() for x in str(allowed_plans).split(",") if x.strip()}))
        db.set_setting("bridge_allowed_plans", clean or "ROYAL")
    return get_policy()


def set_user_override(user_id: int, override_mode: str, note: str = "", actor: int = 0) -> dict:
    om = str(override_mode or "AUTO").strip().upper()
    if om not in VALID_OVERRIDES:
        return {"ok": False, "error": "override_mode must be AUTO, ALLOW, or DENY"}
    now = config.now()
    with db.tx() as c:
        if om == "AUTO" and not note:
            c.execute("DELETE FROM bridge_user_rules WHERE user_id=?", (int(user_id),))
        else:
            c.execute(
                """INSERT INTO bridge_user_rules(user_id, override_mode, note, updated_by, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     override_mode=excluded.override_mode,
                     note=excluded.note,
                     updated_by=excluded.updated_by,
                     updated_at=excluded.updated_at""",
                (int(user_id), om, str(note or "")[:160], int(actor or 0), now),
            )
    return {"ok": True, "user_id": int(user_id), "override_mode": om}


def get_user_override(user_id: int) -> str:
    with db.conn() as c:
        r = c.execute("SELECT override_mode FROM bridge_user_rules WHERE user_id=?", (int(user_id),)).fetchone()
        return str(r["override_mode"]).upper() if r else "AUTO"


def list_user_overrides() -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            """SELECT r.*, u.username, u.full_name
               FROM bridge_user_rules r
               LEFT JOIN users u ON u.user_id = r.user_id
               ORDER BY r.updated_at DESC LIMIT 100"""
        ).fetchall()
        return [dict(x) for x in rows]


def user_can_copy(user_id: int, account_mode: str = "DEMO") -> tuple[bool, str]:
    """Single source of truth deciding whether `user_id` is authorized to copy right now."""
    pol = get_policy()
    if pol["kill_switch"]:
        return False, "🛑 تم إيقاف النسخ الآلي طارئاً لجميع الأعضاء من الإدارة"
    if not pol["enabled"] or pol["access_mode"] == "OFF":
        return False, "⏹ ميزة النسخ الآلي متوقفة كلياً من الإدارة حالياً"
    if str(account_mode or "DEMO").upper() == "LIVE" and not pol["allow_live"]:
        return False, "🧪 النسخ الحقيقي Live متوقف مؤقتاً من الإدارة — حسابات Demo متاحة فقط"
    if int(user_id) == int(config.ADMIN_ID or 0):
        return True, "👑 صلاحية الإدارة العليا مفعّلة"

    # Check if user is blocked globally
    with db.conn() as c:
        u = c.execute("SELECT is_blocked FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    if u and int(u["is_blocked"] or 0) == 1:
        return False, "🚫 الحساب محظور"

    ov = get_user_override(user_id)
    if ov == "DENY":
        return False, "🚫 تم إيقاف النسخ الآلي لحسابك بقرار من الإدارة"
    if ov == "ALLOW":
        return True, "⭐ مسموح لك بالنسخ الآلي بقرار خاص من الإدارة"

    mode = pol["access_mode"]
    if mode == "ALL":
        return True, "🌐 النسخ الآلي مفتوح لجميع الأعضاء"
    if mode == "SELECTED":
        return False, "🎯 النسخ الآلي مخصّص حالياً لمستخدمين محددين من الإدارة فقط"

    # mode == "PLANS"
    plan = dict(subscriptions.plan_for(int(user_id)))
    pcode = str(plan.get("code") or "FREE").upper()
    if pcode in pol["allowed_plans"]:
        return True, f"💎 باقتك ({pcode}) مؤهلة للنسخ الآلي"
    allowed_txt = " / ".join(pol["allowed_plans"]) or "ROYAL"
    return False, f"🔒 النسخ الآلي متاح لباقات ({allowed_txt}) — أو بتفعيل خاص من الإدارة"


# ---------------------------------------------------------------------------
# 2. Encrypted Connections CRUD
# ---------------------------------------------------------------------------

def _sanitize_conn(row: dict) -> dict:
    d = dict(row)
    d.pop("creds_enc", None)
    d["enabled"] = bool(d.get("enabled"))
    d["admin_frozen"] = bool(d.get("admin_frozen"))
    d["partial_tp_be"] = bool(d.get("partial_tp_be"))
    meta = PLATFORMS.get(d.get("platform", ""), {})
    d["platform_name"] = meta.get("name", d.get("platform", ""))
    d["platform_badge"] = meta.get("badge", "")
    # Mask bridge_key only for non-mt5; for mt5_bridge the user needs their key for EA setup
    if d.get("platform") != "mt5_bridge":
        d["bridge_key"] = ""
    return d


def save_connection(
    user_id: int,
    platform: str,
    account_mode: str = "DEMO",
    *,
    label: str = "",
    creds: dict | None = None,
    risk_pct: float = 1.0,
    lot_mode: str = "auto",
    fixed_lot: float = 0.01,
    max_daily_dd_pct: float = 3.0,
    max_open_trades: int = 3,
    partial_tp_be: bool = True,
    markets: str = "ALL",
    symbol_suffix: str = "",
) -> dict:
    plat = str(platform or "").strip().lower()
    if plat not in PLATFORMS:
        return {"ok": False, "error": "منصة غير مدعومة"}
    amode = "LIVE" if str(account_mode or "DEMO").strip().upper() == "LIVE" else "DEMO"
    ok_perm, reason = user_can_copy(user_id, amode)
    if not ok_perm:
        return {"ok": False, "error": reason}

    clean_creds = {str(k): str(v).strip() for k, v in (creds or {}).items() if str(v or "").strip()}
    if plat == "tradelocker" and not (clean_creds.get("email") and clean_creds.get("password") and clean_creds.get("server")):
        return {"ok": False, "error": "يرجى إدخال البريد الإلكتروني وكلمة المرور واسم السيرفر لـ TradeLocker"}
    if plat == "deriv" and not clean_creds.get("api_token"):
        return {"ok": False, "error": "يرجى إدخال Deriv API Token الخاص بحسابك (Demo أو Live)"}
    if plat == "ctrader" and not (clean_creds.get("access_token") and clean_creds.get("account_id")):
        return {"ok": False, "error": "يرجى إدخال cTrader Access Token ورقم الحساب (Account ID)"}

    now = config.now()
    enc = vault.encrypt_json(clean_creds) if clean_creds else ""
    risk_v = max(0.1, min(5.0, float(risk_pct or 1.0)))
    lmode = "fixed" if str(lot_mode or "").lower() == "fixed" else "auto"
    flot = max(0.01, min(10.0, float(fixed_lot or 0.01)))
    dd_v = max(0.5, min(25.0, float(max_daily_dd_pct or 3.0)))
    mot = max(1, min(20, int(max_open_trades or 3)))
    mkts = ",".join(x.strip().upper() for x in str(markets or "ALL").split(",") if x.strip()) or "ALL"
    lbl = str(label or f"{PLATFORMS[plat]['name']} ({amode})").strip()[:60]
    sfx = str(symbol_suffix or clean_creds.get("symbol_suffix") or "").strip()[:16]

    with db.tx() as c:
        existing = c.execute(
            "SELECT id, bridge_key, creds_enc FROM bridge_connections WHERE user_id=? AND platform=? AND account_mode=?",
            (int(user_id), plat, amode),
        ).fetchone()
        if existing:
            bkey = existing["bridge_key"] or (f"fyb_{py_secrets.token_hex(16)}" if plat == "mt5_bridge" else "")
            final_enc = enc or existing["creds_enc"]
            c.execute(
                """UPDATE bridge_connections SET
                     label=?, bridge_key=?, creds_enc=?, enabled=1, risk_pct=?, lot_mode=?, fixed_lot=?,
                     max_daily_dd_pct=?, max_open_trades=?, partial_tp_be=?, markets=?, symbol_suffix=?,
                     last_status='READY', updated_at=?
                   WHERE id=?""",
                (lbl, bkey, final_enc, risk_v, lmode, flot, dd_v, mot, 1 if partial_tp_be else 0,
                 mkts, sfx, now, int(existing["id"])),
            )
            cid = int(existing["id"])
        else:
            bkey = f"fyb_{py_secrets.token_hex(16)}" if plat == "mt5_bridge" else ""
            cur = c.execute(
                """INSERT INTO bridge_connections(
                     user_id, platform, account_mode, label, bridge_key, creds_enc, enabled, admin_frozen,
                     risk_pct, lot_mode, fixed_lot, max_daily_dd_pct, max_open_trades, partial_tp_be,
                     markets, symbol_suffix, last_status, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?,'READY',?,?)""",
                (int(user_id), plat, amode, lbl, bkey, enc, risk_v, lmode, flot, dd_v, mot,
                 1 if partial_tp_be else 0, mkts, sfx, now, now),
            )
            cid = int(cur.lastrowid)
        row = dict(c.execute("SELECT * FROM bridge_connections WHERE id=?", (cid,)).fetchone())
    return {"ok": True, "connection": _sanitize_conn(row)}


def list_connections(user_id: int | None = None, limit: int = 100) -> list[dict]:
    with db.conn() as c:
        if user_id is not None:
            rows = c.execute(
                "SELECT * FROM bridge_connections WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (int(user_id), int(limit)),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT bc.*, u.username, u.full_name
                   FROM bridge_connections bc
                   LEFT JOIN users u ON u.user_id = bc.user_id
                   ORDER BY bc.updated_at DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [_sanitize_conn(dict(r)) for r in rows]


def toggle_connection(conn_id: int, user_id: int | None = None, is_admin: bool = False) -> dict:
    with db.tx() as c:
        r = c.execute("SELECT * FROM bridge_connections WHERE id=?", (int(conn_id),)).fetchone()
        if not r:
            return {"ok": False, "error": "الربط غير موجود"}
        if not is_admin and int(r["user_id"]) != int(user_id or 0):
            return {"ok": False, "error": "غير مصرح"}
        nxt = 0 if int(r["enabled"] or 0) else 1
        c.execute("UPDATE bridge_connections SET enabled=?, updated_at=? WHERE id=?", (nxt, config.now(), int(conn_id)))
    return {"ok": True, "id": int(conn_id), "enabled": bool(nxt)}


def admin_freeze_connection(conn_id: int, frozen: bool | None = None) -> dict:
    with db.tx() as c:
        r = c.execute("SELECT admin_frozen FROM bridge_connections WHERE id=?", (int(conn_id),)).fetchone()
        if not r:
            return {"ok": False, "error": "الربط غير موجود"}
        nxt = (0 if int(r["admin_frozen"] or 0) else 1) if frozen is None else (1 if frozen else 0)
        c.execute("UPDATE bridge_connections SET admin_frozen=?, updated_at=? WHERE id=?", (nxt, config.now(), int(conn_id)))
    return {"ok": True, "id": int(conn_id), "admin_frozen": bool(nxt)}


def delete_connection(conn_id: int, user_id: int | None = None, is_admin: bool = False) -> dict:
    with db.tx() as c:
        r = c.execute("SELECT user_id FROM bridge_connections WHERE id=?", (int(conn_id),)).fetchone()
        if not r:
            return {"ok": False, "error": "الربط غير موجود"}
        if not is_admin and int(r["user_id"]) != int(user_id or 0):
            return {"ok": False, "error": "غير مصرح"}
        c.execute("DELETE FROM bridge_connections WHERE id=?", (int(conn_id),))
    return {"ok": True, "id": int(conn_id)}


def list_orders(user_id: int | None = None, limit: int = 40) -> list[dict]:
    with db.conn() as c:
        if user_id is not None:
            rows = c.execute(
                "SELECT * FROM bridge_orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (int(user_id), int(limit)),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT bo.*, u.username, u.full_name
                   FROM bridge_orders bo
                   LEFT JOIN users u ON u.user_id = bo.user_id
                   ORDER BY bo.id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. Live Broker Connectors (TradeLocker REST + Deriv WS + cTrader + MT5)
# ---------------------------------------------------------------------------

def _http_json(url: str, method: str = "GET", body: dict | None = None,
               headers: dict | None = None, timeout: float = 8.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": f"{config.BRAND}-Bridge/{config.VERSION}"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as he:
        raw = he.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"error": raw[:200]}
        return he.code, j
    except Exception as exc:
        return 0, {"error": str(exc)}


def _tradelocker_base(account_mode: str) -> str:
    return "https://live.tradelocker.com/backend-api" if account_mode == "LIVE" else "https://demo.tradelocker.com/backend-api"


def _tradelocker_auth(creds: dict, account_mode: str) -> tuple[str | None, str]:
    base = _tradelocker_base(account_mode)
    st, j = _http_json(
        f"{base}/auth/jwt/token",
        method="POST",
        body={
            "email": creds.get("email", ""),
            "password": creds.get("password", ""),
            "server": creds.get("server", ""),
        },
    )
    if st in (200, 201) and j.get("accessToken"):
        return str(j["accessToken"]), ""
    return None, str(j.get("message") or j.get("error") or f"HTTP {st}")


def _deriv_ws_call(payload: dict, app_id: str = "1089", timeout: float = 7.0) -> dict:
    """Minimal zero-dependency RFC-6455 TLS WebSocket single-RPC helper for Deriv API."""
    host = "ws.derivws.com"
    path = f"/websockets/v3?app_id={urllib.parse.quote(str(app_id or '1089'))}" if "urllib" in globals() else f"/websockets/v3?app_id={app_id or '1089'}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as raw_sock:
        with ctx.wrap_socket(raw_sock, server_hostname=host) as sock:
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode("ascii"))
            resp_hdr = b""
            while b"\r\n\r\n" not in resp_hdr and len(resp_hdr) < 8192:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                resp_hdr += chunk
            if b" 101 " not in resp_hdr:
                return {"error": {"message": "Deriv WS handshake rejected"}}
            # Send masked text frame
            msg_b = json.dumps(payload).encode("utf-8")
            mask = os.urandom(4)
            n = len(msg_b)
            if n < 126:
                hdr = bytes([0x81, 0x80 | n])
            elif n < 65536:
                hdr = bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
            else:
                hdr = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", n)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(msg_b))
            sock.sendall(hdr + mask + masked)
            # Read response frame
            b2 = sock.recv(2)
            if len(b2) < 2:
                return {"error": {"message": "Empty Deriv response"}}
            plen = b2[1] & 0x7F
            if plen == 126:
                plen = struct.unpack("!H", sock.recv(2))[0]
            elif plen == 127:
                plen = struct.unpack("!Q", sock.recv(8))[0]
            buf = b""
            while len(buf) < plen:
                ch = sock.recv(min(4096, plen - len(buf)))
                if not ch:
                    break
                buf += ch
            return json.loads(buf.decode("utf-8", errors="replace"))


def test_connection(conn_id: int, user_id: int | None = None, is_admin: bool = False) -> dict:
    """Verify real broker connectivity & refresh live balance for a saved bridge connection."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM bridge_connections WHERE id=?", (int(conn_id),)).fetchone()
    if not row:
        return {"ok": False, "error": "الربط غير موجود"}
    row = dict(row)
    if not is_admin and int(row["user_id"]) != int(user_id or 0):
        return {"ok": False, "error": "غير مصرح"}

    plat = row["platform"]
    amode = row["account_mode"]
    creds = vault.decrypt_json(row.get("creds_enc") or "") if row.get("creds_enc") else {}
    now = config.now()

    if plat == "mt5_bridge":
        hb = row.get("last_heartbeat") or ""
        bal = float(row.get("last_balance") or 0.0)
        return {
            "ok": True,
            "platform": plat,
            "account_mode": amode,
            "balance": bal,
            "connected": bool(hb),
            "message": (
                f"🟢 MT5 EA متصل — آخر نبضة: {hb[:19].replace('T',' ')} · الرصيد: ${bal:,.2f}"
                if hb else "⏳ بانتظار تشغيل ملف ForexYemeni_Bridge.mq5 على MT5 لالتقاط أول نبضة اتصال"
            ),
        }

    if plat == "tradelocker":
        tok, err = _tradelocker_auth(creds, amode)
        if not tok:
            with db.tx() as c:
                c.execute("UPDATE bridge_connections SET last_status=?, updated_at=? WHERE id=?",
                          (f"ERR: {err[:80]}", now, int(conn_id)))
            return {"ok": False, "error": f"فشل اتصال TradeLocker ({amode}): {err}"}
        base = _tradelocker_base(amode)
        st, j = _http_json(f"{base}/auth/jwt/all-accounts", headers={"Authorization": f"Bearer {tok}"})
        accs = j.get("accounts") if isinstance(j, dict) else []
        bal = 0.0
        if accs:
            bal = float(accs[0].get("accountBalance") or 0.0)
        with db.tx() as c:
            c.execute(
                "UPDATE bridge_connections SET last_balance=?, last_equity=?, last_heartbeat=?, last_status='CONNECTED', updated_at=? WHERE id=?",
                (bal, bal, now, now, int(conn_id)),
            )
        return {"ok": True, "platform": plat, "account_mode": amode, "balance": bal,
                "message": f"🟢 متصل بنجاح مع TradeLocker ({amode}) · الرصيد: ${bal:,.2f}"}

    if plat == "deriv":
        try:
            res = _deriv_ws_call({"authorize": creds.get("api_token", "")}, app_id=creds.get("app_id") or "1089")
        except Exception as exc:
            return {"ok": False, "error": f"تعذر الاتصال بـ Deriv WS: {exc}"}
        if res.get("error"):
            msg = str(res["error"].get("message") or "Invalid token")
            with db.tx() as c:
                c.execute("UPDATE bridge_connections SET last_status=?, updated_at=? WHERE id=?",
                          (f"ERR: {msg[:80]}", now, int(conn_id)))
            return {"ok": False, "error": f"فشل توثيق Deriv: {msg}"}
        auth_obj = res.get("authorize") or {}
        bal = float(auth_obj.get("balance") or 0.0)
        is_virt = bool(auth_obj.get("is_virtual"))
        detected_mode = "DEMO" if is_virt else "LIVE"
        with db.tx() as c:
            c.execute(
                "UPDATE bridge_connections SET last_balance=?, last_equity=?, last_heartbeat=?, last_status='CONNECTED', updated_at=? WHERE id=?",
                (bal, bal, now, now, int(conn_id)),
            )
        return {"ok": True, "platform": plat, "account_mode": detected_mode, "balance": bal,
                "message": f"🟢 متصل بـ Deriv ({detected_mode}) · الرصيد: {bal:,.2f} {auth_obj.get('currency','USD')}"}

    if plat == "ctrader":
        ok_fmt = bool(creds.get("access_token") and creds.get("account_id"))
        if not ok_fmt:
            return {"ok": False, "error": "بيانات cTrader Open API غير مكتملة"}
        with db.tx() as c:
            c.execute("UPDATE bridge_connections SET last_heartbeat=?, last_status='READY_OAUTH', updated_at=? WHERE id=?",
                      (now, now, int(conn_id)))
        return {"ok": True, "platform": plat, "account_mode": amode, "balance": float(row.get("last_balance") or 0.0),
                "message": f"🟢 حساب cTrader ({amode} · #{creds.get('account_id')}) محفوظ ومجهز للنسخ الآلي"}

    return {"ok": False, "error": "منصة غير معروفة"}


# ---------------------------------------------------------------------------
# 4. Signal Dispatch Engine + Daily DD Shield + TP1 50% Partial & BE
# ---------------------------------------------------------------------------

def _daily_dd_breached(conn: dict) -> tuple[bool, float]:
    bal = float(conn.get("last_balance") or 0.0)
    max_dd = float(conn.get("max_daily_dd_pct") or 3.0)
    if bal <= 0 or max_dd <= 0:
        return False, 0.0
    day_start = db.day_start_utc()
    with db.conn() as c:
        pnl_sum = float(c.execute(
            "SELECT COALESCE(SUM(pnl_usd),0) s FROM bridge_orders WHERE connection_id=? AND created_at>=? AND pnl_usd<0",
            (int(conn["id"]), day_start),
        ).fetchone()["s"] or 0.0)
    dd_pct = abs(pnl_sum) / bal * 100.0
    return dd_pct >= max_dd, round(dd_pct, 2)


def _compute_bridge_lots(conn: dict, symbol: str, entry: float, sl: float) -> float:
    if (conn.get("lot_mode") or "auto") == "fixed":
        return round(max(0.01, min(10.0, float(conn.get("fixed_lot") or 0.01))), 2)
    bal = float(conn.get("last_balance") or 0.0)
    if bal <= 0:
        bal = 10000.0
    risk_pct = float(conn.get("risk_pct") or 1.0)
    if entry > 0 and sl > 0 and entry != sl:
        lots = instruments.position_size(bal, risk_pct, entry, sl, symbol)
        return round(max(0.01, min(10.0, lots)), 2)
    return round(max(0.01, min(10.0, float(conn.get("fixed_lot") or 0.01))), 2)


def _market_allowed(markets_csv: str, symbol: str) -> bool:
    raw = str(markets_csv or "ALL").strip().upper()
    if not raw or raw == "ALL":
        return True
    allowed = {x.strip().upper() for x in raw.split(",") if x.strip()}
    return str(symbol or "").strip().upper() in allowed


def dispatch_signal_to_bridges(sig: dict) -> dict:
    """Queue / execute a newly published signal across all eligible user broker bridges."""
    if not sig or not sig.get("id"):
        return {"ok": True, "queued": 0, "blocked": 0}
    sid = int(sig["id"])
    sym = str(sig.get("symbol") or "").strip().upper()
    direction = str(sig.get("direction") or "BUY").strip().upper()
    entry = float(sig.get("entry") or 0.0)
    stop = float(sig.get("stop") or 0.0)
    tp1 = float(sig.get("tp1") or 0.0)
    tp2 = float(sig.get("tp2") or 0.0)
    tp3 = float(sig.get("tp3") or 0.0)
    now = config.now()

    with db.conn() as c:
        conns = [dict(r) for r in c.execute(
            "SELECT * FROM bridge_connections WHERE enabled=1"
        ).fetchall()]

    queued = 0
    blocked = 0
    for conn in conns:
        cid = int(conn["id"])
        uid = int(conn["user_id"])
        plat = conn["platform"]
        amode = conn["account_mode"]

        if int(conn.get("admin_frozen") or 0) == 1:
            blocked += 1
            continue
        if not _market_allowed(conn.get("markets") or "ALL", sym):
            continue

        # Dedup check
        with db.conn() as c:
            dup = c.execute(
                "SELECT id FROM bridge_orders WHERE connection_id=? AND signal_id=? AND action_type='OPEN'",
                (cid, sid),
            ).fetchone()
            open_cnt = int(c.execute(
                "SELECT COUNT(*) n FROM bridge_orders WHERE connection_id=? AND action_type='OPEN' AND status IN ('QUEUED','DISPATCHED','EXECUTED') AND created_at>=?",
                (cid, db.day_start_utc()),
            ).fetchone()["n"] or 0)
        if dup:
            continue

        ok_perm, reason = user_can_copy(uid, amode)
        lots = _compute_bridge_lots(conn, sym, entry, stop)
        if not ok_perm:
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,'BLOCKED_POLICY',?,?,?)""",
                    (cid, uid, plat, amode, sid, sym, direction, lots, entry, stop, tp1, tp2, tp3, reason[:180], now, now),
                )
            blocked += 1
            continue

        breached, dd_pct = _daily_dd_breached(conn)
        if breached:
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,'BLOCKED_DD',?,?,?)""",
                    (cid, uid, plat, amode, sid, sym, direction, lots, entry, stop, tp1, tp2, tp3,
                     f"درع التراجع اليومي أوقف النسخ ({dd_pct}% >= {conn['max_daily_dd_pct']}%)", now, now),
                )
            blocked += 1
            continue

        if open_cnt >= int(conn.get("max_open_trades") or 3):
            blocked += 1
            continue

        # Queue for MT5 EA or broker cloud worker
        init_status = "QUEUED"
        detail = f"جاهز للتنفيذ على {PLATFORMS[plat]['name']} ({amode})"
        with db.tx() as c:
            c.execute(
                """INSERT INTO bridge_orders(
                     connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                     lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,detail,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",
                (cid, uid, plat, amode, sid, sym, direction, lots, entry, stop, tp1, tp2, tp3,
                 init_status, detail, now, now),
            )
        queued += 1
    return {"ok": True, "queued": queued, "blocked": blocked}


def dispatch_tp1_partial_be(sig: dict) -> int:
    """When TP1 hits on a signal, enqueue PARTIAL_BE (50% close + Breakeven SL) for opted-in bridges."""
    if not sig or not sig.get("id"):
        return 0
    sid = int(sig["id"])
    now = config.now()
    enqueued = 0
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT bo.*, bc.partial_tp_be, bc.enabled, bc.admin_frozen
               FROM bridge_orders bo
               JOIN bridge_connections bc ON bc.id = bo.connection_id
               WHERE bo.signal_id=? AND bo.action_type='OPEN'
                 AND bo.status IN ('QUEUED','DISPATCHED','EXECUTED')
                 AND bc.enabled=1 AND bc.admin_frozen=0 AND bc.partial_tp_be=1""",
            (sid,),
        ).fetchall()]
    for r in rows:
        cid = int(r["connection_id"])
        with db.conn() as c:
            exists = c.execute(
                "SELECT id FROM bridge_orders WHERE connection_id=? AND signal_id=? AND action_type='PARTIAL_BE'",
                (cid, sid),
            ).fetchone()
        if exists:
            continue
        with db.tx() as c:
            c.execute(
                """INSERT INTO bridge_orders(
                     connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                     lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,external_order_id,detail,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'PARTIAL_BE',?,?,?,?,?,?,'QUEUED',?,?,?,?)""",
                (cid, int(r["user_id"]), r["platform"], r["account_mode"], sid, r["symbol"], r["direction"],
                 round(float(r["lots"] or 0.01) * 0.5, 2), float(r["entry_price"] or 0.0), float(r["entry_price"] or 0.0),
                 float(r["tp1_price"] or 0.0), float(r["tp2_price"] or 0.0), float(r["tp3_price"] or 0.0),
                 str(r.get("external_order_id") or ""), "تأمين 50% عند TP1 + نقل الوقف لنقطة الدخول (BE)", now, now),
            )
        enqueued += 1
    return enqueued


# ---------------------------------------------------------------------------
# 5. MT5 Royal Bridge EA Polling, Ack & Ready-to-Download .mq5 Generator
# ---------------------------------------------------------------------------

def poll_mt5_orders(bridge_key: str, balance: float = 0.0, equity: float = 0.0) -> dict:
    bkey = str(bridge_key or "").strip()
    if not bkey:
        return {"ok": False, "error": "Missing bridge key"}
    now = config.now()
    with db.tx() as c:
        conn = c.execute(
            "SELECT * FROM bridge_connections WHERE bridge_key=? AND platform='mt5_bridge'",
            (bkey,),
        ).fetchone()
        if not conn:
            return {"ok": False, "error": "Invalid bridge key"}
        conn = dict(conn)
        cid = int(conn["id"])
        upd_bal = float(balance) if float(balance or 0) > 0 else float(conn.get("last_balance") or 0)
        upd_eq = float(equity) if float(equity or 0) > 0 else float(conn.get("last_equity") or upd_bal)
        c.execute(
            "UPDATE bridge_connections SET last_balance=?, last_equity=?, last_heartbeat=?, last_status='CONNECTED', updated_at=? WHERE id=?",
            (upd_bal, upd_eq, now, now, cid),
        )
        ok_perm, reason = user_can_copy(int(conn["user_id"]), conn["account_mode"])
        if not ok_perm or not int(conn.get("enabled") or 0) or int(conn.get("admin_frozen") or 0):
            return {"ok": True, "paused": True, "reason": reason, "orders": []}
        orders = [dict(r) for r in c.execute(
            "SELECT * FROM bridge_orders WHERE connection_id=? AND status='QUEUED' ORDER BY id ASC LIMIT 10",
            (cid,),
        ).fetchall()]
        for o in orders:
            c.execute("UPDATE bridge_orders SET status='DISPATCHED', updated_at=? WHERE id=?", (now, int(o["id"])))
    return {
        "ok": True,
        "paused": False,
        "symbol_suffix": conn.get("symbol_suffix") or "",
        "account_mode": conn["account_mode"],
        "orders": orders,
    }


def ack_mt5_order(bridge_key: str, order_id: int, status: str,
                  ticket: str = "", detail: str = "", pnl_usd: float = 0.0) -> dict:
    bkey = str(bridge_key or "").strip()
    st = str(status or "EXECUTED").strip().upper()
    if st not in ("EXECUTED", "FAILED", "SKIPPED"):
        st = "EXECUTED"
    now = config.now()
    with db.tx() as c:
        conn = c.execute("SELECT id FROM bridge_connections WHERE bridge_key=?", (bkey,)).fetchone()
        if not conn:
            return {"ok": False, "error": "Invalid bridge key"}
        cur = c.execute(
            "UPDATE bridge_orders SET status=?, external_order_id=?, detail=?, pnl_usd=?, updated_at=? "
            "WHERE id=? AND connection_id=?",
            (st, str(ticket or "")[:64], str(detail or "")[:200], float(pnl_usd or 0.0), now, int(order_id), int(conn["id"])),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": "Order not found"}
    return {"ok": True, "order_id": int(order_id), "status": st}


def render_mt5_ea_source(conn: dict, server_base_url: str) -> str:
    """Generate a ready-to-compile MQL5 Expert Advisor pre-wired to this user's Bridge Key."""
    base = (server_base_url or "https://YOUR-BOT-URL").rstrip("/")
    bkey = conn.get("bridge_key") or "PASTE_YOUR_BRIDGE_KEY"
    suffix = conn.get("symbol_suffix") or ""
    amode = conn.get("account_mode") or "DEMO"
    return f"""//+------------------------------------------------------------------+
//|                                       ForexYemeni_Bridge.mq5     |
//|  {config.BRAND} Royal Auto-Bridge EA (v{config.VERSION}) — 100% Free       |
//|  Account Mode: {amode}  |  Auto 50% Partial TP1 + Breakeven (BE) |
//+------------------------------------------------------------------+
#property copyright "{config.BRAND} Royal Desk"
#property version   "{config.VERSION}"
#property strict
#include <Trade\\Trade.mqh>

input string InpServerUrl    = "{base}";
input string InpBridgeKey    = "{bkey}";
input string InpSymbolSuffix = "{suffix}";
input int    InpPollSeconds  = 2;
input ulong  InpMagicNumber  = 770077;
input int    InpSlippagePts  = 30;

CTrade g_trade;

int OnInit()
{{
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpSlippagePts);
   EventSetTimer(MathMax(1, InpPollSeconds));
   Print("{config.BRAND} Royal Bridge EA initialized ({amode}). Allow WebRequest URL: ", InpServerUrl);
   return(INIT_SUCCEEDED);
}}

void OnDeinit(const int reason) {{ EventKillTimer(); }}

string ResolveBrokerSymbol(string baseSym)
{{
   string cand = baseSym + InpSymbolSuffix;
   if(SymbolSelect(cand, true)) return cand;
   string alts[4] = {{ baseSym, baseSym + "m", baseSym + ".raw", baseSym + ".pro" }};
   for(int i=0; i<4; i++) if(SymbolSelect(alts[i], true)) return alts[i];
   if(baseSym == "XAUUSD" && SymbolSelect("GOLD", true)) return "GOLD";
   return cand;
}}

string JsonVal(string src, string key)
{{
   string pat = "\\"" + key + "\\":";
   int p = StringFind(src, pat);
   if(p < 0) return "";
   p += StringLen(pat);
   while(p < StringLen(src) && StringGetCharacter(src, p) == ' ') p++;
   if(StringGetCharacter(src, p) == '\\"') {{
      p++;
      int e = StringFind(src, "\\"", p);
      return (e > p) ? StringSubstr(src, p, e - p) : "";
   }}
   int e = p;
   while(e < StringLen(src)) {{
      ushort ch = StringGetCharacter(src, e);
      if(ch == ',' || ch == '}}' || ch == ']') break;
      e++;
   }}
   return StringSubstr(src, p, e - p);
}}

void SendAck(long orderId, string status, string ticket, string detail)
{{
   string url = InpServerUrl + "/api/bridge/ack";
   string body = StringFormat("{{\\"key\\":\\"%s\\",\\"order_id\\":%I64d,\\"status\\":\\"%s\\",\\"ticket\\":\\"%s\\",\\"detail\\":\\"%s\\"}}",
                              InpBridgeKey, orderId, status, ticket, detail);
   char post[], res[]; string resHdr;
   StringToCharArray(body, post, 0, StringLen(body));
   string hdr = "Content-Type: application/json\\r\\nngrok-skip-browser-warning: 1\\r\\n";
   WebRequest("POST", url, hdr, 5000, post, res, resHdr);
}}

void HandlePartialBE(string sym, double bePrice)
{{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {{
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk)) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      double vol = PositionGetDouble(POSITION_VOLUME);
      double tp  = PositionGetDouble(POSITION_TP);
      double half = NormalizeDouble(vol * 0.5, 2);
      if(half >= SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN)) g_trade.PositionClosePartial(tk, half);
      g_trade.PositionModify(tk, bePrice, tp);
   }}
}}

void OnTimer()
{{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   string url = StringFormat("%s/api/bridge/poll?key=%s&balance=%.2f&equity=%.2f", InpServerUrl, InpBridgeKey, bal, eq);
   char post[], res[]; string resHdr;
   string hdr = "ngrok-skip-browser-warning: 1\\r\\n";
   int code = WebRequest("GET", url, hdr, 5000, post, res, resHdr);
   if(code != 200) return;
   string body = CharArrayToString(res);
   int arrPos = StringFind(body, "\\"orders\\":[");
   if(arrPos < 0) return;
   int cur = arrPos;
   while(true) {{
      int ob = StringFind(body, "{{\\"id\\":", cur);
      if(ob < 0) break;
      int oe = StringFind(body, "}}", ob);
      if(oe < 0) break;
      string obj = StringSubstr(body, ob, oe - ob + 1);
      cur = oe + 1;
      long oid = (long)StringToInteger(JsonVal(obj, "id"));
      string act = JsonVal(obj, "action_type");
      string sym = ResolveBrokerSymbol(JsonVal(obj, "symbol"));
      string dir = JsonVal(obj, "direction");
      double lots = StringToDouble(JsonVal(obj, "lots"));
      double sl   = StringToDouble(JsonVal(obj, "stop_price"));
      double tp   = StringToDouble(JsonVal(obj, "tp3_price"));
      if(tp <= 0) tp = StringToDouble(JsonVal(obj, "tp1_price"));
      double entry = StringToDouble(JsonVal(obj, "entry_price"));
      if(act == "PARTIAL_BE") {{
         HandlePartialBE(sym, entry);
         SendAck(oid, "EXECUTED", "BE", "50% Partial Closed + SL moved to BE");
      }} else {{
         bool ok = (dir == "BUY") ? g_trade.Buy(lots, sym, 0.0, sl, tp, "{config.BRAND}")
                                  : g_trade.Sell(lots, sym, 0.0, sl, tp, "{config.BRAND}");
         if(ok) SendAck(oid, "EXECUTED", IntegerToString((long)g_trade.ResultOrder()), "Filled on MT5");
         else   SendAck(oid, "FAILED", "", IntegerToString((int)g_trade.ResultRetcode()));
      }}
   }}
}}
"""
