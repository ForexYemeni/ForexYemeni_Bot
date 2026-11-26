"""Royal Channel Partners (Revenue-Share Affiliate) + VIP Gatekeeper Engine (v6.21.0).

Architecture:
  1. Revenue-Share Channel Partners (`channel_partners` + `partner_commissions`):
     * Super Admin registers a channel owner (`user_id`), sets their commission %,
       broadcast mode (`teaser` | `sample` | `full`), and granular permissions.
     * Each partner automatically gets a dedicated `ref_campaigns` deep-link
       (`https://t.me/<bot>?start=ref<CODE>`).
     * Every signal/analysis published to a partner's channel automatically carries
       a native green CTA button linking directly to the partner's referral link.
     * When any referred member activates a paid plan (USDT on-chain auto-verify,
       manual payment approval, or admin grant), `credit_commission_on_subscription()`
       automatically calculates and records the partner's share server-side.
  2. VIP Gatekeeper (`broadcast_channels.is_vip_gate=1` + `vip_channel_members`):
     * Paid subscribers automatically receive a single-use Telegram invite link
       (`create_chat_invite_link(member_limit=1)`) to matching VIP channels.
     * Background sweep (`sweep_expired_vip_members()`) automatically kicks
       members whose paid subscription has expired (`ban_chat_member` +
       `unban_chat_member` so they can rejoin seamlessly upon renewal) and sends
       a polite renewal card.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from telegram import InlineKeyboardMarkup

from fysb import config, db
from fysb.config import log
from fysb.services import referrals
from fysb.theme import dir_badge, esc, strategy_name, tf_name
from fysb.ui import keyboards as kb

POST_MODES = ("teaser", "sample", "full")
POST_MODE_AR = {
    "teaser": "🔒 تشويقي (إخفاء الدخول/الأهداف + زر اشتراك)",
    "sample": "🎯 عينة يومية (2 كاملة يومياً ثم تشويقي)",
    "full": "📡 بث كامل + تتبع حي",
}
POST_MODE_SHORT = {
    "teaser": "🔒 تشويقي",
    "sample": "🎯 عينة",
    "full": "📡 كامل",
}

PARTNER_PERM_FIELDS = (
    ("perm_auto_signals", "📡 بث الإشارات التلقائية"),
    ("perm_auto_analysis", "🔬 بث التحليلات المجدولة"),
    ("perm_manual_post", "🚀 النشر اليدوي (الآن)"),
    ("perm_choose_markets", "🌐 اختيار الأسواق والأطر"),
    ("perm_custom_branding", "🏷 التوقيع الخاص بالقناة"),
    ("perm_vip_gatekeeper", "🔐 حارس قناة VIP (إدخال/طرد تلقائي)"),
)

_ALLOWED_PARTNER_COLS = {
    "title", "commission_pct", "post_mode", "sample_daily_limit",
    "perm_auto_signals", "perm_auto_analysis", "perm_manual_post",
    "perm_choose_markets", "perm_custom_branding", "perm_vip_gatekeeper",
    "max_channels", "manual_analysis_limit", "manual_signal_limit",
    "brand_label", "enabled",
}


def _new_partner_code() -> str:
    for _ in range(30):
        code = "P" + secrets.token_hex(3).upper()
        with db.conn() as c:
            if (
                not c.execute("SELECT 1 FROM ref_campaigns WHERE code=?", (code,)).fetchone()
                and not c.execute("SELECT 1 FROM channel_partners WHERE campaign_code=?", (code,)).fetchone()
            ):
                return code
    return "P" + secrets.token_hex(5).upper()


def partner_link(code: str) -> str:
    return f"https://t.me/{referrals.bot_username()}?start=ref{(code or '').strip().upper()}"


def create_partner(
    user_id: int,
    title: str = "",
    commission_pct: float = 25.0,
    post_mode: str = "teaser",
    perm_vip_gatekeeper: int = 1,
) -> dict:
    """Register a Telegram user as a Channel Partner and bind a dedicated
    server-side referral campaign. Idempotent if user_id is already a partner."""
    uid = int(user_id)
    existing = get_partner_by_user(uid)
    if existing:
        return existing
    pct = max(0.0, min(70.0, float(commission_pct)))
    mode = post_mode if post_mode in POST_MODES else "teaser"
    code = _new_partner_code()
    label = (title or f"شريك #{uid}").strip()[:80]
    now = config.now()
    with db.tx() as c:
        c.execute(
            """INSERT INTO ref_campaigns(code,title,owner_id,target_invited,
               target_converted,enabled,created_at,updated_at)
               VALUES(?,?,?,50,5,1,?,?)""",
            (code, f"🤝 {label}", uid, now, now),
        )
        cur = c.execute(
            """INSERT INTO channel_partners(
               user_id,title,campaign_code,commission_pct,post_mode,
               sample_daily_limit,perm_auto_signals,perm_auto_analysis,
               perm_manual_post,perm_choose_markets,perm_custom_branding,
               perm_vip_gatekeeper,max_channels,brand_label,enabled,
               created_at,updated_at)
               VALUES(?,?,?,?,?,2,1,1,1,1,1,?,2,'',1,?,?)""",
            (uid, label, code, pct, mode, int(bool(perm_vip_gatekeeper)), now, now),
        )
        pid = cur.lastrowid
        r = c.execute("SELECT * FROM channel_partners WHERE id=?", (pid,)).fetchone()
    return dict(r)


def get_partner(pid: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM channel_partners WHERE id=?", (int(pid),)).fetchone()
    return dict(r) if r else None


def get_partner_by_user(uid: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM channel_partners WHERE user_id=?", (int(uid),)).fetchone()
    return dict(r) if r else None


def is_active_partner(uid: int) -> bool:
    p = get_partner_by_user(uid)
    return bool(p and p.get("enabled"))


def active_partner_user_ids() -> set[int]:
    """Return user_ids currently locked in Partner-Only Mode (enabled=1)."""
    with db.conn() as c:
        rows = c.execute("SELECT user_id FROM channel_partners WHERE enabled=1").fetchall()
    return {int(r["user_id"]) for r in rows}


def list_partners() -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM channel_partners ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def update_partner(pid: int, **fields) -> dict | None:
    clean = {k: v for k, v in fields.items() if k in _ALLOWED_PARTNER_COLS}
    if not clean:
        return get_partner(pid)
    if "commission_pct" in clean:
        clean["commission_pct"] = max(0.0, min(70.0, float(clean["commission_pct"])))
    if "post_mode" in clean and clean["post_mode"] not in POST_MODES:
        clean["post_mode"] = "teaser"
    for lim_col in ("manual_analysis_limit", "manual_signal_limit"):
        if lim_col in clean:
            clean[lim_col] = max(0, min(9999, int(clean[lim_col])))
    clean["updated_at"] = config.now()
    sets = ", ".join(f"{k}=?" for k in clean)
    with db.tx() as c:
        c.execute(f"UPDATE channel_partners SET {sets} WHERE id=?", (*clean.values(), int(pid)))
        if "enabled" in clean:
            p = c.execute("SELECT campaign_code FROM channel_partners WHERE id=?", (int(pid),)).fetchone()
            if p:
                c.execute(
                    "UPDATE ref_campaigns SET enabled=?, updated_at=? WHERE code=?",
                    (int(bool(clean["enabled"])), config.now(), p["campaign_code"]),
                )
    return get_partner(pid)


def delete_partner(pid: int) -> bool:
    p = get_partner(pid)
    if not p:
        return False
    with db.tx() as c:
        c.execute("UPDATE broadcast_channels SET owner_partner_id=0, enabled=0 WHERE owner_partner_id=?", (int(pid),))
        c.execute("UPDATE ref_campaigns SET enabled=0, updated_at=? WHERE code=?", (config.now(), p["campaign_code"]))
        cur = c.execute("DELETE FROM channel_partners WHERE id=?", (int(pid),))
    return cur.rowcount > 0


# ----------------------------------------------------------------------------
# Per-Partner Manual Post Daily Quotas (Analysis Now & Signal Now) — v6.21.3
#   limit == 0 -> Unlimited (مفتوح دائماً بلا حد)
#   limit > 0  -> Strict daily cap per UTC day, persisted in DB
# ----------------------------------------------------------------------------

def _utc_day() -> str:
    return config.now()[:10]


def manual_quota_status(pid: int, partner_row: dict | None = None) -> dict:
    """Return today's persistent usage & configured limits for a partner."""
    p = partner_row or get_partner(pid)
    today = _utc_day()
    if not p:
        return {
            "date": today,
            "analysis_used": 0, "analysis_limit": 6,
            "signal_used": 0, "signal_limit": 6,
        }
    same_day = (p.get("manual_usage_date") or "") == today
    a_lim = int(p.get("manual_analysis_limit") if p.get("manual_analysis_limit") is not None else 6)
    s_lim = int(p.get("manual_signal_limit") if p.get("manual_signal_limit") is not None else 6)
    return {
        "date": today,
        "analysis_used": int(p.get("manual_analysis_used") or 0) if same_day else 0,
        "analysis_limit": max(0, a_lim),
        "signal_used": int(p.get("manual_signal_used") or 0) if same_day else 0,
        "signal_limit": max(0, s_lim),
    }


