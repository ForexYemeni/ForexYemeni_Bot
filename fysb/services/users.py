"""Users service — profiles, activity tracking, blocking, admin operations."""
import threading
import time

from fysb import config, db
from fysb.services import subscriptions as subs
from fysb.services import audit as audit_log

# v6.16.0 Phase 6: true in-memory throttle for last_seen writes (60s per user)
# so button taps only perform a lightweight SELECT instead of a write transaction.
TOUCH_THROTTLE_SECONDS = 60.0
_touch_lock = threading.Lock()
_last_touch_mono: dict[int, float] = {}


def reset_touch_cache() -> None:
    """Clear in-memory touch throttle (used in tests)."""
    with _touch_lock:
        _last_touch_mono.clear()


def _should_touch(uid: int) -> bool:
    now_m = time.monotonic()
    with _touch_lock:
        prev = _last_touch_mono.get(uid)
        if prev is not None and (now_m - prev) < TOUCH_THROTTLE_SECONDS:
            return False
        if len(_last_touch_mono) > 10000:
            cutoff = now_m - TOUCH_THROTTLE_SECONDS
            stale = [k for k, v in _last_touch_mono.items() if v < cutoff]
            for k in stale[:5000]:
                _last_touch_mono.pop(k, None)
        _last_touch_mono[uid] = now_m
        return True


def _mark_touched(uid: int) -> None:
    with _touch_lock:
        _last_touch_mono[uid] = time.monotonic()


def ensure_user(tg_user) -> None:
    """Upsert on /start or any interaction. Respects block_new_users policy."""
    with db.conn() as c:
        existing = c.execute("SELECT user_id,is_blocked FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
        if existing:
            c.execute("UPDATE users SET username=?, full_name=?, last_seen=? WHERE user_id=?",
                      (tg_user.username or "", tg_user.full_name or "", config.now(), tg_user.id))
            _mark_touched(tg_user.id)
            return
        if db.get_setting("block_new_users", "0") == "1":
            raise PermissionError("new-user registration is temporarily closed")
        c.execute("""INSERT INTO users(user_id,username,full_name,joined_at,last_seen,
                     market,timeframe,strategy,min_score)
                     VALUES(?,?,?,?,?,'','','',0)""",
                  (tg_user.id, tg_user.username or "", tg_user.full_name or "", config.now(), config.now()))
    _mark_touched(tg_user.id)
    audit_log.log(tg_user.id, "user_registered")


def get_user(uid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None


def touch(uid: int) -> None:
    """Throttled last_seen update (max once per minute to avoid write storms)."""
    if not _should_touch(uid):
        return
    try:
        with db.conn() as c:
            c.execute("UPDATE users SET last_seen=? WHERE user_id=?", (config.now(), uid))
    except Exception:
        pass


def touch_and_get(uid: int):
    """v6.16.0 hot-path helper: throttled last_seen (at most once/60s per user)
    + profile read on ONE pooled connection.

    98%+ of button taps execute a single read-only SELECT (zero SQLite write
    lock contention and half the WAN round trips on PostgreSQL).
    """
    need_write = _should_touch(uid)
    try:
        with db.conn() as c:
            if need_write:
                c.execute("UPDATE users SET last_seen=? WHERE user_id=?", (config.now(), uid))
            r = c.execute(
                """SELECT u.*,
                          EXISTS(SELECT 1 FROM channel_partners cp
                                 WHERE cp.user_id = u.user_id AND cp.enabled = 1) AS is_active_partner
                   FROM users u WHERE u.user_id=?""",
                (uid,),
            ).fetchone()
        return dict(r) if r else None
    except Exception:
        return get_user(uid)


def is_blocked(uid: int) -> bool:
    u = get_user(uid)
    return bool(u and u["is_blocked"])


def set_blocked(uid: int, blocked: bool, actor: int) -> bool:
    if uid == config.ADMIN_ID:
        return False
    with db.conn() as c:
        cur = c.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, uid))
        ok = cur.rowcount > 0
    if ok:
        audit_log.log(actor, f"user_block:{uid}:{'on' if blocked else 'off'}")
    return ok


def set_notifications(uid: int, enabled: bool) -> None:
    with db.conn() as c:
        c.execute("UPDATE users SET notifications=? WHERE user_id=?", (1 if enabled else 0, uid))


def set_preference(uid: int, field: str, value, allowed: dict) -> bool:
    if field not in allowed:
        return False
    with db.conn() as c:
        c.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
    return True


