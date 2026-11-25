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


def is_bridge_visible_to_user(user_id: int) -> bool:
    """Return True iff the Auto-Bridge UI/buttons should be visible to `user_id`.
    When Admin disables Bridge (enabled=0 / kill_switch=1 / mode=OFF), or blocks/excludes
    a user (DENY / SELECTED / non-eligible plan), it is hidden 100% from the user account."""
    if int(user_id) == int(config.ADMIN_ID or 0):
        return True
    ok, _ = user_can_copy(user_id, "DEMO")
    return bool(ok)


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
    if plat == "ctrader":
        g_cid, g_csec = get_global_ctrader_app()
        eff_cid = clean_creds.get("client_id") or g_cid
        eff_csec = clean_creds.get("client_secret") or g_csec
        if not clean_creds.get("access_token"):
            return {"ok": False, "error": "يرجى إدخال cTrader Access Token الخاص بحسابك"}
        if not (eff_cid and eff_csec):
            return {"ok": False, "error": "يرجى إدخال Client ID و Client Secret من تطبيق cTrader Open API (أو ضبطهما مرة واحدة من لوحة الإدارة)"}

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
        # v8.0.0: Clean up pending/queued orders for this connection so no orphans remain
        c.execute(
            "UPDATE bridge_orders SET status='CANCELLED', detail='تم حذف الربط', updated_at=? "
            "WHERE connection_id=? AND status IN ('QUEUED','DISPATCHED')",
            (config.now(), int(conn_id)),
        )
        c.execute("DELETE FROM bridge_connections WHERE id=?", (int(conn_id),))
    return {"ok": True, "id": int(conn_id)}


def mark_signal_bridge_orders_closed(signal_id: int) -> int:
    """v8.0.0 (EXEC-2): Mark EXECUTED/QUEUED bridge OPEN orders for a closed signal
    as CLOSED so they never block `max_open_trades` for the rest of the day."""
    sid = int(signal_id or 0)
    if sid <= 0:
        return 0
    with db.tx() as c:
        cur = c.execute(
            "UPDATE bridge_orders SET status='CLOSED', updated_at=? "
            "WHERE signal_id=? AND action_type='OPEN' AND status IN ('QUEUED','DISPATCHED','EXECUTED')",
            (config.now(), sid),
        )
        return int(cur.rowcount or 0)


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
        timeout=6.0,
    )
    if st in (200, 201) and j.get("accessToken"):
        return str(j["accessToken"]), ""
    return None, str(j.get("message") or j.get("error") or f"HTTP {st}")


def _tradelocker_resolve_account(base: str, tok: str, creds: dict) -> tuple[str, str]:
    """Return (account_id, acc_num) for TradeLocker REST trading endpoints."""
    acc_id = str(creds.get("account_id") or "").strip()
    acc_num = str(creds.get("acc_num") or "").strip()
    if acc_id and acc_num:
        return acc_id, acc_num
    st, j = _http_json(f"{base}/auth/jwt/all-accounts", headers={"Authorization": f"Bearer {tok}"}, timeout=6.0)
    accs = (j.get("accounts") if isinstance(j, dict) else []) or []
    if not accs:
        raise RuntimeError("لم يتم العثور على حسابات نشطة في TradeLocker")
    picked = next((a for a in accs if acc_id and str(a.get("id")) == acc_id), accs[0])
    return str(picked.get("id") or ""), str(picked.get("accNum") or "1")


def _tradelocker_place_order(
    creds: dict,
    account_mode: str,
    symbol: str,
    direction: str,
    lots: float,
    *,
    sl: float = 0.0,
    tp: float = 0.0,
    suffix: str = "",
) -> dict:
    """v8.0.0 (EXEC-1): Real TradeLocker REST Market Order execution with SL & TP."""
    tok, err = _tradelocker_auth(creds, account_mode)
    if not tok:
        raise RuntimeError(f"فشل توثيق TradeLocker: {err}")
    base = _tradelocker_base(account_mode)
    acc_id, acc_num = _tradelocker_resolve_account(base, tok, creds)
    hdrs = {"Authorization": f"Bearer {tok}", "accNum": acc_num}
    st_i, ji = _http_json(f"{base}/trade/accounts/{acc_id}/instruments", headers=hdrs, timeout=6.0)
    insts = ((ji.get("d") or {}).get("instruments") if isinstance(ji, dict) else None) or (ji.get("instruments") if isinstance(ji, dict) else []) or []
    sym_clean = instruments.normalize(symbol)
    wanted = [f"{sym_clean}{suffix}".upper(), sym_clean, f"{sym_clean}.RAW", f"{sym_clean}M"]
    picked = None
    for w in wanted:
        picked = next((x for x in insts if str(x.get("name") or "").upper().replace("/", "") == w), None)
        if picked:
            break
    if not picked:
        picked = next((x for x in insts if sym_clean in str(x.get("name") or "").upper().replace("/", "")), None)
    if not picked:
        raise RuntimeError(f"الرمز {symbol} غير موجود في أدوات TradeLocker")
    inst_id = int(picked.get("tradableInstrumentId") or picked.get("id") or 0)
    routes = picked.get("routes") or []
    trade_route = next((r for r in routes if str(r.get("type") or "").upper() == "TRADE"), routes[0] if routes else {})
    route_id = int(trade_route.get("id") or 0)
    order_body: dict = {
        "price": 0,
        "qty": round(max(0.01, float(lots)), 2),
        "routeId": route_id,
        "side": "buy" if str(direction or "BUY").upper() == "BUY" else "sell",
        "strategyId": "ForexYemeni",
        "trStopOffset": 0,
        "tradableInstrumentId": inst_id,
        "type": "market",
        "validity": "IOC",
    }
    if sl > 0:
        order_body["stopLoss"] = float(sl)
        order_body["stopLossType"] = "absolute"
    if tp > 0:
        order_body["takeProfit"] = float(tp)
        order_body["takeProfitType"] = "absolute"
    st_o, jo = _http_json(f"{base}/trade/accounts/{acc_id}/orders", method="POST", body=order_body, headers=hdrs, timeout=8.0)
    if st_o not in (200, 201) or (isinstance(jo, dict) and str(jo.get("s") or "").lower() == "error"):
        raise RuntimeError(str((jo or {}).get("errmsg") or (jo or {}).get("message") or f"HTTP {st_o}"))
    oid = str(((jo.get("d") or {}).get("orderId") if isinstance(jo, dict) else "") or (jo.get("orderId") if isinstance(jo, dict) else "") or "")
    return {"order_id": oid, "symbol_name": str(picked.get("name") or symbol)}


