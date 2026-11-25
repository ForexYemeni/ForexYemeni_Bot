"""Zero-dependency Asyncio HTTP Server for Telegram Mini App & Admin Web Panel (v7.0.0).

Design:
  * Pure Python stdlib `asyncio.start_server` — zero extra pip dependencies (no FastAPI/Uvicorn needed).
  * Every `/api/*` request validates Telegram WebApp `X-Telegram-Init-Data` HMAC-SHA256.
  * Every `/api/admin/*` request enforces strict `principal["is_admin"]` (uid == config.ADMIN_ID).
  * Exposes a clean testable `dispatch_request(method, path, headers, body)` pure function so unit
    tests can exercise 100% of routes without binding sockets, PLUS real `start_webapp_server()`.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime, timezone

from fysb import config, db, instruments
from fysb.config import log
from fysb.engines import market_data, news_guard
from fysb.services import (
    academy, affiliates, chart_channels, gifts, leaderboard,
    partners, payments, referrals, risk_journal, subscriptions,
)
from fysb.services import audit as audit_log
from fysb.webapp import auth as wa_auth
from fysb.webapp import templates

_BOT_RUNTIME: dict = {"bot": None, "loop": None}

# v8.0.0: In-memory sliding-window rate limiter (anti-brute-force & anti-DoS)
_RL_LOCK = threading.Lock()
_RL_BUCKETS: dict[tuple[str, str], deque] = {}


def reset_rate_limits() -> None:
    """Clear API rate limit buckets (used in unit tests)."""
    with _RL_LOCK:
        _RL_BUCKETS.clear()


def _check_rate_limit(bucket: str, key: str, max_hits: int, window_sec: float = 60.0) -> bool:
    """Return True if request is allowed, False if rate limit exceeded."""
    now = time.monotonic()
    k = (bucket, str(key))
    with _RL_LOCK:
        if len(_RL_BUCKETS) > 8000:
            _RL_BUCKETS.clear()
        dq = _RL_BUCKETS.setdefault(k, deque())
        while dq and (now - dq[0]) > window_sec:
            dq.popleft()
        if len(dq) >= max_hits:
            return False
        dq.append(now)
        return True


def _safe_float(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _plan_allows_symbol(plan: dict, symbol: str) -> bool:
    sym = instruments.normalize(symbol)
    raw = str((plan or {}).get("markets") or "").strip()
    if not raw:
        return True
    allowed = {instruments.normalize(x) for x in raw.split(",") if x.strip()}
    return "ALL" in allowed or sym in allowed


def _plan_allows_tf(plan: dict, tf: str) -> bool:
    t = (tf or "").strip().upper()
    raw = str((plan or {}).get("timeframes") or "").strip()
    if not raw or not t:
        return True
    allowed = {x.strip().upper() for x in raw.split(",") if x.strip()}
    return "ALL" in allowed or t in allowed


def _user_can_access_signal(uid: int, is_admin: bool, sig: dict) -> bool:
    """v8.0.0: Strict signal authorization preventing cross-user & plan leaks.

    * Admin: always allowed.
    * Private SIGNAL_NOW signals: strictly owner-only (NEVER visible to other users or partners).
    * Public/AUTO signals: allowed if delivered to `uid`, already traded in `uid`'s demo,
      already CLOSED (public proof), or matching `uid`'s active plan markets.
    """
    if is_admin:
        return True
    src = str(sig.get("source") or "AUTO").upper()
    owner = int(sig.get("created_by") or 0)
    if src == "SIGNAL_NOW":
        return owner == int(uid)
    st = str(sig.get("status") or "").upper()
    if st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "TP1_BE", "TP2_BE", "CANCELLED", "EXPIRED") and sig.get("closed_at"):
        return True
    if st in ("TP3_HIT", "SL_HIT", "TP1_BE", "TP2_BE", "CANCELLED", "EXPIRED"):
        return True
    sid = int(sig.get("id") or 0)
    with db.conn() as c:
        if c.execute("SELECT 1 FROM signal_deliveries WHERE signal_id=? AND user_id=?", (sid, int(uid))).fetchone():
            return True
        if c.execute("SELECT 1 FROM demo_positions WHERE signal_id=? AND user_id=?", (sid, int(uid))).fetchone():
            return True
    plan = dict(subscriptions.plan_for(int(uid)))
    return _plan_allows_symbol(plan, sig.get("symbol") or "")


def set_bot_runtime(bot, loop=None) -> None:
    """Attach the live Telegram bot & asyncio loop so Admin Web Panel actions can broadcast."""
    _BOT_RUNTIME["bot"] = bot
    _BOT_RUNTIME["loop"] = loop


ALLOWED_ADMIN_TOGGLES = (
    "engine_enabled",
    "global_kill_switch",
    "news_guard_enabled",
    "news_auto_broadcast",
    "news_broadcast_users",
    "news_broadcast_partners",
    "news_broadcast_channels",
    "charts_enabled",
    "academy_enabled",
    "crypto_api_enabled",
    "auto_digest_enabled",
    "stars_enabled",
    "bridge_enabled",
    "bridge_kill_switch",
    "bridge_allow_live",
)


def _png_resp(png: bytes) -> tuple[int, str, bytes]:
    return 200, "image/png", png


def _json_resp(status: int, payload: dict) -> tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _html_resp(status: int, html: str) -> tuple[int, str, bytes]:
    return status, "text/html; charset=utf-8", html.encode("utf-8")


def _parse_ts(iso_str: str) -> int:
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


def dispatch_request(
    method: str,
    target: str,
    headers: dict[str, str],
    body: bytes = b"",
    *,
    bot_token: str | None = None,
) -> tuple[int, str, bytes]:
    """Pure synchronous request dispatcher (executed via `asyncio.to_thread` in live server)."""
    m = (method or "GET").upper()
    parsed = urllib.parse.urlsplit(target or "/")
    raw_path = parsed.path or "/"
    path = raw_path.rstrip("/") or "/"
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}

    # 1) Public static HTML & healthcheck (incl. HEAD/OPTIONS/favicon for tunnels)
    if m == "OPTIONS":
        return 204, "text/plain; charset=utf-8", b""
    if m in ("GET", "HEAD") and path == "/health":
        st, ct, body_b = _json_resp(200, {"ok": True, "version": config.VERSION, "brand": config.BRAND})
        return st, ct, (b"" if m == "HEAD" else body_b)
    if m in ("GET", "HEAD") and path in ("/", "/app"):
        st, ct, body_b = _html_resp(200, templates.render_user_miniapp_html())
        return st, ct, (b"" if m == "HEAD" else body_b)
    if m in ("GET", "HEAD") and path == "/admin":
        st, ct, body_b = _html_resp(200, templates.render_admin_panel_html())
        return st, ct, (b"" if m == "HEAD" else body_b)
    if m == "GET" and path == "/favicon.ico":
        return 204, "image/x-icon", b""

    # 1.5) v7.7.0 MT5 Royal Bridge EA endpoints (authenticated by per-user secret bridge_key)
    if m == "GET" and path == "/api/bridge/poll":
        from fysb.services import auto_bridge as _ab
        bkey = (qs.get("key") or hdrs.get("x-bridge-key") or "").strip()
        if not _check_rate_limit("bridge_poll", bkey or "anon", 90, 60.0):
            return _json_resp(429, {"ok": False, "error": "Rate limit exceeded"})
        bal = _safe_float(qs.get("balance"), 0.0)
        eq = _safe_float(qs.get("equity"), 0.0)
        res = _ab.poll_mt5_orders(bkey, balance=bal, equity=eq)
        return _json_resp(200 if res.get("ok") else 401, res)

    if m == "POST" and path == "/api/bridge/ack":
        from fysb.services import auto_bridge as _ab
        try:
            ack_body = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            return _json_resp(400, {"ok": False, "error": "Invalid JSON"})
        if not isinstance(ack_body, dict):
            return _json_resp(400, {"ok": False, "error": "Invalid JSON object"})
        bkey = str(ack_body.get("key") or hdrs.get("x-bridge-key") or "").strip()
        res = _ab.ack_mt5_order(
            bkey,
            _safe_int(ack_body.get("order_id"), 0),
            str(ack_body.get("status") or "EXECUTED"),
            ticket=str(ack_body.get("ticket") or ""),
            detail=str(ack_body.get("detail") or ""),
            pnl_usd=_safe_float(ack_body.get("pnl_usd"), 0.0),
        )
        return _json_resp(200 if res.get("ok") else 400, res)

    # 2) Authenticated API routes require valid Telegram WebApp initData HMAC
    if path.startswith("/api/"):
        init_data = hdrs.get("x-telegram-init-data") or qs.get("initData") or ""
        principal = wa_auth.verify_init_data(init_data, bot_token=bot_token)
        if not principal:
            return _json_resp(401, {"ok": False, "error": "Unauthorized: invalid Telegram initData signature"})
        uid = int(principal["user_id"])

        payload = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                return _json_resp(400, {"ok": False, "error": "Invalid JSON body"})

        # --- ADMIN APIs (strict ADMIN_ID check) ---
        if path.startswith("/api/admin/"):
            if not principal["is_admin"]:
                return _json_resp(403, {"ok": False, "error": "Forbidden: ADMIN_ID required"})

            if m == "GET" and path == "/api/admin/overview":
                from fysb.engines.tracker import seq_of
                from fysb.ui.cards import signal_creator_label
                with db.conn() as c:
                    users_n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
                    paid_subs = c.execute(
                        "SELECT COUNT(*) n FROM subscriptions WHERE status='ACTIVE' AND plan_code!='FREE'"
                    ).fetchone()["n"]
                    open_sigs = c.execute(
                        "SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')"
                    ).fetchone()["n"]
                    raw_sigs = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 20").fetchall()]
                    cids = sorted({int(r.get("created_by") or 0) for r in raw_sigs if int(r.get("created_by") or 0) > 0})
                    sids = [int(r["id"]) for r in raw_sigs]
                    umap: dict[int, dict] = {}
                    dcnt: dict[int, int] = {}
                    deliv_cnt: dict[int, int] = {}
                    if cids:
                        ph = ",".join("?" * len(cids))
                        for u in c.execute(f"SELECT user_id, username, full_name FROM users WHERE user_id IN ({ph})", tuple(cids)).fetchall():
                            umap[int(u["user_id"])] = dict(u)
                    if sids:
                        ph = ",".join("?" * len(sids))
                        for d in c.execute(f"SELECT signal_id, COUNT(*) n FROM demo_positions WHERE signal_id IN ({ph}) GROUP BY signal_id", tuple(sids)).fetchall():
                            dcnt[int(d["signal_id"])] = int(d["n"])
                        for dl in c.execute(f"SELECT signal_id, COUNT(*) n FROM signal_deliveries WHERE signal_id IN ({ph}) GROUP BY signal_id", tuple(sids)).fetchall():
                            deliv_cnt[int(dl["signal_id"])] = int(dl["n"])
                recent_signals = []
                for r in raw_sigs:
                    sid_i = int(r["id"])
                    r["seq"] = seq_of(sid_i)
                    r["creator"] = signal_creator_label(r, umap)
                    r["demo_count"] = dcnt.get(sid_i, 0)
                    r["deliveries"] = deliv_cnt.get(sid_i, 0)
                    recent_signals.append(r)
                toggles = {k: (db.get_setting(k, "0") == "1") for k in ALLOWED_ADMIN_TOGGLES}
                from fysb.services import auto_bridge as _ab, digest as _digest, promos as _promos
                with db.conn() as c:
                    plans_list = [dict(r) for r in c.execute("SELECT code, title, price, duration_days FROM plans WHERE enabled=1 ORDER BY price ASC").fetchall()]
                return _json_resp(200, {
                    "ok": True,
                    "version": config.VERSION,
                    "kpis": {
                        "users": users_n,
                        "active_paid_subs": paid_subs,
                        "open_signals": open_sigs,
                    },
                    "toggles": toggles,
                    "recent_signals": recent_signals,
                    "pending_payments": payments.list_requests("PENDING", 15),
                    "pending_payouts": affiliates.list_payouts("PENDING", 15),
                    "chart_channels": chart_channels.list_channels(False),
                    "chart_schedule_minutes": db.get_setting_int("chart_schedule_minutes", 0),
                    "markets": chart_channels.available_markets(),
                    "leaderboard": leaderboard.compute_leaderboard("30d"),
                    "news_events": news_guard.upcoming_high_impact(48),
                    "news_feed_error": news_guard.last_feed_error(),
                    "partners_count": len(partners.list_partners()),
                    "promos": _promos.list_promos(False),
                    "stars_per_usd": _promos.stars_per_usd(),
                    "plans": plans_list,
                    "digest_7d": _digest.build_digest_data(7),
                    "bridge_policy": _ab.get_policy(),
                    "bridge_connections_count": len(_ab.list_connections(None, 200)),
                })

            if m == "GET" and path == "/api/admin/bridge/overview":
                from fysb.services import auto_bridge as _ab
                g_cid, g_csec = _ab.get_global_ctrader_app()
                return _json_resp(200, {
                    "ok": True,
                    "policy": _ab.get_policy(),
                    "ctrader_app_configured": bool(g_cid and g_csec),
                    "overrides": _ab.list_user_overrides(),
                    "connections": _ab.list_connections(None, 100),
                    "orders": _ab.list_orders(None, 40),
                })

            if m == "POST" and path == "/api/admin/bridge/ctrader-app":
                from fysb.services import auto_bridge as _ab
                res = _ab.set_global_ctrader_app(
                    str(payload.get("client_id") or ""),
                    str(payload.get("client_secret") or ""),
                )
                audit_log.log(uid, f"webapp_bridge_ctrader_app:configured={int(bool(res.get('configured')))}")
                return _json_resp(200, res)

            if m == "POST" and path == "/api/admin/bridge/policy":
                from fysb.services import auto_bridge as _ab
                pol = _ab.update_policy(
                    enabled=payload.get("enabled") if "enabled" in payload else None,
                    kill_switch=payload.get("kill_switch") if "kill_switch" in payload else None,
                    allow_live=payload.get("allow_live") if "allow_live" in payload else None,
                    access_mode=payload.get("access_mode") if "access_mode" in payload else None,
                    allowed_plans=payload.get("allowed_plans") if "allowed_plans" in payload else None,
                )
                audit_log.log(uid, f"webapp_bridge_policy:{pol['access_mode']}:en={int(pol['enabled'])}:ks={int(pol['kill_switch'])}")
                return _json_resp(200, {"ok": True, "policy": pol})

            if m == "POST" and path == "/api/admin/bridge/user-rule":
                from fysb.services import auto_bridge as _ab
                t_uid = int(payload.get("user_id") or 0)
                if t_uid <= 0:
                    return _json_resp(400, {"ok": False, "error": "user_id required"})
                res = _ab.set_user_override(
                    t_uid,
                    str(payload.get("override_mode") or "AUTO"),
                    note=str(payload.get("note") or ""),
                    actor=uid,
                )
                if res.get("ok"):
                    audit_log.log(uid, f"webapp_bridge_user_rule:{t_uid}:{res['override_mode']}")
                return _json_resp(200 if res.get("ok") else 400, res)

            if m == "POST" and path == "/api/admin/bridge/connection":
                from fysb.services import auto_bridge as _ab
                cid = int(payload.get("connection_id") or 0)
                op = str(payload.get("op") or "").lower()
                if op in ("freeze", "unfreeze"):
                    res = _ab.admin_freeze_connection(cid, frozen=(op == "freeze"))
                elif op == "toggle":
                    res = _ab.toggle_connection(cid, is_admin=True)
                elif op == "delete":
                    res = _ab.delete_connection(cid, is_admin=True)
                elif op == "test":
                    res = _ab.test_connection(cid, is_admin=True)
                elif op == "test_trade":
                    res = _ab.execute_test_trade(
                        cid,
                        symbol=str(payload.get("symbol") or "EURUSD"),
                        direction=str(payload.get("direction") or "BUY"),
                        lots=float(payload.get("lots") or 0.01),
                        is_admin=True,
                    )
                else:
                    return _json_resp(400, {"ok": False, "error": "Unsupported op"})
                return _json_resp(200 if res.get("ok") else 400, res)

            if m == "GET" and path == "/api/admin/users":
                q_str = (qs.get("q") or "").strip()
                plan_f = (qs.get("plan") or "").strip().upper()
                lim = max(1, min(100, int(qs.get("limit") or 40)))
                day_start = db.day_start_utc()
                with db.conn() as c:
                    sql = """
                        SELECT u.user_id, u.username, u.full_name, u.is_blocked, u.notifications,
                               u.joined_at, u.last_seen,
                               COALESCE(s.plan_code, 'FREE') AS plan_code,
                               COALESCE(s.ends_at, '') AS plan_expires,
                               COALESCE(da.auto_trade, 0) AS demo_auto_copy,
                               COALESCE(da.balance, 10000) AS demo_balance
                        FROM users u
                        LEFT JOIN subscriptions s ON s.user_id = u.user_id AND s.status = 'ACTIVE'
                        LEFT JOIN demo_accounts da ON da.user_id = u.user_id
                        WHERE u.user_id != ?
                    """
                    args: list = [int(config.ADMIN_ID or 0)]
                    if q_str:
                        q_clean = q_str.lstrip("@")
                        like = f"%{q_clean}%"
                        sql += " AND (CAST(u.user_id AS TEXT) LIKE ? OR LOWER(COALESCE(u.username,'')) LIKE LOWER(?) OR LOWER(COALESCE(u.full_name,'')) LIKE LOWER(?))"
                        args.extend([like, like, like])
                    if plan_f and plan_f != "ALL":
                        sql += " AND COALESCE(s.plan_code, 'FREE') = ?"
                        args.append(plan_f)
                    sql += " ORDER BY u.joined_at DESC LIMIT ?"
                    args.append(lim)
                    rows = [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]
                    uids = [int(r["user_id"]) for r in rows]
                    used_map: dict[int, int] = {}
                    paid_map: dict[int, float] = {}
                    br_ov_map: dict[int, str] = {}
                    br_cnt_map: dict[int, int] = {}
                    if uids:
                        ph = ",".join("?" * len(uids))
                        for rr in c.execute(
                            f"SELECT user_id, COUNT(*) n FROM signal_deliveries WHERE user_id IN ({ph}) AND delivered_at>=? GROUP BY user_id",
                            tuple(uids) + (day_start,),
                        ).fetchall():
                            used_map[int(rr["user_id"])] = int(rr["n"])
                        for rr in c.execute(
                            f"SELECT user_id, COALESCE(SUM(amount),0) s FROM payments WHERE user_id IN ({ph}) AND status='APPROVED' GROUP BY user_id",
                            tuple(uids),
                        ).fetchall():
                            paid_map[int(rr["user_id"])] = round(float(rr["s"] or 0.0), 2)
                        for rr in c.execute(
                            f"SELECT user_id, override_mode FROM bridge_user_rules WHERE user_id IN ({ph})",
                            tuple(uids),
                        ).fetchall():
                            br_ov_map[int(rr["user_id"])] = str(rr["override_mode"] or "AUTO").upper()
                        for rr in c.execute(
                            f"SELECT user_id, COUNT(*) n FROM bridge_connections WHERE user_id IN ({ph}) GROUP BY user_id",
                            tuple(uids),
                        ).fetchall():
                            br_cnt_map[int(rr["user_id"])] = int(rr["n"])
                for r in rows:
                    u_i = int(r["user_id"])
                    r["used_today"] = used_map.get(u_i, 0)
                    r["total_paid_usd"] = paid_map.get(u_i, 0.0)
                    r["auto_signals"] = bool(r.get("notifications"))
                    r["demo_auto_copy"] = bool(r.get("demo_auto_copy"))
                    r["is_blocked"] = bool(r.get("is_blocked"))
                    r["bridge_override"] = br_ov_map.get(u_i, "AUTO")
                    r["bridge_connections"] = br_cnt_map.get(u_i, 0)
                return _json_resp(200, {"ok": True, "users": rows, "count": len(rows)})

            if m == "POST" and path == "/api/admin/user-action":
                target_uid = int(payload.get("user_id") or 0)
                op = str(payload.get("op") or "").lower()
                if target_uid <= 0:
                    return _json_resp(400, {"ok": False, "error": "user_id required"})
                if op == "set_plan":
                    plan_code = str(payload.get("plan_code") or "VIP").strip().upper()
                    days = max(1, min(3650, int(payload.get("days") or 30)))
                    res = subscriptions.grant_subscription(
                        target_uid, plan_code, source="ADMIN_WEBAPP", actor=uid, override_days=days
                    )
                    audit_log.log(uid, f"webapp_user_plan:{target_uid}:{plan_code}:{days}d")
                    return _json_resp(200, {"ok": True, "user_id": target_uid, "plan_code": plan_code, "days": days, "result": res})
                if op in ("ban", "unban"):
                    blk = 1 if op == "ban" else 0
                    with db.tx() as c:
                        c.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (blk, target_uid))
                    audit_log.log(uid, f"webapp_user_{op}:{target_uid}")
                    return _json_resp(200, {"ok": True, "user_id": target_uid, "is_blocked": bool(blk)})
                if op == "reset_quota":
                    day_start = db.day_start_utc()
                    with db.tx() as c:
                        c.execute("DELETE FROM signal_deliveries WHERE user_id=? AND delivered_at>=?", (target_uid, day_start))
                        c.execute("DELETE FROM analyses WHERE user_id=? AND created_at>=?", (target_uid, day_start))
                    audit_log.log(uid, f"webapp_user_reset_quota:{target_uid}")
                    return _json_resp(200, {"ok": True, "user_id": target_uid, "reset": True})
                if op == "dm":
                    msg_txt = str(payload.get("message") or "").strip()
                    if not msg_txt:
                        return _json_resp(400, {"ok": False, "error": "message required"})
                    bot_obj = _BOT_RUNTIME.get("bot")
                    loop_obj = _BOT_RUNTIME.get("loop")
                    dispatched = False
                    if bot_obj is not None and loop_obj is not None and loop_obj.is_running():
                        from fysb.theme import esc
                        asyncio.run_coroutine_threadsafe(
                            bot_obj.send_message(
                                chat_id=target_uid,
                                text=f"👑 <b>رسالة مباشرة من إدارة {esc(config.BRAND)}</b>\n━━━━━━━━━━━━━━━━━━━━━\n{esc(msg_txt)}",
                                parse_mode="HTML",
                            ),
                            loop_obj,
                        )
                        dispatched = True
                    audit_log.log(uid, f"webapp_user_dm:{target_uid}:sent={int(dispatched)}")
                    return _json_resp(200, {"ok": True, "user_id": target_uid, "dispatched": dispatched})
                return _json_resp(400, {"ok": False, "error": "Unsupported user action"})

            if m == "GET" and path == "/api/admin/promos":
                from fysb.services import promos as _promos
                return _json_resp(200, {"ok": True, "promos": _promos.list_promos(False), "stars_per_usd": _promos.stars_per_usd()})

            if m == "POST" and path == "/api/admin/promo":
                from fysb.services import promos as _promos
                op = str(payload.get("op") or "create").lower()
                if op == "create":
                    res = _promos.create_promo(
                        str(payload.get("code") or ""),
                        int(payload.get("discount_pct") or 15),
                        max_uses=int(payload.get("max_uses") or 100),
                        expires_days=int(payload.get("expires_days") or 30),
                        created_by=uid,
                    )
                    if res.get("ok"):
                        audit_log.log(uid, f"webapp_promo_create:{res['code']}:{res['discount_pct']}%")
                    return _json_resp(200 if res.get("ok") else 400, res)
                if op == "toggle":
                    ok_t = _promos.toggle_promo(str(payload.get("code") or ""))
                    return _json_resp(200 if ok_t else 404, {"ok": ok_t})
                if op == "delete":
                    ok_d = _promos.delete_promo(str(payload.get("code") or ""))
                    return _json_resp(200 if ok_d else 404, {"ok": ok_d})
                return _json_resp(400, {"ok": False, "error": "Unsupported promo op"})

            if m == "GET" and path == "/api/admin/digest-card":
                from fysb.services import digest as _digest
                days = max(1, min(90, int(qs.get("days") or 7)))
                _, _, png = _digest.build_digest_bundle(days=days)
                if not png:
                    return _json_resp(503, {"ok": False, "error": "Pillow unavailable"})
                return _png_resp(png)

            if m == "POST" and path == "/api/admin/digest":
                from fysb.services import digest as _digest
                days = max(1, min(90, int(payload.get("days") or 7)))
                include_ch = bool(payload.get("include_channels", True))
                d_stats = _digest.build_digest_data(days=days)
                bot_obj = _BOT_RUNTIME.get("bot")
                loop_obj = _BOT_RUNTIME.get("loop")
                dispatched = False
                if bot_obj is not None and loop_obj is not None and loop_obj.is_running():
                    asyncio.run_coroutine_threadsafe(
                        _digest.publish_digest(bot_obj, days=days, include_channels=include_ch, notify_admin=True),
                        loop_obj,
                    )
                    dispatched = True
                audit_log.log(uid, f"webapp_digest:{days}d:dispatched={int(dispatched)}")
                return _json_resp(200, {"ok": True, "days": days, "dispatched": dispatched, "stats": d_stats})

            if m == "GET" and path == "/api/admin/quote":
                sym = (qs.get("symbol") or "XAUUSD").strip().upper()
                with db.conn() as c:
                    mrow = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (sym,)).fetchone()
                psym = mrow["provider_symbol"] if mrow else sym
                px = market_data.cached_price(sym, max_age_seconds=60.0)
                if px <= 0:
                    try:
                        q_px = market_data.fetch_price(psym, config.market_api_key())
                        px = float((q_px or {}).get("price") or 0.0)
                    except Exception:
                        px = 0.0
                if px <= 0:
                    return _json_resp(503, {"ok": False, "error": "السعر اللحظي غير متاح الآن"})
                step = round(px * 0.0025, 5 if px < 10 else 2)
                return _json_resp(200, {
                    "ok": True,
                    "symbol": sym,
                    "price": px,
                    "suggested_buy": {
                        "entry": px,
                        "sl": round(px - step, 5 if px < 10 else 2),
                        "tp1": round(px + step * 1.2, 5 if px < 10 else 2),
                        "tp2": round(px + step * 2.2, 5 if px < 10 else 2),
                        "tp3": round(px + step * 3.5, 5 if px < 10 else 2),
                    },
                    "suggested_sell": {
                        "entry": px,
                        "sl": round(px + step, 5 if px < 10 else 2),
                        "tp1": round(px - step * 1.2, 5 if px < 10 else 2),
                        "tp2": round(px - step * 2.2, 5 if px < 10 else 2),
                        "tp3": round(px - step * 3.5, 5 if px < 10 else 2),
                    },
                })

            if m == "POST" and path == "/api/admin/publish-signal":
                from fysb.engines import demo as demo_engine
                from fysb.engines.tracker import seq_of
                sym = str(payload.get("symbol") or "").strip().upper()
                direction = str(payload.get("direction") or "BUY").strip().upper()
                if direction not in ("BUY", "SELL"):
                    return _json_resp(400, {"ok": False, "error": "direction must be BUY or SELL"})
                try:
                    entry = float(payload.get("entry") or 0)
                    sl = float(payload.get("sl") or 0)
                    tp1 = float(payload.get("tp1") or 0)
                    tp2 = float(payload.get("tp2") or 0)
                    tp3 = float(payload.get("tp3") or 0)
                except (TypeError, ValueError):
                    return _json_resp(400, {"ok": False, "error": "أسعار غير صالحة"})
                if not sym or entry <= 0 or sl <= 0 or tp1 <= 0:
                    return _json_resp(400, {"ok": False, "error": "الرمز وسعر الدخول والوقف والهدف الأول مطلوبة"})
                if direction == "BUY" and (sl >= entry or tp1 <= entry):
                    return _json_resp(400, {"ok": False, "error": "في الشراء يجب أن يكون الوقف أسفل الدخول والهدف أعلاه"})
                if direction == "SELL" and (sl <= entry or tp1 >= entry):
                    return _json_resp(400, {"ok": False, "error": "في البيع يجب أن يكون الوقف أعلى الدخول والهدف أسفله"})
                tf = str(payload.get("timeframe") or "M15").strip().upper()
                strat = str(payload.get("strategy") or "MANUAL").strip().upper()
                reason = str(payload.get("reason") or "إشارة احترافية من استوديو الإدارة").strip()[:300]
                score = max(50, min(100, int(payload.get("score") or 90)))
                risk_dist = abs(entry - sl)
                reward_dist = abs((tp3 or tp2 or tp1) - entry)
                rr = round(reward_dist / max(1e-9, risk_dist), 2)
                targets_str = ",".join(f"{x:g}" for x in (tp1, tp2, tp3) if x > 0)
                now_iso = config.now()
                with db.tx() as c:
                    cur = c.execute(
                        """INSERT INTO signals(
                            symbol, direction, entry, stop, tp1, tp2, tp3, targets, rr, score,
                            strategy, timeframe, reason, source, status, created_by, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'ADMIN_WEBAPP','ACTIVE',?,?)""",
                        (sym, direction, entry, sl, tp1, tp2, tp3, targets_str, rr, score,
                         strat, tf, reason, uid, now_iso),
                    )
                    sid = int(cur.lastrowid)
                    sig_row = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())
                copied = demo_engine.auto_copy_signal_for_subscribers(sig_row, config.market_api_key())
                from fysb.services import auto_bridge as _ab
                br_res = _ab.dispatch_signal_to_bridges(sig_row)
                bot_obj = _BOT_RUNTIME.get("bot")
                loop_obj = _BOT_RUNTIME.get("loop")
                broadcasted = False
                if bool(payload.get("broadcast", True)) and bot_obj is not None and loop_obj is not None and loop_obj.is_running():
                    from fysb.engines.signal_engine import emit_to_users
                    async def _send_fn(t_uid: int, txt: str, markup=None):
                        return await bot_obj.send_message(chat_id=t_uid, text=txt, reply_markup=markup, parse_mode="HTML")
                    asyncio.run_coroutine_threadsafe(
                        emit_to_users(sig_row, _send_fn, bot=bot_obj, include_channels=True),
                        loop_obj,
                    )
                    broadcasted = True
                audit_log.log(uid, f"webapp_manual_signal:{sid}:{sym}:{direction}")
                sig_row["seq"] = seq_of(sid)
                return _json_resp(200, {
                    "ok": True,
                    "signal": sig_row,
                    "auto_copied_count": len(copied),
                    "bridge_queued_count": int(br_res.get("queued") or 0),
                    "broadcasted": broadcasted,
                })

            if m == "POST" and path == "/api/admin/signal":
                sid = _safe_int(payload.get("signal_id"), 0)
                op = str(payload.get("op") or "").lower()
                if op == "cancel" and sid > 0:
                    with db.tx() as c:
                        sig_row = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
                        c.execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=?", (config.now(), sid))
                        delivs = [dict(r) for r in c.execute(
                            "SELECT DISTINCT user_id, chat_id FROM signal_deliveries WHERE signal_id=?", (sid,)
                        ).fetchall()]
                    try:
                        from fysb.services import auto_bridge as _ab
                        _ab.mark_signal_bridge_orders_closed(sid)
                    except Exception:
                        pass
                    # v8.0.0: Notify holders when Admin cancels a signal from the Web Panel
                    bot_obj = _BOT_RUNTIME.get("bot")
                    loop_obj = _BOT_RUNTIME.get("loop")
                    notified = 0
                    if sig_row and delivs and bot_obj is not None and loop_obj is not None and loop_obj.is_running():
                        from fysb.engines.tracker import seq_of
                        sym_txt = sig_row["symbol"]
                        seq_n = seq_of(sid)
                        note_txt = (
                            f"🚫 <b>تم إلغاء الإشارة #{seq_n} ({sym_txt}) من غرفة الإدارة</b>\n"
                            "<i>يرجى إلغاء أي أمر معلق أو مراجعة الصفقة المفتوحة فوراً.</i>"
                        )
                        async def _notify_cancel():
                            seen = set()
                            for d in delivs:
                                tgt = d.get("chat_id") or d.get("user_id")
                                if not tgt or str(tgt) in seen:
                                    continue
                                seen.add(str(tgt))
                                try:
                                    await bot_obj.send_message(tgt, note_txt, parse_mode="HTML")
                                except Exception:
                                    pass
                        asyncio.run_coroutine_threadsafe(_notify_cancel(), loop_obj)
                        notified = len(delivs)
                    audit_log.log(uid, f"webapp_signal_cancel:{sid}:notified={notified}")
                    return _json_resp(200, {"ok": True, "signal_id": sid, "status": "CANCELLED", "notified": notified})
                return _json_resp(400, {"ok": False, "error": "Unsupported signal op"})

            if m == "POST" and path == "/api/admin/chart-channel":
                op = str(payload.get("op") or "").lower()
                if op == "add":
                    chat_id = str(payload.get("chat_id") or "").strip()
                    title = str(payload.get("title") or "").strip()
                    mkts = str(payload.get("markets") or "ALL").strip()
                    if not chat_id:
                        return _json_resp(400, {"ok": False, "error": "chat_id required"})
                    cid = chart_channels.add_channel(chat_id, title, mkts)
                    audit_log.log(uid, f"webapp_chart_channel_add:{cid}:{chat_id}")
                    return _json_resp(200, {"ok": True, "id": cid, "channels": chart_channels.list_channels(False)})
                if op == "toggle":
                    cid = int(payload.get("id") or 0)
                    en = chart_channels.toggle_channel(cid)
                    return _json_resp(200, {"ok": True, "id": cid, "enabled": en})
                if op == "markets":
                    cid = int(payload.get("id") or 0)
                    mkts = chart_channels.set_markets(cid, str(payload.get("markets") or "ALL"))
                    return _json_resp(200, {"ok": True, "id": cid, "markets": mkts})
                if op == "delete":
                    cid = int(payload.get("id") or 0)
                    ok_del = chart_channels.delete_channel(cid)
                    return _json_resp(200 if ok_del else 404, {"ok": ok_del, "id": cid})
                return _json_resp(400, {"ok": False, "error": "Unsupported op"})

            if m == "POST" and path == "/api/admin/toggle":
                key = str(payload.get("key") or "").strip()
                if key not in ALLOWED_ADMIN_TOGGLES:
                    return _json_resp(400, {"ok": False, "error": "Unsupported toggle key"})
                cur = db.get_setting(key, "0") == "1"
                nxt = "0" if cur else "1"
                db.set_setting(key, nxt)
                audit_log.log(uid, f"webapp_toggle:{key}={nxt}")
                return _json_resp(200, {"ok": True, "key": key, "enabled": nxt == "1"})

            if m == "POST" and path == "/api/admin/payment":
                pid = int(payload.get("payment_id") or 0)
                dec = str(payload.get("decision") or "").lower()
                if dec == "approve":
                    res = payments.approve_request(pid, actor=uid)
                elif dec == "reject":
                    res = payments.reject_request(pid, "Rejected via Admin Web Panel", actor=uid)
                else:
                    return _json_resp(400, {"ok": False, "error": "decision must be approve|reject"})
                return _json_resp(200 if res.get("ok") else 400, res)

            if m == "POST" and path == "/api/admin/payout":
                pid = _safe_int(payload.get("payout_id"), 0)
                dec = str(payload.get("decision") or "").strip().lower()
                if dec not in ("approve", "reject"):
                    return _json_resp(400, {"ok": False, "error": "decision must be approve|reject"})
                row = affiliates.approve_payout(pid, "Admin Web Panel") if dec == "approve" else affiliates.reject_payout(pid, "Admin Web Panel")
                if not row:
                    return _json_resp(404, {"ok": False, "error": "Pending payout not found"})
                audit_log.log(uid, f"webapp_payout_{dec}:{pid}")
                return _json_resp(200, {"ok": True, "payout_id": pid, "decision": dec})

            if m == "POST" and path == "/api/admin/news-broadcast":
                card = news_guard.render_luxury_news_digest_card(force_refresh=True)
                dests = news_guard.broadcast_destinations()
                bot_obj = _BOT_RUNTIME.get("bot")
                loop_obj = _BOT_RUNTIME.get("loop")
                dispatched = False
                if bot_obj is not None and loop_obj is not None and loop_obj.is_running():
                    asyncio.run_coroutine_threadsafe(
                        news_guard.broadcast_news_to_targets(bot_obj, card, include_admin=True),
                        loop_obj,
                    )
                    dispatched = True
                audit_log.log(uid, f"webapp_news_broadcast:dispatched={int(dispatched)}")
                return _json_resp(200, {
                    "ok": True,
                    "dispatched": dispatched,
                    "destinations": dests,
                    "events_count": len(news_guard.upcoming_high_impact(48)),
                })

            if m == "POST" and path == "/api/admin/gift":
                try:
                    res = gifts.create_gift_code(
                        str(payload.get("target_plan") or "VIP"),
                        int(payload.get("duration_days") or 30),
                        max_uses=int(payload.get("max_uses") or 1),
                        note=str(payload.get("note") or ""),
                        created_by=uid,
                    )
                    audit_log.log(uid, f"webapp_gift:{res['code']}")
                    return _json_resp(200, {"ok": True, **res})
                except Exception as exc:
                    return _json_resp(400, {"ok": False, "error": str(exc)})

            return _json_resp(404, {"ok": False, "error": "Unknown admin endpoint"})

        # --- USER APIs ---
        if m == "GET" and path == "/api/me":
            from fysb.engines import demo as demo_engine
            plan = dict(subscriptions.plan_for(uid))
            day_start = db.day_start_utc()
            with db.conn() as c:
                used = c.execute(
                    "SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                    (uid, day_start),
                ).fetchone()["n"]
                urow = c.execute("SELECT notifications, full_name, username FROM users WHERE user_id=?", (uid,)).fetchone()
            auto_sig_on = bool(urow["notifications"]) if urow else True
            try:
                demo_ov = demo_engine.account_overview(uid, config.market_api_key())
            except Exception:
                acc = demo_engine.get_or_create_account(uid)
                demo_ov = {
                    "balance": float(acc.get("balance") or 10000.0),
                    "equity": float(acc.get("balance") or 10000.0),
                    "floating": 0.0,
                    "drawdown_pct": 0.0,
                    "open": [],
                    "closed": [],
                    "win_rate": None,
                    "settings": demo_engine.demo_settings(uid),
                }
            lim = int(plan.get("max_signals_per_day") or 0)
            aff_data = affiliates.affiliate_stats(uid)
            try:
                bot_uname = referrals.bot_username()
            except Exception:
                bot_uname = config.BOT_SIGNATURE.lstrip("@")
            aff_data["referral_link"] = f"https://t.me/{bot_uname}?start=aff{uid}"

            # v7.4.0: Full Channel Partner Studio payload for registered partners
            p_row = partners.get_partner_by_user(uid)
            if p_row and int(p_row.get("enabled") or 0) == 1:
                p_stats = partners.partner_stats(p_row["id"])
                p_chans = [
                    {
                        "id": int(ch["id"]),
                        "title": ch.get("title") or ch.get("chat_id"),
                        "chat_id": ch.get("chat_id") or "",
                        "enabled": bool(ch.get("enabled")),
                        "markets": ch.get("markets") or "ALL",
                        "post_mode": ch.get("post_mode") or p_row.get("post_mode") or "teaser",
                        "is_vip_gate": bool(ch.get("is_vip_gate")),
                        "auto_signals": bool(ch.get("auto_signals")),
                        "auto_analysis": bool(ch.get("auto_analysis")),
                    }
                    for ch in partners.partner_channels(p_row["id"])
                ]
                partner_payload = {
                    "is_channel_partner": True,
                    "id": int(p_row["id"]),
                    "title": p_row.get("title") or "",
                    "brand_label": p_row.get("brand_label") or p_row.get("title") or "",
                    "commission_pct": float(p_row.get("commission_pct") or 0.0),
                    "post_mode": p_row.get("post_mode") or "teaser",
                    "post_mode_ar": partners.POST_MODE_SHORT.get(p_row.get("post_mode") or "teaser", "تشويقي"),
                    "campaign_code": p_row.get("campaign_code") or "",
                    "referral_link": partners.partner_link(p_row.get("campaign_code") or ""),
                    "perm_vip_gatekeeper": bool(p_row.get("perm_vip_gatekeeper")),
                    "stats": p_stats,
                    "manual_quota": partners.manual_quota_status(p_row["id"], p_row),
                    "channels": p_chans,
                }
            else:
                partner_payload = {"is_channel_partner": False}

            from fysb.services import auto_bridge as _ab
            return _json_resp(200, {
                "ok": True,
                "user_id": uid,
                "is_admin": principal["is_admin"],
                "bridge_visible": _ab.is_bridge_visible_to_user(uid),
                "auto_signals": auto_sig_on,
                "plan": {"code": plan.get("code", "FREE"), "title": plan.get("title", "FREE")},
                "quota": {"used": used, "limit": lim},
                "demo": {
                    "balance": demo_ov["balance"],
                    "equity": demo_ov["equity"],
                    "floating": demo_ov["floating"],
                    "drawdown_pct": demo_ov["drawdown_pct"],
                    "open_count": len(demo_ov.get("open") or []),
                    "win_rate": demo_ov.get("win_rate"),
                    "auto_trade": bool((demo_ov.get("settings") or {}).get("auto_trade")),
                },
                "risk": risk_journal.get_risk_profile(uid),
                "drawdown": risk_journal.daily_drawdown_status(uid),
                "journal": risk_journal.journal_summary(uid, 30),
                "academy": {
                    k: v for k, v in academy.user_progress(uid).items() if k != "completed_codes"
                },
                "affiliate": aff_data,
                "partner": partner_payload,
                "markets": chart_channels.available_markets(),
            })

        if m == "POST" and path == "/api/user/auto-signals":
            from fysb.services import users as users_svc
            with db.conn() as c:
                r = c.execute("SELECT notifications FROM users WHERE user_id=?", (uid,)).fetchone()
            cur = bool(r["notifications"]) if r else True
            nxt = (not cur) if payload.get("enabled") is None else bool(payload.get("enabled"))
            users_svc.set_notifications(uid, nxt)
            return _json_resp(200, {"ok": True, "auto_signals": nxt})

        if m == "GET" and path == "/api/demo/overview":
            from fysb.engines import demo as demo_engine
            ov = demo_engine.account_overview(uid, config.market_api_key())
            return _json_resp(200, {"ok": True, **ov})

        if m == "POST" and path == "/api/demo/open-signal":
            from fysb.engines import demo as demo_engine
            from fysb.engines.tracker import seq_of
            sid = _safe_int(payload.get("signal_id"), 0)
            with db.conn() as c:
                sig_check = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
            if not sig_check:
                return _json_resp(404, {"ok": False, "error": "الإشارة غير موجودة"})
            if not _user_can_access_signal(uid, principal["is_admin"], dict(sig_check)):
                return _json_resp(403, {"ok": False, "error": "⛔ هذه الإشارة غير مشمولة في باقتك أو خاصة بمستخدم آخر"})
            lots_raw = payload.get("lots")
            lots_ov = _safe_float(lots_raw, 0.0) if lots_raw not in (None, "") else None
            if lots_ov is not None and lots_ov <= 0:
                lots_ov = None
            res = demo_engine.open_signal_interactive(uid, sid, config.market_api_key(), lots_override=lots_ov)
            if not res.get("ok"):
                return _json_resp(400, res)
            sig = dict(res["signal"])
            sig["seq"] = seq_of(int(sig["id"]))
            ov = demo_engine.account_overview(uid, config.market_api_key())
            return _json_resp(200, {
                "ok": True,
                "already_open": bool(res.get("already_open")),
                "position_id": int(res["position_id"]),
                "position": res["position"],
                "signal": sig,
                "overview": ov,
            })

        if m == "POST" and path == "/api/demo/close":
            from fysb.engines import demo as demo_engine
            pid = int(payload.get("position_id") or 0)
            with db.conn() as c:
                pos = c.execute("SELECT * FROM demo_positions WHERE id=? AND user_id=? AND status='OPEN'", (pid, uid)).fetchone()
            if not pos:
                return _json_resp(404, {"ok": False, "error": "الصفقة المفتوحة غير موجودة"})
            px = float(pos["entry"])
            try:
                q_px = market_data.fetch_price(pos["symbol"], config.market_api_key())
                if q_px and float(q_px.get("price") or 0) > 0:
                    px = float(q_px["price"])
            except Exception:
                pass
            res = demo_engine.close_at_price(uid, pid, px, "إغلاق يدوي من التطبيق")
            ov = demo_engine.account_overview(uid, config.market_api_key())
            return _json_resp(200 if res.get("ok") else 400, {**res, "overview": ov})

        if m == "POST" and path == "/api/demo/breakeven":
            from fysb.engines import demo as demo_engine
            pid = int(payload.get("position_id") or 0)
            with db.tx() as c:
                pos = c.execute("SELECT * FROM demo_positions WHERE id=? AND user_id=? AND status='OPEN'", (pid, uid)).fetchone()
                if not pos:
                    return _json_resp(404, {"ok": False, "error": "الصفقة المفتوحة غير موجودة"})
                c.execute("UPDATE demo_positions SET sl=? WHERE id=?", (float(pos["entry"]), pid))
            ov = demo_engine.account_overview(uid, config.market_api_key())
            return _json_resp(200, {"ok": True, "position_id": pid, "sl": float(pos["entry"]), "overview": ov})

        if m == "POST" and path == "/api/demo/auto-copy":
            from fysb.engines import demo as demo_engine
            cur = demo_engine.demo_settings(uid)
            if "auto_trade" in payload:
                demo_engine.set_auto_trade(uid, bool(payload.get("auto_trade")))
            elif "partial_tp_be" not in payload:
                demo_engine.set_auto_trade(uid, not cur["auto_trade"])
            if "partial_tp_be" in payload:
                demo_engine.set_partial_tp_be(uid, bool(payload.get("partial_tp_be")))
            ov = demo_engine.account_overview(uid, config.market_api_key())
            return _json_resp(200, {"ok": True, "settings": ov["settings"], "overview": ov})

        if m == "POST" and path == "/api/signal-now":
            from fysb.engines import signal_engine
            from fysb.engines.tracker import seq_of
            if not _check_rate_limit("signal_now", str(uid), 12, 60.0):
                return _json_resp(429, {"ok": False, "error": "⏳ طلبات كثيرة خلال دقيقة — انتظر لحظات ثم أعد المحاولة"})
            sym = instruments.normalize(str(payload.get("symbol") or "XAUUSD"))
            tf = str(payload.get("timeframe") or "M15").strip().upper()
            strat = str(payload.get("strategy") or "AUTO").strip().upper()
            plan = dict(subscriptions.plan_for(uid))
            if not principal["is_admin"]:
                if not _plan_allows_symbol(plan, sym):
                    return _json_resp(403, {"ok": False, "error": f"⛔ السوق {sym} غير مشمول في باقتك — رقِّ باقتك لفتحه"})
                if not _plan_allows_tf(plan, tf):
                    return _json_resp(403, {"ok": False, "error": f"⛔ الإطار الزمني {tf} غير مشمول في باقتك"})
                if strat not in ("", "AUTO", "ALL"):
                    p_strats = {x.strip().upper() for x in str(plan.get("strategies") or "").split(",") if x.strip()}
                    if p_strats and "ALL" not in p_strats and strat not in p_strats:
                        return _json_resp(403, {"ok": False, "error": f"⛔ استراتيجية {strat} غير مشمولة في باقتك"})
            max_day = int(plan.get("max_signals_per_day") or 3)
            day_start = db.day_start_utc()
            with db.conn() as c:
                used = c.execute(
                    "SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                    (uid, day_start),
                ).fetchone()["n"]
            if not principal["is_admin"] and max_day > 0 and used >= max_day:
                return _json_resp(403, {"ok": False, "error": f"استهلكت حدك اليومي ({used}/{max_day}) — رقِّ باقتك لفتح إشارات غير محدودة"})
            # 1) Try live engine scan for this symbol + strategy (fixed v8.0.0: reads `signal_id`!)
            gen_sig = None
            only_s = None if strat in ("", "AUTO", "ALL") else strat
            sig_src = "ADMIN_SIGNAL_NOW" if principal["is_admin"] else "SIGNAL_NOW"
            try:
                res_gen = signal_engine.generate_signal(
                    sym,
                    entry_tf=tf,
                    source=sig_src,
                    created_by=uid,
                    force=False,
                    only_strategy=only_s,
                )
                new_sid = int((res_gen or {}).get("signal_id") or 0) if isinstance(res_gen, dict) else 0
                if new_sid > 0:
                    with db.conn() as c:
                        row_new = c.execute("SELECT * FROM signals WHERE id=?", (new_sid,)).fetchone()
                        if row_new:
                            gen_sig = dict(row_new)
            except Exception as exc:
                log.debug("webapp signal-now generate_signal fallback: %s", exc)
            # 2) Safe fallback: latest open PUBLIC signal on the requested symbol (NEVER leaks private SIGNAL_NOW or out-of-plan VIP!)
            if not gen_sig:
                with db.conn() as c:
                    cand = c.execute(
                        """SELECT * FROM signals
                           WHERE REPLACE(UPPER(symbol),'/','')=?
                             AND (source IS NULL OR source != 'SIGNAL_NOW' OR created_by=?)
                             AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                             AND (closed_at IS NULL OR closed_at='')
                           ORDER BY id DESC LIMIT 1""",
                        (sym, uid),
                    ).fetchone()
                    if cand:
                        gen_sig = dict(cand)
            if not gen_sig:
                return _json_resp(200, {
                    "ok": False,
                    "error": f"لا توجد فرصة مطابقة لشروط الجودة الصارمة على {sym} ({tf}) في هذه اللحظة — جرّب رمزاً أو فريم آخر.",
                })
            sid = int(gen_sig["id"])
            with db.tx() as c:
                exists = c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?", (uid, sid)).fetchone()
                if not exists:
                    c.execute(
                        "INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                        (sid, uid, config.now()),
                    )
            gen_sig["seq"] = seq_of(sid)
            return _json_resp(200, {"ok": True, "signal": gen_sig})

        if m == "GET" and path == "/api/share-card/signal":
            from fysb.ui import charts as _charts, share_cards as _sc
            sid = _safe_int(qs.get("id"), 0)
            with db.conn() as c:
                sig = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
                if not sig:
                    return _json_resp(404, {"ok": False, "error": "Signal not found"})
                sig_d = dict(sig)
                if not _user_can_access_signal(uid, principal["is_admin"], sig_d):
                    return _json_resp(403, {"ok": False, "error": "Forbidden: signal not accessible for your plan"})
                mrow = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (sig_d["symbol"],)).fetchone()
            metrics = _charts.live_signal_metrics(sig_d, row=dict(mrow) if mrow else None)
            bn = (config.BRAND or "ForexYemeniBot").lstrip("@")
            png = _sc.render_pnl_share_card({
                "kind": "signal",
                "symbol": sig_d["symbol"],
                "direction": sig_d["direction"],
                "strategy": sig_d.get("strategy") or "ROYAL",
                "timeframe": sig_d.get("timeframe") or "M15",
                "entry": float(sig_d.get("entry") or 0),
                "exit_price": float(metrics.get("live_px") or sig_d.get("tp1") or sig_d.get("entry") or 0),
                "tp3": float(sig_d.get("tp3") or sig_d.get("tp1") or 0),
                "tp_label": str(sig_d.get("status") or "ACTIVE").upper(),
                "pips": float(metrics.get("pips") or sig_d.get("pips_result") or 0),
                "rr": float(sig_d.get("rr") or 0),
                "score": int(sig_d.get("score") or 0),
                "ref_link": f"t.me/{bn}?start=ref_{uid}",
                "ref_label": f"VERIFIED BY {config.BRAND} • REF #{uid}",
                "timestamp": str(sig_d.get("closed_at") or sig_d.get("created_at") or config.now()),
            })
            if not png:
                return _json_resp(503, {"ok": False, "error": "Pillow unavailable"})
            return _png_resp(png)

        if m == "GET" and path == "/api/share-card/demo":
            from fysb.engines import demo as demo_engine
            from fysb.ui import share_cards as _sc
            pid = int(qs.get("id") or 0)
            p_data = demo_engine.share_card_payload_for_position(uid, pid, config.BRAND)
            if not p_data:
                return _json_resp(404, {"ok": False, "error": "Demo position not found"})
            png = _sc.render_pnl_share_card(p_data)
            if not png:
                return _json_resp(503, {"ok": False, "error": "Pillow unavailable"})
            return _png_resp(png)

        # --- v7.7.0 USER ROYAL AUTO-BRIDGE APIs ---
        if m == "GET" and path == "/api/bridge/overview":
            from fysb.services import auto_bridge as _ab
            visible = _ab.is_bridge_visible_to_user(uid)
            if not visible:
                return _json_resp(200, {
                    "ok": True,
                    "bridge_visible": False,
                    "can_copy_demo": False,
                    "can_copy_live": False,
                    "connections": [],
                    "orders": [],
                })
            can_demo, reason_demo = _ab.user_can_copy(uid, "DEMO")
            can_live, reason_live = _ab.user_can_copy(uid, "LIVE")
            return _json_resp(200, {
                "ok": True,
                "bridge_visible": True,
                "can_copy_demo": can_demo,
                "reason_demo": reason_demo,
                "can_copy_live": can_live,
                "reason_live": reason_live,
                "policy": _ab.get_policy(),
                "platforms": {k: {"name": v["name"], "badge": v["badge"], "desc": v["desc"]} for k, v in _ab.PLATFORMS.items()},
                "connections": _ab.list_connections(uid, 20),
                "orders": _ab.list_orders(uid, 25),
                "server_url": config.WEBAPP_URL or "",
            })

        if m == "POST" and path == "/api/bridge/connect":
            from fysb.services import auto_bridge as _ab
            res = _ab.save_connection(
                uid,
                str(payload.get("platform") or ""),
                str(payload.get("account_mode") or "DEMO"),
                label=str(payload.get("label") or ""),
                creds=payload.get("creds") if isinstance(payload.get("creds"), dict) else {},
                risk_pct=float(payload.get("risk_pct") or 1.0),
                lot_mode=str(payload.get("lot_mode") or "auto"),
                fixed_lot=float(payload.get("fixed_lot") or 0.01),
                max_daily_dd_pct=float(payload.get("max_daily_dd_pct") or 3.0),
                max_open_trades=int(payload.get("max_open_trades") or 3),
                partial_tp_be=bool(payload.get("partial_tp_be", True)),
                markets=str(payload.get("markets") or "ALL"),
                symbol_suffix=str(payload.get("symbol_suffix") or ""),
            )
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "POST" and path == "/api/bridge/test":
            from fysb.services import auto_bridge as _ab
            res = _ab.test_connection(int(payload.get("connection_id") or 0), user_id=uid, is_admin=principal["is_admin"])
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "POST" and path == "/api/bridge/test-trade":
            from fysb.services import auto_bridge as _ab
            res = _ab.execute_test_trade(
                int(payload.get("connection_id") or 0),
                symbol=str(payload.get("symbol") or "EURUSD"),
                direction=str(payload.get("direction") or "BUY"),
                lots=float(payload.get("lots") or 0.01),
                user_id=uid,
                is_admin=principal["is_admin"],
            )
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "POST" and path == "/api/bridge/toggle":
            from fysb.services import auto_bridge as _ab
            res = _ab.toggle_connection(int(payload.get("connection_id") or 0), user_id=uid, is_admin=principal["is_admin"])
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "POST" and path == "/api/bridge/delete":
            from fysb.services import auto_bridge as _ab
            res = _ab.delete_connection(int(payload.get("connection_id") or 0), user_id=uid, is_admin=principal["is_admin"])
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "GET" and path == "/api/bridge/ea-download":
            from fysb.services import auto_bridge as _ab
            cid = int(qs.get("id") or 0)
            conns = _ab.list_connections(uid, 20)
            target = next((c for c in conns if int(c["id"]) == cid), None)
            if not target:
                return _json_resp(404, {"ok": False, "error": "MT5 connection not found"})
            src = _ab.render_mt5_ea_source(target, config.WEBAPP_URL or "https://YOUR-BOT-URL")
            return 200, "text/plain; charset=utf-8", src.encode("utf-8")

        if m == "POST" and path == "/api/partner/payout":
            wallet = str(payload.get("wallet_address") or "").strip()
            amt_raw = payload.get("amount_usd")
            amt = float(amt_raw) if amt_raw not in (None, "") else None
            res = affiliates.request_payout(uid, wallet, amt)
            if res.get("ok"):
                audit_log.log(uid, f"webapp_partner_payout:{res.get('payout_id')}:{res.get('amount_usd')}")
            return _json_resp(200 if res.get("ok") else 400, res)

        if m == "POST" and path == "/api/partner/channel-toggle":
            p_row = partners.get_partner_by_user(uid)
            if not p_row or int(p_row.get("enabled") or 0) != 1:
                return _json_resp(403, {"ok": False, "error": "Partner account required"})
            ch_id = int(payload.get("channel_id") or 0)
            with db.tx() as c:
                ch = c.execute(
                    "SELECT id, enabled FROM broadcast_channels WHERE id=? AND owner_partner_id=?",
                    (ch_id, int(p_row["id"])),
                ).fetchone()
                if not ch:
                    return _json_resp(404, {"ok": False, "error": "Channel not found"})
                nxt = 0 if int(ch["enabled"] or 0) == 1 else 1
                c.execute(
                    "UPDATE broadcast_channels SET enabled=?, updated_at=? WHERE id=?",
                    (nxt, config.now(), ch_id),
                )
            audit_log.log(uid, f"webapp_partner_ch_toggle:{ch_id}={nxt}")
            return _json_resp(200, {"ok": True, "channel_id": ch_id, "enabled": nxt == 1})

        if m == "GET" and path == "/api/signals":
            from fysb.engines.tracker import seq_of
            from fysb.ui.cards import signal_creator_label
            p_row = partners.get_partner_by_user(uid)
            is_partner = bool(p_row and int(p_row.get("enabled") or 0) == 1)
            with db.conn() as c:
                if principal["is_admin"]:
                    rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 25").fetchall()]
                elif is_partner:
                    # Partners see public/auto signals + their own, NEVER other users' private SIGNAL_NOW
                    rows = [
                        dict(r)
                        for r in c.execute(
                            """SELECT * FROM signals
                               WHERE (source IS NULL OR source != 'SIGNAL_NOW' OR created_by=?)
                               ORDER BY id DESC LIMIT 25""",
                            (uid,),
                        ).fetchall()
                    ]
                else:
                    rows = [
                        dict(r)
                        for r in c.execute(
                            """SELECT DISTINCT s.* FROM signals s
                               LEFT JOIN signal_deliveries d ON d.signal_id = s.id AND d.user_id=?
                               WHERE d.user_id IS NOT NULL OR s.created_by=?
                               ORDER BY s.id DESC LIMIT 25""",
                            (uid, uid),
                        ).fetchall()
                    ]
                open_demo_sids = {
                    int(r["signal_id"])
                    for r in c.execute(
                        "SELECT signal_id FROM demo_positions WHERE user_id=? AND status='OPEN' AND signal_id>0",
                        (uid,),
                    ).fetchall()
                }
            for r in rows:
                r["seq"] = seq_of(int(r["id"]))
                r["creator"] = signal_creator_label(r)
                r["demo_open"] = int(r["id"]) in open_demo_sids
            return _json_resp(200, {"ok": True, "signals": rows})

        if m == "GET" and path == "/api/candles":
            if not _check_rate_limit("candles", str(uid), 40, 60.0):
                return _json_resp(429, {"ok": False, "error": "Rate limit exceeded for /api/candles"})
            sym = instruments.normalize(qs.get("symbol") or "XAUUSD")
            tf = (qs.get("tf") or "H1").strip().upper()
            if not principal["is_admin"]:
                plan = dict(subscriptions.plan_for(uid))
                if not _plan_allows_symbol(plan, sym):
                    return _json_resp(403, {"ok": False, "error": f"⛔ السوق {sym} غير مشمول في باقتك"})
            with db.conn() as c:
                mrow = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (sym,)).fetchone()
            prov_sym = (mrow["provider_symbol"] if mrow and mrow["provider_symbol"] else sym)
            try:
                payload = market_data.fetch_candles(
                    prov_sym, market_data.parse_interval(tf), api_key=config.market_api_key(), outputsize=80
                )
                candles = list((payload or {}).get("values") or [])
                src = str((payload or {}).get("source") or "")
            except Exception:
                candles, src = [], ""
            formatted = []
            seen_t = set()
            for c in candles:
                t = _parse_ts(c.get("datetime", ""))
                if t in seen_t:
                    continue
                seen_t.add(t)
                formatted.append({
                    "time": t,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                })
            formatted.sort(key=lambda x: x["time"])
            return _json_resp(200, {"ok": True, "symbol": sym, "tf": tf, "source": src, "candles": formatted})

        if m == "GET" and path == "/api/leaderboard":
            if not principal["is_admin"]:
                return _json_resp(403, {"ok": False, "error": "Forbidden: Admin analytics only"})
            period = (qs.get("period") or "30d").strip().lower()
            lb_data = leaderboard.compute_leaderboard(period)
            return _json_resp(200, {
                "ok": True,
                "period": period,
                "rows": lb_data["strategies"],
                "summary": {
                    "total_trades": lb_data["total_trades"],
                    "overall_win_rate": lb_data["overall_win_rate"],
                    "total_net_pips": lb_data["total_net_pips"],
                },
            })

        if m == "GET" and path == "/api/news":
            if not principal["is_admin"]:
                return _json_resp(403, {"ok": False, "error": "Forbidden: Admin analytics only"})
            return _json_resp(200, {
                "ok": True,
                "enabled": news_guard.is_enabled(),
                "auto_broadcast": news_guard.auto_broadcast_enabled(),
                "destinations": news_guard.broadcast_destinations(),
                "events": news_guard.upcoming_high_impact(48),
                "feed_error": news_guard.last_feed_error(),
            })

        if m == "POST" and path == "/api/risk":
            prof = risk_journal.update_risk_profile(
                uid,
                balance=payload.get("balance"),
                risk_pct=payload.get("risk_pct"),
                leverage=payload.get("leverage"),
                max_daily_dd_pct=payload.get("max_daily_dd_pct"),
            )
            return _json_resp(200, {"ok": True, "profile": prof})

        if m == "POST" and path == "/api/journal":
            sid = _safe_int(payload.get("signal_id"), 0)
            act = str(payload.get("action") or "TAKEN").upper()
            with db.conn() as c:
                s = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
            if not s:
                return _json_resp(404, {"ok": False, "error": "Signal not found"})
            if not _user_can_access_signal(uid, principal["is_admin"], dict(s)):
                return _json_resp(403, {"ok": False, "error": "Forbidden: signal not accessible for your plan"})
            res = risk_journal.log_journal_action(uid, dict(s), act)
            return _json_resp(200, {"ok": True, **res})

        if m == "POST" and path == "/api/redeem":
            if not _check_rate_limit("redeem", str(uid), 6, 60.0):
                return _json_resp(429, {"ok": False, "error": "⏳ محاولات كثيرة لتفعيل الأكواد — انتظر دقيقة ثم أعد المحاولة"})
            res = gifts.redeem_code(uid, str(payload.get("code") or ""))
            return _json_resp(200 if res.get("ok") else 400, res)

    return _json_resp(404, {"ok": False, "error": "Not Found"})


_STATUS_TEXT = {
    200: "OK", 204: "No Content", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
    413: "Payload Too Large", 429: "Too Many Requests", 500: "Internal Server Error",
}


async def _read_chunked_body(reader: asyncio.StreamReader, max_bytes: int = 262_144) -> bytes:
    """Decode HTTP/1.1 Transfer-Encoding: chunked bodies (used by Cloudflare Workers proxies)."""
    out = bytearray()
    while True:
        size_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not size_line:
            break
        size_hex = size_line.split(b";", 1)[0].strip()
        try:
            n = int(size_hex, 16)
        except ValueError:
            break
        if n <= 0:
            # consume trailing CRLF / optional trailers
            try:
                await asyncio.wait_for(reader.readline(), timeout=2.0)
            except Exception:
                pass
            break
        if len(out) + n > max_bytes:
            n = max(0, max_bytes - len(out))
        if n > 0:
            out.extend(await asyncio.wait_for(reader.readexactly(n), timeout=10.0))
        await asyncio.wait_for(reader.readexactly(2), timeout=5.0)  # chunk CRLF
        if len(out) >= max_bytes:
            break
    return bytes(out)


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
        head_lines = raw_head.decode("iso-8859-1", errors="replace").split("\r\n")
        req_line = head_lines[0].split(" ")
        method = req_line[0] if len(req_line) >= 1 else "GET"
        target = req_line[1] if len(req_line) >= 2 else "/"
        headers: dict[str, str] = {}
        for line in head_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if "chunked" in (headers.get("transfer-encoding") or "").lower():
            body = await _read_chunked_body(reader)
        else:
            raw_cl = (headers.get("content-length") or "0").strip()
            try:
                clen_req = int(raw_cl)
            except ValueError:
                status, ctype, data = _json_resp(400, {"ok": False, "error": "Invalid Content-Length"})
                clen_req = -1
            if clen_req > 262_144:
                status, ctype, data = _json_resp(413, {"ok": False, "error": "Payload Too Large"})
                body = None
            elif clen_req < 0:
                body = None
            else:
                body = await asyncio.wait_for(reader.readexactly(clen_req), timeout=10.0) if clen_req else b""
        if body is not None:
            status, ctype, data = await asyncio.to_thread(dispatch_request, method, target, headers, body)
        resp = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"X-Content-Type-Options: nosniff\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Access-Control-Allow-Methods: GET, POST, OPTIONS, HEAD\r\n"
            f"Access-Control-Allow-Headers: Content-Type, X-Telegram-Init-Data, X-Bridge-Key, ngrok-skip-browser-warning, X-Pinggy-No-Screen\r\n"
            f"Access-Control-Max-Age: 86400\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii") + data
        writer.write(resp)
        await writer.drain()
    except Exception as exc:
        log.debug("webapp client error: %s", type(exc).__name__)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_webapp_server(host: str | None = None, port: int | None = None):
    """Start embedded lightweight Mini App + Admin Web Panel HTTP server.
    If the preferred port is occupied (common on shared Bot-Hosting nodes),
    automatically binds an OS-assigned free local port and updates config.WEBAPP_PORT."""
    h = host or config.WEBAPP_HOST
    p = int(port if port is not None else config.WEBAPP_PORT)
    if p < 0:
        return None
    try:
        srv = await asyncio.start_server(_handle_client, h, p)
    except OSError as exc:
        if port is not None and port != 0:
            log.warning("WebApp server port %s busy (%s) — falling back to auto free port", p, exc)
        try:
            srv = await asyncio.start_server(_handle_client, "127.0.0.1", 0)
        except Exception as exc2:
            log.warning("WebApp server skipped (%s) — bot continues normally", exc2)
            return None
    except Exception as exc:
        log.warning("WebApp server skipped on %s:%s (%s) — bot continues normally", h, p, exc)
        return None

    try:
        sock = srv.sockets[0] if srv.sockets else None
        if sock is not None:
            actual_port = int(sock.getsockname()[1])
            if actual_port > 0:
                config.WEBAPP_PORT = actual_port
    except Exception:
        pass
    log.info("Royal Mini App & Admin Web Panel listening on http://%s:%s (WEBAPP_URL=%s)",
             h, config.WEBAPP_PORT, config.WEBAPP_URL or "<auto-tunnel pending>")
    return srv
