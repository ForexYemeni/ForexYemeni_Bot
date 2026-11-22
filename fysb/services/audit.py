"""Audit service — append-only trail of every meaningful action."""
from fysb import config, db


def log(actor, action: str) -> None:
    try:
        with db.conn() as c:
            c.execute("INSERT INTO audit(actor,action,created_at) VALUES(?,?,?)", (actor, str(action)[:500], config.now()))
    except Exception:
        pass  # auditing must never break the main flow


def recent(limit: int = 15, offset: int = 0) -> list:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def security_events(limit: int = 10) -> list:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM audit WHERE action LIKE 'security:%' OR action LIKE 'admin_probe%' OR action LIKE '%block%' OR action LIKE '%kill_switch%' ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]