def _tradelocker_partial_be(creds: dict, account_mode: str, position_id: str, be_price: float) -> dict:
    """v8.0.0 (EXEC-1): Move TradeLocker position SL to Breakeven on TP1."""
    tok, err = _tradelocker_auth(creds, account_mode)
    if not tok:
        return {"ok": False, "detail": f"فشل توثيق TradeLocker: {err}"}
    base = _tradelocker_base(account_mode)
    acc_id, acc_num = _tradelocker_resolve_account(base, tok, creds)
    hdrs = {"Authorization": f"Bearer {tok}", "accNum": acc_num}
    st, j = _http_json(
        f"{base}/trade/positions/{position_id}",
        method="PATCH",
        body={"stopLoss": float(be_price)},
        headers=hdrs,
        timeout=6.0,
    )
    ok = st in (200, 201) and str((j or {}).get("s") or "ok").lower() != "error"
    return {"ok": ok, "detail": "TradeLocker TP1: نقل الوقف لنقطة الدخول BE ✅" if ok else f"فشل تعديل BE ({st})"}


def _deriv_symbol(symbol: str) -> str:
    s = instruments.normalize(symbol)
    if s in ("XAUUSD", "XAGUSD"):
        return f"frx{s}"
    if s.startswith(("EUR", "GBP", "USD", "AUD", "NZD", "CAD", "CHF")) and len(s) == 6:
        return f"frx{s}"
    if s in ("BTCUSD", "ETHUSD"):
        return f"cry{s}"
    return f"frx{s}"


def _deriv_place_order(
    creds: dict,
    symbol: str,
    direction: str,
    stake_usd: float,
    *,
    sl_usd: float = 0.0,
    tp_usd: float = 0.0,
) -> dict:
    """v8.0.0 (EXEC-1): Real Deriv WebSocket Multiplier contract execution with SL/TP."""
    token = str(creds.get("api_token") or "").strip()
    app_id = str(creds.get("app_id") or "1089").strip() or "1089"
    auth = _deriv_ws_call({"authorize": token}, app_id=app_id, timeout=6.0)
    if auth.get("error"):
        raise RuntimeError(str((auth["error"] or {}).get("message") or "Deriv auth failed"))
    auth_obj = auth.get("authorize") or {}
    currency = str(auth_obj.get("currency") or "USD")
    d_sym = _deriv_symbol(symbol)
    amt = round(max(1.0, float(stake_usd or 10.0)), 2)
    params: dict = {
        "amount": amt,
        "basis": "stake",
        "contract_type": "MULTUP" if str(direction or "BUY").upper() == "BUY" else "MULTDOWN",
        "currency": currency,
        "multiplier": 100,
        "symbol": d_sym,
    }
    lim: dict = {}
    if sl_usd > 0:
        lim["stop_loss"] = round(min(amt * 0.95, float(sl_usd)), 2)
    if tp_usd > 0:
        lim["take_profit"] = round(float(tp_usd), 2)
    if lim:
        params["limit_order"] = lim
    res = _deriv_ws_call(
        {"buy": 1, "price": amt, "parameters": params, "passthrough": {"token_mode": True}},
        app_id=app_id,
        timeout=8.0,
    )
    if res.get("error"):
        raise RuntimeError(str((res["error"] or {}).get("message") or "Deriv buy failed"))
    buy_obj = res.get("buy") or {}
    return {
        "contract_id": str(buy_obj.get("contract_id") or ""),
        "symbol_name": d_sym,
        "buy_price": float(buy_obj.get("buy_price") or amt),
    }


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


# ---------------------------------------------------------------------------
# 3b. Official Spotware cTrader Open API v2 (Pure Stdlib JSON over WSS :5036)
# ---------------------------------------------------------------------------

CTRADER_PT_HEARTBEAT = 51
CTRADER_PT_APP_AUTH_REQ = 2100
CTRADER_PT_APP_AUTH_RES = 2101
CTRADER_PT_ACC_AUTH_REQ = 2102
CTRADER_PT_ACC_AUTH_RES = 2103
CTRADER_PT_NEW_ORDER_REQ = 2106
CTRADER_PT_AMEND_SLTP_REQ = 2110
CTRADER_PT_CLOSE_POS_REQ = 2111
CTRADER_PT_SYMBOLS_LIST_REQ = 2114
CTRADER_PT_SYMBOLS_LIST_RES = 2115
CTRADER_PT_SYMBOL_BY_ID_REQ = 2116
CTRADER_PT_SYMBOL_BY_ID_RES = 2117
CTRADER_PT_TRADER_REQ = 2121
CTRADER_PT_TRADER_RES = 2122
CTRADER_PT_RECONCILE_REQ = 2124
CTRADER_PT_RECONCILE_RES = 2125
CTRADER_PT_EXECUTION_EVENT = 2126
CTRADER_PT_ORDER_ERROR_EVENT = 2132
CTRADER_PT_ERROR_RES = 2142
CTRADER_PT_GET_ACCOUNTS_REQ = 2149
CTRADER_PT_GET_ACCOUNTS_RES = 2150


