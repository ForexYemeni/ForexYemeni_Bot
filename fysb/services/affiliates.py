"""v8.7.0 — Unified Royal User Commission & Plan-Unlock Engine.

Features:
  * Admin-configurable commission engine:
      - Global ON/OFF toggle (`affiliate_enabled`)
      - Mode: TIERED (Bronze 15% -> Silver 20% -> Gold 25% -> Royal 35%, every rate editable!)
              OR FLAT (custom single % for all users, 1%..60%)
      - Editable minimum cash payout (`affiliate_min_payout_usd`, default 20$)
  * Feature A — Balance-to-Plan Instant Unlock (`redeem_balance_for_plan`):
      - Users can spend their available commission balance ($0 cash out for Admin!)
        to instantly unlock any paid Plan (PRO / VIP), «👑 تحليل ForexYemeni الحقيقي»,
        or «🎓 الأكاديمية الملكية» with atomic balance deduction & double-spend guard.
  * Feature B — Paid-Referral Auto-Unlock Milestones (`evaluate_milestone_unlock`):
      - Strictly based on UNIQUE PAID REFERRALS ONLY (immune to fake free-account abuse!).
      - Default: every 3 unique paid referrals automatically unlocks PRO for 30 days
        (Admin can change target to PRO/VIP/TRUE_ANALYSIS/ACADEMY, threshold, days, or OFF).
  * Unified cross-table first-link-wins & accurate stats across `affiliate_links` + `referrals`.
"""
from __future__ import annotations

import math

from fysb import config, db
from fysb.theme import bar, esc

# Default tiers: (min_paid_refs, code, arabic_label, default_rate_pct)
TIERS = (
    (0, "BRONZE", "🥉 برونزي", 15.0),
    (5, "SILVER", "🥈 فضي", 20.0),
    (15, "GOLD", "🥇 ذهبي", 25.0),
    (30, "ROYAL", "👑 سفير ملكي", 35.0),
)

_TIER_SETTING_KEYS = {
    "BRONZE": ("affiliate_tier_bronze", 15.0),
    "SILVER": ("affiliate_tier_silver", 20.0),
    "GOLD": ("affiliate_tier_gold", 25.0),
    "ROYAL": ("affiliate_tier_royal", 35.0),
}

MILESTONE_TARGETS = ("PRO", "VIP", "TRUE_ANALYSIS", "ACADEMY")
MILESTONE_DAYS_CHOICES = (7, 15, 30, 60)