def can_manual_post(pid: int, kind: str) -> tuple[bool, int, int]:
    """Check whether partner `pid` can perform a manual post of `kind`
    ('analysis' or 'signal'). Returns `(allowed, used_today, limit)` where
    `limit == 0` means unlimited (always allowed)."""
    st = manual_quota_status(pid)
    is_sig = (kind == "signal")
    used = st["signal_used"] if is_sig else st["analysis_used"]
    limit = st["signal_limit"] if is_sig else st["analysis_limit"]
    if limit == 0:
        return True, used, 0
    return used < limit, used, limit


def record_manual_post(pid: int, kind: str) -> tuple[int, int]:
    """Increment today's persistent manual-post counter AFTER a successful post.
    Returns `(new_used, limit)`."""
    today = _utc_day()
    is_sig = (kind == "signal")
    with db.tx() as c:
        r = c.execute(
            """SELECT manual_usage_date, manual_analysis_used, manual_signal_used,
                      manual_analysis_limit, manual_signal_limit
               FROM channel_partners WHERE id=?""",
            (int(pid),),
        ).fetchone()
        if not r:
            return 0, 6
        same_day = (r["manual_usage_date"] or "") == today
        a_used = int(r["manual_analysis_used"] or 0) if same_day else 0
        s_used = int(r["manual_signal_used"] or 0) if same_day else 0
        if is_sig:
            s_used += 1
        else:
            a_used += 1
        c.execute(
            """UPDATE channel_partners
               SET manual_usage_date=?, manual_analysis_used=?, manual_signal_used=?, updated_at=?
               WHERE id=?""",
            (today, a_used, s_used, config.now(), int(pid)),
        )
        lim = int(r["manual_signal_limit"] if is_sig else r["manual_analysis_limit"])
        return (s_used if is_sig else a_used), max(0, lim)


