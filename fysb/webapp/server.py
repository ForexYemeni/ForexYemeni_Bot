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
import urllib.parse
from datetime import datetime, timezone

from fysb import config, db
from fysb.config import log
from fysb.engines import market_data, news_guard
from fysb.services import academy, affiliates, gifts, leaderboard, payments, risk_journal, subscriptions
from fysb.services import audit as audit_log
from fysb.webapp import auth as wa_auth
from fysb.webapp import templates

ALLOWED_ADMIN_TOGGLES = (
    "engine_enabled",
    "global_kill_switch",
    "news_guard_enabled",
    "charts_enabled",
    "academy_enabled",
    "crypto_api_enabled",
)


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
    path = parsed.path or "/"
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}

    # 1) Public static HTML & healthcheck
    if m == "GET" and path == "/health":
        return _json_resp(200, {"ok": True, "version": config.VERSION, "brand": config.BRAND})
    if m == "GET" and path in ("/", "/app"):
        return _html_resp(200, templates.render_user_miniapp_html())
    if m == "GET" and path == "/admin":
        return _html_resp(200, templates.render_admin_panel_html())

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
                with db.conn() as c:
                    users_n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
                    paid_subs = c.execute(
                        "SELECT COUNT(*) n FROM subscriptions WHERE status='ACTIVE' AND plan_code!='FREE'"
                    ).fetchone()["n"]
                    open_sigs = c.execute(
                        "SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')"
                    ).fetchone()["n"]
                toggles = {k: (db.get_setting(k, "0") == "1") for k in ALLOWED_ADMIN_TOGGLES}
                return _json_resp(200, {
                    "ok": True,
                    "version": config.VERSION,
                    "kpis": {
                        "users": users_n,
                        "active_paid_subs": paid_subs,
                        "open_signals": open_sigs,
                    },
                    "toggles": toggles,
                    "pending_payments": payments.list_requests("PENDING", 15),
                    "pending_payouts": affiliates.list_payouts("PENDING", 15),
                })

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
                pid = int(payload.get("payout_id") or 0)
                dec = str(payload.get("decision") or "").lower()
                row = affiliates.approve_payout(pid, "Admin Web Panel") if dec == "approve" else affiliates.reject_payout(pid, "Admin Web Panel")
                if not row:
                    return _json_resp(404, {"ok": False, "error": "Pending payout not found"})
                audit_log.log(uid, f"webapp_payout_{dec}:{pid}")
                return _json_resp(200, {"ok": True, "payout_id": pid, "decision": dec})

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
            plan = dict(subscriptions.plan_for(uid))
            day_start = db.day_start_utc()
            with db.conn() as c:
                used = c.execute(
                    "SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                    (uid, day_start),
                ).fetchone()["n"]
            lim = int(plan.get("max_signals_per_day") or 0)
            return _json_resp(200, {
                "ok": True,
                "user_id": uid,
                "is_admin": principal["is_admin"],
                "plan": {"code": plan.get("code", "FREE"), "title": plan.get("title", "FREE")},
                "quota": {"used": used, "limit": lim},
                "risk": risk_journal.get_risk_profile(uid),
                "drawdown": risk_journal.daily_drawdown_status(uid),
                "journal": risk_journal.journal_summary(uid, 30),
                "academy": {
                    k: v for k, v in academy.user_progress(uid).items() if k != "completed_codes"
                },
                "affiliate": affiliates.affiliate_stats(uid),
            })

        if m == "GET" and path == "/api/signals":
            with db.conn() as c:
                if principal["is_admin"]:
                    rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 20").fetchall()]
                else:
                    rows = [
                        dict(r)
                        for r in c.execute(
                            """SELECT s.* FROM signals s
                               JOIN signal_deliveries d ON d.signal_id = s.id
                               WHERE d.user_id=? ORDER BY s.id DESC LIMIT 20""",
                            (uid,),
                        ).fetchall()
                    ]
            return _json_resp(200, {"ok": True, "signals": rows})

        if m == "GET" and path == "/api/candles":
            sym = (qs.get("symbol") or "XAUUSD").strip().upper()
            tf = (qs.get("tf") or "H1").strip().upper()
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
            period = (qs.get("period") or "30d").strip().lower()
            return _json_resp(200, {"ok": True, "period": period, "rows": leaderboard.compute_leaderboard(period)})

        if m == "GET" and path == "/api/news":
            return _json_resp(200, {"ok": True, "enabled": news_guard.is_enabled(), "events": news_guard.upcoming_high_impact(48)})

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
            sid = int(payload.get("signal_id") or 0)
            act = str(payload.get("action") or "TAKEN").upper()
            with db.conn() as c:
                s = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
            if not s:
                return _json_resp(404, {"ok": False, "error": "Signal not found"})
            res = risk_journal.log_journal_action(uid, dict(s), act)
            return _json_resp(200, {"ok": True, **res})

        if m == "POST" and path == "/api/redeem":
            res = gifts.redeem_code(uid, str(payload.get("code") or ""))
            return _json_resp(200 if res.get("ok") else 400, res)

    return _json_resp(404, {"ok": False, "error": "Not Found"})


_STATUS_TEXT = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


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
        clen = min(262_144, max(0, int(headers.get("content-length") or "0")))
        body = await asyncio.wait_for(reader.readexactly(clen), timeout=10.0) if clen else b""
        status, ctype, data = await asyncio.to_thread(dispatch_request, method, target, headers, body)
        resp = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii") + data
        writer.write(resp)
        await writer.drain()
    except Exception:
        pass
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
