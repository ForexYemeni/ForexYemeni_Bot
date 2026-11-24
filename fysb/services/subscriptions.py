"""Subscriptions service — lifecycle, expiry, reminders, and FREE quota ledger.

FREE anti-bypass: the daily quota is enforced by signal_deliveries rows written
inside IMMEDIATE transactions (atomic). Restart / re-tap / parallel sessions /
direct callback replay all re-enter the same server check — there is no client
state to trick. Viewing a signal card or receiving a push consumes quota.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.config import log

# v6.20.1 performance: tiny plans table (3-5 rows) + per-user active plan code
# cached in memory with short TTL and write-through invalidation on any grant/
# cancel/expiry. Cuts plan_for() from 2 WAN round trips down to 0-1.
_PLANS_TTL = 60.0
_USER_PLAN_TTL = 15.0
_cache_lock = threading.Lock()
_plans_cache: dict[str, dict] = {}
_plans_loaded_at: float = 0.0
_user_plan_cache: dict[int, tuple[str, float]] = {}  # uid -> (plan_code, expires_mono)


def invalidate_plans_cache(user_id: int | None = None) -> None:
    """Clear plans and/or a specific user's active plan cache."""
    global _plans_loaded_at
    with _cache_lock:
        if user_id is None:
            _plans_cache.clear()
            _plans_loaded_at = 0.0
            _user_plan_cache.clear()
        else:
            _user_plan_cache.pop(int(user_id), None)


def _plans_map(conn_obj=None) -> dict[str, dict]:
    global _plans_loaded_at
    now_m = time.monotonic()
    with _cache_lock:
        if _plans_cache and (now_m - _plans_loaded_at) < _PLANS_TTL:
            return dict(_plans_cache)
    if conn_obj is not None:
        rows = conn_obj.execute("SELECT * FROM plans").fetchall()
    else:
        with db.conn() as c:
            rows = c.execute("SELECT * FROM plans").fetchall()
    fresh = {r["code"]: dict(r) for r in rows}
    with _cache_lock:
        _plans_cache.clear()
        _plans_cache.update(fresh)
        _plans_loaded_at = time.monotonic()
    return fresh


def active_plan(uid: int):
    """The user's ACTIVE subscription row joined with plan perks, or None."""
    with db.conn() as c:
        r = c.execute("""SELECT s.*, p.title, p.markets, p.timeframes, p.strategies, p.min_score,
                         p.max_signals_per_day, p.price, p.currency, p.duration_days
                         FROM subscriptions s JOIN plans p ON p.code=s.plan_code
                         WHERE s.user_id=? AND s.status='ACTIVE'
                         ORDER BY s.id DESC LIMIT 1""", (uid,)).fetchone()
    if r and (not r["ends_at"] or r["ends_at"] > config.now()):
        return r
    return None


def plan_for(uid: int, conn_obj=None):
    """Plan row for the user (FREE fallback, always a valid row).

    v6.20.1: uses cached plans table + single lightweight subscription lookup
    (15s per-user cache, invalidated immediately on grant/cancel/expiry),
    eliminating 2-4 sequential WAN queries on every user button tap.
    """
    uid_i = int(uid)
    now_m = time.monotonic()
    cached_code: str | None = None
    with _cache_lock:
        ent = _user_plan_cache.get(uid_i)
        if ent is not None and now_m < ent[1]:
            cached_code = ent[0]
    if cached_code is not None:
        pmap = _plans_map(conn_obj)
        row = pmap.get(cached_code) or pmap.get("FREE")
        if row is not None:
            return row

    def _query(c):
        return c.execute(
            "SELECT plan_code, ends_at FROM subscriptions "
            "WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1",
            (uid_i,),
        ).fetchone()

    if conn_obj is not None:
        sub = _query(conn_obj)
        pmap = _plans_map(conn_obj)
    else:
        with db.conn() as c:
            sub = _query(c)
            pmap = _plans_map(c)

    code = "FREE"
    if sub and (not sub["ends_at"] or sub["ends_at"] > config.now()):
        code = sub["plan_code"]
    with _cache_lock:
        if len(_user_plan_cache) > 5000:
            _user_plan_cache.clear()
        _user_plan_cache[uid_i] = (code, time.monotonic() + _USER_PLAN_TTL)
    return pmap.get(code) or pmap.get("FREE")