def reset_manual_quota(pid: int) -> None:
    """Reset today's manual-post usage counters for partner `pid` to 0."""
    with db.tx() as c:
        c.execute(
            """UPDATE channel_partners
               SET manual_usage_date=?, manual_analysis_used=0, manual_signal_used=0, updated_at=?
               WHERE id=?""",
            (_utc_day(), config.now(), int(pid)),
        )


def partner_channels(pid: int) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM broadcast_channels WHERE owner_partner_id=? ORDER BY id DESC",
            (int(pid),),
        ).fetchall()
    return [dict(r) for r in rows]


def link_partner_channel(
    pid: int,
    chat_id: str,
    title: str,
    kind: str = "channel",
    is_vip_gate: int = 0,
) -> dict:
    """Link a Telegram channel/group under a specific partner (respecting max_channels)."""
    p = get_partner(pid)
    if not p or not p.get("enabled"):
        return {"ok": False, "error": "حساب الشريك غير مفعّل."}
    existing = partner_channels(pid)
    chat_s = str(chat_id).strip()
    for ch in existing:
        if str(ch["chat_id"]).strip() == chat_s:
            return {"ok": True, "channel_id": ch["id"], "already": True}
    if len(existing) >= int(p.get("max_channels") or 2):
        return {"ok": False, "error": f"بلغت الحد الأقصى للقنوات المربوطة ({p.get('max_channels') or 2})."}
    now = config.now()
    vip_flag = 1 if (is_vip_gate and p.get("perm_vip_gatekeeper")) else 0
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO broadcast_channels(
               chat_id,title,kind,enabled,auto_analysis,auto_signals,
               markets,timeframes,strategies,interval_minutes,last_sent,
               owner_partner_id,post_mode,is_vip_gate,created_at,updated_at)
               VALUES(?,?,?,1,?,?, 'ALL','ALL','ALL',60,'', ?,?,?,?,?)""",
            (
                chat_s,
                (title or chat_s)[:100],
                kind if kind in ("channel", "group") else "channel",
                int(bool(p.get("perm_auto_analysis"))),
                int(bool(p.get("perm_auto_signals"))),
                int(pid),
                p.get("post_mode") or "teaser",
                vip_flag,
                now,
                now,
            ),
        )
        cid = cur.lastrowid
    return {"ok": True, "channel_id": cid}


def partner_stats(pid: int) -> dict:
    p = get_partner(pid)
    if not p:
        return {}
    with db.conn() as c:
        camp = c.execute(
            "SELECT id FROM ref_campaigns WHERE code=?", (p["campaign_code"],)
        ).fetchone()
        invited = 0
        converted = 0
        if camp:
            invited = c.execute(
                "SELECT COUNT(*) n FROM referrals WHERE campaign_id=?", (camp["id"],)
            ).fetchone()["n"]
            converted = c.execute(
                "SELECT COUNT(*) n FROM referrals WHERE campaign_id=? AND converted=1", (camp["id"],)
            ).fetchone()["n"]
        gross = float(
            c.execute(
                "SELECT COALESCE(SUM(gross_amount), 0) s FROM partner_commissions WHERE partner_id=?",
                (int(pid),),
            ).fetchone()["s"] or 0.0
        )
        pending = float(
            c.execute(
                "SELECT COALESCE(SUM(commission_amount), 0) s FROM partner_commissions WHERE partner_id=? AND status='EARNED'",
                (int(pid),),
            ).fetchone()["s"] or 0.0
        )
        paid = float(
            c.execute(
                "SELECT COALESCE(SUM(commission_amount), 0) s FROM partner_commissions WHERE partner_id=? AND status='PAID'",
                (int(pid),),
            ).fetchone()["s"] or 0.0
        )
        chans = c.execute(
            "SELECT id FROM broadcast_channels WHERE owner_partner_id=?", (int(pid),)
        ).fetchall()
        chan_ids = [int(r["id"]) for r in chans]
        vip_active = 0
        if chan_ids:
            ph = ",".join("?" * len(chan_ids))
            vip_active = c.execute(
                f"SELECT COUNT(*) n FROM vip_channel_members WHERE channel_id IN ({ph}) AND status='ACTIVE'",
                tuple(chan_ids),
            ).fetchone()["n"]
    conv_pct = round((converted / invited) * 100.0, 1) if invited else 0.0
    return {
        "invited": int(invited),
        "converted": int(converted),
        "conv_rate_pct": conv_pct,
        "gross_sales": round(gross, 2),
        "earned_pending": round(pending, 2),
        "paid_out": round(paid, 2),
        "total_earned": round(pending + paid, 2),
        "channels_count": len(chan_ids),
        "vip_active_members": int(vip_active),
    }


def credit_commission_on_subscription(
    buyer_user_id: int,
    plan_code: str,
    payment_id: int = 0,
    amount: float | None = None,
    currency: str = "USD",
) -> dict | None:
    """Atomically credit a partner's revenue share when a referred user activates
    a paid subscription. Server-side only; blocks self-referrals & duplicates."""
    if not plan_code or str(plan_code).upper() == "FREE":
        return None
    buyer_id = int(buyer_user_id)
    with db.tx(immediate=True) as c:
        ref = c.execute(
            """SELECT r.campaign_id, rc.code
               FROM referrals r JOIN ref_campaigns rc ON rc.id=r.campaign_id
               WHERE r.invitee_id=?""",
            (buyer_id,),
        ).fetchone()
        if not ref:
            return None
        partner = c.execute(
            "SELECT * FROM channel_partners WHERE campaign_code=? AND enabled=1",
            (ref["code"],),
        ).fetchone()
        if not partner:
            return None
        if int(partner["user_id"]) == buyer_id:
            return None
        pct = float(partner["commission_pct"] or 0.0)
        if pct <= 0:
            return None
        if payment_id:
            dup = c.execute(
                "SELECT id FROM partner_commissions WHERE partner_id=? AND payment_id=?",
                (partner["id"], int(payment_id)),
            ).fetchone()
            if dup:
                return None
        gross = float(amount) if amount is not None else 0.0
        cur_code = currency or "USD"
        if gross <= 0:
            pl = c.execute("SELECT price, currency FROM plans WHERE code=?", (plan_code,)).fetchone()
            if pl:
                gross = float(pl["price"] or 0.0)
                cur_code = pl["currency"] or cur_code
        if gross <= 0:
            return None
        comm = round(gross * (pct / 100.0), 2)
        if comm <= 0:
            return None
        c.execute(
            """INSERT INTO partner_commissions(
               partner_id,buyer_user_id,plan_code,payment_id,
               gross_amount,currency,commission_pct,commission_amount,
               status,created_at)
               VALUES(?,?,?,?,?,?,?,?,'EARNED',?)""",
            (
                partner["id"], buyer_id, plan_code, int(payment_id or 0),
                round(gross, 2), cur_code, pct, comm, config.now(),
            ),
        )
        return {
            "credited": True,
            "partner_id": int(partner["id"]),
            "partner_user_id": int(partner["user_id"]),
            "partner_title": partner["title"] or f"شريك #{partner['id']}",
            "buyer_user_id": buyer_id,
            "plan_code": plan_code,
            "gross_amount": round(gross, 2),
            "commission_pct": pct,
            "commission_amount": comm,
            "currency": cur_code,
        }


def settle_partner(pid: int) -> dict:
    """Mark all pending EARNED commissions for partner as PAID."""
    now = config.now()
    with db.tx(immediate=True) as c:
        row = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(commission_amount), 0) s "
            "FROM partner_commissions WHERE partner_id=? AND status='EARNED'",
            (int(pid),),
        ).fetchone()
        cnt = int(row["n"] or 0)
        total = round(float(row["s"] or 0.0), 2)
        if cnt > 0:
            c.execute(
                "UPDATE partner_commissions SET status='PAID', paid_at=? "
                "WHERE partner_id=? AND status='EARNED'",
                (now, int(pid)),
            )
    return {"count": cnt, "total": total}


def partner_for_channel(ch: dict) -> dict | None:
    pid = int(ch.get("owner_partner_id") or 0)
    if pid <= 0:
        return None
    return get_partner(pid)


def effective_post_mode(ch: dict, partner: dict | None = None) -> str:
    """Resolve channel broadcast mode: 'teaser' | 'sample' | 'full'."""
    partner = partner if partner is not None else partner_for_channel(ch)
    explicit = (ch.get("post_mode") or "").strip().lower()
    if explicit in POST_MODES:
        return explicit
    if partner:
        pm = (partner.get("post_mode") or "teaser").strip().lower()
        return pm if pm in POST_MODES else "teaser"
    return "full"


def should_send_full_signal(ch: dict, partner: dict | None = None) -> bool:
    """Decide whether this channel receives the full signal card or the Teaser
    card (handling daily Sample quota atomically)."""
    partner = partner if partner is not None else partner_for_channel(ch)
    mode = effective_post_mode(ch, partner)
    if mode == "full":
        return True
    if mode == "teaser":
        return False
    # mode == "sample": allow up to sample_daily_limit full signals per UTC day
    limit = int((partner or {}).get("sample_daily_limit") or 2)
    today = config.mecca_today_iso()
    with db.tx(immediate=True) as c:
        row = c.execute(
            "SELECT daily_full_date, daily_full_count FROM broadcast_channels WHERE id=?",
            (int(ch["id"]),),
        ).fetchone()
        cur_date = (row["daily_full_date"] if row else "") or ""
        cur_cnt = int((row["daily_full_count"] if row else 0) or 0)
        if cur_date != today:
            cur_cnt = 0
        if cur_cnt < limit:
            c.execute(
                "UPDATE broadcast_channels SET daily_full_date=?, daily_full_count=? WHERE id=?",
                (today, cur_cnt + 1, int(ch["id"])),
            )
            return True
    return False


def channel_cta_markup(ch: dict, partner: dict | None = None) -> InlineKeyboardMarkup | None:
    """Return a luxury Bot API 9.4 green CTA button under every partner channel
    post linking directly to the partner's personal affiliate deep-link."""
    partner = partner if partner is not None else partner_for_channel(ch)
    if not partner or not partner.get("enabled"):
        return None
    url = partner_link(partner["campaign_code"])
    return InlineKeyboardMarkup([
        [kb.url_btn("⚡ افتح الإشارات الكاملة والتتبع الحي في البوت", url, style="success")]
    ])


