"""Gift Subscriptions & Redeem Codes Service (v6.27.0).

Supports:
  * ANY target plan: PRO, VIP, ROYAL, or ACADEMY (independent Paid Academy pass).
  * ANY custom duration in days chosen by Admin (e.g. 3d, 7d, 14d, 30d, 90d, 365d, or custom).
  * Single-use or multi-use Redeem Codes (`FY-XXXX-XXXX`).
  * Direct UID gifting straight to any user.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.theme import esc

VALID_TARGETS = ("PRO", "VIP", "ROYAL", "ACADEMY")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen_code() -> str:
    p1 = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    p2 = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return f"FY-{p1}-{p2}"


def normalize_target(plan: str) -> str:
    p = (plan or "").strip().upper()
    if p not in VALID_TARGETS:
        raise ValueError(f"Unsupported gift target plan: {plan}")
    return p


def create_gift_code(
    target_plan: str,
    duration_days: int,
    *,
    max_uses: int = 1,
    note: str = "",
    created_by: int = 0,
    expires_in_days: int = 0,
    custom_code: str = "",
) -> dict:
    """Create a new redeemable gift code for ANY plan and ANY custom duration."""
    target = normalize_target(target_plan)
    days = max(0 if target == "ACADEMY" else 1, min(3650, int(duration_days)))
    uses_lim = max(1, min(10000, int(max_uses)))
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    exp_iso = (now_dt + timedelta(days=int(expires_in_days))).isoformat() if int(expires_in_days or 0) > 0 else ""

    code = (custom_code or "").strip().upper()
    if not code:
        code = _gen_code()

    with db.conn() as c:
        # Ensure uniqueness
        while c.execute("SELECT 1 FROM gift_codes WHERE code=?", (code,)).fetchone():
            code = _gen_code()
        cur = c.execute(
            """INSERT INTO gift_codes(code,target_plan,duration_days,max_uses,uses_count,note,created_by,active,created_at,expires_at)
               VALUES(?,?,?,?,0,?,?,1,?,?)""",
            (code, target, days, uses_lim, (note or "")[:200], int(created_by), now_iso, exp_iso),
        )
        cid = int(cur.lastrowid)
    return {
        "id": cid,
        "code": code,
        "target_plan": target,
        "duration_days": days,
        "max_uses": uses_lim,
        "note": note,
        "expires_at": exp_iso,
    }


def _apply_gift_entitlement(user_id: int, target_plan: str, duration_days: int, source: str, granted_by: int = 0) -> dict:
    target = normalize_target(target_plan)
    if target == "ACADEMY":
        from fysb.services import academy
        return academy.grant_paid_access(user_id, duration_days=duration_days, source=source, granted_by=granted_by)
    from fysb.services import subscriptions
    days_clean = max(1, int(duration_days))
    res = subscriptions.grant_subscription(
        int(user_id),
        target,
        source=source,
        actor=int(granted_by),
        override_days=days_clean,
    )
    return {"user_id": int(user_id), "target_plan": target, "duration_days": days_clean, "sub": res}


def gift_direct(
    user_id: int,
    target_plan: str,
    duration_days: int,
    *,
    granted_by: int = 0,
    note: str = "",
) -> dict:
    """Directly gift ANY plan (PRO/VIP/ROYAL/ACADEMY) for ANY custom duration to a UID."""
    target = normalize_target(target_plan)
    days = max(0 if target == "ACADEMY" else 1, min(3650, int(duration_days)))
    ent = _apply_gift_entitlement(user_id, target, days, source="GIFT_DIRECT", granted_by=granted_by)
    return {
        "ok": True,
        "user_id": int(user_id),
        "target_plan": target,
        "duration_days": days,
        "note": note,
        "entitlement": ent,
    }


def redeem_code(user_id: int, raw_code: str) -> dict:
    """Atomically validate & redeem a gift code for `user_id`."""
    code = (raw_code or "").strip().upper()
    if not code:
        return {"ok": False, "error": "يرجى إدخال كود الهدية بشكل صحيح."}

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    with db.conn() as c:
        row = c.execute("SELECT * FROM gift_codes WHERE UPPER(code)=?", (code,)).fetchone()
        if not row:
            return {"ok": False, "error": "كود الهدية غير موجود — تأكد من كتابته بدقة."}
        g = dict(row)
        if int(g.get("active") or 0) != 1:
            return {"ok": False, "error": "هذا الكود متوقف حالياً."}
        if g.get("expires_at"):
            try:
                exp = datetime.fromisoformat(str(g["expires_at"]).replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= now_dt:
                    return {"ok": False, "error": "انتهت صلاحية هذا الكود."}
            except Exception:
                pass
        if int(g.get("uses_count") or 0) >= int(g.get("max_uses") or 1):
            return {"ok": False, "error": "تم استنفاد عدد استخدامات هذا الكود بالكامل."}
        already = c.execute(
            "SELECT 1 FROM gift_redemptions WHERE code_id=? AND user_id=?",
            (g["id"], int(user_id)),
        ).fetchone()
        if already:
            return {"ok": False, "error": "لقد استخدمت هذا الكود مسبقاً بالفعل."}

        # Atomic increment & redemption log
        cur = c.execute(
            "UPDATE gift_codes SET uses_count = uses_count + 1 WHERE id=? AND uses_count < max_uses AND active=1",
            (g["id"],),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": "تم استنفاد هذا الكود للتو."}
        c.execute(
            "INSERT INTO gift_redemptions(code_id,user_id,target_plan,duration_days,redeemed_at) VALUES(?,?,?,?,?)",
            (g["id"], int(user_id), g["target_plan"], g["duration_days"], now_iso),
        )

    ent = _apply_gift_entitlement(
        user_id,
        g["target_plan"],
        int(g["duration_days"]),
        source=f"GIFT:{code}",
        granted_by=int(g.get("created_by") or 0),
    )
    return {
        "ok": True,
        "code": code,
        "target_plan": g["target_plan"],
        "duration_days": int(g["duration_days"]),
        "note": g.get("note") or "",
        "entitlement": ent,
    }


def list_gift_codes(limit: int = 15) -> list[dict]:
    with db.conn() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM gift_codes ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        ]


def deactivate_code(code_id: int) -> bool:
    with db.conn() as c:
        cur = c.execute("UPDATE gift_codes SET active=0 WHERE id=?", (int(code_id),))
        return cur.rowcount > 0


def target_label(target_plan: str) -> str:
    return {
        "PRO": "💎 باقة PRO",
        "VIP": "🔥 باقة VIP",
        "ROYAL": "👑 باقة ROYAL",
        "ACADEMY": "🎓 الأكاديمية الاحترافية (مستقلة)",
    }.get((target_plan or "").upper(), target_plan)


def render_gift_received_card(target_plan: str, duration_days: int, note: str = "", code: str = "") -> str:
    dur_txt = "مدى الحياة ♾" if int(duration_days) == 0 else f"{int(duration_days)} يوماً"
    extra = f"\n▎📝 رسالة الإهداء: <i>{esc(note)}</i>" if note else ""
    code_line = f"\n▎🎟 الكود: <code>{esc(code)}</code>" if code else ""
    return (
        "🎁 <b><u>مبروك! تم تفعيل اشتراك هدية ملكية لحسابك</u></b>\n"
        "<blockquote>"
        f"▎🏆 الهدية: <b>{target_label(target_plan)}</b>\n"
        f"▎⏳ المدة المفعّلة: <b>{dur_txt}</b>{code_line}{extra}\n"
        "▎✨ استمتع بكامل المزايا فوراً — تمت الإضافة تلقائياً إلى حسابك."
        "</blockquote>"
    )


def render_admin_gifts_card() -> str:
    codes = list_gift_codes(10)
    lines = [
        "🎁 <b><u>إدارة الاشتراكات الهدية وأكواد الاسترداد</u></b>",
        "<blockquote>▎يمكنك إصدار كود هدية أو إهداء مستخدم مباشرة لأي باقة (PRO / VIP / ROYAL / ACADEMY) وبأي مدة تختارها.</blockquote>",
    ]
    if codes:
        lines.append("\n<b>🎟 أحدث الأكواد الصادرة:</b>")
        for g in codes[:8]:
            st = "🟢" if (g["active"] and g["uses_count"] < g["max_uses"]) else "⚪"
            dur = "♾" if int(g["duration_days"]) == 0 else f"{g['duration_days']}d"
            lines.append(
                f"  {st} <code>{esc(g['code'])}</code> · <b>{esc(g['target_plan'])}</b> ({dur}) · "
                f"{g['uses_count']}/{g['max_uses']}"
            )
    else:
        lines.append("\n<i>لم يتم إصدار أكواد هدية بعد.</i>")
    return "\n".join(lines)
