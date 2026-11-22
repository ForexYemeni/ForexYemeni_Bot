"""Payments service — methods, requests, approval gate. No double-approval."""
from fysb import config, db


def list_methods(enabled_only: bool = False) -> list:
    with db.conn() as c:
        q = "SELECT * FROM payment_methods" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(q).fetchall()]


def get_method(mid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM payment_methods WHERE id=?", (mid,)).fetchone()
    return dict(r) if r else None


def add_method(title: str, instructions: str, account_ref: str, country: str = "", linked_plans: str = "") -> int:
    with db.conn() as c:
        cur = c.execute("""INSERT INTO payment_methods(title,instructions,account_ref,enabled,created_at,country,linked_plans)
                           VALUES(?,?,?,1,?,?,?)""", (title, instructions, account_ref, config.now(), country, linked_plans))
        return cur.lastrowid


def update_method(mid: int, **fields) -> bool:
    allowed = {"title", "instructions", "account_ref", "enabled", "country", "linked_plans"}
    sets = ", ".join(f"{k}=?" for k in fields if k in allowed)
    vals = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return False
    with db.conn() as c:
        cur = c.execute(f"UPDATE payment_methods SET {sets} WHERE id=?", (*vals, mid))
        return cur.rowcount > 0


def delete_method(mid: int) -> bool:
    with db.conn() as c:
        return c.execute("DELETE FROM payment_methods WHERE id=?", (mid,)).rowcount > 0


def create_request(user_id: int, plan_code: str, amount: float, method: str) -> int:
    with db.conn() as c:
        cur = c.execute("INSERT INTO payments(user_id,plan_code,amount,method,status,created_at) VALUES(?,?,?,?,'PENDING',?)",
                        (user_id, plan_code, amount, method, config.now()))
        return cur.lastrowid


def attach_receipt(payment_id: int, user_id: int, file_id: str) -> bool:
    with db.conn() as c:
        cur = c.execute("UPDATE payments SET receipt_file_id=? WHERE id=? AND user_id=? AND status='PENDING'",
                        (file_id, payment_id, user_id))
        return cur.rowcount > 0


def get_request(pid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def list_requests(status: str = "PENDING", limit: int = 15) -> list:
    with db.conn() as c:
        if status:
            rows = c.execute("SELECT * FROM payments WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def approve_request(pid: int, actor: int) -> dict:
    """Approve exactly once (guarded by PENDING state inside a transaction)."""
    from fysb.services.subscriptions import grant_subscription
    with db.tx(immediate=True) as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pid,)).fetchone()
        if not p:
            return {"ok": False, "error": "طلب غير موجود أو تمت مراجعته مسبقاً"}
        c.execute("UPDATE payments SET status='APPROVED', reviewed_at=? WHERE id=?", (config.now(), pid))
    plan = None
    with db.conn() as c:
        plan = c.execute("SELECT * FROM plans WHERE code=?", (p["plan_code"],)).fetchone()
    if plan:
        grant_subscription(p["user_id"], p["plan_code"], source="PAYMENT", actor=actor)
    from fysb.services.audit import log as audit_log
    audit_log(actor, f"payment_approved:{pid}")
    return {"ok": True, "payment": dict(p)}


def reject_request(pid: int, reason: str, actor: int) -> dict:
    with db.tx(immediate=True) as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pid,)).fetchone()
        if not p:
            return {"ok": False, "error": "طلب غير موجود أو تمت مراجعته مسبقاً"}
        c.execute("UPDATE payments SET status='REJECTED', rejection_reason=?, reviewed_at=? WHERE id=?",
                  (reason[:400], config.now(), pid))
    return {"ok": True, "payment": dict(p)}


def stats() -> dict:
    with db.conn() as c:
        return {
            "pending": c.execute("SELECT COUNT(*) n FROM payments WHERE status='PENDING'").fetchone()["n"],
            "approved": c.execute("SELECT COUNT(*) n FROM payments WHERE status='APPROVED'").fetchone()["n"],
            "rejected": c.execute("SELECT COUNT(*) n FROM payments WHERE status='REJECTED'").fetchone()["n"],
            "approved_amount": c.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status='APPROVED'").fetchone()["s"],
        }