def _float_setting(key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(db.get_setting(key, str(default)) or default)
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _int_setting(key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float(db.get_setting(key, str(default)) or default))
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def min_payout_usd() -> float:
    return _float_setting("affiliate_min_payout_usd", 20.0, 1.0, 5000.0)


def target_label(code: str) -> str:
    c = str(code or "PRO").strip().upper()
    if c == "TRUE_ANALYSIS":
        return "👑 تحليل ForexYemeni الحقيقي"
    if c == "ACADEMY":
        return "🎓 الأكاديمية الملكية"
    with db.conn() as c2:
        r = c2.execute("SELECT title FROM plans WHERE UPPER(code)=?", (c,)).fetchone()
    if r and r["title"]:
        return f"💎 باقة {r['title']} ({c})"
    return f"💎 باقة {c}"


def get_config() -> dict:
    """Return full Admin-configurable commission & plan-unlock settings."""
    mode = str(db.get_setting("affiliate_mode", "TIERED") or "TIERED").strip().upper()
    if mode not in ("TIERED", "FLAT"):
        mode = "TIERED"
    target = str(db.get_setting("affiliate_milestone_target", "PRO") or "PRO").strip().upper()
    if target not in MILESTONE_TARGETS:
        target = "PRO"
    rates = {
        code: _float_setting(k, default, 1.0, 60.0)
        for code, (k, default) in _TIER_SETTING_KEYS.items()
    }
    return {
        "enabled": db.get_setting("affiliate_enabled", "1") == "1",
        "mode": mode,
        "flat_pct": _float_setting("affiliate_flat_pct", 15.0, 1.0, 60.0),
        "tier_rates": rates,
        "min_payout_usd": min_payout_usd(),
        "balance_unlock_enabled": db.get_setting("affiliate_balance_unlock_enabled", "1") == "1",
        "milestone_enabled": db.get_setting("affiliate_milestone_enabled", "1") == "1",
        "milestone_paid_refs": _int_setting("affiliate_milestone_paid_refs", 3, 1, 100),
        "milestone_target": target,
        "milestone_days": _int_setting("affiliate_milestone_days", 30, 1, 365),
    }


def set_config(**kwargs) -> dict:
    """Persist validated affiliate/commission/plan-unlock settings."""
    if "enabled" in kwargs:
        db.set_setting("affiliate_enabled", "1" if kwargs["enabled"] else "0")
    if "mode" in kwargs:
        m = str(kwargs["mode"] or "TIERED").strip().upper()
        db.set_setting("affiliate_mode", m if m in ("TIERED", "FLAT") else "TIERED")
    if "flat_pct" in kwargs:
        v = max(1.0, min(60.0, float(kwargs["flat_pct"])))
        db.set_setting("affiliate_flat_pct", f"{v:g}")
    for code, (skey, _) in _TIER_SETTING_KEYS.items():
        arg_k = f"tier_{code.lower()}"
        if arg_k in kwargs:
            v = max(1.0, min(60.0, float(kwargs[arg_k])))
            db.set_setting(skey, f"{v:g}")
    if "min_payout_usd" in kwargs:
        v = max(1.0, min(5000.0, float(kwargs["min_payout_usd"])))
        db.set_setting("affiliate_min_payout_usd", f"{v:g}")
    if "balance_unlock_enabled" in kwargs:
        db.set_setting("affiliate_balance_unlock_enabled", "1" if kwargs["balance_unlock_enabled"] else "0")
    if "milestone_enabled" in kwargs:
        db.set_setting("affiliate_milestone_enabled", "1" if kwargs["milestone_enabled"] else "0")
    if "milestone_paid_refs" in kwargs:
        v = max(1, min(100, int(kwargs["milestone_paid_refs"])))
        db.set_setting("affiliate_milestone_paid_refs", str(v))
    if "milestone_target" in kwargs:
        t = str(kwargs["milestone_target"] or "PRO").strip().upper()
        if t in MILESTONE_TARGETS:
            db.set_setting("affiliate_milestone_target", t)
    if "milestone_days" in kwargs:
        v = max(1, min(365, int(kwargs["milestone_days"])))
        db.set_setting("affiliate_milestone_days", str(v))
    return get_config()


def dynamic_tiers(cfg: dict | None = None) -> tuple[tuple[int, str, str, float], ...]:
    """Tier table (min_refs, code, label, rate). v8.9.0: accepts a pre-loaded
    config so callers that already hold one never re-read 13 settings again."""
    cfg = cfg or get_config()
    rates = cfg["tier_rates"]
    return tuple(
        (min_refs, code, label, rates.get(code, default_rate))
        for min_refs, code, label, default_rate in TIERS
    )


def tier_for_count(paid_refs: int, cfg: dict | None = None) -> dict:
    """Rank for a paid-referral count.

    v8.9.0: `cfg` may be passed in. It used to call get_config() here AND again
    inside dynamic_tiers() → 24 needless settings reads per single rank lookup.
    """
    n = max(0, int(paid_refs))
    cfg = cfg or get_config()
    if cfg["mode"] == "FLAT":
        flat = cfg["flat_pct"]
        return {
            "code": "FLAT",
            "label": "💎 شريك موحّد",
            "rate_pct": flat,
            "paid_refs": n,
            "next_label": None,
            "next_rate_pct": None,
            "refs_to_next": 0,
        }
    tiers = dynamic_tiers(cfg)
    cur = tiers[0]
    nxt = None
    for idx, t in enumerate(tiers):
        if n >= t[0]:
            cur = t
            nxt = tiers[idx + 1] if idx + 1 < len(tiers) else None
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
    """Bind a user-to-user affiliate referral if invitee wasn't already referred
    across EITHER `affiliate_links` OR `referrals` (strict first-link-wins!)."""
    ref_id = int(referrer_id)
    inv_id = int(invitee_id)
    if ref_id <= 0 or inv_id <= 0 or ref_id == inv_id:
        return False
    with db.tx(immediate=True) as c:
        if c.execute("SELECT 1 FROM affiliate_links WHERE invitee_id=?", (inv_id,)).fetchone():
            return False
        # v8.7.0 Cross-table first-link-wins: never hijack a Channel Partner / campaign invitee!
        if c.execute("SELECT 1 FROM referrals WHERE invitee_id=?", (inv_id,)).fetchone():
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


def _grant_reward_product(user_id: int, target_code: str, duration_days: int, *, source: str) -> dict:
    """Grant a subscription Plan, True Analysis, or Academy cleanly."""
    uid = int(user_id)
    code = str(target_code or "PRO").strip().upper()
    days = max(1, int(duration_days or 30))
    if code == "TRUE_ANALYSIS":
        from fysb.services import true_analysis as _ta
        _ta.grant_access(uid, days=days, actor=0)
        return {"ok": True, "type": "TRUE_ANALYSIS", "code": code, "days": days, "label": target_label(code)}
    if code == "ACADEMY":
        from fysb.services import academy as _acad
        _acad.grant_paid_access(uid, source=source, granted_by=0)
        return {"ok": True, "type": "ACADEMY", "code": code, "days": 0, "label": target_label(code)}
    from fysb.services import subscriptions as _subs
    res = _subs.grant_subscription(uid, code, source=source, actor=0, override_days=days)
    if not res or not res.get("ok"):
        return {"ok": False, "error": (res or {}).get("error") or "تعذر تفعيل الباقة المطلوبة."}
    return {"ok": True, "type": "PLAN", "code": code, "days": days, "label": target_label(code)}


def evaluate_milestone_unlock(referrer_id: int) -> dict | None:
    """v8.7.0 Feature B: Auto-unlock a reward Plan strictly on UNIQUE PAID REFERRALS.
    Fires idempotently once per milestone step (e.g., 3, 6, 9 paid referrals)."""
    cfg = get_config()
    if not cfg["enabled"] or not cfg["milestone_enabled"]:
        return None
    uid = int(referrer_id)
    if uid <= 0 or uid == config.ADMIN_ID:
        return None
    # Do not grant user-plan milestones to Channel Partners (they have separate Partner accounts)
    with db.conn() as c:
        is_p = c.execute("SELECT 1 FROM channel_partners WHERE user_id=? AND enabled=1", (uid,)).fetchone()
    if is_p:
        return None

    threshold = max(1, int(cfg["milestone_paid_refs"]))
    paid_refs = _paid_referral_count(uid)
    step = paid_refs // threshold
    if step < 1:
        return None

    target = cfg["milestone_target"]
    days = cfg["milestone_days"]
    now_iso = config.now()

    try:
        with db.tx(immediate=True) as c:
            if c.execute(
                "SELECT 1 FROM affiliate_milestones WHERE user_id=? AND milestone_step=?",
                (uid, step),
            ).fetchone():
                return None
            c.execute(
                """INSERT INTO affiliate_milestones(
                     user_id, milestone_step, paid_refs, target_code, duration_days, created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (uid, step, paid_refs, target, days, now_iso),
            )
    except db.IntegrityError:
        return None

    grant = _grant_reward_product(uid, target, days, source="AFFILIATE_MILESTONE")
    if not grant.get("ok"):
        with db.conn() as c:
            c.execute(
                "DELETE FROM affiliate_milestones WHERE user_id=? AND milestone_step=?",
                (uid, step),
            )
        return None
    return {
        "user_id": uid,
        "step": step,
        "paid_refs": paid_refs,
        "threshold": threshold,
        "target_code": target,
        "target_label": grant["label"],
        "duration_days": days,
    }


def record_commission_on_payment(
    referred_user_id: int,
    amount_usd: float,
    *,
    payment_id: int = 0,
    referrer_id: int | None = None,
) -> dict | None:
    """Idempotently credit user affiliate commission upon paid purchase and
    evaluate paid-referral auto-unlock milestones."""
    cfg = get_config()
    if not cfg["enabled"]:
        return None
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

    milestone_unlocked = evaluate_milestone_unlock(ref_id)

    return {
        "referrer_id": ref_id,
        "referred_user_id": int(referred_user_id),
        "amount_usd": amt,
        "rate_pct": rate,
        "commission_usd": comm,
        "tier": tier,
        "milestone_unlocked": milestone_unlocked,
    }


def _compute_balances_conn(c, uid: int) -> tuple[float, float, float, float, float]:
    """Return (earned, paid_out, pending_out, redeemed_usd, available_usd) inside an open conn."""
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
    redeemed = float(c.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) s FROM affiliate_redemptions WHERE user_id=?",
        (uid,),
    ).fetchone()["s"] or 0.0)
    avail = round(max(0.0, earned - paid_out - pending_out - redeemed), 2)
    return round(earned, 2), round(paid_out, 2), round(pending_out, 2), round(redeemed, 2), avail


def affiliate_stats(user_id: int) -> dict:
    """Unified commissions/referrals snapshot for one user.

    v8.9.0 performance rewrite — identical numbers, 1 connection instead of 38:
      * The two invitation sets (`affiliate_links` + personal `ref_campaigns`)
        are unioned in ONE SQL statement instead of two round-trips + Python set
        work, and the paid-referral count is folded into the same query.
      * All four money sums (earned / approved / pending / redeemed) come from a
        single UNION ALL statement instead of 4 separate SELECTs.
      * `get_config()` is read ONCE and threaded into `tier_for_count`, which
        used to re-read it (and `dynamic_tiers` a third time) for one rank.
      * `target_label()` (another connection) is only hit when the plan label
        is actually needed, never for a plain stats read.
    """
    uid = int(user_id)
    cfg = get_config()
    with db.conn() as c:
        # Identical semantics to the old Python sets: total = DISTINCT invitees
        # reachable through BOTH link tables; paid = DISTINCT commissioned payers.
        agg = c.execute(
            """SELECT
                 (SELECT COUNT(*) FROM (
                    SELECT invitee_id FROM affiliate_links WHERE referrer_id=?
                    UNION
                    SELECT r.invitee_id FROM referrals r
                      JOIN ref_campaigns rc ON rc.id = r.campaign_id
                     WHERE rc.owner_id=?
                 ) inv) AS total_refs,
                 (SELECT COUNT(DISTINCT referred_user_id) FROM affiliate_commissions
                   WHERE referrer_id=? AND status='CREDITED') AS paid_refs""",
            (uid, uid, uid),
        ).fetchone()
        total_refs = int((agg["total_refs"] if agg else 0) or 0)
        paid_refs = int((agg["paid_refs"] if agg else 0) or 0)
        money = c.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN kind='E' THEN amount ELSE 0 END),0) AS earned,
                 COALESCE(SUM(CASE WHEN kind='P' THEN amount ELSE 0 END),0) AS paid_out,
                 COALESCE(SUM(CASE WHEN kind='Q' THEN amount ELSE 0 END),0) AS pending_out,
                 COALESCE(SUM(CASE WHEN kind='R' THEN amount ELSE 0 END),0) AS redeemed
               FROM (
                 SELECT 'E' AS kind, commission_usd AS amount FROM affiliate_commissions
                  WHERE referrer_id=? AND status='CREDITED'
                 UNION ALL
                 SELECT 'P', amount_usd FROM affiliate_payouts WHERE user_id=? AND status='APPROVED'
                 UNION ALL
                 SELECT 'Q', amount_usd FROM affiliate_payouts WHERE user_id=? AND status='PENDING'
                 UNION ALL
                 SELECT 'R', amount_usd FROM affiliate_redemptions WHERE user_id=?
               ) m""",
            (uid, uid, uid, uid),
        ).fetchone()
        earned = round(float((money["earned"] if money else 0) or 0.0), 2)
        paid_out = round(float((money["paid_out"] if money else 0) or 0.0), 2)
        pending_out = round(float((money["pending_out"] if money else 0) or 0.0), 2)
        redeemed = round(float((money["redeemed"] if money else 0) or 0.0), 2)
        avail = round(max(0.0, earned - paid_out - pending_out - redeemed), 2)
        ms_count = int(c.execute(
            "SELECT COUNT(*) n FROM affiliate_milestones WHERE user_id=?",
            (uid,),
        ).fetchone()["n"] or 0)
        recent = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM affiliate_commissions WHERE referrer_id=? ORDER BY id DESC LIMIT 5",
                (uid,),
            ).fetchall()
        ]
    tier = tier_for_count(paid_refs, cfg)
    th = max(1, int(cfg["milestone_paid_refs"]))
    rem_in_cycle = paid_refs % th
    needed = th - rem_in_cycle
    return {
        "user_id": uid,
        "enabled": cfg["enabled"],
        "mode": cfg["mode"],
        "total_refs": int(total_refs),
        "referred": int(total_refs),
        "paid_refs": int(paid_refs),
        "tier": tier,
        "tier_label": tier["label"],
        "rate_pct": float(tier["rate_pct"]),
        "total_earned": earned,
        "earned_usd": earned,
        "paid_out": paid_out,
        "pending_out": pending_out,
        "redeemed_usd": redeemed,
        "available_usd": avail,
        "min_payout_usd": cfg["min_payout_usd"],
        "balance_unlock_enabled": cfg["balance_unlock_enabled"],
        "milestone_enabled": cfg["milestone_enabled"],
        "milestone_paid_refs": th,
        "milestone_progress": rem_in_cycle,
        "milestone_needed": needed,
        "milestone_remaining": needed,   # v8.7.0 alias — the referrals screen reads this key
        "milestone_target": cfg["milestone_target"],
        "milestone_target_label": target_label(cfg["milestone_target"]),
        "milestone_days": cfg["milestone_days"],
        "milestones_unlocked": ms_count,
        "recent": recent,
    }


