"""v8.4.0 — Royal «تحليل ForexYemeni الحقيقي» (True Analysis Live Radar & Instant Execution).

100% REAL, CANDLE-GROUNDED INSTITUTIONAL ENGINE — ZERO FAKE OR SYNTHETIC SIGNALS:
  1. Access & Monetization:
     - Strictly private per-user subscription (NEVER enabled for public channels
       or regular users by default).
     - Admin-editable price ($), subscription duration (days), and configurable
       free-trial duration (days; 0 = disabled).
     - Admin can grant (trial / 30d / 90d / unlimited) or revoke access for ANY
       user in 1 tap from User Detail, and configure settings from Admin Panel.
  2. Continuity Guard:
     - When a subscriber requests a new analysis on a symbol that ALREADY has an
       active signal born from their previous True Analysis, the bot protects
       them from conflicting views and displays the live running status + pips +
       targets of their ongoing analysis & signal!
     - When a live chart radar monitor is already watching that symbol, displays
       its live stage (waiting stability close vs waiting confirm close) with an
       optional 1-tap cancel button.
  3. Instant Real Execution OR 2-Stage Closed-Candle Live Monitoring:
     - Path A (INSTANT_SIGNAL): When the real chart at analysis time ALREADY
       satisfies closed-candle confirmation (body >= 55% in direction, beyond
       activation level + 0.15*ATR buffer, valid R:R), immediately emits a
       PRIVATE real signal + ForexYemeni institutional chart!
     - Path B (MONITORING): Otherwise registers an active 2-stage radar:
       * Stage 1 (`WATCHING_STABILITY`): verifies an ACTUAL CLOSED candle on the
         stability timeframe (e.g. H1) with body >= 55% beyond activation + ATR
         buffer. Wick-only spikes are rejected.
       * Stage 2 (`WATCHING_CONFIRM`): waits for a SUBSEQUENT CLOSED candle on
         the confirmation timeframe (e.g. M15) holding in the same direction
         without violating invalidation, then emits the PRIVATE real signal +
         fresh ForexYemeni chart and links it to live TP1/TP2/TP3 tracking.
       * Protection (`INVALIDATED` / `EXPIRED`): if a real closed candle breaks
         invalidation before trigger, cancels immediately and notifies the user.
"""
from __future__ import annotations

import asyncio
import io
from datetime import datetime, timedelta, timezone

from fysb import config, db, instruments
from fysb.config import log
from fysb.engines import market_data
from fysb.theme import esc, tf_name

# Exact stability -> confirmation timeframe pairing + honest expiry window (hours)
_TF_SPEC = {
    # tf: (stability_tf, confirm_tf, expiry_hours, tf_seconds_stab, tf_seconds_conf)
    "M1":  ("M1",  "M1",  2,  60,    60),
    "M5":  ("M5",  "M1",  4,  300,   60),
    "M15": ("M15", "M5",  6,  900,   300),
    "M30": ("M30", "M15", 12, 1800,  900),
    "H1":  ("H1",  "M15", 24, 3600,  900),
    "H4":  ("H4",  "H1",  48, 14400, 3600),
    "D1":  ("D1",  "H1",  72, 86400, 3600),
    "W1":  ("D1",  "H4",  96, 86400, 14400),
}

_TF_TO_INTERVAL = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1day", "W1": "1week",
}

_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}

_ACTIVE_SIG_STATUSES = ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT")
_WATCH_STATUSES = ("WATCHING_STABILITY", "WATCHING_CONFIRM")


def _parse_utc(raw: str) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Admin-Editable Settings & Per-User Access / Trial Control
# ---------------------------------------------------------------------------
def settings_snapshot() -> dict:
    enabled_global = db.get_setting("true_analysis_enabled_global", "1") == "1"
    try:
        price_usd = max(0.0, float(db.get_setting("true_analysis_price_usd", "99") or 99))
    except Exception:
        price_usd = 99.0
    try:
        duration_days = max(1, int(float(db.get_setting("true_analysis_days", "30") or 30)))
    except Exception:
        duration_days = 30
    try:
        trial_days = max(0, int(float(db.get_setting("true_analysis_trial_days", "3") or 3)))
    except Exception:
        trial_days = 3
    with db.conn() as c:
        subs_cnt = c.execute(
            "SELECT COUNT(*) n FROM users WHERE COALESCE(true_analysis_enabled,0)=1"
        ).fetchone()["n"]
        watch_cnt = c.execute(
            "SELECT COUNT(*) n FROM analysis_monitors WHERE status IN ('WATCHING_STABILITY','WATCHING_CONFIRM')"
        ).fetchone()["n"]
        active_sig_cnt = c.execute(
            "SELECT COUNT(*) n FROM analysis_monitors WHERE status='SIGNAL_ACTIVE'"
        ).fetchone()["n"]
    return {
        "enabled_global": enabled_global,
        "price_usd": price_usd,
        "duration_days": duration_days,
        "trial_days": trial_days,
        "subscribers_count": int(subs_cnt or 0),
        "watching_count": int(watch_cnt or 0),
        "active_signals_count": int(active_sig_cnt or 0),
    }


