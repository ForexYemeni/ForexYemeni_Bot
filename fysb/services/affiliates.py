"""Tiered Affiliate & Commission System (v6.27.0: 15% -> 20% -> 25% -> 35%).

Features:
  * Automatic tier progression based on unique paid referrals:
      BRONZE (0-4 paid refs):             15%
      SILVER (5-14 paid refs):            20%
      GOLD   (15-29 paid refs):           25%
      ROYAL AMBASSADOR (30+ paid refs):   35%
  * Automatic idempotent commission crediting when any referred user's payment is approved.
  * User Affiliate Dashboard with personal referral link, balance, earnings history,
    and 1-tap Payout Request (USDT/TRC20 or wallet).
  * Admin Payout Queue with 1-tap Approve / Reject + Top Affiliates leaderboard.
"""
from __future__ import annotations

from fysb import config, db
from fysb.theme import bar, esc

# (min_paid_refs, code, arabic_label, rate_pct)
TIERS = (
    (0, "BRONZE", "🥉 برونزي", 15.0),
    (5, "SILVER", "🥈 فضي", 20.0),
    (15, "GOLD", "🥇 ذهبي", 25.0),
    (30, "ROYAL", "👑 سفير ملكي", 35.0),
)


def min_payout_usd() -> float:
    try:
        return max(1.0, float(db.get_setting("affiliate_min_payout_usd", "20") or "20"))
    except (TypeError, ValueError):
        return 20.0


def tier_for_count(paid_refs: int) -> dict:
    n = max(0, int(paid_refs))
    cur = TIERS[0]
    nxt = None
    for idx, t in enumerate(TIERS):
        if n >= t[0]:
            cur = t
            nxt = TIERS[idx + 1] if idx + 1 < len(TIERS) else None
    return {
        "code": cur[1],
        "label": cur[2],
        "rate_pct": cur[3],
        "paid_refs": n,
        "next_label": nxt[2] if nxt else None,
        "next_rate_pct": nxt[3] if nxt else None,
        "refs_to_next": max(0, nxt[0] - n) if nxt else 0,
    }


def referrer_of(referred_user_id: int) -> int | None:
    """Resolve who invited `referred_user_id` via `affiliate_links` or existing `referrals` campaigns."""
    uid = int(referred_user_id)
    with db.conn() as c:
        r = c.execute(
            "SELECT referrer_id FROM affiliate_links WHERE invitee_id=?",
            (uid,),
        ).fetchone()
        if r and r["referrer_id"]:
            inv = int(r["referrer_id"])
            return inv if inv != uid else None
        r2 = c.execute(
            """SELECT rc.owner_id FROM referrals r
               JOIN ref_campaigns rc ON rc.id = r.campaign_id
               WHERE r.invitee_id=? ORDER BY r.id ASC LIMIT 1""",
            (uid,),
        ).fetchone()
    if r2 and r2["owner_id"]:
        inv = int(r2["owner_id"])
        return inv if inv != uid else None
    return None


def bind_referral(referrer_id: int, invitee_id: int) -> bool:
    """Bind a user-to-user affiliate referral if invitee wasn't already referred."""
    ref_id = int(referrer_id)
    inv_id = int(invitee_id)
    if ref_id <= 0 or inv_id <= 0 or ref_id == inv_id:
        return False
    with db.conn() as c:
        exists = c.execute("SELECT 1 FROM affiliate_links WHERE invitee_id=?", (inv_id,)).fetchone()
        if exists:
            return False
        c.execute(
            "INSERT INTO affiliate_links(invitee_id,referrer_id,converted,created_at) VALUES(?,?,0,?)",
            (inv_id, ref_id, config.now()),
        )
        return True


def _paid_referral_count(referrer_id: int) -> int:
    with db.conn() as c:
        r = c.execute(
            "SELECT COUNT(DISTINCT referred_user_id) n FROM affiliate_commissions WHERE referrer_id=? AND status='CREDITED'",
            (int(referrer_id),),
        ).fetchone()
    return int(r["n"] or 0)