def list_unlockable_products() -> list[dict]:
    """List all paid products a user can unlock directly with their commission balance."""
    out: list[dict] = []
    with db.conn() as c:
        plans = c.execute(
            "SELECT code, title, price, duration_days FROM plans WHERE enabled=1 AND UPPER(code)!='FREE' AND price>0 ORDER BY price ASC"
        ).fetchall()
    for p in plans:
        out.append({
            "code": str(p["code"]).upper(),
            "type": "PLAN",
            "title": f"💎 باقة {p['title']} ({str(p['code']).upper()})",
            "price_usd": round(float(p["price"] or 0), 2),
            "duration_days": int(p["duration_days"] or 30),
        })
    try:
        from fysb.services import true_analysis as _ta
        ta_cfg = _ta.settings_snapshot()
        if ta_cfg.get("enabled_global", True) and float(ta_cfg.get("price_usd") or 0) > 0:
            out.append({
                "code": "TRUE_ANALYSIS",
                "type": "TRUE_ANALYSIS",
                "title": "👑 تحليل ForexYemeni الحقيقي",
                "price_usd": round(float(ta_cfg["price_usd"]), 2),
                "duration_days": int(ta_cfg.get("duration_days") or 30),
            })
    except Exception:
        pass
    try:
        from fysb.services import academy as _acad
        ap = float(_acad.paid_price_usd() or 0)
        ad = int(_acad.paid_duration_days() or 0)
        if ap > 0:
            out.append({
                "code": "ACADEMY",
                "type": "ACADEMY",
                "title": "🎓 الأكاديمية الملكية الاحترافية",
                "price_usd": round(ap, 2),
                "duration_days": ad,
            })
    except Exception:
        pass
    return out