def channel_brand_footer(ch: dict, partner: dict | None = None) -> str:
    partner = partner if partner is not None else partner_for_channel(ch)
    if partner and partner.get("perm_custom_branding") and (partner.get("brand_label") or "").strip():
        return f"\n\n👑 <b>{esc(partner['brand_label'].strip())}</b> · 📡 <b>{config.BOT_SIGNATURE}</b>"
    return f"\n\n📡 <b>{config.BOT_SIGNATURE}</b>"


def render_teaser_signal_card(sig: dict, ch: dict | None = None, partner: dict | None = None) -> str:
    """Luxury Teaser Signal Card for public partner channels — proves real-time
    institutional activity while hiding exact Entry / SL / TP prices to drive
    conversions into the bot via the partner's CTA button."""
    sym = esc(sig.get("symbol") or "")
    tf = tf_name(sig.get("timeframe") or "M15")
    strat = strategy_name(sig.get("strategy") or "")
    score = int(sig.get("score") or 0)
    rr = float(sig.get("rr") or 0.0)
    badge = dir_badge(sig.get("direction") or "BUY")
    footer = channel_brand_footer(ch or {}, partner=partner)
    return (
        f"⚡ <b><u>إشارة مؤسسية حقيقية جديدة</u></b> — <b>{sym}</b> {badge}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>"
        f"▎⏱ الإطار الزمني: <b>{tf}</b>\n"
        f"▎🧠 المنهجية الفائزة: <b>{strat}</b>\n"
        f"▎⭐ تقييم الجودة: <b>{score}/100</b>"
        + (f" · ⚖️ العائد للمخاطرة: <b>1:{rr:.1f}</b>" if rr > 0 else "")
        + "\n"
        f"▎🔒 منطقة الدخول: <code>مخفية للمشتركين فقط</code>\n"
        f"▎🔒 وقف الخسارة والأهداف (TP1 / TP2 / TP3): <code>مخفية للمشتركين فقط</code>\n"
        f"</blockquote>\n"
        f"👑 <i>اضغط الزر بالأسفل لفتح الإشارة كاملة + التتبع اللحظي المباشر داخل البوت.</i>"
        f"{footer}"
    )