def record_commission_on_payment(
    referred_user_id: int,
    amount_usd: float,
    *,
    payment_id: int = 0,
    referrer_id: int | None = None,
) -> dict | None:
    """Idempotently credit tiered affiliate commission upon paid purchase."""
    amt = float(amount_usd or 0.0)
    if amt <= 0:
        return None
    ref_id = int(referrer_id) if referrer_id else referrer_of(referred_user_id)
    if not ref_id or ref_id == int(referred_user_id):
        return None

    with db.conn() as c:
        if payment_id:
            dup = c.execute(
                "SELECT id FROM affiliate_commissions WHERE payment_id=? AND referrer_id=?",
                (int(payment_id), ref_id),
            ).fetchone()
            if dup:
                return None

    # Include this referred user in tier calculation if new
    paid_refs = _paid_referral_count(ref_id)
    tier = tier_for_count(paid_refs)
    rate = tier["rate_pct"]
    comm = round(amt * (rate / 100.0), 2)
    now_iso = config.now()

    with db.conn() as c:
        c.execute(
            """INSERT INTO affiliate_commissions(
                 referrer_id,referred_user_id,payment_id,amount_usd,rate_pct,commission_usd,tier_name,status,created_at
               ) VALUES(?,?,?,?,?,?,?,'CREDITED',?)""",
            (ref_id, int(referred_user_id), int(payment_id), amt, rate, comm, tier["code"], now_iso),
        )
        c.execute(
            "UPDATE affiliate_links SET converted=1 WHERE referrer_id=? AND invitee_id=?",
            (ref_id, int(referred_user_id)),
        )

    return {
        "referrer_id": ref_id,
        "referred_user_id": int(referred_user_id),
        "amount_usd": amt,
        "rate_pct": rate,
        "commission_usd": comm,
        "tier": tier,
    }


def affiliate_stats(user_id: int) -> dict:
    uid = int(user_id)
    with db.conn() as c:
        total_refs = c.execute("SELECT COUNT(*) n FROM affiliate_links WHERE referrer_id=?", (uid,)).fetchone()["n"]
        paid_refs = c.execute(
            "SELECT COUNT(DISTINCT referred_user_id) n FROM affiliate_commissions WHERE referrer_id=? AND status='CREDITED'",
            (uid,),
        ).fetchone()["n"]
        earned = float(c.execute(
            "SELECT COALESCE(SUM(commission_usd), 0) s FROM affiliate_commissions WHERE referrer_id=? AND status='CREDITED'",
            (uid,),
        ).fetchone()["s"] or 0.0)
        paid_out = float(c.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) s FROM affiliate_payouts WHERE user_id=? AND status='APPROVED'",
            (uid,),
        ).fetchone()["s"] or 0.0)
        pending_out = float(c.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) s FROM affiliate_payouts WHERE user_id=? AND status='PENDING'",
            (uid,),
        ).fetchone()["s"] or 0.0)
        recent = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM affiliate_commissions WHERE referrer_id=? ORDER BY id DESC LIMIT 5",
                (uid,),
            ).fetchall()
        ]
    avail = round(max(0.0, earned - paid_out - pending_out), 2)
    tier = tier_for_count(paid_refs)
    return {
        "user_id": uid,
        "total_refs": int(total_refs),
        "paid_refs": int(paid_refs),
        "tier": tier,
        "total_earned": round(earned, 2),
        "paid_out": round(paid_out, 2),
        "pending_out": round(pending_out, 2),
        "available_usd": avail,
        "min_payout_usd": min_payout_usd(),
        "recent": recent,
    }


def request_payout(user_id: int, wallet_address: str, amount_usd: float | None = None) -> dict:
    wallet = (wallet_address or "").strip()
    if len(wallet) < 8:
        return {"ok": False, "error": "يرجى إدخال عنوان محفظة (USDT TRC20 أو حساب استلام) صحيح."}
    st = affiliate_stats(user_id)
    avail = st["available_usd"]
    min_p = st["min_payout_usd"]
    req_amt = round(float(amount_usd), 2) if amount_usd is not None else avail
    if req_amt < min_p:
        return {"ok": False, "error": f"الحد الأدنى لطلب السحب هو {min_p:g}$ (رصيدك المتاح حالياً: {avail:.2f}$)."}
    if req_amt > avail:
        return {"ok": False, "error": f"المبلغ المطلوب ({req_amt:.2f}$) يتجاوز رصيدك المتاح ({avail:.2f}$)."}
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO affiliate_payouts(user_id,amount_usd,wallet_address,status,created_at) VALUES(?,?,?,'PENDING',?)",
            (int(user_id), req_amt, wallet[:160], config.now()),
        )
        pid = int(cur.lastrowid)
    return {"ok": True, "payout_id": pid, "amount_usd": req_amt, "wallet_address": wallet[:160]}


def list_payouts(status: str | None = "PENDING", limit: int = 15) -> list[dict]:
    sql = "SELECT * FROM affiliate_payouts"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status.upper())
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]