def has_access(uid: int) -> dict:
    """Return full access state for `uid` (auto-expiring elapsed subscriptions)."""
    cfg = settings_snapshot()
    if uid == config.ADMIN_ID:
        return {
            **cfg,
            "active": True,
            "mode": "ADMIN",
            "until": "",
            "days_left": 9999,
            "trial_used": True,
            "trial_available": False,
        }
    with db.conn() as c:
        row = c.execute(
            "SELECT true_analysis_enabled, true_analysis_until, true_analysis_trial_used "
            "FROM users WHERE user_id=?",
            (uid,),
        ).fetchone()
    if not row:
        return {
            **cfg,
            "active": False,
            "mode": "NONE",
            "until": "",
            "days_left": 0,
            "trial_used": False,
            "trial_available": bool(cfg["enabled_global"] and cfg["trial_days"] > 0),
        }
    enabled = bool(int(row["true_analysis_enabled"] or 0))
    until_s = str(row["true_analysis_until"] or "").strip()
    trial_used = bool(int(row["true_analysis_trial_used"] or 0))
    days_left = 0
    if enabled and until_s:
        dt_until = _parse_utc(until_s)
        if dt_until is not None:
            rem_sec = (dt_until - _now_utc()).total_seconds()
            if rem_sec <= 0:
                # Expired cleanly -> turn off automatically
                with db.tx() as c:
                    c.execute(
                        "UPDATE users SET true_analysis_enabled=0 WHERE user_id=?",
                        (uid,),
                    )
                enabled = False
                days_left = 0
            else:
                days_left = max(1, int((rem_sec + 86399) // 86400))
    elif enabled and not until_s:
        days_left = 9999  # Unlimited / lifetime granted by Admin

    active = bool(enabled and cfg["enabled_global"])
    mode = "NONE"
    if active:
        mode = "LIFETIME" if not until_s else ("TRIAL" if (trial_used and days_left <= max(cfg["trial_days"], 1) and cfg["trial_days"] > 0) else "SUB")
    return {
        **cfg,
        "active": active,
        "mode": mode,
        "until": until_s,
        "days_left": days_left,
        "trial_used": trial_used,
        "trial_available": bool((not active) and cfg["enabled_global"] and cfg["trial_days"] > 0 and (not trial_used)),
    }


def grant_access(uid: int, days: int | None, actor: int = 0, *, is_trial: bool = False) -> dict:
    """Grant True Analysis access to `uid`. `days=0` or `None` => Unlimited."""
    until_s = ""
    if days and int(days) > 0:
        until_s = (_now_utc() + timedelta(days=int(days))).isoformat()
    with db.tx() as c:
        if is_trial:
            c.execute(
                "UPDATE users SET true_analysis_enabled=1, true_analysis_until=?, "
                "true_analysis_trial_used=1 WHERE user_id=?",
                (until_s, uid),
            )
        else:
            c.execute(
                "UPDATE users SET true_analysis_enabled=1, true_analysis_until=? WHERE user_id=?",
                (until_s, uid),
            )
    try:
        from fysb.services import audit as audit_log
        audit_log.log(actor or uid, f"true_analysis_grant:{uid}:{days or 0}:{'trial' if is_trial else 'sub'}")
    except Exception:
        pass
    return {"ok": True, "user_id": uid, "days": int(days or 0), "until": until_s, "is_trial": is_trial}


def grant_paid_access(uid: int, actor: int = 0) -> dict:
    """Called automatically by payments.approve_request when plan_code == 'TRUE_ANALYSIS'."""
    cfg = settings_snapshot()
    return grant_access(uid, cfg["duration_days"], actor=actor, is_trial=False)


def revoke_access(uid: int, actor: int = 0) -> None:
    with db.tx() as c:
        c.execute(
            "UPDATE users SET true_analysis_enabled=0, true_analysis_until='' WHERE user_id=?",
            (uid,),
        )
        c.execute(
            "UPDATE analysis_monitors SET status='CANCELLED', updated_at=? "
            "WHERE user_id=? AND status IN ('WATCHING_STABILITY','WATCHING_CONFIRM')",
            (config.now(), uid),
        )
    try:
        from fysb.services import audit as audit_log
        audit_log.log(actor or uid, f"true_analysis_revoke:{uid}")
    except Exception:
        pass


def activate_user_trial(uid: int) -> dict:
    st = has_access(uid)
    if st["active"]:
        return {"ok": False, "error": "الميزة مفعّلة لديك بالفعل."}
    if not st["enabled_global"]:
        return {"ok": False, "error": "الميزة متوقفة مؤقتاً من الإدارة."}
    if st["trial_days"] <= 0:
        return {"ok": False, "error": "الفترة التجريبية غير مفعّلة حالياً من الإدارة."}
    if st["trial_used"]:
        return {"ok": False, "error": "لقد استفدت من الفترة التجريبية المجانية مسبقاً."}
    return grant_access(uid, st["trial_days"], actor=uid, is_trial=True)


def list_subscribers_and_monitors(limit: int = 25) -> dict:
    with db.conn() as c:
        subs_rows = [dict(r) for r in c.execute(
            "SELECT user_id, username, full_name, true_analysis_until, true_analysis_trial_used "
            "FROM users WHERE COALESCE(true_analysis_enabled,0)=1 ORDER BY user_id DESC LIMIT ?",
            (limit,),
        ).fetchall()]
        mon_rows = [dict(r) for r in c.execute(
            "SELECT * FROM analysis_monitors WHERE status IN ('WATCHING_STABILITY','WATCHING_CONFIRM','SIGNAL_ACTIVE') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()]
    return {"subscribers": subs_rows, "monitors": mon_rows}


def user_monitors(uid: int) -> list[dict]:
    _sync_completed_signal_monitors(uid)
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM analysis_monitors WHERE user_id=? "
            "AND status IN ('WATCHING_STABILITY','WATCHING_CONFIRM','SIGNAL_ACTIVE') "
            "ORDER BY id DESC LIMIT 15",
            (uid,),
        ).fetchall()]


def cancel_monitor(uid: int, monitor_id: int) -> bool:
    with db.tx() as c:
        cur = c.execute(
            "UPDATE analysis_monitors SET status='CANCELLED', updated_at=? "
            "WHERE id=? AND user_id=? AND status IN ('WATCHING_STABILITY','WATCHING_CONFIRM')",
            (config.now(), int(monitor_id), int(uid)),
        )
        return bool(cur.rowcount)


def _sync_completed_signal_monitors(uid: int | None = None) -> None:
    """Close SIGNAL_ACTIVE monitors whose linked private signal has already finished."""
    with db.tx() as c:
        if uid is not None:
            rows = c.execute(
                "SELECT m.id, s.status AS s_status FROM analysis_monitors m "
                "LEFT JOIN signals s ON s.id = m.signal_id "
                "WHERE m.user_id=? AND m.status='SIGNAL_ACTIVE'",
                (uid,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT m.id, s.status AS s_status FROM analysis_monitors m "
                "LEFT JOIN signals s ON s.id = m.signal_id "
                "WHERE m.status='SIGNAL_ACTIVE'",
            ).fetchall()
        now_s = config.now()
        for r in rows:
            st = str(r["s_status"] or "").upper()
            if st not in _ACTIVE_SIG_STATUSES:
                c.execute(
                    "UPDATE analysis_monitors SET status='COMPLETED', updated_at=? WHERE id=?",
                    (now_s, int(r["id"])),
                )


# ---------------------------------------------------------------------------
# Continuity Guard — Active Analysis Signal or Live Radar Check
# ---------------------------------------------------------------------------
def check_active_continuity(uid: int, symbol: str) -> dict | None:
    """Return active signal or live radar monitor for `(uid, symbol)` if any.

    Guarantees the user is notified that their previous analysis & signal are
    still active instead of getting conflicting analyses while a trade runs!
    """
    sym = instruments.normalize(symbol)
    _sync_completed_signal_monitors(uid)
    now_iso = config.now()

    with db.conn() as c:
        # 1. Check active signal spawned from a previous True Analysis on this symbol
        m_sig = c.execute(
            "SELECT * FROM analysis_monitors WHERE user_id=? AND UPPER(symbol)=? "
            "AND status='SIGNAL_ACTIVE' AND signal_id>0 ORDER BY id DESC LIMIT 1",
            (uid, sym),
        ).fetchone()
        if m_sig:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (int(m_sig["signal_id"]),)).fetchone()
            if sig and str(sig["status"] or "").upper() in _ACTIVE_SIG_STATUSES:
                return {"kind": "ACTIVE_SIGNAL", "monitor": dict(m_sig), "signal": dict(sig)}

        # 2. Check active live radar watching this symbol (auto-expire if past expires_at)
        m_watch = c.execute(
            "SELECT * FROM analysis_monitors WHERE user_id=? AND UPPER(symbol)=? "
            "AND status IN ('WATCHING_STABILITY','WATCHING_CONFIRM') ORDER BY id DESC LIMIT 1",
            (uid, sym),
        ).fetchone()
    if m_watch:
        m_d = dict(m_watch)
        if m_d.get("expires_at") and m_d["expires_at"] <= now_iso:
            with db.tx() as c:
                c.execute(
                    "UPDATE analysis_monitors SET status='EXPIRED', updated_at=? WHERE id=?",
                    (now_iso, int(m_d["id"])),
                )
            return None
        return {"kind": "WATCHING", "monitor": m_d}
    return None


# ---------------------------------------------------------------------------
# 100% Real Closed-Candle Verification Math (Zero Fakeouts!)
# ---------------------------------------------------------------------------
def _select_latest_closed_candle(candles: list[dict], tf: str, now_dt: datetime | None = None) -> dict | None:
    """Return the most recent CLOSED candle (never a still-forming unclosed wick)."""
    if not candles:
        return None
    if len(candles) == 1:
        return candles[-1]
    now = now_dt or _now_utc()
    tf_sec = _TF_SECONDS.get(str(tf or "M15").upper(), 900)
    last = candles[-1]
    dt_last = _parse_utc(last.get("datetime") or "")
    if dt_last is not None:
        age = (now - dt_last).total_seconds()
        # If the latest bar opened less than one full period ago, it is STILL FORMING!
        if 0 <= age < tf_sec:
            return candles[-2]
    return last


def evaluate_closed_candle(
    candles: list[dict],
    *,
    tf: str,
    direction: str,
    level_px: float,
    buffer_px: float,
    invalidation_px: float,
    min_body_pct: float = 55.0,
    after_ts: str = "",
    now_dt: datetime | None = None,
) -> dict:
    """Evaluate a REAL closed candle for Confirmation, Invalidation, or Still Waiting.

    - Strictly requires a closed bar (never an in-progress wick).
    - Checks invalidation FIRST to protect capital.
    - Enforces candle direction + real body ratio >= `min_body_pct` + close beyond
      `level_px +/- buffer_px`.
    """
    c = _select_latest_closed_candle(candles, tf, now_dt=now_dt)
    if not c:
        return {"state": "WAITING", "reason": "no_candles"}
    c_ts = str(c.get("datetime") or "")
    if after_ts and c_ts and c_ts <= after_ts:
        return {"state": "WAITING", "reason": "awaiting_next_closed_candle", "candle_ts": c_ts}

    op = float(c.get("open") or 0.0)
    hi = float(c.get("high") or op)
    lo = float(c.get("low") or op)
    cl = float(c.get("close") or op)
    if cl <= 0:
        return {"state": "WAITING", "reason": "invalid_price"}

    rng = max(hi - lo, 1e-9)
    body_pct = round(100.0 * abs(cl - op) / rng, 1)
    is_buy = str(direction).upper() == "BUY"

    # 1. Hard invalidation check on closed candle
    if invalidation_px > 0:
        if (is_buy and cl < invalidation_px) or ((not is_buy) and cl > invalidation_px):
            return {
                "state": "INVALIDATED",
                "candle": c,
                "candle_ts": c_ts,
                "close": cl,
                "body_pct": body_pct,
                "reason": "closed_beyond_invalidation",
            }

    # 2. Direction + real body + ATR buffer confirmation check
    dir_ok = (cl > op) if is_buy else (cl < op)
    buf = max(0.0, float(buffer_px or 0.0))
    lvl_ok = (cl >= level_px + buf) if is_buy else (cl <= level_px - buf)
    if dir_ok and lvl_ok and body_pct >= min_body_pct:
        return {
            "state": "CONFIRMED",
            "candle": c,
            "candle_ts": c_ts,
            "close": cl,
            "body_pct": body_pct,
            "reason": "closed_body_confirmed",
        }
    return {
        "state": "WAITING",
        "candle": c,
        "candle_ts": c_ts,
        "close": cl,
        "body_pct": body_pct,
        "reason": "wick_or_below_level",
    }


# ---------------------------------------------------------------------------
# Trade-Level Derivation & Strictly Private Signal Creation
# ---------------------------------------------------------------------------
def _derive_real_trade_plan(
    *,
    symbol: str,
    row,
    direction: str,
    entry_px: float,
    invalidation_px: float,
    atr: float,
    setup: dict | None = None,
) -> dict | None:
    """Compute valid, strictly ordered real Entry / SL / TP1 / TP2 / TP3 / RR."""
    sp = instruments.spec(symbol, row)
    digits = sp.digits
    is_buy = str(direction).upper() == "BUY"
    if entry_px <= 0:
        return None

    # Use explicit institutional setup levels when present and aligned with current entry
    if setup and str(setup.get("direction") or "").upper() == str(direction).upper():
        s_sl = float(setup.get("stop") or 0.0)
        s_tp1 = float(setup.get("tp1") or 0.0)
        s_tp2 = float(setup.get("tp2") or 0.0)
        s_tp3 = float(setup.get("tp3") or 0.0)
        sl_ok = (s_sl < entry_px) if is_buy else (s_sl > entry_px)
        tp_ok = (s_tp1 > entry_px and s_tp2 > s_tp1 and s_tp3 > s_tp2) if is_buy else (
            s_tp1 < entry_px and s_tp2 < s_tp1 and s_tp3 < s_tp2
        )
        if sl_ok and tp_ok:
            risk = abs(entry_px - s_sl)
            rr = round(abs(s_tp2 - entry_px) / max(risk, 1e-9), 2)
            if rr >= 1.2:
                return {
                    "entry": round(entry_px, digits),
                    "sl": round(s_sl, digits),
                    "tp1": round(s_tp1, digits),
                    "tp2": round(s_tp2, digits),
                    "tp3": round(s_tp3, digits),
                    "rr": rr,
                }

    # Structure + ATR grounded risk & targets (1.5R / 2.6R / 4.0R)
    min_risk = max(atr * 1.1, sp.pip_size * 8.0)
    if invalidation_px > 0 and ((is_buy and invalidation_px < entry_px) or ((not is_buy) and invalidation_px > entry_px)):
        sl_pad = max(atr * 0.25, sp.pip_size * 2.0)
        sl = (invalidation_px - sl_pad) if is_buy else (invalidation_px + sl_pad)
        risk = abs(entry_px - sl)
        if risk < min_risk:
            risk = min_risk
            sl = (entry_px - risk) if is_buy else (entry_px + risk)
    else:
        risk = min_risk
        sl = (entry_px - risk) if is_buy else (entry_px + risk)

    sign = 1.0 if is_buy else -1.0
    tp1 = entry_px + sign * risk * 1.5
    tp2 = entry_px + sign * risk * 2.6
    tp3 = entry_px + sign * risk * 4.0
    return {
        "entry": round(entry_px, digits),
        "sl": round(sl, digits),
        "tp1": round(tp1, digits),
        "tp2": round(tp2, digits),
        "tp3": round(tp3, digits),
        "rr": 2.6,
    }


def _create_private_signal(
    *,
    uid: int,
    symbol: str,
    tf: str,
    strategy: str,
    direction: str,
    plan_levels: dict,
    score: int,
    reason: str,
    source: str,
    data_time: str = "",
) -> dict:
    """Insert a STRICTLY PRIVATE real signal owned solely by `uid` (NEVER channels/others)."""
    now_s = config.now()
    exp_h = max(6, db.get_setting_int("signal_expiry_hours", 72))
    exp_s = (_now_utc() + timedelta(hours=exp_h)).isoformat()
    entry = float(plan_levels["entry"])
    sl = float(plan_levels["sl"])
    tp1 = float(plan_levels["tp1"])
    tp2 = float(plan_levels["tp2"])
    tp3 = float(plan_levels["tp3"])
    rr = float(plan_levels["rr"])
    targets_s = f"{tp1},{tp2},{tp3}"

    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO signals(
                symbol, direction, entry, stop, tp1, tp2, tp3, targets, rr,
                timeframe, strategy, score, status, reason, source,
                data_source, data_time, created_at, expires_at,
                filled_at, live_px, private_user_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, direction, entry, sl, tp1, tp2, tp3, targets_s, rr,
                tf, strategy or "TRUE_ANALYSIS", int(score or 80), "RUNNING",
                reason[:500], source, "LIVE_TRUE_ANALYSIS", data_time or now_s,
                now_s, exp_s, now_s, entry, int(uid),
            ),
        )
        sid = int(cur.lastrowid)
        # Register ONLY `uid` in signal_deliveries so tracker.py sends TP1/TP2/TP3/SL
        # alerts EXCLUSIVELY to `uid` — NEVER to channels or any other user!
        c.execute(
            "INSERT OR IGNORE INTO signal_deliveries(signal_id, user_id, delivered_at, chat_id, message_id) "
            "VALUES(?,?,?,?,?)",
            (sid, int(uid), now_s, str(uid), 0),
        )
        sig_row = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
    return dict(sig_row)


# ---------------------------------------------------------------------------
# Entry Point Called Right After an Analysis Completes
# ---------------------------------------------------------------------------
def evaluate_on_analysis_created(uid: int, analysis: dict) -> dict:
    """Decide honestly: Instant Real Signal OR 2-Stage Live Chart Radar.

    Returns:
      - {"mode": "NO_ACCESS"} when user does not have True Analysis access
      - {"mode": "NEUTRAL"} when analysis has no directional bias
      - {"mode": "INSTANT_SIGNAL", "signal": dict, "monitor_id": int, ...}
      - {"mode": "MONITORING", "monitor": dict, ...}
    """
    acc = has_access(uid)
    if not acc["active"]:
        return {"mode": "NO_ACCESS", "access": acc}

    direction = str(analysis.get("direction") or "NEUTRAL").upper()
    if direction not in ("BUY", "SELL"):
        return {"mode": "NEUTRAL"}

    symbol = instruments.normalize(analysis.get("symbol") or "XAUUSD")
    chart_data = analysis.get("chart_data") or {}
    conf = analysis.get("confirmation") or {}
    base_tf = str(chart_data.get("tf") or analysis.get("timeframe") or "H1").upper()
    if base_tf not in _TF_SPEC:
        base_tf = "H1"
    stab_tf, conf_tf, exp_hours, *_ = _TF_SPEC[base_tf]

    with db.conn() as c:
        mrow = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (symbol,)).fetchone()
    mrow_d = dict(mrow) if mrow else None

    cur_px = float(analysis.get("price") or 0.0)
    atr = float(chart_data.get("atr") or 0.0)
    activation_px = float(chart_data.get("activation_px") or cur_px)
    invalidation_px = float(chart_data.get("invalidation_px") or 0.0)
    buffer_px = max(atr * 0.15, 0.0)
    setup = chart_data.get("setup") or None
    strategy = str((setup or {}).get("code") or analysis.get("strategy") or "ALL").upper()
    quality = int(analysis.get("quality") or 75)

    plan_levels = _derive_real_trade_plan(
        symbol=symbol,
        row=mrow_d,
        direction=direction,
        entry_px=cur_px,
        invalidation_px=invalidation_px,
        atr=atr,
        setup=setup,
    )
    if not plan_levels:
        return {"mode": "NEUTRAL"}

    now_s = config.now()
    exp_s = (_now_utc() + timedelta(hours=exp_hours)).isoformat()

    # Check if the real chart ALREADY has a confirmed closed-candle trigger RIGHT NOW!
    body_pct = float(conf.get("body_pct") or 0.0)
    already_activated = bool(conf.get("activated")) and body_pct >= 55.0
    candles = chart_data.get("candles") or []
    closed_check = evaluate_closed_candle(
        candles,
        tf=stab_tf,
        direction=direction,
        level_px=activation_px,
        buffer_px=buffer_px,
        invalidation_px=invalidation_px,
        min_body_pct=55.0,
    ) if candles else {"state": "WAITING"}

    instant_ready = already_activated and closed_check.get("state") == "CONFIRMED"

    # Supersede any old WATCHING monitor for same (uid, symbol)
    with db.tx() as c:
        c.execute(
            "UPDATE analysis_monitors SET status='CANCELLED', updated_at=? "
            "WHERE user_id=? AND UPPER(symbol)=? AND status IN ('WATCHING_STABILITY','WATCHING_CONFIRM')",
            (now_s, uid, symbol),
        )

    if instant_ready:
        reason = (
            f"تنفيذ فوري حقيقي ومؤكّد على الشارت ({stab_tf}) — "
            f"إغلاق جسم شمعة حقيقي ({int(closed_check.get('body_pct') or body_pct)}%) "
            f"بفارق يتجاوز 0.15×ATR دون حاجة لانتظار إضافي."
        )
        sig = _create_private_signal(
            uid=uid,
            symbol=symbol,
            tf=stab_tf,
            strategy=strategy,
            direction=direction,
            plan_levels=plan_levels,
            score=quality,
            reason=reason,
            source="TRUE_ANALYSIS_INSTANT",
            data_time=str(analysis.get("price_time") or now_s),
        )
        with db.tx() as c:
            cur = c.execute(
                """INSERT INTO analysis_monitors(
                    user_id, symbol, timeframe, strategy, direction,
                    stability_tf, confirm_tf, activation_px, invalidation_px, buffer_px,
                    entry_ref, sl, tp1, tp2, tp3, score, confidence,
                    status, stability_candle_ts, stability_confirmed_at, signal_id,
                    last_checked_at, last_price, note, created_at, expires_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'SIGNAL_ACTIVE',?,?,?,?,?,?,?,?,?)""",
                (
                    uid, symbol, stab_tf, strategy, direction,
                    stab_tf, conf_tf, activation_px, invalidation_px, buffer_px,
                    plan_levels["entry"], plan_levels["sl"], plan_levels["tp1"],
                    plan_levels["tp2"], plan_levels["tp3"], quality, quality,
                    str(closed_check.get("candle_ts") or ""), now_s, int(sig["id"]),
                    now_s, cur_px, reason, now_s, exp_s, now_s,
                ),
            )
            mid = int(cur.lastrowid)
        return {
            "mode": "INSTANT_SIGNAL",
            "signal": sig,
            "monitor_id": mid,
            "stability_tf": stab_tf,
            "confirm_tf": conf_tf,
            "body_pct": int(closed_check.get("body_pct") or body_pct),
            "candles": candles,
        }

    # Path B: Register live 2-stage radar monitoring
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO analysis_monitors(
                user_id, symbol, timeframe, strategy, direction,
                stability_tf, confirm_tf, activation_px, invalidation_px, buffer_px,
                entry_ref, sl, tp1, tp2, tp3, score, confidence,
                status, last_checked_at, last_price, note, created_at, expires_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'WATCHING_STABILITY',?,?,?,?,?,?)""",
            (
                uid, symbol, stab_tf, strategy, direction,
                stab_tf, conf_tf, activation_px, invalidation_px, buffer_px,
                plan_levels["entry"], plan_levels["sl"], plan_levels["tp1"],
                plan_levels["tp2"], plan_levels["tp3"], quality, quality,
                now_s, cur_px, f"بانتظار إغلاق {stab_tf} + تأكيد {conf_tf}",
                now_s, exp_s, now_s,
            ),
        )
        mid = int(cur.lastrowid)
        mon = dict(c.execute("SELECT * FROM analysis_monitors WHERE id=?", (mid,)).fetchone())
    return {
        "mode": "MONITORING",
        "monitor": mon,
        "stability_tf": stab_tf,
        "confirm_tf": conf_tf,
        "expires_hours": exp_hours,
    }


# ---------------------------------------------------------------------------
# Ultra-Luxury Telegram Card Renderers
# ---------------------------------------------------------------------------
def render_continuity_card(cont: dict, live_px: float = 0.0) -> tuple[str, list[list]]:
    """Build the luxury card shown when user requests analysis on an active trade/radar."""
    from fysb.ui import keyboards as kb

    kind = cont["kind"]
    mon = cont["monitor"]
    sym = mon["symbol"]
    with db.conn() as c:
        mrow = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (sym.upper(),)).fetchone()
    fmt = lambda v: instruments.fmt_price(sym, float(v or 0.0), mrow)
    dir_u = str(mon["direction"]).upper()
    dir_ar = "شراء 🟢 BUY" if dir_u == "BUY" else "بيع 🔴 SELL"

    if kind == "ACTIVE_SIGNAL":
        sig = cont["signal"]
        entry = float(sig.get("entry") or 0.0)
        cur = float(live_px or sig.get("live_px") or entry)
        pips = instruments.signed_pips(sym, dir_u, entry, cur, mrow) if (entry > 0 and cur > 0) else 0.0
        st_map = {
            "ACTIVE": "🟡 نشطة — قيد المراقبة",
            "RUNNING": "🟢 نشطة ومستمرة في السوق الآن",
            "TP1_HIT": "🏆 تحقق الهدف الأول TP1 — ومستمرة للهدف الثاني",
            "TP2_HIT": "💎 تحقق الهدف الثاني TP2 — ومستمرة للهدف الثالث",
        }
        st_txt = st_map.get(str(sig.get("status") or "").upper(), str(sig.get("status") or ""))
        pnl_icon = "📈" if pips >= 0 else "📉"
        body = (
            f"👑 <b><u>تحليل وإشارة ForexYemeni ما زالا مستمرّين — {esc(sym)}</u></b>\n"
            f"<blockquote>"
            f"▎💎 <b>الحالة:</b> {st_txt}\n"
            f"▎🧭 <b>الاتجاه:</b> <b>{dir_ar}</b> · الإطار: <code>{esc(sig.get('timeframe') or mon['stability_tf'])}</code>\n"
            f"▎💰 <b>سعر الدخول:</b> <code>{fmt(entry)}</code> · <b>السعر الحي:</b> <code>{fmt(cur)}</code>\n"
            f"▎{pnl_icon} <b>الأداء الجاري:</b> <b>{pips:+.1f} نقطة</b>\n"
            f"▎🛡 <b>وقف الخسارة (SL):</b> <code>{fmt(sig.get('stop', sig.get('sl', 0)))}</code>\n"
            f"▎🎯 <b>الأهداف:</b> TP1 <code>{fmt(sig['tp1'])}</code> · TP2 <code>{fmt(sig['tp2'])}</code> · TP3 <code>{fmt(sig['tp3'])}</code>"
            f"</blockquote>\n"
            f"<i>✨ لحمايتك من التشتّت وتضارب الرؤى: يظل التحليل السابق والإشارة المبنية عليه ساريَين ومُتابعَين لحظة بلحظة حتى ضرب الأهداف أو الوقف.</i>"
        )
        rows = [
            [kb.btn("📊 عرض شارت الصفقة الحي", f"u:sigchart:{sig['id']}", style="success")],
            [kb.btn("👑 لوحة رادار تحليلاتي", "u:true_analysis", style="primary")],
            [kb.btn("🔬 تحليل سوق آخر", "u:analytics")],
        ]
        return body, rows

    # kind == "WATCHING"
    st = mon["status"]
    stage_txt = (
        f"⏳ <b>المرحلة 1/2:</b> بانتظار إغلاق شمعة <b>{esc(mon['stability_tf'])}</b> بجسم حقيقي (≥ 55%)"
        if st == "WATCHING_STABILITY" else
        f"✅ تم تأكيد إغلاق <b>{esc(mon['stability_tf'])}</b> · ⏳ <b>المرحلة 2/2:</b> بانتظار تأكيد شمعة <b>{esc(mon['confirm_tf'])}</b>"
    )
    body = (
        f"👁‍🗨 <b><u>رادار ForexYemeni يراقب الشارت الآن — {esc(sym)}</u></b>\n"
        f"<blockquote>"
        f"▎{stage_txt}\n"
        f"▎🧭 <b>الاتجاه المرصود:</b> <b>{dir_ar}</b>\n"
        f"▎🎯 <b>مستوى التفعيل الحقيقي:</b> <code>{fmt(mon['activation_px'])}</code>\n"
        f"▎🚫 <b>مستوى الإبطال التلقائي:</b> <code>{fmt(mon['invalidation_px'])}</code>\n"
        f"▎🛡 <b>الوقف المخطط:</b> <code>{fmt(mon['sl'])}</code> · <b>TP1:</b> <code>{fmt(mon['tp1'])}</code> · <b>TP2:</b> <code>{fmt(mon['tp2'])}</code>"
        f"</blockquote>\n"
        f"<i>✨ لا داعي لإعادة التحليل — سأرسل لك الإشارة الحقيقية مرفقة بشارت ForexYemeni تلقائياً فور اكتمال الإغلاق والتأكيد.</i>"
    )
    rows = [
        [kb.btn("🗑 إلغاء هذه المراقبة وطلب تحليل جديد", f"u:tamoncancel:{mon['id']}", style="danger")],
        [kb.btn("👑 لوحة رادار تحليلاتي", "u:true_analysis", style="primary")],
        [kb.btn("🔬 تحليل سوق آخر", "u:analytics")],
    ]
    return body, rows


def render_private_signal_card(sig: dict, *, instant: bool, stab_tf: str, conf_tf: str, body_pct: float = 0.0) -> str:
    """Ultra-luxury card for a REAL private signal triggered by True Analysis."""
    sym = sig["symbol"]
    with db.conn() as c:
        mrow = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (sym.upper(),)).fetchone()
    fmt = lambda v: instruments.fmt_price(sym, float(v or 0.0), mrow)
    dir_u = str(sig["direction"]).upper()
    dir_badge = "🟢 شراء مؤسسي — BUY" if dir_u == "BUY" else "🔴 بيع مؤسسي — SELL"
    mode_header = (
        "⚡ <b><u>إشارة فورية حقيقية — تنفيذ مباشر من شارت ForexYemeni</u></b>"
        if instant else
        "👑 <b><u>إشارة حقيقية مؤكّدة — رادار تحليل ForexYemeni</u></b>"
    )
    proof_line = (
        f"⚡ <b>التحقق الفوري:</b> إغلاق جسم شمعة <b>{esc(stab_tf)}</b> حقيقي الآن ({int(round(body_pct))}%) — التنفيذ مباشر دون انتظار!"
        if instant else
        f"🔐 <b>التحقق المزدوج الحقيقي:</b> تأكّد إغلاق <b>{esc(stab_tf)}</b> بجسم حقيقي + تأكيد <b>{esc(conf_tf)}</b> ({int(round(body_pct))}%) ✅"
    )
    return (
        f"{mode_header}\n"
        f"<blockquote>"
        f"▎💎 <b>الأصل:</b> <b>{esc(sym)}</b> · <b>{dir_badge}</b>\n"
        f"▎{proof_line}\n"
        f"▎💰 <b>سعر الدخول الحقيقي:</b> <code>{fmt(sig['entry'])}</code>\n"
        f"▎🛡 <b>وقف الخسارة (SL):</b> <code>{fmt(sig.get('stop', sig.get('sl', 0)))}</code>\n"
        f"▎🎯 <b>الهدف الأول (TP1):</b> <code>{fmt(sig['tp1'])}</code>\n"
        f"▎🎯 <b>الهدف الثاني (TP2):</b> <code>{fmt(sig['tp2'])}</code>\n"
        f"▎🏆 <b>الهدف الثالث (TP3):</b> <code>{fmt(sig['tp3'])}</code>\n"
        f"▎⚖️ <b>نسبة العائد للمخاطرة:</b> <b>1 : {float(sig.get('rr') or 0):.2f}</b> · جودة: <b>{int(sig.get('score') or 80)}/100</b>"
        f"</blockquote>\n"
        f"<i>🔒 إشارة خاصة بحسابك فقط (غير منشورة في القنوات) · التتبع الحي مفعل تلقائياً حتى الأهداف.</i>"
    )


def render_monitoring_started_banner(res: dict) -> str:
    mon = res["monitor"]
    sym = mon["symbol"]
    with db.conn() as c:
        mrow = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (sym.upper(),)).fetchone()
    fmt = lambda v: instruments.fmt_price(sym, float(v or 0.0), mrow)
    return (
        f"\n\n👁‍🗨 <b><u>رادار تحليل ForexYemeni الحقيقي — مفعّل الآن</u></b>\n"
        f"<blockquote>"
        f"▎🛡 <b>أراقب الشارت عنك لحظة بلحظة!</b>\n"
        f"▎1️⃣ انتظار إغلاق شمعة <b>{esc(mon['stability_tf'])}</b> بجسم حقيقي (≥ 55%) عند <code>{fmt(mon['activation_px'])}</code>\n"
        f"▎2️⃣ تأكيد الإغلاق على إطار <b>{esc(mon['confirm_tf'])}</b> دون كسر الإبطال <code>{fmt(mon['invalidation_px'])}</code>\n"
        f"▎3️⃣ إرسال <b>إشارتك الحقيقية الخاصة + الشارت المحدّث</b> فور اكتمال الشرطين تلقائياً.\n"
        f"▎⛔ إن كسر السعر مستوى الإبطال قبل التأكيد، سأُلغي التحليل وأُبلغك فوراً لحماية رأس مالك."
        f"</blockquote>"
    )


# ---------------------------------------------------------------------------
# Background Live Radar Poll Loop (Runs every 60s in background.py)
# ---------------------------------------------------------------------------
async def _deliver_private_signal_with_chart(bot, uid: int, sig: dict, *, instant: bool, stab_tf: str, conf_tf: str, body_pct: float, candles: list[dict] | None = None) -> None:
    from fysb.ui import charts as _charts
    from fysb.ui import keyboards as _kb

    cap = render_private_signal_card(sig, instant=instant, stab_tf=stab_tf, conf_tf=conf_tf, body_pct=body_pct)
    markup = _kb.InlineKeyboardMarkup([
        [_kb.btn("🧮 حاسبة اللوت", f"u:lotcalc:{sig['id']}", style="primary"),
         _kb.btn("👑 رادار تحليلاتي", "u:true_analysis", style="success")],
    ])
    png = None
    try:
        bars = list(candles or [])
        if not bars:
            with db.conn() as c:
                mrow = c.execute("SELECT provider_symbol FROM market_catalog WHERE UPPER(symbol)=?",
                                 (sig["symbol"].upper(),)).fetchone()
            psym = (mrow["provider_symbol"] if mrow else sig["symbol"])
            iv = _TF_TO_INTERVAL.get(stab_tf, "15min")
            pl = await asyncio.to_thread(market_data.fetch_candles, psym, iv, config.market_api_key(), 90, True)
            bars = list(pl.get("values") or [])
        if bars:
            png = await asyncio.to_thread(_charts.render_signal_chart, sig, bars)
    except Exception:
        log.debug("true_analysis chart render skipped for sig #%s", sig.get("id"))

    try:
        if png and len(cap) <= 1020:
            msg = await bot.send_photo(
                chat_id=uid,
                photo=io.BytesIO(png),
                caption=cap,
                reply_markup=markup,
                parse_mode="HTML",
            )
        else:
            if png:
                await bot.send_photo(chat_id=uid, photo=io.BytesIO(png))
            msg = await bot.send_message(chat_id=uid, text=cap, reply_markup=markup, parse_mode="HTML")
        if msg and getattr(msg, "message_id", None):
            with db.tx() as c:
                c.execute(
                    "UPDATE signal_deliveries SET chat_id=?, message_id=? WHERE signal_id=? AND user_id=?",
                    (str(uid), int(msg.message_id), int(sig["id"]), int(uid)),
                )
    except Exception as exc:
        log.warning("true_analysis deliver failed for uid=%s sig=%s: %s", uid, sig.get("id"), exc)


async def poll_active_monitors(bot, *, now_dt: datetime | None = None) -> dict:
    """Inspect every active True Analysis monitor against REAL closed candles.

    100% honest & deterministic:
      - Stage 1 (WATCHING_STABILITY): confirms real closed stability candle.
      - Stage 2 (WATCHING_CONFIRM): confirms subsequent real closed confirm candle
        and fires PRIVATE signal + ForexYemeni chart exclusively to owner.
      - Cancels immediately on real invalidation or expiry.
    """
    summary = {"checked": 0, "stage1_confirmed": 0, "signals_fired": 0, "invalidated": 0, "expired": 0}
    _sync_completed_signal_monitors()
    with db.conn() as c:
        mons = [dict(r) for r in c.execute(
            "SELECT * FROM analysis_monitors WHERE status IN ('WATCHING_STABILITY','WATCHING_CONFIRM') "
            "ORDER BY id ASC LIMIT 60"
        ).fetchall()]
    if not mons:
        return summary

    now = now_dt or _now_utc()
    now_iso = now.isoformat()

    for m in mons:
        summary["checked"] += 1
        mid = int(m["id"])
        uid = int(m["user_id"])
        sym = str(m["symbol"]).upper()

        # Verify user still has access
        if not has_access(uid)["active"]:
            with db.tx() as c:
                c.execute("UPDATE analysis_monitors SET status='CANCELLED', updated_at=? WHERE id=?", (now_iso, mid))
            continue

        # 1. Expiry guard
        exp_dt = _parse_utc(m.get("expires_at") or "")
        if exp_dt is not None and now >= exp_dt:
            with db.tx() as c:
                c.execute("UPDATE analysis_monitors SET status='EXPIRED', updated_at=? WHERE id=?", (now_iso, mid))
            summary["expired"] += 1
            if bot:
                try:
                    await bot.send_message(
                        uid,
                        f"⌛ <b><u>انتهت نافذة مراقبة التحليل — {esc(sym)}</u></b>\n"
                        f"<blockquote>▎لم تكتمل شروط الإغلاق والثبات الحقيقية خلال المدة المحددة — لم يتم إصدار أي إشارة غير مؤكّدة حفاظاً على رأس مالك.</blockquote>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            continue

        # Lookup provider symbol
        with db.conn() as c:
            mrow = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (sym,)).fetchone()
        mrow_d = dict(mrow) if mrow else None
        psym = (mrow_d["provider_symbol"] if mrow_d else sym)
        fmt = lambda v: instruments.fmt_price(sym, float(v or 0.0), mrow_d)

        st = str(m["status"]).upper()
        check_tf = str(m["stability_tf"] if st == "WATCHING_STABILITY" else m["confirm_tf"]).upper()
        iv = _TF_TO_INTERVAL.get(check_tf, "15min")

        try:
            payload = await asyncio.to_thread(
                market_data.fetch_candles, psym, iv, config.market_api_key(), 80, True
            )
            candles = list(payload.get("values") or [])
        except Exception as exc:
            log.debug("true_analysis fetch skipped for %s/%s: %s", sym, check_tf, exc)
            continue

        if not candles:
            continue

        last_px = float(candles[-1].get("close") or 0.0)
        res = evaluate_closed_candle(
            candles,
            tf=check_tf,
            direction=m["direction"],
            level_px=float(m["activation_px"]),
            buffer_px=(float(m["buffer_px"]) if st == "WATCHING_STABILITY" else 0.0),
            invalidation_px=float(m["invalidation_px"]),
            min_body_pct=(55.0 if st == "WATCHING_STABILITY" else 45.0),
            after_ts=(str(m.get("stability_candle_ts") or "") if st == "WATCHING_CONFIRM" else ""),
            now_dt=now,
        )

        # Invalidation -> cancel immediately and alert user!
        if res["state"] == "INVALIDATED":
            with db.tx() as c:
                c.execute(
                    "UPDATE analysis_monitors SET status='INVALIDATED', last_checked_at=?, last_price=?, "
                    "note=?, updated_at=? WHERE id=?",
                    (now_iso, last_px, f"كسر مستوى الإبطال عند {res['close']}", now_iso, mid),
                )
            summary["invalidated"] += 1
            if bot:
                try:
                    await bot.send_message(
                        uid,
                        f"⛔ <b><u>تم إلغاء تحليل {esc(sym)} تلقائياً لحمايتك</u></b>\n"
                        f"<blockquote>"
                        f"▎أغلقت شمعة <b>{esc(check_tf)}</b> حقيقية عند <code>{fmt(res['close'])}</code> "
                        f"خلف مستوى الإبطال <code>{fmt(m['invalidation_px'])}</code>.\n"
                        f"▎🛡 تم إيقاف الرادار فوراً — لا توجد صفقة صالحة حالياً."
                        f"</blockquote>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            continue

        # Confirmed!
        if res["state"] == "CONFIRMED":
            same_tf = (str(m["stability_tf"]).upper() == str(m["confirm_tf"]).upper())
            if st == "WATCHING_STABILITY" and not same_tf:
                # Advance to Stage 2 (WATCHING_CONFIRM) and send real milestone update!
                with db.tx() as c:
                    c.execute(
                        "UPDATE analysis_monitors SET status='WATCHING_CONFIRM', "
                        "stability_candle_ts=?, stability_confirmed_at=?, last_checked_at=?, "
                        "last_price=?, note=?, updated_at=? WHERE id=?",
                        (
                            res["candle_ts"], now_iso, now_iso, last_px,
                            f"تأكد إغلاق {m['stability_tf']} ({res['body_pct']}%) — بانتظار {m['confirm_tf']}",
                            now_iso, mid,
                        ),
                    )
                summary["stage1_confirmed"] += 1
                if bot:
                    try:
                        await bot.send_message(
                            uid,
                            f"✅ <b><u>تحديث رادار ForexYemeni — {esc(sym)}</u></b>\n"
                            f"<blockquote>"
                            f"▎1️⃣ <b>تم تأكيد إغلاق شمعة {esc(m['stability_tf'])} بنجاح!</b>\n"
                            f"▎📊 إغلاق جسم حقيقي: <b>{res['body_pct']:.0f}%</b> عند <code>{fmt(res['close'])}</code>\n"
                            f"▎2️⃣ ⏳ <b>أراقب الآن تأكيد شمعة {esc(m['confirm_tf'])}</b> لإصدار إشارتك الحقيقية مع الشارت..."
                            f"</blockquote>",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                continue

            # Final Confirmation -> Compute live levels from confirmed close & FIRE PRIVATE REAL SIGNAL!
            confirmed_entry = float(res["close"])
            atr_est = abs(float(m["entry_ref"]) - float(m["sl"])) / 1.5 if float(m["sl"]) > 0 else 0.0
            live_plan = _derive_real_trade_plan(
                symbol=sym,
                row=mrow_d,
                direction=m["direction"],
                entry_px=confirmed_entry,
                invalidation_px=float(m["invalidation_px"]),
                atr=atr_est,
                setup={
                    "direction": m["direction"],
                    "stop": float(m["sl"]),
                    "tp1": float(m["tp1"]),
                    "tp2": float(m["tp2"]),
                    "tp3": float(m["tp3"]),
                },
            )
            if not live_plan:
                with db.tx() as c:
                    c.execute(
                        "UPDATE analysis_monitors SET status='EXPIRED', note=?, updated_at=? WHERE id=?",
                        ("نسبة العائد/المخاطرة لم تعد صالحة بعد الإغلاق", now_iso, mid),
                    )
                summary["expired"] += 1
                continue

            reason = (
                f"إشارة حقيقية مؤكّدة من رادار ForexYemeni — "
                f"تم إغلاق {m['stability_tf']} + تأكيد {m['confirm_tf']} بجسم حقيقي ({res['body_pct']:.0f}%)."
            )
            sig = _create_private_signal(
                uid=uid,
                symbol=sym,
                tf=str(m["confirm_tf"]),
                strategy=str(m["strategy"] or "TRUE_ANALYSIS"),
                direction=str(m["direction"]),
                plan_levels=live_plan,
                score=int(m["score"] or 85),
                reason=reason,
                source="TRUE_ANALYSIS_CONFIRMED",
                data_time=res["candle_ts"] or now_iso,
            )
            with db.tx() as c:
                c.execute(
                    "UPDATE analysis_monitors SET status='SIGNAL_ACTIVE', confirm_candle_ts=?, "
                    "signal_id=?, last_checked_at=?, last_price=?, note=?, updated_at=? WHERE id=?",
                    (res["candle_ts"], int(sig["id"]), now_iso, confirmed_entry, reason, now_iso, mid),
                )
            summary["signals_fired"] += 1
            if bot:
                await _deliver_private_signal_with_chart(
                    bot, uid, sig,
                    instant=False,
                    stab_tf=str(m["stability_tf"]),
                    conf_tf=str(m["confirm_tf"]),
                    body_pct=float(res["body_pct"]),
                    candles=candles,
                )
            continue

        # Still waiting -> update heartbeat quietly
        with db.tx() as c:
            c.execute(
                "UPDATE analysis_monitors SET last_checked_at=?, last_price=?, updated_at=? WHERE id=?",
                (now_iso, last_px, now_iso, mid),
            )

    return summary