def plans_for_users(uids: list[int]) -> dict[int, dict]:
    """v6.16.0 batch resolver: resolve active plan (with FREE fallback) for a
    whole batch of user_ids in 2 SQL queries instead of 2N queries."""
    if not uids:
        return {}
    now_iso = config.now()
    uid_set = {int(u) for u in uids}
    with db.conn() as c:
        plans_by_code = {
            r["code"]: dict(r)
            for r in c.execute("SELECT * FROM plans").fetchall()
        }
        subs_rows = c.execute(
            "SELECT user_id, plan_code, ends_at FROM subscriptions "
            "WHERE status='ACTIVE' ORDER BY id DESC"
        ).fetchall()
    free_plan = plans_by_code.get("FREE")
    active_code: dict[int, str] = {}
    for r in subs_rows:
        u = int(r["user_id"])
        if u not in uid_set or u in active_code:
            continue
        ends = r["ends_at"]
        if not ends or ends > now_iso:
            active_code[u] = r["plan_code"]
    out: dict[int, dict] = {}
    for u in uid_set:
        code = active_code.get(u, "FREE")
        plan = plans_by_code.get(code) or free_plan
        if plan:
            out[u] = plan
    return out


def grant_subscription(user_id: int, plan_code: str, source: str = "ADMIN", actor: int = 0,
                       override_days: int | None = None, payment_id: int = 0,
                       amount: float | None = None, currency: str = "USD") -> dict:
    """Activate/extend a subscription. Previous ACTIVE subs of the user end now
    (upgrade/downgrade/replace semantics). Returns the new subscription id."""
    with db.conn() as c:
        plan = c.execute("SELECT * FROM plans WHERE code=?", (plan_code,)).fetchone()
    if not plan:
        return {"ok": False, "error": "plan not found"}
    days = override_days if override_days is not None else int(plan["duration_days"] or 0)
    start = datetime.now(timezone.utc)
    ends = (start + timedelta(days=days)).isoformat() if days > 0 else None
    with db.tx(immediate=True) as c:
        c.execute("UPDATE subscriptions SET status='REPLACED', ends_at=? WHERE user_id=? AND status='ACTIVE'",
                  (config.now(), user_id))
        cur = c.execute("""INSERT INTO subscriptions(user_id,plan_code,starts_at,ends_at,status,created_at,source)
                           VALUES(?,?,?,?,?,?,?)""",
                        (user_id, plan_code, start.isoformat(), ends, "ACTIVE", config.now(), source))
        sub_id = cur.lastrowid
    invalidate_plans_cache(user_id)
    log.info("SUBSCRIPTION %s | user=%s plan=%s source=%s actor=%s", source, user_id, plan_code, source, actor)
    # --- Royal referral conversion (server-side, idempotent) ---------------
    try:
        from fysb.services import referrals
        conv = referrals.mark_converted(user_id, plan_code)
        if conv:
            log.info("REFERRAL CONVERTED | user=%s campaign=%s total=%s/%s target_met=%s",
                     user_id, conv.get("code"), conv.get("converted_total"),
                     conv.get("target_converted"), conv.get("target_met"))
    except Exception:
        log.exception("referral conversion mark failed (non-fatal)")
    # --- Channel Partner Revenue-Share Commission (v6.21.0) ----------------
    comm_info = None
    try:
        from fysb.services import partners
        comm_info = partners.credit_commission_on_subscription(
            user_id, plan_code, payment_id=payment_id, amount=amount, currency=currency,
        )
        if comm_info:
            log.info("PARTNER COMMISSION | partner=%s buyer=%s plan=%s comm=%s %s",
                     comm_info["partner_id"], user_id, plan_code,
                     comm_info["commission_amount"], comm_info["currency"])
    except Exception:
        log.exception("partner commission credit failed (non-fatal)")
    return {"ok": True, "subscription_id": sub_id, "ends_at": ends, "plan": plan_code, "commission": comm_info}


def cancel_subscription(user_id: int, actor: int = 0) -> bool:
    with db.tx() as c:
        cur = c.execute("UPDATE subscriptions SET status='CANCELLED', ends_at=? WHERE user_id=? AND status='ACTIVE'",
                        (config.now(), user_id))
        ok = cur.rowcount > 0
    invalidate_plans_cache(user_id)
    return ok


