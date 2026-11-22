"""Referral & conversion engine — invite → open → subscribe, tracked for real.

The royal metric (owner requirement, 2026-11-22): «أدعو 50 شخصاً لفتح باقة
يكون منهم 5 على الأقل مشتركين فعلاً». Every campaign tracks BOTH counts —
invited (joined the bot via the campaign link) and actually-subscribed
(granted a paid package) — and shows the conversion progress toward the
campaign targets with luxury bars.

Integrity rules (enforced server-side, no self-reporting possible):
  * capture happens ONLY in /start with the deep-link payload
  * first link wins — one invitee belongs to exactly one campaign
  * no self-referral (owner cannot be captured by their own campaign)
  * conversion is marked inside grant_subscription() — the same atomic
    transaction path used by payment approval and admin grants
"""
import secrets
import sqlite3

from fysb import config, db


def bot_username() -> str:
    """The public @username of the bot (from the fixed signature)."""
    return (config.BOT_SIGNATURE or "ForexYemeni1_BOT").lstrip("@").strip()


def new_code() -> str:
    """Short luxury campaign code — collision-safe via retry."""
    for _ in range(20):
        code = "R" + secrets.token_hex(3).upper()
        with db.conn() as c:
            if not c.execute("SELECT 1 FROM ref_campaigns WHERE code=?", (code,)).fetchone():
                return code
    return "R" + secrets.token_hex(6).upper()


def ensure_campaign(code: str, title: str, owner_id: int = 0,
                    target_invited: int = 50, target_converted: int = 5) -> dict:
    with db.tx() as c:
        c.execute("""INSERT OR IGNORE INTO ref_campaigns(code,title,owner_id,target_invited,
                     target_converted,enabled,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)""",
                  (code.strip().upper(), title, owner_id, target_invited, target_converted,
                   config.now(), config.now()))
        r = c.execute("SELECT * FROM ref_campaigns WHERE code=?", (code.strip().upper(),)).fetchone()
    return dict(r)


