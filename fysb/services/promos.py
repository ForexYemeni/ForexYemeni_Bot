"""Percentage Promo Codes & Telegram Stars (XTR) Pricing Service (v7.6.0).

Supports:
  - Creating/toggling/deleting % discount promo codes (e.g. ROYAL30 = 30% OFF)
  - Validating code existence, active flag, expiry, max_uses, and per-user reuse
  - Computing exact discounted USD & Telegram Stars (XTR) checkout amounts
  - Recording redemptions atomically upon payment approval / instant Stars payment
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fysb import config, db

_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,24}$")


def stars_enabled() -> bool:
    return (db.get_setting("stars_enabled", "1") or "1") == "1"


def stars_per_usd() -> int:
    try:
        v = int(float(db.get_setting("stars_per_usd", "50") or 50))
        return max(1, min(500, v))
    except Exception:
        return 50


def plan_stars_price(price_usd: float) -> int:
    """Convert a USD plan price into Telegram Stars (XTR) integer amount."""
    p = max(0.0, float(price_usd or 0.0))
    if p <= 0:
        return 0
    return max(1, int(round(p * stars_per_usd())))


def normalize_code(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "").strip().upper())


def create_promo(code: str, discount_pct: int, max_uses: int = 100,
                 expires_days: int = 30, created_by: int = 0) -> dict:
    c_norm = normalize_code(code)
    if not _CODE_RE.match(c_norm):
        return {"ok": False, "error": "صيغة الكود يجب أن تكون أحرفاً إنجليزية/أرقام (3-24 خانة)"}
    pct = max(1, min(95, int(discount_pct or 10)))
    mu = max(1, min(100000, int(max_uses or 100)))
    exp = ""
    if int(expires_days or 0) > 0:
        exp = (datetime.now(timezone.utc) + timedelta(days=int(expires_days))).isoformat()
    now = config.now()
    try:
        with db.tx() as c:
            c.execute(
                "INSERT INTO promo_codes(code,discount_pct,max_uses,used_count,expires_at,active,created_by,created_at) "
                "VALUES(?,?,?,0,?,1,?,?)",
                (c_norm, pct, mu, exp, int(created_by or 0), now),
            )
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            return {"ok": False, "error": f"كود الخصم {c_norm} موجود مسبقاً"}
        return {"ok": False, "error": str(e)}
    return {"ok": True, "code": c_norm, "discount_pct": pct, "max_uses": mu, "expires_at": exp}


def list_promos(active_only: bool = False) -> list[dict]:
    q = "SELECT * FROM promo_codes"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY id DESC LIMIT 100"
    with db.conn() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def toggle_promo(code: str) -> bool:
    c_norm = normalize_code(code)
    with db.tx() as c:
        row = c.execute("SELECT active FROM promo_codes WHERE code=?", (c_norm,)).fetchone()
        if not row:
            return False
        nxt = 0 if int(row["active"] or 0) else 1
        c.execute("UPDATE promo_codes SET active=? WHERE code=?", (nxt, c_norm))
    return True


def delete_promo(code: str) -> bool:
    c_norm = normalize_code(code)
    with db.tx() as c:
        cur = c.execute("DELETE FROM promo_codes WHERE code=?", (c_norm,))
        return cur.rowcount > 0


def validate_promo(code: str, user_id: int = 0) -> dict:
    c_norm = normalize_code(code)
    if not c_norm:
        return {"ok": False, "error": "أدخل كود خصم صالحاً"}
    with db.conn() as c:
        row = c.execute("SELECT * FROM promo_codes WHERE code=?", (c_norm,)).fetchone()
        if not row:
            return {"ok": False, "error": "كود الخصم غير موجود"}
        row = dict(row)
        if not int(row.get("active") or 0):
            return {"ok": False, "error": "كود الخصم متوقف حالياً"}
        exp = str(row.get("expires_at") or "").strip()
        if exp and exp < config.now():
            return {"ok": False, "error": "انتهت صلاحية كود الخصم"}
        if int(row.get("used_count") or 0) >= int(row.get("max_uses") or 1):
            return {"ok": False, "error": "تم استنفاد الحد الأقصى لاستخدام هذا الكود"}
        if user_id:
            prev = c.execute(
                "SELECT id FROM promo_redemptions WHERE code=? AND user_id=?",
                (c_norm, int(user_id)),
            ).fetchone()
            if prev:
                return {"ok": False, "error": "لقد استخدمت هذا الكود مسبقاً"}
    return {
        "ok": True,
        "code": c_norm,
        "discount_pct": int(row["discount_pct"]),
        "remaining": max(0, int(row["max_uses"]) - int(row["used_count"])),
    }


def apply_discount(amount_usd: float, discount_pct: int) -> tuple[float, float]:
    """Return (final_usd, saved_usd) rounded to 2 decimals."""
    orig = max(0.0, float(amount_usd or 0.0))
    pct = max(0, min(95, int(discount_pct or 0)))
    saved = round(orig * (pct / 100.0), 2)
    final = round(max(0.01 if orig > 0 else 0.0, orig - saved), 2)
    return final, saved


def redeem_promo(code: str, user_id: int, payment_id: int = 0,
                 plan_code: str = "", saved_amount: float = 0.0) -> bool:
    c_norm = normalize_code(code)
    if not c_norm:
        return False
    v = validate_promo(c_norm, user_id=user_id)
    if not v.get("ok"):
        return False
    with db.tx(immediate=True) as c:
        row = c.execute("SELECT discount_pct, used_count, max_uses, active FROM promo_codes WHERE code=?", (c_norm,)).fetchone()
        if not row or not int(row["active"] or 0) or int(row["used_count"]) >= int(row["max_uses"]):
            return False
        c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (c_norm,))
        c.execute(
            "INSERT INTO promo_redemptions(code,user_id,payment_id,plan_code,discount_pct,saved_amount,redeemed_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (c_norm, int(user_id), int(payment_id or 0), str(plan_code or ""),
             int(row["discount_pct"]), float(saved_amount or 0.0), config.now()),
        )
    return True
