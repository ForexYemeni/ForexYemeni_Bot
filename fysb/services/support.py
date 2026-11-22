"""Support center — threaded tickets with replies, close/reopen, config."""
from fysb import config, db


def config_row() -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM support_config WHERE id=1").fetchone()
    return dict(r) if r else {}


def set_config(username: str, phone: str, channel: str, group_chat: str) -> None:
    with db.conn() as c:
        c.execute("""UPDATE support_config SET username=?,phone=?,channel=?,group_chat=?,updated_at=?
                     WHERE id=1""",
                  (username.lstrip("@"), phone, channel, group_chat, config.now()))


def create_ticket(user_id: int, subject: str, ttype: str, message: str, file_id: str = "") -> int:
    with db.tx() as c:
        cur = c.execute("""INSERT INTO tickets(user_id,subject,type,message,status,created_at,updated_at)
                           VALUES(?,?,?,?, 'OPEN', ?, ?)""",
                        (user_id, subject[:120], ttype, message[:4000], config.now(), config.now()))
        tid = cur.lastrowid
        c.execute("""INSERT INTO ticket_messages(ticket_id,sender_role,sender_id,body,file_id,created_at)
                     VALUES(?,?,?,?,?,?)""",
                  (tid, "user", user_id, message[:4000], file_id, config.now()))
    return tid


def add_message(ticket_id: int, sender_role: str, sender_id: int, body: str, file_id: str = "") -> int:
    with db.tx() as c:
        cur = c.execute("""INSERT INTO ticket_messages(ticket_id,sender_role,sender_id,body,file_id,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (ticket_id, sender_role, sender_id, body[:4000], file_id, config.now()))
        c.execute("UPDATE tickets SET updated_at=? WHERE id=?", (config.now(), ticket_id))
        return cur.lastrowid


def set_status(ticket_id: int, status: str) -> bool:
    with db.conn() as c:
        cur = c.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, config.now(), ticket_id))
        return cur.rowcount > 0


def get_ticket(tid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def ticket_messages(tid: int, limit: int = 20) -> list:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id ASC LIMIT ?",
                         (tid, limit)).fetchall()
    return [dict(r) for r in rows]


def list_tickets(status: str = "OPEN", limit: int = 10) -> list:
    with db.conn() as c:
        q = "SELECT * FROM tickets" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        args = (status, limit) if status else (limit,)
        return [dict(r) for r in c.execute(q, args).fetchall()]


def user_tickets(uid: int, limit: int = 8) -> list:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT ?",
                                           (uid, limit)).fetchall()]


def open_count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM tickets WHERE status='OPEN'").fetchone()["n"]