def redeem_balance_for_plan(user_id: int, target_code: str) -> dict:
    """v8.7.0 Feature A: Atomically spend available commission balance to unlock a Plan /
    True Analysis / Academy immediately (zero cash outflow, zero double-spend!)."""
    cfg = get_config()
    if not cfg["balance_unlock_enabled"]:
        return {"ok": False, "error": "ميزة فتح الباقات من رصيد العمولات متوقفة حالياً من الإدارة."}
    uid = int(user_id)
    code = str(target_code or "").strip().upper()
    products = {p["code"]: p for p in list_unlockable_products()}
    prod = products.get(code)
    if not prod or prod["price_usd"] <= 0:
        return {"ok": False, "error": "الباقة المطلوبة غير متاحة للفتح بالرصيد حالياً."}
    price = float(prod["price_usd"])
    days = int(prod["duration_days"] or 30)

    with db.tx(immediate=True) as c:
        _, _, _, _, avail = _compute_balances_conn(c, uid)
        if avail < price:
            short = round(price - avail, 2)
            return {
                "ok": False,
                "error": (
                    f"رصيدك المتاح حالياً ({avail:,.2f}$) غير كافٍ لفتح {prod['title']} "
                    f"(السعر {price:,.2f}$ — يتبقّى لك {short:,.2f}$ فقط)."
                ),
            }
        cur = c.execute(
            """INSERT INTO affiliate_redemptions(user_id,target_type,target_code,duration_days,amount_usd,created_at)
               VALUES(?,?,?,?,?,?)""",
            (uid, prod["type"], code, days, price, config.now()),
        )
        red_id = int(cur.lastrowid)

    grant = _grant_reward_product(uid, code, days, source="AFFILIATE_BALANCE")
    if not grant.get("ok"):
        with db.conn() as c:
            c.execute("DELETE FROM affiliate_redemptions WHERE id=?", (red_id,))
        return {"ok": False, "error": grant.get("error") or "تعذر تفعيل الباقة."}

    rem = affiliate_stats(uid)["available_usd"]
    return {
        "ok": True,
        "redemption_id": red_id,
        "target_code": code,
        "target_title": prod["title"],
        "price_usd": price,
        "duration_days": days,
        "remaining_usd": rem,
    }