def get_campaign(cid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM ref_campaigns WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def get_campaign_by_code(code: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM ref_campaigns WHERE code=?", ((code or "").strip().upper(),)).fetchone()
    return dict(r) if r else None


def list_campaigns() -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM ref_campaigns ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.update(campaign_stats(d["id"]))
        out.append(d)
    return out


def link_for(code: str) -> str:
    return f"https://t.me/{bot_username()}?start=ref{code.strip().upper()}"


def capture(code: str, invitee_id: int) -> dict:
    """Record that *invitee_id* joined via the campaign deep-link payload.

    Returns {"captured": bool, "reason": str}. First link wins (UNIQUE on
    invitee_id), self-referral is impossible, disabled campaigns are dead.
    """
    camp = get_campaign_by_code(code)
    if not camp or not camp["enabled"]:
        return {"captured": False, "reason": "unknown_or_disabled"}
    if camp["owner_id"] and camp["owner_id"] == invitee_id:
        return {"captured": False, "reason": "self_referral"}
    try:
        with db.tx(immediate=True) as c:
            c.execute("""INSERT INTO referrals(campaign_id,invitee_id,invited_at)
                         VALUES(?,?,?)""", (camp["id"], invitee_id, config.now()))
        return {"captured": True, "campaign": camp["title"]}
    except sqlite3.IntegrityError:
        return {"captured": False, "reason": "already_linked"}


def mark_converted(user_id: int, plan_code: str) -> dict | None:
    """Mark the invitee's referral converted (idempotent, server-side).

    Called from grant_subscription(). Returns campaign info when a NEW
    conversion was recorded (for celebration/audit), None otherwise.
    """
    with db.conn() as c:
        r = c.execute("""SELECT rr.id, rr.converted, rc.id AS camp_id, rc.code, rc.title,
                         rc.owner_id, rc.target_converted
                         FROM referrals rr JOIN ref_campaigns rc ON rc.id=rr.campaign_id
                         WHERE rr.invitee_id=?""", (user_id,)).fetchone()
    if not r:
        return None
    if r["converted"]:
        return None  # already converted once — never double-count
    with db.tx(immediate=True) as c:
        cur = c.execute("""UPDATE referrals SET converted=1, converted_at=?, plan_code=?
                           WHERE id=? AND converted=0""",
                        (config.now(), plan_code, r["id"]))
        if cur.rowcount != 1:
            return None
        converted_total = c.execute("SELECT COUNT(*) n FROM referrals WHERE campaign_id=? AND converted=1",
                                    (r["camp_id"],)).fetchone()["n"]
    info = {**dict(r), "converted_total": converted_total,
            "target_met": converted_total >= (r["target_converted"] or 5)}
    # Notify the campaign owner (if a real user, not the house campaign).
    if r["owner_id"]:
        try:
            with db.conn() as c:
                c.execute("""INSERT INTO notification_events(user_id,kind,payload,read,created_at)
                             VALUES(?,?,?,?,?)""",
                          (r["owner_id"], "REFERRAL_CONVERTED",
                           f"{r['title']}:{converted_total}/{r['target_converted']}", 0, config.now()))
        except Exception:
            pass
    return info


def campaign_stats(cid: int) -> dict:
    with db.conn() as c:
        camp = c.execute("SELECT target_invited, target_converted FROM ref_campaigns WHERE id=?", (cid,)).fetchone()
        if not camp:
            return {}
        invited = c.execute("SELECT COUNT(*) n FROM referrals WHERE campaign_id=?", (cid,)).fetchone()["n"]
        converted = c.execute("SELECT COUNT(*) n FROM referrals WHERE campaign_id=? AND converted=1", (cid,)).fetchone()["n"]
    ti = max(1, int(camp["target_invited"] or 50))
    tc = max(1, int(camp["target_converted"] or 5))
    return {
        "invited": invited, "converted": converted,
        "rate": (converted / invited * 100.0) if invited else 0.0,
        "target_invited": ti, "target_converted": tc,
        "invited_progress": min(100.0, invited / ti * 100.0),
        "converted_progress": min(100.0, converted / tc * 100.0),
        "invited_met": invited >= ti, "converted_met": converted >= tc,
    }


def invitees(cid: int, limit: int = 10, offset: int = 0) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("""SELECT rr.*, u.username, u.full_name
                            FROM referrals rr LEFT JOIN users u ON u.user_id=rr.invitee_id
                            WHERE rr.campaign_id=? ORDER BY rr.id DESC LIMIT ? OFFSET ?""",
                         (cid, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def invitees_total(cid: int) -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM referrals WHERE campaign_id=?", (cid,)).fetchone()["n"]


def personal_campaign(user_id: int) -> dict:
    """Every user owns a personal invite campaign (targets: 50/5 royal standard)."""
    code = f"U{user_id}"
    camp = get_campaign_by_code(code)
    if camp:
        return camp
    return ensure_campaign(code, f"دعوات المستخدم {user_id}", owner_id=user_id, target_invited=50, target_converted=5)


def user_stats(user_id: int) -> dict:
    camp = personal_campaign(user_id)
    st = campaign_stats(camp["id"])
    st["code"] = camp["code"]
    st["link"] = link_for(camp["code"])
    return st


def set_targets(cid: int, target_invited: int | None = None, target_converted: int | None = None) -> None:
    with db.tx() as c:
        if target_invited is not None:
            c.execute("UPDATE ref_campaigns SET target_invited=?, updated_at=? WHERE id=?",
                      (max(1, int(target_invited)), config.now(), cid))
        if target_converted is not None:
            c.execute("UPDATE ref_campaigns SET target_converted=?, updated_at=? WHERE id=?",
                      (max(1, int(target_converted)), config.now(), cid))


def set_enabled(cid: int, enabled: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE ref_campaigns SET enabled=?, updated_at=? WHERE id=?",
                  (1 if enabled else 0, config.now(), cid))


def delete_campaign(cid: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM referrals WHERE campaign_id=?", (cid,))
        c.execute("DELETE FROM ref_campaigns WHERE id=?", (cid,))


def overall_stats() -> dict:
    with db.conn() as c:
        invited = c.execute("SELECT COUNT(*) n FROM referrals").fetchone()["n"]
        converted = c.execute("SELECT COUNT(*) n FROM referrals WHERE converted=1").fetchone()["n"]
        campaigns = c.execute("SELECT COUNT(*) n FROM ref_campaigns").fetchone()["n"]
    return {"invited": invited, "converted": converted,
            "rate": (converted / invited * 100.0) if invited else 0.0,
            "campaigns": campaigns}