# ============================================================================
# VIP Gatekeeper Engine — Auto-Invite Paid Subscribers & Auto-Kick on Expiry
# ============================================================================

def vip_channels_for_user(user_id: int, plan_code: str) -> list[dict]:
    """Return active VIP Gatekeeper channels eligible for this paid subscriber:
      * Admin-owned VIP channels (owner_partner_id = 0, is_vip_gate = 1)
      * Referring partner's VIP channel (if partner has perm_vip_gatekeeper = 1)
    """
    if not plan_code or str(plan_code).upper() == "FREE":
        return []
    uid = int(user_id)
    with db.conn() as c:
        ref = c.execute(
            """SELECT rc.code FROM referrals r
               JOIN ref_campaigns rc ON rc.id=r.campaign_id
               WHERE r.invitee_id=?""",
            (uid,),
        ).fetchone()
        ref_partner_id = 0
        if ref:
            pr = c.execute(
                "SELECT id FROM channel_partners WHERE campaign_code=? AND enabled=1 AND perm_vip_gatekeeper=1",
                (ref["code"],),
            ).fetchone()
            if pr:
                ref_partner_id = int(pr["id"])
        rows = c.execute(
            "SELECT * FROM broadcast_channels WHERE enabled=1 AND is_vip_gate=1"
        ).fetchall()
    out = []
    for r in rows:
        ch = dict(r)
        owner_pid = int(ch.get("owner_partner_id") or 0)
        if owner_pid != 0 and owner_pid != ref_partner_id:
            continue
        min_plan = (ch.get("vip_min_plan") or "").strip().upper()
        if min_plan and min_plan != str(plan_code).upper():
            continue
        out.append(ch)
    return out


