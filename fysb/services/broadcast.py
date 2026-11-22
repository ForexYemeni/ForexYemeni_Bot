"""Broadcast service — audience filtering, throttled worker, abort, counters.

Telegram rate limit respected (~20 msg/sec, configurable). Handles users who
blocked the bot (Forbidden -> failed counter). Progress persisted so the admin
panel reflects the live state and a repeat tap cannot double-send.
"""
import asyncio

from fysb import config, db
from fysb.config import log


def audience_ids(audience: str, plan_code: str = "") -> list[int]:
    with db.conn() as c:
        if audience == "all":
            rows = c.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()
        elif audience == "free":
            rows = c.execute("""SELECT DISTINCT u.user_id FROM users u
                                LEFT JOIN subscriptions s ON s.user_id=u.user_id AND s.status='ACTIVE'
                                WHERE u.is_blocked=0 AND (s.plan_code='FREE' OR s.plan_code IS NULL)""").fetchall()
        elif audience == "paid":
            rows = c.execute("""SELECT DISTINCT u.user_id FROM users u
                                JOIN subscriptions s ON s.user_id=u.user_id AND s.status='ACTIVE'
                                WHERE u.is_blocked=0 AND s.plan_code!='FREE'""").fetchall()
        elif audience == "plan" and plan_code:
            rows = c.execute("""SELECT DISTINCT u.user_id FROM users u
                                JOIN subscriptions s ON s.user_id=u.user_id AND s.status='ACTIVE'
                                WHERE u.is_blocked=0 AND s.plan_code=?""", (plan_code,)).fetchall()
        elif audience == "notified":
            rows = c.execute("SELECT user_id FROM users WHERE is_blocked=0 AND notifications=1").fetchall()
        else:
            rows = c.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()
    # The admin is never a broadcast target (admin ≠ user).
    return [r["user_id"] for r in rows if r["user_id"] != config.ADMIN_ID]


def create(admin_id: int, audience: str, plan_code: str, text: str, file_id: str = "") -> int:
    with db.conn() as c:
        cur = c.execute("""INSERT INTO broadcasts(admin_id,audience,plan_code,text,file_id,has_photo,status,created_at)
                           VALUES(?,?,?,?,?,?, 'PENDING', ?)""",
                        (admin_id, audience, plan_code, text[:4000], file_id, 1 if file_id else 0, config.now()))
        return cur.lastrowid


def get(bid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM broadcasts WHERE id=?", (bid,)).fetchone()
    return dict(r) if r else None


def list_recent(limit: int = 8) -> list:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def set_total(bid: int, total: int) -> None:
    with db.conn() as c:
        c.execute("UPDATE broadcasts SET total=?, status='RUNNING' WHERE id=? AND status='PENDING'", (total, bid))


def update_progress(bid: int, sent: int, failed: int) -> None:
    with db.conn() as c:
        c.execute("UPDATE broadcasts SET sent=?, failed=? WHERE id=?", (sent, failed, bid))


def mark_status(bid: int, status: str) -> None:
    with db.conn() as c:
        c.execute("UPDATE broadcasts SET status=?, finished_at=? WHERE id=?",
                  (status, config.now() if status in ("DONE", "ABORTED", "FAILED") else None, bid))


class AbortFlag:
    def __init__(self):
        self.aborted = False


ABORTS: dict[int, AbortFlag] = {}


def abort(bid: int) -> bool:
    if bid in ABORTS:
        ABORTS[bid].aborted = True
        return True
    mark_status(bid, "ABORTED")
    return True


async def run_broadcast(bid: int, bot, targets: list[int]) -> dict:
    """Throttled sender with persisted progress. Never exceeds rate limit."""
    b = get(bid)
    if not b:
        return {"error": "broadcast not found"}
    set_total(bid, len(targets))
    ABORTS[bid] = AbortFlag()
    flag = ABORTS[bid]
    rate = max(1, min(30, db.get_setting_int("broadcast_rate_per_sec", 20)))
    delay = 1.0 / rate
    sent = failed = 0
    for uid in targets:
        if flag.aborted:
            break
        try:
            if b["file_id"]:
                await bot.send_photo(uid, b["file_id"], caption=b["text"][:1024], parse_mode="HTML")
            else:
                await bot.send_message(uid, b["text"], parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            log.debug("broadcast fail uid=%s err=%s", uid, type(e).__name__)
        if (sent + failed) % 10 == 0:
            update_progress(bid, sent, failed)
        await asyncio.sleep(delay)
    update_progress(bid, sent, failed)
    mark_status(bid, "ABORTED" if flag.aborted else "DONE")
    ABORTS.pop(bid, None)
    log.info("broadcast #%s finished sent=%s failed=%s", bid, sent, failed)
    return {"sent": sent, "failed": failed, "aborted": flag.aborted}
