"""Reports service — every number comes from real SQL against the live DB.

Rendering: sections are native Telegram cards (blockquote) with
bold labels and progress chips. The admin account is excluded from user counts
(the admin is never a user).
"""
from fysb import config, db
from fysb.theme import ICON, bar, card, esc


def dashboard() -> dict:
    """Super Admin dashboard — the full metric list from the spec, real queries."""
    week_ago = _days_ago(7)
    admin_id = config.ADMIN_ID
    with db.conn() as c:
        users = {
            "total": c.execute("SELECT COUNT(*) n FROM users WHERE user_id!=?", (admin_id,)).fetchone()["n"],
            "active_7d": c.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=? AND user_id!=?", (week_ago, admin_id)).fetchone()["n"],
            "new_7d": c.execute("SELECT COUNT(*) n FROM users WHERE joined_at>=? AND user_id!=?", (week_ago, admin_id)).fetchone()["n"],
            "blocked": c.execute("SELECT COUNT(*) n FROM users WHERE is_blocked=1 AND user_id!=?", (admin_id,)).fetchone()["n"],
        }
        subs = {
            "free": c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='ACTIVE' AND plan_code='FREE'").fetchone()["n"],
            "paid": c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='ACTIVE' AND plan_code!='FREE'").fetchone()["n"],
            "expired": c.execute("SELECT COUNT(*) n FROM subscriptions WHERE status IN ('EXPIRED','REPLACED')").fetchone()["n"],
        }
        pays = {
            "pending": c.execute("SELECT COUNT(*) n FROM payments WHERE status='PENDING'").fetchone()["n"],
            "approved": c.execute("SELECT COUNT(*) n FROM payments WHERE status='APPROVED'").fetchone()["n"],
            "rejected": c.execute("SELECT COUNT(*) n FROM payments WHERE status='REJECTED'").fetchone()["n"],
            "approved_amount": c.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status='APPROVED'").fetchone()["s"],
        }
        signals = {
            "total": c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"],
            "open": c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')").fetchone()["n"],
            "completed": c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('TP3_HIT','SL_HIT','CLOSED_WIN')").fetchone()["n"],
            "cancelled": c.execute("SELECT COUNT(*) n FROM signals WHERE status='CANCELLED'").fetchone()["n"],
            "expired": c.execute("SELECT COUNT(*) n FROM signals WHERE status='EXPIRED'").fetchone()["n"],
            "wins": c.execute("SELECT COUNT(*) n FROM signals WHERE status='TP3_HIT'").fetchone()["n"],
            "sl_hits": c.execute("SELECT COUNT(*) n FROM signals WHERE status='SL_HIT'").fetchone()["n"],
        }
        analyses_today = c.execute("SELECT COUNT(*) n FROM analyses WHERE created_at>=?", (db.day_start_utc(),)).fetchone()["n"]
        syshealth = {
            "engine_enabled": db.get_setting("engine_enabled", "0") == "1",
            "kill_switch": db.get_setting("global_kill_switch", "1") == "1",
            "maintenance": db.get_setting("maintenance", "0") == "1",
            "live_enabled": db.get_setting("live_enabled", "0") == "1",
        }
        last_engine = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT 1").fetchone()
        tickets_open = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='OPEN'").fetchone()["n"]
        broadcasts = c.execute("SELECT COUNT(*) n FROM broadcasts WHERE status='PENDING' OR status='RUNNING'").fetchone()["n"]
    return {"users": users, "subscriptions": subs, "payments": pays, "signals": signals,
            "system": syshealth, "last_engine": dict(last_engine) if last_engine else None,
            "tickets_open": tickets_open, "broadcasts_active": broadcasts,
            "analyses_today": analyses_today}


