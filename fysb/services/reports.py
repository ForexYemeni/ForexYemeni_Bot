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
        # One aggregate per table, not one WAN round trip per counter.
        # COUNT(CASE...) returns zero on an empty table on SQLite AND PostgreSQL.
        users = dict(c.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN last_seen>=? THEN 1 END) AS active_7d,
                   COUNT(CASE WHEN joined_at>=? THEN 1 END) AS new_7d,
                   COUNT(CASE WHEN is_blocked=1 THEN 1 END) AS blocked
            FROM users WHERE user_id!=?
        """, (week_ago, week_ago, admin_id)).fetchone())
        subs = dict(c.execute("""
            SELECT COUNT(CASE WHEN status='ACTIVE' AND plan_code='FREE' THEN 1 END) AS free,
                   COUNT(CASE WHEN status='ACTIVE' AND plan_code!='FREE' THEN 1 END) AS paid,
                   COUNT(CASE WHEN status IN ('EXPIRED','REPLACED') THEN 1 END) AS expired
            FROM subscriptions
        """).fetchone())
        pays = dict(c.execute("""
            SELECT COUNT(CASE WHEN status='PENDING' THEN 1 END) AS pending,
                   COUNT(CASE WHEN status='APPROVED' THEN 1 END) AS approved,
                   COUNT(CASE WHEN status='REJECTED' THEN 1 END) AS rejected,
                   COALESCE(SUM(CASE WHEN status='APPROVED' THEN amount END),0) AS approved_amount
            FROM payments
        """).fetchone())
        signals = dict(c.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                         AND (closed_at IS NULL OR closed_at='') THEN 1 END) AS open,
                   COUNT(CASE WHEN status IN ('TP3_HIT','SL_HIT','CLOSED_WIN') THEN 1 END) AS completed,
                   COUNT(CASE WHEN status='CANCELLED' THEN 1 END) AS cancelled,
                   COUNT(CASE WHEN status='EXPIRED' THEN 1 END) AS expired,
                   COUNT(CASE WHEN status='TP3_HIT' THEN 1 END) AS wins,
                   COUNT(CASE WHEN status='SL_HIT' THEN 1 END) AS sl_hits
            FROM signals
        """).fetchone())
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
        f"🖥 <b>النظام والحماية</b>",
        f"▎🧠 المحرك: <b>{engine}</b>  ·  🛡 Kill Switch: <b>{'مفعل' if sysd['kill_switch'] else 'آمن'}</b>  ·  🔧 صيانة: <b>{'نعم' if sysd['maintenance'] else 'لا'}</b>",
        f"▎🔒 LIVE: <b>{'مفعل ⚠️' if sysd['live_enabled'] else 'مغلق (آمن)'}</b>  ·  🎫 تذاكر مفتوحة: <b>{d['tickets_open']}</b>  ·  📣 حملات نشطة: <b>{d['broadcasts_active']}</b>",
    ])
    head = (
        f"{ICON['crown']} <b><u>لوحة المعلومات المباشرة — {esc(config.BRAND)}</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <code>{esc(config.mecca_dt_12h())}</code> · محدثة لحظياً من قاعدة البيانات"
    )
    out = [head, card(users_card), card(subs_card), card(pay_card), card(sig_card), card(anl_card), card(sys_card)]
    le = d["last_engine"]
    if le:
        out.append(f"🧠 <b>آخر دورة محرك:</b> <b>{esc(le['symbol'])}</b> {esc(le['timeframe'])} → "
                   f"<b>{esc(le['result'])}</b> ({esc(config.mecca_dt_12h(le['created_at']))})")
    return "\n".join(out)