def get_global_ctrader_app() -> tuple[str, str]:
    """Optional bot-wide cTrader Open API App credentials (encrypted in settings)."""
    raw = db.get_setting("bridge_ctrader_app_enc", "")
    if raw:
        data = vault.decrypt_json(raw)
        cid = str(data.get("client_id") or "").strip()
        csec = str(data.get("client_secret") or "").strip()
        if cid and csec:
            return cid, csec
    return (
        os.environ.get("CTRADER_CLIENT_ID", "").strip(),
        os.environ.get("CTRADER_CLIENT_SECRET", "").strip(),
    )


def set_global_ctrader_app(client_id: str, client_secret: str) -> dict:
    cid = str(client_id or "").strip()
    csec = str(client_secret or "").strip()
    if not cid or not csec:
        db.set_setting("bridge_ctrader_app_enc", "")
        return {"ok": True, "configured": False}
    db.set_setting("bridge_ctrader_app_enc", vault.encrypt_json({"client_id": cid, "client_secret": csec}))
    return {"ok": True, "configured": True, "client_id_masked": cid[:6] + "…" + cid[-4:] if len(cid) > 10 else cid}


def _ctrader_effective_creds(creds: dict) -> tuple[str, str, str]:
    g_cid, g_csec = get_global_ctrader_app()
    cid = str(creds.get("client_id") or g_cid or "").strip()
    csec = str(creds.get("client_secret") or g_csec or "").strip()
    tok = str(creds.get("access_token") or "").strip()
    return cid, csec, tok


def _normalize_sym_key(name: str) -> str:
    return "".join(ch for ch in str(name or "").upper() if ch.isalnum())


def _match_ctrader_symbol(symbols: list[dict], target_sym: str, suffix: str = "") -> dict | None:
    """Match a ForexYemeni symbol (e.g. XAUUSD, EURUSD, US30) against broker's cTrader symbol list."""
    want_raw = str(target_sym or "").strip().upper()
    want_sfx = (want_raw + str(suffix or "").strip().upper()) if suffix else want_raw
    aliases = {
        "XAUUSD": ["XAUUSD", "GOLD", "XAUUSDPRO", "XAUUSDRAW", "XAUUSDM", "XAUUSDC"],
        "XAGUSD": ["XAGUSD", "SILVER"],
        "US30": ["US30", "DJ30", "WS30", "US30CASH", "DJI30"],
        "NAS100": ["NAS100", "USTEC", "US100", "NDX100", "USTECH"],
        "BTCUSD": ["BTCUSD", "BTCUSDT", "BITCOIN"],
        "ETHUSD": ["ETHUSD", "ETHUSDT", "ETHEREUM"],
    }
    want_keys = [_normalize_sym_key(want_sfx), _normalize_sym_key(want_raw)]
    for a in aliases.get(want_raw, []):
        k = _normalize_sym_key(a)
        if k not in want_keys:
            want_keys.append(k)

    enabled_syms = [s for s in symbols if s.get("enabled", True)]
    pool = enabled_syms or symbols
    # 1. Exact normalized match
    for wk in want_keys:
        for s in pool:
            if _normalize_sym_key(s.get("symbolName", "")) == wk:
                return s
    # 2. Prefix match (e.g. EURUSD.raw, XAUUSD_i, XAUUSDm)
    for wk in want_keys[:2]:
        for s in pool:
            sn = _normalize_sym_key(s.get("symbolName", ""))
            if sn.startswith(wk) and len(sn) <= len(wk) + 5:
                return s
    return None