def _days_ago(days: int):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def dashboard_view() -> str:
    """LUXURY dashboard — native Telegram cards, bold labels, progress chips."""
    d = dashboard()
    u, s, p, sig, sysd = d["users"], d["subscriptions"], d["payments"], d["signals"], d["system"]
    users_card = "\n".join([
        f"{ICON['users']} <b>المستخدمون الحقيقيون</b> (بدون حساب الإدارة)",
        f"▎الإجمالي: <b>{u['total']}</b>  ·  نشطون (7 أيام): <b>{u['active_7d']}</b>",
        f"▎جدد (7 أيام): <b>{u['new_7d']}</b>  ·  محظورون: <b>{u['blocked']}</b>",
        f"▎{bar(u['active_7d'], max(1, u['total']))}  <i>النشاط الأسبوعي</i>",
    ])
    subs_card = "\n".join([
        f"{ICON['diamond']} <b>الاشتراكات</b>",
        f"▎مجانية: <b>{s['free']}</b>  ·  مدفوعة: <b>{s['paid']}</b>  ·  منتهية: <b>{s['expired']}</b>",
        f"▎{bar(s['paid'], max(1, s['paid'] + s['free']))}  <i>نسبة المدفوع من النشط</i>",
    ])
    pay_card = "\n".join([
        f"{ICON['money']} <b>المدفوعات</b>",
        f"▎معلقة: <b>{p['pending']}</b>  ·  مقبولة: <b>{p['approved']}</b> ({p['approved_amount']:.2f} USD)",
        f"▎مرفوضة: <b>{p['rejected']}</b>",
    ])
    sig_card = "\n".join([
        f"{ICON['signal']} <b>الإشارات</b>",
        f"▎الإجمالي: <b>{sig['total']}</b>  ·  مفتوحة: <b>{sig['open']}</b>  ·  مكتملة: <b>{sig['completed']}</b>  ·  ملغاة: <b>{sig['cancelled']}</b>",
        f"▎TP3: <b>{sig['wins']}</b>  ·  SL: <b>{sig['sl_hits']}</b>",
    ])
    anl_card = "\n".join([
        f"{ICON['lab']} <b>التحليلات</b>",
        f"▎تحليلات اليوم: <b>{d['analyses_today']}</b>",
    ])
    engine = "🟢 يعمل" if sysd["engine_enabled"] else "⚪ متوقف"
    sys_card = "\n".join([
        f"🖥 <b>النظام</b>",
        f"▎{engine}  ·  🛡 Kill Switch: <b>{'مفعل' if sysd['kill_switch'] else 'غير مفعل'}</b>  ·  🔧 صيانة: <b>{'نعم' if sysd['maintenance'] else 'لا'}</b>",
        f"▎🔒 LIVE: <b>{'مفعل ⚠️' if sysd['live_enabled'] else 'مغلق (آمن)'}</b>  ·  🎫 تذاكر مفتوحة: <b>{d['tickets_open']}</b>  ·  📣 حملات نشطة: <b>{d['broadcasts_active']}</b>",
    ])
    head = (f"{ICON['crown']} <b>لوحة المعلومات الملكية المباشرة</b>\n"
            f"🕐 محدثة لحظياً من قاعدة البيانات الحقيقية")
    out = [head, card(users_card), card(subs_card), card(pay_card), card(sig_card), card(anl_card), card(sys_card)]
    le = d["last_engine"]
    if le:
        out.append(f"🧠 <b>آخر دورة محرك:</b> <b>{esc(le['symbol'])}</b> {esc(le['timeframe'])} → "
                   f"<b>{esc(le['result'])}</b> ({esc(config.mecca_dt_12h(le['created_at']))})")
    return "\n".join(out)


def users_report() -> str:
    admin_id = config.ADMIN_ID
    with db.conn() as c:
        lines = [f"{ICON['users']} <b><u>تقرير المستخدمين (آخر 7 أيام)</u></b>"]
        rows = c.execute("""SELECT substr(joined_at,1,10) d, COUNT(*) n FROM users
                            WHERE joined_at>=? AND user_id!=? GROUP BY d ORDER BY d DESC LIMIT 7""",
                         (_days_ago(7), admin_id)).fetchall()
        total = c.execute("SELECT COUNT(*) n FROM users WHERE user_id!=?", (admin_id,)).fetchone()["n"]
        body = [f"  {r['d']}: <b>+{r['n']}</b>" for r in rows] or ["  لا تسجيلات جديدة."]
        body.append(f"  ─────────────")
        body.append(f"  <b>الإجمالي (بدون المدير):</b> <b>{total}</b>")
        lines.append(card("\n".join(body)))
    return "\n".join(lines)


def signals_report() -> str:
    with db.conn() as c:
        lines = [f"{ICON['signal']} <b><u>تقرير الإشارات</u></b>"]
        rows = c.execute("SELECT status, COUNT(*) n FROM signals GROUP BY status ORDER BY n DESC").fetchall()
        by_status = "\n".join(f"▎<b>{esc(r['status'])}</b>: <b>{r['n']}</b>" for r in rows) or "▎لا إشارات."
        lines.append(card(by_status))
        strat = c.execute("SELECT strategy, COUNT(*) n, AVG(score) avg_s FROM signals GROUP BY strategy ORDER BY n DESC").fetchall()
        by_strat = "\n".join(f"▎<b>{esc(r['strategy'])}</b>: <b>{r['n']}</b> (متوسط الجودة <b>{float(r['avg_s'] or 0):.1f}</b>)"
                             for r in strat) or "▎لا بيانات استراتيجيات."
        lines.append(f"{ICON['brain']} <b>حسب الاستراتيجية</b>")
        lines.append(card(by_strat))
        pips = c.execute(
            """SELECT COALESCE(SUM(pips_result), 0) s,
                      COALESCE(SUM(CASE WHEN pips_result > 0 THEN 1 ELSE 0 END), 0) w,
                      COALESCE(SUM(CASE WHEN pips_result < 0 THEN 1 ELSE 0 END), 0) l
               FROM signals
               WHERE status IN ('TP3_HIT','SL_HIT')
                  OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='')"""
        ).fetchone()
        if pips and (int(pips["w"] or 0) + int(pips["l"] or 0)) > 0:
            lines.append(card(f"📊 <b>النتائج المغلقة:</b> <b>{float(pips['s'] or 0):+.1f}</b> pips إجمالاً "
                              f"(رابحة: <b>{int(pips['w'] or 0)}</b> / خاسرة: <b>{int(pips['l'] or 0)}</b>)"))
    return "\n".join(lines)