def request_payout(user_id: int, wallet_address: str, amount_usd: float | None = None) -> dict:
    wallet = (wallet_address or "").strip()
    if len(wallet) < 8:
        return {"ok": False, "error": "يرجى إدخال عنوان محفظة (USDT TRC20 أو حساب استلام) صحيح."}
    uid = int(user_id)
    min_p = min_payout_usd()
    with db.tx(immediate=True) as c:
        _, _, _, _, avail = _compute_balances_conn(c, uid)
        if amount_usd is not None:
            try:
                raw_amt = float(amount_usd)
            except (TypeError, ValueError):
                return {"ok": False, "error": "مبلغ السحب غير صالح."}
            if not math.isfinite(raw_amt) or raw_amt <= 0:
                return {"ok": False, "error": "مبلغ السحب غير صالح."}
            req_amt = round(raw_amt, 2)
        else:
            req_amt = avail
        if req_amt < min_p:
            return {"ok": False, "error": f"الحد الأدنى لطلب السحب هو {min_p:g}$ (رصيدك المتاح حالياً: {avail:.2f}$)."}
        if req_amt > avail:
            return {"ok": False, "error": f"المبلغ المطلوب ({req_amt:.2f}$) يتجاوز رصيدك المتاح ({avail:.2f}$)."}
        cur = c.execute(
            "INSERT INTO affiliate_payouts(user_id,amount_usd,wallet_address,status,created_at) VALUES(?,?,?,'PENDING',?)",
            (uid, req_amt, wallet[:160], config.now()),
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
    _cfg = None
    for r in rows:
        d = dict(r)
        d["total_earned"] = round(float(d.get("total_earned") or 0.0), 2)
        # v8.9.0: one config read for the whole list, not one per row
        if _cfg is None:
            _cfg = get_config()
        d["tier"] = tier_for_count(d["paid_refs"], _cfg)
        out.append(d)
    return out


_BOT_USERNAME_CACHE: dict = {"ts": 0.0, "value": ""}


def cached_bot_username() -> str:
    """The bot's @username, cached for 10 minutes.

    v8.9.0: every «دعوة الأصدقاء» / «محفظة العمولات» tap used to await
    `bot.get_me()` — a full Telegram API round-trip (~60 ms, and a hard failure
    whenever Telegram is briefly unreachable, which silently produced a
    `/start ref_…` placeholder link instead of a real t.me URL). The username of
    a running bot never changes, so one cached lookup is enough.
    """
    import time as _time
    from fysb.services import referrals as _refs
    if _BOT_USERNAME_CACHE["value"] and (_time.time() - _BOT_USERNAME_CACHE["ts"]) < 600:
        return _BOT_USERNAME_CACHE["value"]
    try:
        from fysb import config as _cfg
        bu = str(getattr(_cfg, "BOT_USERNAME", "") or "").strip().lstrip("@")
    except Exception:
        bu = ""
    if not bu:
        try:
            bu = _refs.bot_username()
        except Exception:
            bu = ""
    if bu:
        _BOT_USERNAME_CACHE.update(ts=_time.time(), value=bu)
    return bu


def unified_invite_link(user_id: int, bot_username: str = "") -> str:
    bu = (bot_username or "").strip().lstrip("@")
    if not bu:
        try:
            from fysb.services import referrals as _refs
            bu = _refs.bot_username()
        except Exception:
            bu = ""
    return f"https://t.me/{bu}?start=ref_{user_id}" if bu else f"/start ref_{user_id}"


def render_affiliate_dashboard(
    user_id: int,
    bot_username: str = "",
    st: dict | None = None,
    cfg: dict | None = None,
) -> str:
    """Commission dashboard body.

    v8.9.0: `st` / `cfg` may be supplied by the caller. The bot handler used to
    call this (63 connections), then `affiliate_stats()` AGAIN (38) just to read
    one field from the same numbers, and `unified_invite_link()` a third time —
    100 connections for one screen. The handler now loads the snapshot once and
    reuses it everywhere.
    """
    st = st if st is not None else affiliate_stats(user_id)
    cfg = cfg or get_config()
    t = st["tier"]
    link = unified_invite_link(user_id, bot_username)
    now_ksa = config.mecca_time_12h()
    tiers = dynamic_tiers(cfg)
    min_r = int(tiers[0][3])
    max_r = int(tiers[-1][3])
    mode_lbl = (
        f"نسبة موحّدة {cfg['flat_pct']:g}%"
        if cfg["mode"] == "FLAT"
        else f"متدرج {min_r}% → {max_r}%"
    )
    next_line = (
        f"▎🚀 للترقية إلى <b>{t['next_label']} ({t['next_rate_pct']:g}%)</b>: تبقّى <b>{t['refs_to_next']}</b> مشترك مدفوع"
        if t["next_label"]
        else f"▎👑 <b>أنت على أعلى نسبة عمولة ({t['rate_pct']:g}% دائمة)!</b>"
    )
    ms_block = ""
    if st["milestone_enabled"]:
        days_txt = f" ({st['milestone_days']} يوم)" if st["milestone_target"] != "ACADEMY" else ""
        ms_block = (
            "\n<blockquote>"
            f"▎🎁 <b>مكافأة فتح الباقة التلقائية (كل {st['milestone_paid_refs']} مشتركين مدفوعين):</b>\n"
            f"▎🏆 الجائزة: <b>{esc(st['milestone_target_label'])}{days_txt}</b> مجاناً تلقائياً!\n"
            f"▎📊 تقدمك الحالي: <b>{st['milestone_progress']}/{st['milestone_paid_refs']}</b> "
            f"{bar(st['milestone_progress'], st['milestone_paid_refs'])} (متبقي <b>{st['milestone_needed']}</b> مشترك مدفوع)\n"
            f"▎🏅 باقات فُتحت لك تلقائياً حتى الآن: <b>{st['milestones_unlocked']}</b>"
            "</blockquote>"
        )
    bal_hint = (
        "\n▎💎 <b>فتح فوري من الرصيد:</b> يمكنك استخدام رصيدك الآن لفتح أي باقة أو «تحليل ForexYemeni الحقيقي» أو الأكاديمية بضغطة زر!"
        if st["balance_unlock_enabled"]
        else ""
    )
    return (
        f"🤝 <b><u>برنامج الشركاء وعمولات المستخدمين ({mode_lbl})</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <code>{esc(now_ksa)}</code>\n\n"
        "<blockquote>"
        f"▎🏅 رتبتك الحالية: <b>{t['label']}</b> — نسبة العمولة: <b>{t['rate_pct']:g}%</b>\n"
        f"{next_line}\n"
        f"▎👥 إجمالي المدعوين: <b>{st['total_refs']}</b> · 💎 المشتركون المدفوعون: <b>{st['paid_refs']}</b>"
        "</blockquote>\n"
        "<blockquote>"
        f"▎💰 إجمالي أرباحك: <b>{st['total_earned']:,.2f} $</b>\n"
        f"▎✅ المتاح الآن (للسحب أو فتح باقة): <b>{st['available_usd']:,.2f} $</b>\n"
        f"▎🧾 تفاصيل: مستبدل بباقات <b>{st['redeemed_usd']:,.2f}$</b> · قيد السحب <b>{st['pending_out']:,.2f}$</b> · مسحوب <b>{st['paid_out']:,.2f}$</b>\n"
        f"▎💳 الحد الأدنى للسحب النقدي: <b>{st['min_payout_usd']:g} $</b>"
        f"{bal_hint}"
        "</blockquote>"
        f"{ms_block}\n\n"
        f"🔗 <b>رابط دعوتك الموحّد الحصري:</b>\n<code>{esc(link)}</code>\n"
        "<i>✨ كل صديق يسجّل من رابطك ويشترك في أي باقة أو أكاديمية أو تحليل ForexYemeni تُضاف عمولتك تلقائياً لرصيدك فوراً!</i>"
    )


def render_admin_affiliates_card() -> str:
    cfg = get_config()
    pending = list_payouts("PENDING", 10)
    top = top_affiliates(6)
    with db.conn() as c:
        # v8.9.0: one statement instead of three round-trips (v8.9.0 perf pass)
        agg = c.execute(
            """SELECT
                 (SELECT COUNT(*) FROM affiliate_redemptions)                    AS red_n,
                 (SELECT COALESCE(SUM(amount_usd),0) FROM affiliate_redemptions) AS red_sum,
                 (SELECT COUNT(*) FROM affiliate_milestones)                     AS ms_n"""
        ).fetchone()
        red_n = int((agg["red_n"] if agg else 0) or 0)
        red_sum = float((agg["red_sum"] if agg else 0) or 0.0)
        ms_n = int((agg["ms_n"] if agg else 0) or 0)
    tiers = dynamic_tiers(cfg)
    tier_str = " → ".join(f"{t[3]:g}%" for t in tiers)
    mode_txt = f"نسبة موحّدة ({cfg['flat_pct']:g}%)" if cfg["mode"] == "FLAT" else f"رتب متدرجة ({tier_str})"
    ms_txt = (
        f"🟢 مفعّل — كل <b>{cfg['milestone_paid_refs']}</b> مشترك مدفوع ← <b>{esc(target_label(cfg['milestone_target']))}</b> ({cfg['milestone_days']} يوم)"
        if cfg["milestone_enabled"]
        else "⚪ متوقف"
    )
    lines = [
        "💸 <b><u>إدارة عمولات المستخدمين وميزة فتح الباقات</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🕒 <code>{esc(config.mecca_time_12h())}</code>",
        "<blockquote>"
        f"▎⚡ حالة النظام: <b>{'🟢 مفعّل' if cfg['enabled'] else '🔴 متوقف'}</b>\n"
        f"▎📊 نظام النسبة: <b>{mode_txt}</b>\n"
        f"▎💳 الحد الأدنى للسحب: <b>{cfg['min_payout_usd']:g} $</b> · طلبات معلقة: <b>{len(pending)}</b>\n"
        f"▎💎 فتح باقة من الرصيد: <b>{'🟢 مفعّل' if cfg['balance_unlock_enabled'] else '⚪ متوقف'}</b> "
        f"(استبدالات: <b>{red_n}</b> بقيمة <b>{red_sum:,.2f}$</b>)\n"
        f"▎🎁 الفتح التلقائي بالأهداف (مدفوع فقط): {ms_txt} (فُتحت: <b>{ms_n}</b>)"
        "</blockquote>",
    ]
    if pending:
        lines.append("\n<b>💸 طلبات السحب المعلقة:</b>")
        for p in pending[:6]:
            lines.append(
                f"  • #{p['id']} · UID <code>{p['user_id']}</code> · <b>{p['amount_usd']:,.2f}$</b> · "
                f"<code>{esc(p['wallet_address'])}</code>"
            )
    if top:
        lines.append("\n<b>🏆 أفضل المستخدمين المسوّقين:</b>")
        for i, row in enumerate(top, 1):
            lines.append(
                f"  {i}. UID <code>{row['referrer_id']}</code> · {row['tier']['label']} ({row['tier']['rate_pct']:g}%) · "
                f"<b>{row['total_earned']:,.2f}$</b> ({row['paid_refs']} مشترك)"
            )
    if not pending and not top:
        lines.append("\n<i>لا توجد عمولات أو طلبات سحب مسجلة بعد.</i>")
    return "\n".join(lines)


def render_admin_config_card() -> str:
    cfg = get_config()
    r = cfg["tier_rates"]
    mode_ar = "نسبة موحّدة لكل المستخدمين" if cfg["mode"] == "FLAT" else "رتب متدرجة حسب عدد المشتركين المدفوعين"
    return (
        "⚙️ <b><u>ضبط عمولات المستخدمين وميزة فتح الباقات</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"▎⚡ النظام: <b>{'🟢 مفعّل' if cfg['enabled'] else '🔴 متوقف'}</b>\n"
        f"▎🔀 وضع الاحتساب: <b>{mode_ar}</b>\n"
        + (
            f"▎💰 النسبة الموحّدة الحالية: <b>{cfg['flat_pct']:g}%</b>\n"
            if cfg["mode"] == "FLAT" else
            f"▎🥉 برونزي (0–4): <b>{r['BRONZE']:g}%</b> · 🥈 فضي (5–14): <b>{r['SILVER']:g}%</b>\n"
            f"▎🥇 ذهبي (15–29): <b>{r['GOLD']:g}%</b> · 👑 ملكي (30+): <b>{r['ROYAL']:g}%</b>\n"
        )
        + f"▎💳 الحد الأدنى للسحب النقدي: <b>{cfg['min_payout_usd']:g} $</b>"
        "</blockquote>\n"
        "<blockquote>"
        f"▎💎 <b>فتح باقة من رصيد العمولات:</b> <b>{'🟢 مفعّل' if cfg['balance_unlock_enabled'] else '⚪ متوقف'}</b>\n"
        f"▎🎁 <b>فتح باقة تلقائياً بالأهداف (مشتركون مدفوعون فقط):</b> <b>{'🟢 مفعّل' if cfg['milestone_enabled'] else '⚪ متوقف'}</b>\n"
        f"▎👥 الهدف المطلوب: كل <b>{cfg['milestone_paid_refs']}</b> مشترك مدفوع\n"
        f"▎🏆 الجائزة المفتوحة تلقائياً: <b>{esc(target_label(cfg['milestone_target']))}</b> لمدة <b>{cfg['milestone_days']} يوم</b>"
        "</blockquote>\n"
        "<i>✨ عدّل أي قيمة بالأسفل بضغطة زر — تُحفظ وتطبّق فوراً على كل المنصة:</i>"
    )