class _CTraderClient:
    """Zero-dependency RFC-6455 TLS WebSocket JSON client for Spotware cTrader Open API v2 (:5036)."""

    def __init__(self, account_mode: str = "DEMO", timeout: float = 10.0):
        self.account_mode = "LIVE" if str(account_mode or "DEMO").upper() == "LIVE" else "DEMO"
        self.host = "live.ctraderapi.com" if self.account_mode == "LIVE" else "demo.ctraderapi.com"
        self.port = 5036
        self.timeout = timeout
        self._raw = None
        self._sock = None
        self._seq = 0

    def __enter__(self) -> "_CTraderClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def connect(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        ctx = ssl.create_default_context()
        self._raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock = ctx.wrap_socket(self._raw, server_hostname=self.host)
        self._sock.settimeout(self.timeout)
        req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        hdr = b""
        while b"\r\n\r\n" not in hdr and len(hdr) < 8192:
            ch = self._sock.recv(1024)
            if not ch:
                break
            hdr += ch
        if b" 101 " not in hdr:
            raise ConnectionError(f"cTrader WS handshake failed ({self.host}:{self.port})")

    def close(self) -> None:
        for s in (self._sock, self._raw):
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass
        self._sock = None
        self._raw = None

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            ch = self._sock.recv(n - len(buf))
            if not ch:
                raise ConnectionError("cTrader WS closed unexpectedly")
            buf.extend(ch)
        return bytes(buf)

    def _send_frame(self, payload: bytes, opcode: int = 0x01) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(hdr + mask + masked)

    def _recv_message(self) -> dict:
        chunks = bytearray()
        while True:
            b0, b1 = self._recv_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            plen = b1 & 0x7F
            if plen == 126:
                plen = struct.unpack("!H", self._recv_exact(2))[0]
            elif plen == 127:
                plen = struct.unpack("!Q", self._recv_exact(8))[0]
            mask_key = self._recv_exact(4) if masked else b""
            data = self._recv_exact(plen) if plen else b""
            if masked:
                data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
            if opcode == 0x09:  # PING -> reply PONG
                self._send_frame(data, opcode=0x0A)
                continue
            if opcode == 0x0A:  # PONG
                continue
            if opcode == 0x08:  # CLOSE
                raise ConnectionError("cTrader server closed connection")
            chunks.extend(data)
            if fin:
                break
        return json.loads(chunks.decode("utf-8", errors="replace"))

    def rpc(self, payload_type: int, payload: dict, expect_types: set[int]) -> dict:
        self._seq += 1
        cid = f"fy_{self._seq}_{py_secrets.token_hex(4)}"
        msg = {"clientMsgId": cid, "payloadType": int(payload_type), "payload": payload}
        self._send_frame(json.dumps(msg).encode("utf-8"), opcode=0x01)
        for _ in range(25):
            res = self._recv_message()
            pt = int(res.get("payloadType") or 0)
            if pt == CTRADER_PT_HEARTBEAT:
                continue
            if pt in (CTRADER_PT_ERROR_RES, CTRADER_PT_ORDER_ERROR_EVENT):
                err_p = res.get("payload") or {}
                code = err_p.get("errorCode") or "CTRADER_ERROR"
                desc = err_p.get("description") or ""
                raise RuntimeError(f"{code}: {desc}".strip(": "))
            if pt in expect_types:
                if res.get("clientMsgId") and res.get("clientMsgId") != cid and pt != CTRADER_PT_EXECUTION_EVENT:
                    continue
                return res
        raise TimeoutError(f"cTrader RPC {payload_type} timed out waiting for {expect_types}")

    def auth_session(self, client_id: str, client_secret: str, access_token: str,
                     hint_account_id: str = "") -> tuple[int, dict]:
        """App Auth -> Auto-Discover/Verify Account -> Account Auth -> Trader Info."""
        self.rpc(
            CTRADER_PT_APP_AUTH_REQ,
            {"clientId": client_id, "clientSecret": client_secret},
            {CTRADER_PT_APP_AUTH_RES},
        )
        # Discover accounts linked to this Access Token (also fixes login-vs-ctid confusion!)
        acc_res = self.rpc(
            CTRADER_PT_GET_ACCOUNTS_REQ,
            {"accessToken": access_token},
            {CTRADER_PT_GET_ACCOUNTS_RES},
        )
        accs = (acc_res.get("payload") or {}).get("ctidTraderAccount") or []
        want_live = self.account_mode == "LIVE"
        chosen = None
        hint = str(hint_account_id or "").strip()
        if hint:
            for a in accs:
                if str(a.get("ctidTraderAccountId") or "") == hint or str(a.get("traderLogin") or "") == hint:
                    chosen = a
                    break
        if chosen is None:
            matching = [a for a in accs if bool(a.get("isLive")) == want_live]
            chosen = matching[0] if matching else (accs[0] if accs else None)
        if chosen is None and hint.isdigit():
            ctid = int(hint)
        elif chosen is not None:
            if bool(chosen.get("isLive")) != want_live:
                env_lbl = "LIVE" if chosen.get("isLive") else "DEMO"
                raise RuntimeError(
                    f"الحساب المكتشف #{chosen.get('traderLogin') or chosen.get('ctidTraderAccountId')} هو حساب {env_lbl} وليس {self.account_mode} — بدّل وضع الحساب في النموذج إلى {env_lbl}"
                )
            ctid = int(chosen["ctidTraderAccountId"])
        else:
            raise RuntimeError("لم يتم العثور على أي حساب cTrader مرتبط بهذا Access Token")

        self.rpc(
            CTRADER_PT_ACC_AUTH_REQ,
            {"ctidTraderAccountId": ctid, "accessToken": access_token},
            {CTRADER_PT_ACC_AUTH_RES},
        )
        tr_res = self.rpc(
            CTRADER_PT_TRADER_REQ,
            {"ctidTraderAccountId": ctid},
            {CTRADER_PT_TRADER_RES},
        )
        trader = (tr_res.get("payload") or {}).get("trader") or {}
        if chosen:
            trader.setdefault("brokerTitleShort", chosen.get("brokerTitleShort") or "")
            trader.setdefault("traderLogin", chosen.get("traderLogin") or ctid)
        return ctid, trader

    def execute_market_order(
        self,
        ctid: int,
        symbol: str,
        direction: str,
        lots: float,
        entry: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        suffix: str = "",
        comment: str = "ForexYemeni",
    ) -> dict:
        slist_res = self.rpc(
            CTRADER_PT_SYMBOLS_LIST_REQ,
            {"ctidTraderAccountId": ctid, "includeArchivedSymbols": False},
            {CTRADER_PT_SYMBOLS_LIST_RES},
        )
        syms = (slist_res.get("payload") or {}).get("symbol") or []
        light = _match_ctrader_symbol(syms, symbol, suffix=suffix)
        if not light:
            raise RuntimeError(f"الرمز {symbol} غير موجود في قائمة رموز وسيط cTrader")
        sym_id = int(light["symbolId"])
        sym_name = str(light.get("symbolName") or symbol)

        # Fetch exact symbol volume spec (lotSize, minVolume, stepVolume)
        sinfo_res = self.rpc(
            CTRADER_PT_SYMBOL_BY_ID_REQ,
            {"ctidTraderAccountId": ctid, "symbolId": [sym_id]},
            {CTRADER_PT_SYMBOL_BY_ID_RES},
        )
        sinfo_list = (sinfo_res.get("payload") or {}).get("symbol") or []
        sinfo = sinfo_list[0] if sinfo_list else {}
        lot_size = int(sinfo.get("lotSize") or 10_000_000)  # standard FX 100k units * 100 cents = 10,000,000
        min_vol = int(sinfo.get("minVolume") or max(100, lot_size // 100))
        step_vol = int(sinfo.get("stepVolume") or min_vol)
        raw_vol = int(round(max(0.01, float(lots)) * lot_size))
        if step_vol > 0:
            raw_vol = max(min_vol, (raw_vol // step_vol) * step_vol)
        else:
            raw_vol = max(min_vol, raw_vol)

        is_buy = str(direction or "BUY").upper() == "BUY"
        order_payload: dict = {
            "ctidTraderAccountId": ctid,
            "symbolId": sym_id,
            "orderType": 1,  # MARKET
            "tradeSide": 1 if is_buy else 2,  # 1=BUY, 2=SELL
            "volume": raw_vol,
            "label": "ForexYemeni",
            "comment": str(comment or "ForexYemeni")[:96],
        }
        # Relative SL/TP in 1/100_000 price units (supported directly on MARKET orders!)
        if entry > 0 and sl > 0 and abs(entry - sl) > 0:
            order_payload["relativeStopLoss"] = max(1, int(round(abs(entry - sl) * 100_000)))
        if entry > 0 and tp > 0 and abs(tp - entry) > 0:
            order_payload["relativeTakeProfit"] = max(1, int(round(abs(tp - entry) * 100_000)))

        ev = self.rpc(
            CTRADER_PT_NEW_ORDER_REQ,
            order_payload,
            {CTRADER_PT_EXECUTION_EVENT},
        )
        ep = ev.get("payload") or {}
        pos = ep.get("position") or {}
        order_obj = ep.get("order") or {}
        pos_id = str(pos.get("positionId") or order_obj.get("positionId") or ep.get("orderId") or "")
        fill_px = float(pos.get("price") or order_obj.get("executionPrice") or entry or 0.0)
        return {
            "position_id": pos_id,
            "symbol_name": sym_name,
            "volume": raw_vol,
            "fill_price": fill_px,
            "execution_type": ep.get("executionType"),
        }

    def partial_close_and_be(self, ctid: int, position_id: int, be_price: float) -> dict:
        """Close 50% of an open cTrader position & move Stop Loss to Entry (Breakeven)."""
        rec = self.rpc(
            CTRADER_PT_RECONCILE_REQ,
            {"ctidTraderAccountId": ctid},
            {CTRADER_PT_RECONCILE_RES},
        )
        positions = (rec.get("payload") or {}).get("position") or []
        target = next((p for p in positions if int(p.get("positionId") or 0) == int(position_id)), None)
        if not target:
            return {"ok": False, "detail": f"الصفقة #{position_id} غير مفتوحة على cTrader"}
        td = target.get("tradeData") or {}
        cur_vol = int(td.get("volume") or 0)
        sym_id = int(td.get("symbolId") or 0)
        # v8.0.0 (EXEC-3): Respect broker's exact minVolume & stepVolume before halving
        min_vol = 100
        step_vol = 100
        if sym_id > 0:
            try:
                sinfo_res = self.rpc(
                    CTRADER_PT_SYMBOL_BY_ID_REQ,
                    {"ctidTraderAccountId": ctid, "symbolId": [sym_id]},
                    {CTRADER_PT_SYMBOL_BY_ID_RES},
                )
                sinfo_list = (sinfo_res.get("payload") or {}).get("symbol") or []
                if sinfo_list:
                    min_vol = max(1, int(sinfo_list[0].get("minVolume") or 100))
                    step_vol = max(1, int(sinfo_list[0].get("stepVolume") or min_vol))
            except Exception:
                pass
        raw_half = cur_vol // 2
        half_vol = (raw_half // step_vol) * step_vol if step_vol > 0 else raw_half
        remaining_vol = cur_vol - half_vol
        closed_half = False
        if half_vol >= min_vol and remaining_vol >= min_vol:
            try:
                self.rpc(
                    CTRADER_PT_CLOSE_POS_REQ,
                    {"ctidTraderAccountId": ctid, "positionId": int(position_id), "volume": half_vol},
                    {CTRADER_PT_EXECUTION_EVENT},
                )
                closed_half = True
            except Exception:
                closed_half = False
        be_ok = False
        if be_price > 0:
            amend_p: dict = {
                "ctidTraderAccountId": ctid,
                "positionId": int(position_id),
                "stopLoss": float(be_price),
            }
            if target.get("takeProfit"):
                amend_p["takeProfit"] = float(target["takeProfit"])
            self.rpc(CTRADER_PT_AMEND_SLTP_REQ, amend_p, {CTRADER_PT_EXECUTION_EVENT})
            be_ok = True
        return {
            "ok": closed_half or be_ok,
            "detail": f"cTrader TP1: {'50% إغلاق ✅' if closed_half else 'بدون جزئي'} · {'BE ✅' if be_ok else ''}".strip(),
        }


def _ctrader_live_test(creds: dict, account_mode: str) -> dict:
    cid, csec, tok = _ctrader_effective_creds(creds)
    if not (cid and csec and tok):
        return {"ok": False, "error": "بيانات cTrader غير مكتملة (يلزم Client ID و Client Secret و Access Token)"}
    try:
        with _CTraderClient(account_mode=account_mode, timeout=10.0) as cli:
            ctid, trader = cli.auth_session(cid, csec, tok, hint_account_id=str(creds.get("account_id") or ""))
            digits = int(trader.get("moneyDigits") or 2)
            bal = round(float(trader.get("balance") or 0.0) / (10 ** digits), 2)
            login = str(trader.get("traderLogin") or ctid)
            broker = str(trader.get("brokerTitleShort") or creds.get("broker_name") or "cTrader")
            return {
                "ok": True,
                "ctid": ctid,
                "login": login,
                "broker": broker,
                "balance": bal,
                "message": f"🟢 متصل فعلياً بـ cTrader ({account_mode} · {broker} #{login}) · الرصيد الحي: ${bal:,.2f}",
            }
    except Exception as exc:
        return {"ok": False, "error": f"فشل اتصال cTrader ({account_mode}): {exc}"}


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
        res = _ctrader_live_test(creds, amode)
        if not res.get("ok"):
            err_msg = str(res.get("error") or "cTrader auth failed")
            with db.tx() as c:
                c.execute("UPDATE bridge_connections SET last_status=?, updated_at=? WHERE id=?",
                          (f"ERR: {err_msg[:80]}", now, int(conn_id)))
            return {"ok": False, "error": err_msg}
        bal = float(res.get("balance") or 0.0)
        # Persist auto-discovered ctidTraderAccountId inside encrypted creds for instant execution
        if res.get("ctid"):
            creds["account_id"] = str(res["ctid"])
            if res.get("broker"):
                creds["broker_name"] = str(res["broker"])
            enc_upd = vault.encrypt_json(creds)
        else:
            enc_upd = row.get("creds_enc") or ""
        with db.tx() as c:
            c.execute(
                "UPDATE bridge_connections SET creds_enc=?, last_balance=?, last_equity=?, last_heartbeat=?, last_status='CONNECTED', updated_at=? WHERE id=?",
                (enc_upd, bal, bal, now, now, int(conn_id)),
            )
        return {
            "ok": True,
            "platform": plat,
            "account_mode": amode,
            "balance": bal,
            "ctid": res.get("ctid"),
            "login": res.get("login"),
            "broker": res.get("broker"),
            "message": res["message"],
        }

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

        # Dedup & TRUE open-trades check (v8.0.0 EXEC-2: ignore orders whose signal is already CLOSED!)
        with db.conn() as c:
            dup = c.execute(
                "SELECT id FROM bridge_orders WHERE connection_id=? AND signal_id=? AND action_type='OPEN'",
                (cid, sid),
            ).fetchone()
            open_cnt = int(c.execute(
                """SELECT COUNT(*) n FROM bridge_orders bo
                   LEFT JOIN signals s ON s.id = bo.signal_id
                   WHERE bo.connection_id=? AND bo.action_type='OPEN'
                     AND bo.status IN ('QUEUED','DISPATCHED','EXECUTED')
                     AND (
                         bo.signal_id = 0
                         OR (
                             (s.closed_at IS NULL OR s.closed_at='')
                             AND s.status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                         )
                     )""",
                (cid,),
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

        # Execute directly on cTrader / TradeLocker / Deriv or queue for MT5 EA
        init_status = "QUEUED"
        ext_id = ""
        detail = f"جاهز للتنفيذ على {PLATFORMS[plat]['name']} ({amode})"
        if plat == "ctrader":
            try:
                creds = vault.decrypt_json(conn.get("creds_enc") or "") if conn.get("creds_enc") else {}
                c_cid, c_sec, c_tok = _ctrader_effective_creds(creds)
                if c_cid and c_sec and c_tok:
                    with _CTraderClient(account_mode=amode, timeout=10.0) as cli:
                        ctid, _tr = cli.auth_session(c_cid, c_sec, c_tok, hint_account_id=str(creds.get("account_id") or ""))
                        ex = cli.execute_market_order(
                            ctid=ctid,
                            symbol=sym,
                            direction=direction,
                            lots=lots,
                            entry=entry,
                            sl=stop,
                            tp=tp3 or tp2 or tp1,
                            suffix=str(conn.get("symbol_suffix") or ""),
                            comment=f"FY-SIG#{sid}",
                        )
                        init_status = "EXECUTED"
                        ext_id = str(ex.get("position_id") or "")
                        detail = f"🟢 نُفذ فعلياً على cTrader ({ex.get('symbol_name', sym)} · #{ext_id})"
            except Exception as exc:
                init_status = "FAILED"
                detail = f"❌ فشل تنفيذ cTrader: {str(exc)[:160]}"
        elif plat == "tradelocker":
            try:
                creds = vault.decrypt_json(conn.get("creds_enc") or "") if conn.get("creds_enc") else {}
                if creds.get("email") and creds.get("password"):
                    ex = _tradelocker_place_order(
                        creds, amode, sym, direction, lots,
                        sl=stop, tp=tp3 or tp2 or tp1,
                        suffix=str(conn.get("symbol_suffix") or ""),
                    )
                    init_status = "EXECUTED"
                    ext_id = str(ex.get("order_id") or "")
                    detail = f"🟢 نُفذ فعلياً على TradeLocker ({ex.get('symbol_name', sym)} · #{ext_id})"
            except Exception as exc:
                init_status = "FAILED"
                detail = f"❌ فشل تنفيذ TradeLocker: {str(exc)[:160]}"
        elif plat == "deriv":
            try:
                creds = vault.decrypt_json(conn.get("creds_enc") or "") if conn.get("creds_enc") else {}
                if creds.get("api_token"):
                    bal = max(100.0, float(conn.get("last_balance") or 1000.0))
                    stake = max(5.0, round(bal * (float(conn.get("risk_pct") or 1.0) / 100.0), 2))
                    ex = _deriv_place_order(creds, sym, direction, stake, sl_usd=stake * 0.9, tp_usd=stake * 2.0)
                    init_status = "EXECUTED"
                    ext_id = str(ex.get("contract_id") or "")
                    detail = f"🟢 نُفذ فعلياً على Deriv ({ex.get('symbol_name', sym)} · #{ext_id})"
            except Exception as exc:
                init_status = "FAILED"
                detail = f"❌ فشل تنفيذ Deriv: {str(exc)[:160]}"
        with db.tx() as c:
            c.execute(
                """INSERT INTO bridge_orders(
                     connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                     lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,external_order_id,detail,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, uid, plat, amode, sid, sym, direction, lots, entry, stop, tp1, tp2, tp3,
                 init_status, ext_id, detail, now, now),
            )
        queued += 1
    return {"ok": True, "queued": queued, "blocked": blocked}


def execute_test_trade(
    conn_id: int,
    symbol: str = "EURUSD",
    direction: str = "BUY",
    lots: float = 0.01,
    user_id: int | None = None,
    is_admin: bool = False,
) -> dict:
    """Send an instant 0.01 lot real verification trade (Demo recommended) to test end-to-end execution."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM bridge_connections WHERE id=?", (int(conn_id),)).fetchone()
    if not row:
        return {"ok": False, "error": "الربط غير موجود"}
    row = dict(row)
    if not is_admin and int(row["user_id"]) != int(user_id or 0):
        return {"ok": False, "error": "غير مصرح"}
    if int(row.get("admin_frozen") or 0) == 1:
        return {"ok": False, "error": "هذا الحساب مجمّد حالياً من الإدارة"}
    amode = row["account_mode"]
    ok_perm, reason = user_can_copy(int(row["user_id"]), amode)
    if not ok_perm:
        return {"ok": False, "error": reason}

    sym = str(symbol or "EURUSD").strip().upper()
    side = "SELL" if str(direction or "BUY").strip().upper() == "SELL" else "BUY"
    lot_v = round(max(0.01, min(0.10, float(lots or 0.01))), 2)
    plat = row["platform"]
    now = config.now()

    if plat == "ctrader":
        creds = vault.decrypt_json(row.get("creds_enc") or "") if row.get("creds_enc") else {}
        c_cid, c_sec, c_tok = _ctrader_effective_creds(creds)
        if not (c_cid and c_sec and c_tok):
            return {"ok": False, "error": "بيانات cTrader غير مكتملة"}
        try:
            with _CTraderClient(account_mode=amode, timeout=12.0) as cli:
                ctid, trader = cli.auth_session(c_cid, c_sec, c_tok, hint_account_id=str(creds.get("account_id") or ""))
                ex = cli.execute_market_order(
                    ctid=ctid,
                    symbol=sym,
                    direction=side,
                    lots=lot_v,
                    suffix=str(row.get("symbol_suffix") or ""),
                    comment="FY-TEST-001",
                )
                digits = int(trader.get("moneyDigits") or 2)
                bal = round(float(trader.get("balance") or 0.0) / (10 ** digits), 2)
                pos_id = str(ex.get("position_id") or "")
                fill_px = float(ex.get("fill_price") or 0.0)
                detail = f"🧪 صفقة اختبار ناجحة على cTrader ({ex.get('symbol_name', sym)} · #{pos_id} @ {fill_px})"
                with db.tx() as c:
                    c.execute(
                        "UPDATE bridge_connections SET last_balance=?, last_equity=?, last_heartbeat=?, last_status='CONNECTED', updated_at=? WHERE id=?",
                        (bal, bal, now, now, int(conn_id)),
                    )
                    c.execute(
                        """INSERT INTO bridge_orders(
                             connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                             lots,entry_price,status,external_order_id,detail,created_at,updated_at
                           ) VALUES(?,?,?,?,0,?,?,'OPEN',?,?,'EXECUTED',?,?,?,?)""",
                        (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, fill_px, pos_id, detail, now, now),
                    )
                return {
                    "ok": True,
                    "status": "EXECUTED",
                    "position_id": pos_id,
                    "symbol": ex.get("symbol_name", sym),
                    "fill_price": fill_px,
                    "lots": lot_v,
                    "balance": bal,
                    "message": detail,
                }
        except Exception as exc:
            err = str(exc)
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,status,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'FAILED',?,?,?)""",
                    (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, f"❌ اختبار فشل: {err[:160]}", now, now),
                )
            return {"ok": False, "error": f"فشل تنفيذ صفقة الاختبار على cTrader: {err}"}

    if plat == "tradelocker":
        creds = vault.decrypt_json(row.get("creds_enc") or "") if row.get("creds_enc") else {}
        try:
            ex = _tradelocker_place_order(
                creds, amode, sym, side, lot_v,
                suffix=str(row.get("symbol_suffix") or ""),
            )
            oid = str(ex.get("order_id") or "")
            detail = f"🧪 صفقة اختبار ناجحة على TradeLocker ({ex.get('symbol_name', sym)} · #{oid})"
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,status,external_order_id,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'EXECUTED',?,?,?,?)""",
                    (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, oid, detail, now, now),
                )
            return {"ok": True, "status": "EXECUTED", "position_id": oid, "symbol": ex.get("symbol_name", sym), "lots": lot_v, "message": detail}
        except Exception as exc:
            err = str(exc)
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,status,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'FAILED',?,?,?)""",
                    (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, f"❌ اختبار TradeLocker فشل: {err[:160]}", now, now),
                )
            return {"ok": False, "error": f"فشل تنفيذ صفقة الاختبار على TradeLocker: {err}"}

    if plat == "deriv":
        creds = vault.decrypt_json(row.get("creds_enc") or "") if row.get("creds_enc") else {}
        try:
            ex = _deriv_place_order(creds, sym, side, stake_usd=5.0, sl_usd=4.0, tp_usd=8.0)
            cid_ext = str(ex.get("contract_id") or "")
            detail = f"🧪 صفقة اختبار ناجحة على Deriv ({ex.get('symbol_name', sym)} · #{cid_ext})"
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,status,external_order_id,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'EXECUTED',?,?,?,?)""",
                    (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, cid_ext, detail, now, now),
                )
            return {"ok": True, "status": "EXECUTED", "position_id": cid_ext, "symbol": ex.get("symbol_name", sym), "lots": lot_v, "message": detail}
        except Exception as exc:
            err = str(exc)
            with db.tx() as c:
                c.execute(
                    """INSERT INTO bridge_orders(
                         connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                         lots,status,detail,created_at,updated_at
                       ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'FAILED',?,?,?)""",
                    (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v, f"❌ اختبار Deriv فشل: {err[:160]}", now, now),
                )
            return {"ok": False, "error": f"فشل تنفيذ صفقة الاختبار على Deriv: {err}"}

    # For MT5 EA: enqueue a test order for immediate pickup
    with db.tx() as c:
        c.execute(
            """INSERT INTO bridge_orders(
                 connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                 lots,status,detail,created_at,updated_at
               ) VALUES(?,?,?,?,0,?,?,'OPEN',?,'QUEUED',?,?,?)""",
            (int(conn_id), int(row["user_id"]), plat, amode, sym, side, lot_v,
             f"🧪 صفقة اختبار {lot_v} لوت جاهزة للالتقاط على {PLATFORMS[plat]['name']}", now, now),
        )
    return {"ok": True, "status": "QUEUED", "message": f"🧪 تم إرسال صفقة اختبار {sym} {side} ({lot_v} لوت) لقائمة التنفيذ"}


def dispatch_tp1_partial_be(sig: dict) -> int:
    """When TP1 hits on a signal, enqueue PARTIAL_BE (50% close + Breakeven SL) for opted-in bridges."""
    if not sig or not sig.get("id"):
        return 0
    sid = int(sig["id"])
    now = config.now()
    enqueued = 0
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT bo.*, bc.partial_tp_be, bc.enabled, bc.admin_frozen, bc.creds_enc
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
        pbe_status = "QUEUED"
        pbe_detail = "تأمين 50% عند TP1 + نقل الوقف لنقطة الدخول (BE)"
        if r["platform"] == "ctrader" and str(r.get("external_order_id") or "").isdigit():
            try:
                creds = vault.decrypt_json(r.get("creds_enc") or "") if r.get("creds_enc") else {}
                c_cid, c_sec, c_tok = _ctrader_effective_creds(creds)
                if c_cid and c_sec and c_tok:
                    with _CTraderClient(account_mode=r["account_mode"], timeout=10.0) as cli:
                        ctid, _tr = cli.auth_session(c_cid, c_sec, c_tok, hint_account_id=str(creds.get("account_id") or ""))
                        res_be = cli.partial_close_and_be(ctid, int(r["external_order_id"]), float(r["entry_price"] or 0.0))
                        pbe_status = "EXECUTED" if res_be.get("ok") else "FAILED"
                        pbe_detail = str(res_be.get("detail") or pbe_detail)
            except Exception as exc:
                pbe_status = "FAILED"
                pbe_detail = f"❌ فشل تأمين cTrader: {str(exc)[:140]}"
        elif r["platform"] == "tradelocker" and str(r.get("external_order_id") or "").strip():
            try:
                creds = vault.decrypt_json(r.get("creds_enc") or "") if r.get("creds_enc") else {}
                res_be = _tradelocker_partial_be(creds, r["account_mode"], str(r["external_order_id"]), float(r["entry_price"] or 0.0))
                pbe_status = "EXECUTED" if res_be.get("ok") else "FAILED"
                pbe_detail = str(res_be.get("detail") or pbe_detail)
            except Exception as exc:
                pbe_status = "FAILED"
                pbe_detail = f"❌ فشل تأمين TradeLocker: {str(exc)[:140]}"
        with db.tx() as c:
            c.execute(
                """INSERT INTO bridge_orders(
                     connection_id,user_id,platform,account_mode,signal_id,symbol,direction,action_type,
                     lots,entry_price,stop_price,tp1_price,tp2_price,tp3_price,status,external_order_id,detail,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'PARTIAL_BE',?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, int(r["user_id"]), r["platform"], r["account_mode"], sid, r["symbol"], r["direction"],
                 round(float(r["lots"] or 0.01) * 0.5, 2), float(r["entry_price"] or 0.0), float(r["entry_price"] or 0.0),
                 float(r["tp1_price"] or 0.0), float(r["tp2_price"] or 0.0), float(r["tp3_price"] or 0.0),
                 pbe_status, str(r.get("external_order_id") or ""), pbe_detail, now, now),
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
