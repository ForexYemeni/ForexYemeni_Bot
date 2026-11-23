"""Users service — profiles, activity tracking, blocking, admin operations."""
from fysb import config, db
from fysb.services import subscriptions as subs
from fysb.services import audit as audit_log


def ensure_user(tg_user) -> None:
    """Upsert on /start or any interaction. Respects block_new_users policy."""
    with db.conn() as c:
        existing = c.execute("SELECT user_id,is_blocked FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
        if existing:
            c.execute("UPDATE users SET username=?, full_name=?, last_seen=? WHERE user_id=?",
                      (tg_user.username or "", tg_user.full_name or "", config.now(), tg_user.id))
            return
        if db.get_setting("block_new_users", "0") == "1":
            raise PermissionError("new-user registration is temporarily closed")
        c.execute("""INSERT INTO users(user_id,username,full_name,joined_at,last_seen)
                     VALUES(?,?,?,?,?)""",
                  (tg_user.id, tg_user.username or "", tg_user.full_name or "", config.now(), config.now()))
    audit_log.log(tg_user.id, "user_registered")


def get_user(uid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None


def touch(uid: int) -> None:
    """Throttled last_seen update (max once per minute to avoid write storms)."""
    try:
        with db.conn() as c:
            c.execute("UPDATE users SET last_seen=? WHERE user_id=? AND (last_seen IS NULL OR last_seen<=?)",
                      (config.now(), uid, config.now()))
    except Exception:
        pass


def touch_and_get(uid: int):
    """v6.8.1 hot-path helper: ONE pooled connection for the two calls every
    button press makes (throttled last_seen + profile read). Halves the
    round trips on the user navigation path — WAN PostgreSQL makes each one
    cost real latency."""
    try:
        with db.conn() as c:
            c.execute("UPDATE users SET last_seen=? WHERE user_id=? AND (last_seen IS NULL OR last_seen<=?)",
                      (config.now(), uid, config.now()))
            r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
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


def profile_summary(uid: int) -> dict:
    u = get_user(uid)
    if not u:
        return {}
    plan = subs.active_plan(uid)
    state = subs.quota_state(uid)
    with db.conn() as c:
        consumed_total = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=?", (uid,)).fetchone()["n"]
        payments = c.execute("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status='APPROVED' THEN amount ELSE 0 END),0) s FROM payments WHERE user_id=?", (uid,)).fetchone()
        demo = c.execute("SELECT balance FROM demo_accounts WHERE user_id=?", (uid,)).fetchone()
    return {"user": u, "plan_code": plan["plan_code"] if plan else "FREE",
            "plan_title": plan["title"] if plan else "Free",
            "plan_ends": plan["ends_at"] if plan else None,
            "quota": state, "consumed_total": consumed_total,
            "payments_count": payments["n"], "payments_total": payments["s"],
            "demo_balance": demo["balance"] if demo else None}


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