def _norm_chat_ref(chat_id):
    s = str(chat_id or "").strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return s


async def grant_vip_access(bot, user_id: int, plan_code: str) -> list[dict]:
    """Generate single-use Telegram invite links (member_limit=1) for every
    matching VIP Gatekeeper channel and deliver a luxury VIP Access Card to user."""
    if not bot or not plan_code or str(plan_code).upper() == "FREE":
        return []
    uid = int(user_id)
    channels = vip_channels_for_user(uid, plan_code)
    if not channels:
        return []
    granted: list[dict] = []
    for ch in channels:
        with db.conn() as c:
            already = c.execute(
                "SELECT id, invite_link FROM vip_channel_members "
                "WHERE channel_id=? AND user_id=? AND status='ACTIVE'",
                (int(ch["id"]), uid),
            ).fetchone()
        if already:
            continue
        try:
            inv = await bot.create_chat_invite_link(
                chat_id=_norm_chat_ref(ch["chat_id"]),
                member_limit=1,
                name=f"FYB-{uid}",
            )
            link = getattr(inv, "invite_link", "") or ""
            if not link:
                continue
            with db.tx() as c:
                c.execute(
                    """INSERT INTO vip_channel_members(channel_id,chat_id,user_id,invite_link,status,granted_at)
                       VALUES(?,?,?,?,'ACTIVE',?)""",
                    (int(ch["id"]), str(ch["chat_id"]), uid, link, config.now()),
                )
            granted.append({"channel_id": ch["id"], "title": ch["title"] or ch["chat_id"], "invite_link": link})
        except Exception as exc:
            log.warning("VIP invite creation failed ch=#%s user=%s: %s", ch.get("id"), uid, exc)

    if granted:
        rows = [
            [kb.url_btn(f"🔐 دخول {g['title'][:28]}", g["invite_link"], style="success")]
            for g in granted
        ]
        rows.append([kb.btn("📡 الإشارات المباشرة", "u:signals", style="primary")])
        lines = [
            "👑 <b><u>بطاقة الدخول الحصري لقنوات VIP</u></b>",
            "━━━━━━━━━━━━━━━━━━",
            "<blockquote>",
            f"▎💎 مبروك تفعيل باقتك <b>{esc(plan_code)}</b>!\n"
            "▎🔐 تم إنشاء رابط دخول شخصي خاص بك (صالح لمرة واحدة فقط ولا يُشارك مع أحد):\n"
            + "\n".join(f"▎• <b>{esc(g['title'])}</b>" for g in granted),
            "▎🛡 <i>تظل عضويتك نشطة تلقائياً طوال فترة اشتراكك.</i>",
            "</blockquote>",
        ]
        try:
            await bot.send_message(
                uid,
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(rows),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("VIP invite DM failed user=%s: %s", uid, exc)
    return granted


async def sweep_expired_vip_members(bot) -> dict:
    """Auto-kick any VIP Gatekeeper member whose paid subscription has ended
    (ban + immediate unban so they can rejoin cleanly upon renewing)."""
    from fysb.services import subscriptions
    stats = {"checked": 0, "removed": 0, "errors": 0}
    with db.conn() as c:
        active_rows = [
            dict(r)
            for r in c.execute(
                "SELECT m.*, b.title AS channel_title FROM vip_channel_members m "
                "LEFT JOIN broadcast_channels b ON b.id=m.channel_id "
                "WHERE m.status='ACTIVE'"
            ).fetchall()
        ]
    for m in active_rows:
        stats["checked"] += 1
        uid = int(m["user_id"])
        if uid == int(config.ADMIN_ID or 0):
            continue
        act = subscriptions.active_plan(uid)
        if act and str(act["plan_code"]).upper() != "FREE":
            continue
        cid_ref = _norm_chat_ref(m["chat_id"])
        try:
            await bot.ban_chat_member(chat_id=cid_ref, user_id=uid)
            await bot.unban_chat_member(chat_id=cid_ref, user_id=uid, only_if_banned=True)
            with db.tx() as c:
                c.execute(
                    "UPDATE vip_channel_members SET status='REMOVED', removed_at=? WHERE id=?",
                    (config.now(), int(m["id"])),
                )
            stats["removed"] += 1
            try:
                ch_title = m.get("channel_title") or "قناة VIP"
                await bot.send_message(
                    uid,
                    f"⏳ <b><u>انتهاء صلاحية اشتراكك في {esc(ch_title)}</u></b>\n"
                    f"<blockquote>"
                    f"▎🔒 تم إخراجك تلقائياً من قناة VIP لانتهاء مدة باقتك.\n"
                    f"▎💎 يمكنك التجديد الآن في أي لحظة وسيصلك رابط دخول جديد فوراً بضغطة واحدة!"
                    f"</blockquote>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [kb.btn("💎 تجديد الاشتراك والعودة الآن", "u:plans", style="success")]
                    ]),
                )
            except Exception:
                pass
        except Exception as exc:
            stats["errors"] += 1
            log.warning("VIP kick failed ch=%s user=%s: %s", m["chat_id"], uid, exc)
    return stats