def approve_payout(payout_id: int, admin_note: str = "") -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM affiliate_payouts WHERE id=? AND status='PENDING'", (int(payout_id),)).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE affiliate_payouts SET status='APPROVED', admin_note=?, processed_at=? WHERE id=?",
            ((admin_note or "")[:200], config.now(), int(payout_id)),
        )
        return dict(row)


def reject_payout(payout_id: int, admin_note: str = "") -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM affiliate_payouts WHERE id=? AND status='PENDING'", (int(payout_id),)).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE affiliate_payouts SET status='REJECTED', admin_note=?, processed_at=? WHERE id=?",
            ((admin_note or "")[:200], config.now(), int(payout_id)),
        )
        return dict(row)


def top_affiliates(limit: int = 8) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            """SELECT referrer_id,
                      COUNT(DISTINCT referred_user_id) AS paid_refs,
                      COALESCE(SUM(commission_usd), 0) AS total_earned
               FROM affiliate_commissions
               WHERE status='CREDITED'
               GROUP BY referrer_id
               ORDER BY total_earned DESC
               LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["total_earned"] = round(float(d.get("total_earned") or 0.0), 2)
        d["tier"] = tier_for_count(d["paid_refs"])
        out.append(d)
    return out


def render_affiliate_dashboard(user_id: int, bot_username: str = "") -> str:
    st = affiliate_stats(user_id)
    t = st["tier"]
    link = f"https://t.me/{bot_username}?start=ref_{user_id}" if bot_username else f"/start ref_{user_id}"
    next_line = (
        f"▎🚀 للترقية إلى <b>{t['next_label']} ({t['next_rate_pct']:g}%)</b>: تبقّى <b>{t['refs_to_next']}</b> مشترك مدفوع"
        if t["next_label"]
        else "▎👑 <b>وصلت لأعلى رتبة سفير ملكي (35% عمولة دائمة)!</b>"
    )
    return (
        "🤝 <b><u>برنامج الشركاء والعمولات المتدرج (15% → 35%)</u></b>\n"
        "<blockquote>"
        f"▎🏅 رتبتك الحالية: <b>{t['label']}</b> — نسبة العمولة: <b>{t['rate_pct']:g}%</b>\n"
        f"{next_line}\n"
        f"▎👥 إجمالي المدعوين: <b>{st['total_refs']}</b> · المشتركون المدفوعون: <b>{st['paid_refs']}</b>\n"
        f"▎💰 إجمالي أرباحك: <b>{st['total_earned']:,.2f} $</b>\n"
        f"▎✅ المتاح للسحب الآن: <b>{st['available_usd']:,.2f} $</b> (قيد المراجعة: {st['pending_out']:,.2f}$ · مسحوب: {st['paid_out']:,.2f}$)\n"
        f"▎💳 الحد الأدنى للسحب: <b>{st['min_payout_usd']:g} $</b>"
        "</blockquote>\n\n"
        f"🔗 <b>رابط دعوتك الخاص:</b>\n<code>{esc(link)}</code>\n"
        "<i>✨ كل مشترك يدخل من رابطك ويشتري أي باقة أو أكاديمية تُضاف عمولتك تلقائياً لرصيدك!</i>"
    )


def render_admin_affiliates_card() -> str:
    pending = list_payouts("PENDING", 10)
    top = top_affiliates(6)
    lines = [
        "🤝 <b><u>إدارة الشركاء والعمولات (15%–35%) وطلبات السحب</u></b>",
        f"<blockquote>▎⏳ طلبات سحب بانتظار الاعتماد: <b>{len(pending)}</b> · الحد الأدنى للسحب: <b>{min_payout_usd():g} $</b></blockquote>",
    ]
    if pending:
        lines.append("\n<b>💸 طلبات السحب المعلقة:</b>")
        for p in pending[:6]:
            lines.append(
                f"  • #{p['id']} · UID <code>{p['user_id']}</code> · <b>{p['amount_usd']:,.2f}$</b> · "
                f"<code>{esc(p['wallet_address'])}</code>"
            )
    if top:
        lines.append("\n<b>🏆 أفضل الشركاء:</b>")
        for i, row in enumerate(top, 1):
            lines.append(
                f"  {i}. UID <code>{row['referrer_id']}</code> · {row['tier']['label']} ({row['tier']['rate_pct']:g}%) · "
                f"<b>{row['total_earned']:,.2f}$</b> ({row['paid_refs']} مشترك)"
            )
    if not pending and not top:
        lines.append("\n<i>لا توجد عمولات أو طلبات سحب مسجلة بعد.</i>")
    return "\n".join(lines)