def search(query: str, limit: int = 10) -> list:
    q = (query or "").strip()
    if not q:
        return []
    with db.conn() as c:
        if q.isdigit():
            rows = c.execute("SELECT * FROM users WHERE user_id=? AND user_id!=? OR (username LIKE ? OR full_name LIKE ?) AND user_id!=? LIMIT ?",
                             (int(q), config.ADMIN_ID, f"%{q}%", f"%{q}%", config.ADMIN_ID, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM users WHERE (username LIKE ? OR full_name LIKE ?) AND user_id!=? LIMIT ?",
                             (f"%{q}%", f"%{q}%", config.ADMIN_ID, limit)).fetchall()
    return [dict(r) for r in rows]


def page_list(page: int = 0, size: int = 8) -> dict:
    """Directory of REAL users — the admin account is never listed here."""
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM users WHERE user_id!=?", (config.ADMIN_ID,)).fetchone()["n"]
        rows = c.execute("SELECT * FROM users WHERE user_id!=? ORDER BY joined_at DESC LIMIT ? OFFSET ?",
                         (config.ADMIN_ID, size, page * size)).fetchall()
    return {"rows": [dict(r) for r in rows], "total": total, "page": page,
            "pages": max(1, (total + size - 1) // size)}


def profile_summary(uid: int, preloaded_u=None) -> dict:
    """Single-connection user profile summary (v6.20.1 — 1 checkout instead of 6)."""
    day = db.day_start_utc()
    with db.conn() as c:
        if preloaded_u is not None:
            u = dict(preloaded_u)
        else:
            ur = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not ur:
                return {}
            u = dict(ur)
        sub_r = c.execute(
            "SELECT plan_code, ends_at FROM subscriptions "
            "WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1",
            (uid,),
        ).fetchone()
        plan_row = subs.plan_for(uid, conn_obj=c)
        valid_sub = bool(sub_r and (not sub_r["ends_at"] or sub_r["ends_at"] > config.now()))
        quota_cap = (max(int(plan_row["max_signals_per_day"] or 5), 5)
                     if (plan_row and plan_row["code"] != "FREE")
                     else max(1, db.get_setting_int("free_daily_signals", 2)))
        deliv = c.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(CASE WHEN delivered_at>=? THEN 1 ELSE 0 END),0) today "
            "FROM signal_deliveries WHERE user_id=?",
            (day, uid),
        ).fetchone()
        payments = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status='APPROVED' THEN amount ELSE 0 END),0) s "
            "FROM payments WHERE user_id=?",
            (uid,),
        ).fetchone()
        demo = c.execute("SELECT balance FROM demo_accounts WHERE user_id=?", (uid,)).fetchone()
    used_today = int(deliv["today"] or 0) if deliv else 0
    state = {
        "plan": plan_row["code"] if plan_row else "FREE",
        "quota": quota_cap,
        "used": used_today,
        "remaining": max(0, quota_cap - used_today),
        "day": day,
    }
    return {
        "user": u,
        "plan_code": plan_row["code"] if plan_row else "FREE",
        "plan_title": plan_row["title"] if plan_row else "Free",
        "plan_ends": sub_r["ends_at"] if valid_sub else None,
        "quota": state,
        "consumed_total": int(deliv["total"] or 0) if deliv else 0,
        "payments_count": int(payments["n"] or 0) if payments else 0,
        "payments_total": float(payments["s"] or 0) if payments else 0.0,
        "demo_balance": demo["balance"] if demo else None,
    }


def admin_edit_subscription(uid: int, plan_code: str, actor: int, days: int | None = None) -> dict:
    res = subs.grant_subscription(uid, plan_code, source="ADMIN", actor=actor, override_days=days)
    if res.get("ok"):
        audit_log.log(actor, f"subscription_set:{uid}:{plan_code}")
    return res


def delete_user_forever(uid: int, actor: int = 0) -> dict:
    """PERMANENT deletion — the user and every trace of him, gone for good.

    Single IMMEDIATE transaction wipes: profile, subscriptions, payments,
    signal deliveries, demo account(s) + positions + trades, saved analyses,
    notification events, support tickets (+ their messages), broker accounts,
    and referral links. Irreversible BY DESIGN — this is not a ban; the user
    can re-register as brand-new only through the normal /start flow.
    The admin account can never be deleted. Audit-logged with per-table counts.
    """
    if uid == config.ADMIN_ID:
        return {"ok": False, "error": "الإدارة لا تُحذف"}
    with db.tx(immediate=True) as c:
        if not c.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
            return {"ok": False, "error": "المستخدم غير موجود"}
        counts: dict[str, int] = {}
        for t in c.execute("SELECT id FROM tickets WHERE user_id=?", (uid,)).fetchall():
            counts["ticket_messages"] = counts.get("ticket_messages", 0) + abs(
                c.execute("DELETE FROM ticket_messages WHERE ticket_id=?", (t["id"],)).rowcount)
        for table in ("analyses", "demo_positions", "demo_trades", "demo_accounts",
                      "notification_events", "payments", "signal_deliveries",
                      "subscriptions", "user_accounts"):
            counts[table] = abs(c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,)).rowcount)
        counts["tickets"] = abs(c.execute("DELETE FROM tickets WHERE user_id=?", (uid,)).rowcount)
        counts["referrals"] = abs(c.execute("DELETE FROM referrals WHERE invitee_id=?", (uid,)).rowcount)
        counts["users"] = abs(c.execute("DELETE FROM users WHERE user_id=?", (uid,)).rowcount)
    audit_log.log(actor, f"user_deleted_forever:{uid}")
    from fysb.config import log
    log.info("USER DELETED FOREVER | user=%s actor=%s wiped=%s", uid, actor, counts)
    return {"ok": True, "counts": counts}


def counts() -> dict:
    """Platform user counts — the admin account is excluded everywhere."""
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM users WHERE user_id!=?", (config.ADMIN_ID,)).fetchone()["n"]
        week_ago = (config_now_minus(days=7)).isoformat()
        return {
            "total": total,
            "active_7d": c.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=? AND user_id!=?", (week_ago, config.ADMIN_ID)).fetchone()["n"],
            "new_7d": c.execute("SELECT COUNT(*) n FROM users WHERE joined_at>=? AND user_id!=?", (week_ago, config.ADMIN_ID)).fetchone()["n"],
            "blocked": c.execute("SELECT COUNT(*) n FROM users WHERE is_blocked=1 AND user_id!=?", (config.ADMIN_ID,)).fetchone()["n"],
            "notified": c.execute("SELECT COUNT(*) n FROM users WHERE notifications=1 AND user_id!=?", (config.ADMIN_ID,)).fetchone()["n"],
        }


def config_now_minus(days: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(days=days)