async def notify_commission_and_vip(bot, buyer_user_id: int, plan_code: str, commission_info: dict | None) -> None:
    """Post-activation orchestrator: notify Partner & Admin about credited
    commission AND dispatch single-use VIP Gatekeeper invite links to buyer."""
    if commission_info and commission_info.get("credited") and bot:
        comm = commission_info["commission_amount"]
        gross = commission_info["gross_amount"]
        cur = commission_info["currency"]
        pct = commission_info["commission_pct"]
        p_uid = commission_info["partner_user_id"]
        p_title = commission_info["partner_title"]
        try:
            await bot.send_message(
                p_uid,
                "🎉 <b><u>عمولة شراكة جديدة أُضيفت لرصيدك!</u></b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>"
                f"▎💎 الباقة المشتراة: <b>{esc(plan_code)}</b> (قيمة: <b>{gross:.2f} {esc(cur)}</b>)\n"
                f"▎💰 عمولتك ({pct:.0f}%): <b>+{comm:.2f} {esc(cur)}</b>\n"
                f"▎👑 افتح لوحة الشريك لمتابعة أرباحك أو طلب السحب في أي وقت."
                "</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [kb.btn("👑 فتح لوحة الشريك", "p:home", style="success")]
                ]),
            )
        except Exception:
            pass
        if config.ADMIN_ID and int(config.ADMIN_ID) != int(p_uid):
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"🤝 <b>عمولة شريك مسجّلة تلقائياً</b>\n"
                    f"<blockquote>"
                    f"▎👤 الشريك: <b>{esc(p_title)}</b> (<code>{p_uid}</code>)\n"
                    f"▎💎 اشتراك المستخدم <code>{buyer_user_id}</code> في <b>{esc(plan_code)}</b> ({gross:.2f} {esc(cur)})\n"
                    f"▎💰 حصة الشريك ({pct:.0f}%): <b>{comm:.2f} {esc(cur)}</b> · صافي المنصة: <b>{gross - comm:.2f} {esc(cur)}</b>"
                    f"</blockquote>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [kb.btn("🤝 إدارة الشركاء", f"a:partview:{commission_info['partner_id']}", style="primary")]
                    ]),
                )
            except Exception:
                pass
    if bot:
        await grant_vip_access(bot, buyer_user_id, plan_code)