def filtered_signals_report(market: str | None = None, strategy: str | None = None,
                            timeframe: str | None = None) -> str:
    """v8.14.18 — the COMPREHENSIVE INTERACTIVE REPORT: the owner picks any
    market/strategy/timeframe slice (or ALL of a dimension) and gets the
    honest closed-ledger truth for EXACTLY that slice. Every number is a real
    SQL aggregate of the live signals table:
      * counts by status (the whole lifecycle, nothing hidden)
      * closed ledger classified by STATUS semantics (banked TP / lock = win,
        SL = loss) — per-symbol pips + their TRUE usd_move at 0.01 lot
      * hit-rate, average recorded quality (score), average planned RR (rr)
    Zero mixed-unit sums: pips are NEVER aggregated across symbols — USD is."""
    where, params = ["1=1"], []
    if market:
        where.append("symbol=?")
        params.append(market)
    if strategy:
        where.append("strategy=?")
        params.append(strategy)
    if timeframe:
        where.append("timeframe=?")
        params.append(timeframe)
    filt_txt = (f"📈 السوق: <b>{esc(market or 'الكل')}</b> · "
                f"🧠 الاستراتيجية: <b>{esc(strategy or 'الكل')}</b> · "
                f"⏱ الإطار: <b>{esc(timeframe or 'الكل')}</b>")
    with db.conn() as c:
        w = " AND ".join(where)
        total = c.execute(f"SELECT COUNT(*) n FROM signals WHERE {w}", params).fetchone()["n"]
        if total == 0:
            return (f"📊 <b><u>التقرير التفاعلي الصادق</u></b>\n"
                    f"<blockquote>▎{filt_txt}</blockquote>\n"
                    "▎لا إشارات مطابقة لهذه التصفية بعد — وسّع التصفية أو أرسل إشارات.")
        rows = c.execute(f"""SELECT status, COUNT(*) n FROM signals WHERE {w}
                             GROUP BY status ORDER BY n DESC""", params).fetchall()
        status_txt = "\n".join(f"▎<b>{esc(r['status'])}</b>: <b>{r['n']}</b>" for r in rows)
        closed = c.execute(f"""SELECT symbol, status, COUNT(*) n, COALESCE(SUM(pips_result),0) p
                               FROM signals
                               WHERE {w} AND (status IN ('TP3_HIT','SL_HIT','LOCKED_50')
                                  OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!=''))
                               GROUP BY symbol, status""", params).fetchall()
        per_symbol: dict[str, dict] = {}
        for r in closed:
            sym = r["symbol"] or "?"
            row = per_symbol.setdefault(sym, {"w": 0, "l": 0, "pips": 0.0})
            is_win = r["status"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "LOCKED_50")
            row["w"] += int(r["n"]) if is_win else 0
            row["l"] += 0 if is_win else int(r["n"])
            row["pips"] += float(r["p"] or 0)
        from fysb import instruments as _inst
        tot_w = sum(v["w"] for v in per_symbol.values())
        tot_l = sum(v["l"] for v in per_symbol.values())
        quality = c.execute(f"SELECT AVG(score) a FROM signals WHERE {w} AND score>0",
                            params).fetchone()["a"]
        avg_rr = c.execute(f"SELECT AVG(rr) a FROM signals WHERE {w} AND rr>0",
                           params).fetchone()["a"]
    out = [
        "📊 <b><u>التقرير التفاعلي الصادق</u></b>",
        f"<blockquote>▎{filt_txt}\n▎📡 الإجمالي المطابق: <b>{total}</b></blockquote>",
        card(status_txt),
    ]
    body = [f"{ICON['signal']} <b>المغلق — الحقيقة كاملة</b>"]
    if per_symbol:
        tot_usd = 0.0
        for sym in sorted(per_symbol):
            v = per_symbol[sym]
            usd = _inst.usd_move(sym, v["pips"], 0.01)
            tot_usd += usd
            body.append(f"▎<b>{esc(sym)}</b>: رابحة <b>{v['w']}</b> · خاسرة <b>{v['l']}</b> "
                        f"· <b>{v['pips']:+.1f}</b> نقطة ≈ <b>{usd:+.2f}$</b>")
        body.append("  ─────────────")
        body.append(f"▎<b>الإجمالي:</b> رابحة <b>{tot_w}</b> · خاسرة <b>{tot_l}</b> · "
                    f"النتيجة <b>{tot_usd:+.2f}$</b> <i>(عند 0.01 لوت لكل صفقة)</i>")
        if tot_w + tot_l > 0:
            body.append(f"▎نسبة الإصابة: <b>{tot_w / (tot_w + tot_l) * 100:.0f}%</b>")
        if quality:
            body.append(f"▎متوسط الجودة المسجل: <b>{float(quality):.1f}/100</b>")
        if avg_rr:
            body.append(f"▎متوسط RR المخطط: <b>1:{float(avg_rr):.1f}</b>")
    else:
        body.append("▎لا صفقات مغلقة ضمن هذه التصفية بعد — الأرقام تظهر لحظة إغلاق أول صفقة.")
    out.append(card("\n".join(body)))
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
        # v8.14.17 — the honest closed ledger: LOCKED_50 is a CLOSED profit
        # (by construction it only exists when usd_move reached the lock
        # threshold) — it was silently excluded before. Wins are classified by
        # STATUS semantics (banked TP / locked), losses by SL_HIT. Pips are
        # NEVER summed across symbols (a BTC pip ≠ a gold pip) — per-symbol
        # rows + one USD total via the real usd_move contract at 0.01 lot.
        closed = c.execute(
            """SELECT symbol, status, COUNT(*) n, COALESCE(SUM(pips_result), 0) p
               FROM signals
               WHERE status IN ('TP3_HIT','SL_HIT','LOCKED_50')
                  OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='')
               GROUP BY symbol, status"""
        ).fetchall()
        from fysb import instruments as _inst
        if closed:
            per_symbol: dict[str, dict] = {}
            for r in closed:
                sym = r["symbol"] or "?"
                row = per_symbol.setdefault(sym, {"w": 0, "l": 0, "pips": 0.0})
                is_win = r["status"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "LOCKED_50")
                is_loss = r["status"] == "SL_HIT"
                row["w"] += int(r["n"]) if is_win else 0
                row["l"] += int(r["n"]) if is_loss else 0
                row["pips"] += float(r["p"] or 0)
            tot_w = sum(v["w"] for v in per_symbol.values())
            tot_l = sum(v["l"] for v in per_symbol.values())
            tot_usd = 0.0
            body = [f"{ICON['signal']} <b>النتائج المغلقة — الحقيقة كاملة</b>"]
            for sym in sorted(per_symbol):
                v = per_symbol[sym]
                usd = _inst.usd_move(sym, v["pips"], 0.01)
                tot_usd += usd
                usd_txt = f" ≈ <b>{usd:+.2f}$</b>" if abs(usd) > 0.005 else ""
                body.append(f"▎<b>{esc(sym)}</b>: رابحة <b>{v['w']}</b> · "
                            f"خاسرة <b>{v['l']}</b> · <b>{v['pips']:+.1f}</b> نقطة{usd_txt}")
            body.append(f"  ─────────────")
            body.append(f"▎<b>الإجمالي:</b> رابحة <b>{tot_w}</b> · خاسرة <b>{tot_l}</b> · "
                        f"النتيجة <b>{tot_usd:+.2f}$</b> <i>(عند 0.01 لوت لكل صفقة)</i>")
            if tot_w + tot_l > 0:
                body.append(f"▎نسبة الإصابة: <b>{tot_w / (tot_w + tot_l) * 100:.0f}%</b>")
            lines.append(card("\n".join(body)))
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