def payments_report() -> str:
    with db.conn() as c:
        lines = [f"{ICON['money']} <b><u>تقرير المدفوعات</u></b>"]
        rows = c.execute("SELECT status, COUNT(*) n, COALESCE(SUM(amount), 0) s FROM payments GROUP BY status").fetchall()
        body = "\n".join(f"▎<b>{esc(r['status'])}</b>: <b>{r['n']}</b> طلب — <b>{float(r['s'] or 0):.2f} USD</b>"
                         for r in rows) or "▎لا مدفوعات بعد."
        lines.append(card(body))
        plans = c.execute("SELECT plan_code, COUNT(*) n FROM payments WHERE status='APPROVED' GROUP BY plan_code").fetchall()
        if plans:
            sales = "\n".join(f"▎<b>{esc(r['plan_code'])}</b>: <b>{r['n']}</b>" for r in plans)
            lines.append(f"💎 <b>المبيعات حسب الباقة</b>")
            lines.append(card(sales))
    return "\n".join(lines)


def engine_report(limit: int = 12) -> str:
    with db.conn() as c:
        lines = [f"{ICON['brain']} <b><u>آخر دورات المحرك</u></b>"]
        rows = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        body = [f"▎{esc(r['created_at'][11:16])} <b>{esc(r['symbol'])}</b> {esc(r['timeframe'])}: <b>{esc(r['result'])}</b>"
                for r in rows] or ["▎لا دورات بعد."]
        lines.append(card("\n".join(body)))
        summary = {}
        for r in c.execute("SELECT result, COUNT(*) n FROM engine_runs WHERE created_at>=? GROUP BY result", (_days_ago(1),)).fetchall():
            summary[r["result"]] = r["n"]
        if summary:
            lines.append(card("🕒 <b>ملخص 24 ساعة:</b> " + "، ".join(f"<b>{esc(k)}</b>: <b>{v}</b>" for k, v in summary.items())))
    return "\n".join(lines)


def subscriptions_report() -> str:
    with db.conn() as c:
        lines = [f"{ICON['diamond']} <b><u>تقرير الاشتراكات النشطة</u></b>"]
        rows = c.execute("SELECT plan_code, COUNT(*) n FROM subscriptions WHERE status='ACTIVE' GROUP BY plan_code").fetchall()
        body = "\n".join(f"▎<b>{esc(r['plan_code'])}</b>: <b>{r['n']}</b>" for r in rows) or "▎لا اشتراكات نشطة."
        lines.append(card(body))
        soon = c.execute("""SELECT s.user_id, s.plan_code, s.ends_at FROM subscriptions s
                            WHERE s.status='ACTIVE' AND s.ends_at IS NOT NULL ORDER BY s.ends_at ASC LIMIT 8""").fetchall()
        if soon:
            soon_txt = "\n".join(f"▎<code>{r['user_id']}</code> · <b>{esc(r['plan_code'])}</b> · ينتهي {esc(r['ends_at'][:10])}"
                                 for r in soon)
            lines.append("⏳ <b>أقرب الانتهاءات</b>")
            lines.append(card(soon_txt))
    return "\n".join(lines)


def analyses_report() -> str:
    with db.conn() as c:
        lines = [f"{ICON['lab']} <b><u>تقرير التحليلات</u></b>"]
        today = c.execute("SELECT COUNT(*) n FROM analyses WHERE created_at>=?", (db.day_start_utc(),)).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM analyses").fetchone()["n"]
        by_tool = c.execute("SELECT tool, COUNT(*) n FROM analyses GROUP BY tool ORDER BY n DESC LIMIT 6").fetchall()
        body = [f"▎اليوم: <b>{today}</b>  ·  الإجمالي: <b>{total}</b>"]
        body += [f"▎<b>{esc(r['tool'] or '—')}</b>: <b>{r['n']}</b>" for r in by_tool]
        lines.append(card("\n".join(body)))
    return "\n".join(lines)