def expired_but_unmarked() -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM subscriptions WHERE status='ACTIVE' AND ends_at IS NOT NULL AND ends_at<=?",
                         (config.now(),)).fetchall()
    return [dict(r) for r in rows]


def mark_expired(sub_id: int) -> None:
    with db.conn() as c:
        c.execute("UPDATE subscriptions SET status='EXPIRED' WHERE id=?", (sub_id,))
    invalidate_plans_cache()


def due_reminders() -> list[dict]:
    """Subscriptions expiring in <= reminder window without a reminder sent yet."""
    window = db.get_setting_int("expiry_remind_days", 3)
    horizon = (datetime.now(timezone.utc) + timedelta(days=window)).isoformat()
    out = []
    with db.conn() as c:
        rows = c.execute("""SELECT * FROM subscriptions WHERE status='ACTIVE' AND ends_at IS NOT NULL
                            AND ends_at>? AND ends_at<=?""", (config.now(), horizon)).fetchall()
    for r in rows:
        days_left = (datetime.fromisoformat(r["ends_at"]) - datetime.now(timezone.utc)).total_seconds() / 86400
        if days_left <= 1 and not r["remind_1d"]:
            out.append({**dict(r), "stage": 1})
        elif days_left <= window and not r["remind_3d"]:
            out.append({**dict(r), "stage": 3})
    return out


def mark_reminded(sub_id: int, stage: int) -> None:
    col = "remind_1d" if stage == 1 else "remind_3d"
    with db.conn() as c:
        c.execute(f"UPDATE subscriptions SET {col}=1 WHERE id=?", (sub_id,))


# --- FREE quota ledger -------------------------------------------------------
def _quota_for(user_id: int, plan_row=None) -> int:
    if plan_row is None:
        plan_row = plan_for(user_id)
    if plan_row and (plan_row["code"] != "FREE"):
        return max(int(plan_row["max_signals_per_day"] or 5), 5)
    return max(1, db.get_setting_int("free_daily_signals", 2))


def quota_state(user_id: int, plan_row=None) -> dict:
    if plan_row is None:
        plan_row = plan_for(user_id)
    quota = _quota_for(user_id, plan_row)
    day = db.day_start_utc()
    with db.conn() as c:
        used = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                         (user_id, day)).fetchone()["n"]
    return {"plan": plan_row["code"] if plan_row else "FREE",
            "quota": quota, "used": used, "remaining": max(0, quota - used), "day": day}


def try_consume_signal(user_id: int, signal_row, plan_row=None) -> bool:
    """Atomically consume one quota slot. False => quota exhausted (send blocked)."""
    if plan_row is None:
        plan_row = plan_for(user_id)
    quota = _quota_for(user_id, plan_row)
    try:
        with db.tx(immediate=True) as c:
            # Re-check inside the write-lock: two racing consumers can't both pass.
            day = db.day_start_utc()
            used = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                             (user_id, day)).fetchone()["n"]
            if used >= quota:
                return False
            c.execute("""INSERT OR IGNORE INTO signal_deliveries(user_id,signal_id,delivered_at)
                         VALUES(?,?,?)""", (user_id, signal_row["id"], config.now()))
            return True
    except Exception:
        log.exception("quota consume failed user=%s signal=%s", user_id, signal_row["id"])
        return False


def release_signal(user_id: int, signal_id: int) -> None:
    """Roll back a consumption when delivery failed (e.g. user blocked the bot)."""
    with db.conn() as c:
        c.execute("DELETE FROM signal_deliveries WHERE user_id=? AND signal_id=?", (user_id, signal_id))


def consume_on_view(user_id: int, signal_id: int) -> dict:
    """Viewing a signal card consumes quota (server-checked)."""
    state = quota_state(user_id)
    with db.conn() as c:
        already = c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?",
                            (user_id, signal_id)).fetchone()
    if already:
        return {"ok": True, "already": True, **state}
    if state["remaining"] <= 0:
        return {"ok": False, **state}
    ok = try_consume_signal(user_id, {"id": signal_id})
    return {"ok": ok, **state}


def mark_expired_and_notify_targets() -> list[dict]:
    """Return users whose subscription just expired (for notification)."""
    expired = expired_but_unmarked()
    targets = []
    for s in expired:
        mark_expired(s["id"])
        targets.append({"user_id": s["user_id"], "plan": s["plan_code"]})
    if targets:
        log.info("subscriptions expired: %s", [t["user_id"] for t in targets])
    return targets
