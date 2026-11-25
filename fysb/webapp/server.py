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
from fysb.services import (
    academy, affiliates, chart_channels, gifts, leaderboard,
    partners, payments, referrals, risk_journal, subscriptions,
)
from fysb.services import audit as audit_log
from fysb.webapp import auth as wa_auth
from fysb.webapp import templates

_BOT_RUNTIME: dict = {"bot": None, "loop": None}


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
                })

            if m == "POST" and path == "/api/admin/signal":
                sid = int(payload.get("signal_id") or 0)
                op = str(payload.get("op") or "").lower()
                if op == "cancel" and sid > 0:
                    with db.tx() as c:
                        c.execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=?", (config.now(), sid))
                    audit_log.log(uid, f"webapp_signal_cancel:{sid}")
                    return _json_resp(200, {"ok": True, "signal_id": sid, "status": "CANCELLED"})
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
                pid = int(payload.get("payout_id") or 0)
                dec = str(payload.get("decision") or "").lower()
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

            return _json_resp(200, {
                "ok": True,
                "user_id": uid,
                "is_admin": principal["is_admin"],
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
            sid = int(payload.get("signal_id") or 0)
            lots_raw = payload.get("lots")
            lots_ov = float(lots_raw) if lots_raw not in (None, "") else None
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
                if principal["is_admin"] or is_partner:
                    rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 25").fetchall()]
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


_STATUS_TEXT = {
    200: "OK", 204: "No Content", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error",
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
            clen = min(262_144, max(0, int(headers.get("content-length") or "0")))
            body = await asyncio.wait_for(reader.readexactly(clen), timeout=10.0) if clen else b""
        status, ctype, data = await asyncio.to_thread(dispatch_request, method, target, headers, body)
        resp = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Access-Control-Allow-Methods: GET, POST, OPTIONS, HEAD\r\n"
            f"Access-Control-Allow-Headers: Content-Type, X-Telegram-Init-Data, ngrok-skip-browser-warning, X-Pinggy-No-Screen\r\n"
            f"Access-Control-Max-Age: 86400\r\n"
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
