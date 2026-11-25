"""Personal Risk Management & Auto Trading Journal (v6.26.0 — #21..#24).

  #21 1-Tap Lot Size Calculator   — exact lot, margin, SL risk $, TP1/2/3 profit $
  #22 Personal Risk Profile       — balance, risk %, leverage, max daily DD %
  #23 Daily Drawdown Guard        — real-time warning when today's loss hits cap
  #24 Auto Trading Journal        — 1-tap TAKEN/SKIPPED logging + auto P/L sync
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fysb import config, db, instruments
from fysb.theme import bar, dir_badge, esc, strategy_name, tf_name

DEFAULT_BALANCE = 1000.0
DEFAULT_RISK_PCT = 1.0
DEFAULT_LEVERAGE = 100
DEFAULT_MAX_DD_PCT = 3.0


# ---------------------------------------------------------------------------
# #22 Personal Risk Profile
# ---------------------------------------------------------------------------

def get_risk_profile(user_id: int) -> dict:
    """Fetch (or return sensible defaults for) a user's real-account risk profile."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM user_risk_profiles WHERE user_id=?", (int(user_id),)).fetchone()
    if r:
        return {
            "user_id": int(user_id),
            "balance": float(r["balance"] or DEFAULT_BALANCE),
            "risk_pct": float(r["risk_pct"] or DEFAULT_RISK_PCT),
            "leverage": int(r["leverage"] or DEFAULT_LEVERAGE),
            "max_daily_dd_pct": float(r["max_daily_dd_pct"] or DEFAULT_MAX_DD_PCT),
            "configured": True,
            "updated_at": r["updated_at"] or "",
        }
    return {
        "user_id": int(user_id),
        "balance": DEFAULT_BALANCE,
        "risk_pct": DEFAULT_RISK_PCT,
        "leverage": DEFAULT_LEVERAGE,
        "max_daily_dd_pct": DEFAULT_MAX_DD_PCT,
        "configured": False,
        "updated_at": "",
    }


def update_risk_profile(
    user_id: int,
    *,
    balance: float | None = None,
    risk_pct: float | None = None,
    leverage: int | None = None,
    max_daily_dd_pct: float | None = None,
) -> dict:
    cur = get_risk_profile(user_id)
    bal = round(max(10.0, min(10_000_000.0, float(balance if balance is not None else cur["balance"]))), 2)
    rp = round(max(0.1, min(20.0, float(risk_pct if risk_pct is not None else cur["risk_pct"]))), 2)
    lev = max(1, min(2000, int(leverage if leverage is not None else cur["leverage"])))
    dd = round(max(0.5, min(50.0, float(max_daily_dd_pct if max_daily_dd_pct is not None else cur["max_daily_dd_pct"]))), 2)
    now_iso = config.now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO user_risk_profiles(user_id, balance, risk_pct, leverage, max_daily_dd_pct, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 balance=excluded.balance,
                 risk_pct=excluded.risk_pct,
                 leverage=excluded.leverage,
                 max_daily_dd_pct=excluded.max_daily_dd_pct,
                 updated_at=excluded.updated_at""",
            (int(user_id), bal, rp, lev, dd, now_iso),
        )
    return get_risk_profile(user_id)


# ---------------------------------------------------------------------------
# #21 Exact 1-Tap Lot Size Calculator
# ---------------------------------------------------------------------------

def calculate_signal_lot(
    user_id: int,
    sig: dict,
    row=None,
    *,
    custom_balance: float | None = None,
    custom_risk_pct: float | None = None,
) -> dict:
    """Compute exact position size, SL dollar risk, margin required, and TP1-3 profits."""
    prof = get_risk_profile(user_id)
    bal = float(custom_balance) if custom_balance is not None else prof["balance"]
    rp = float(custom_risk_pct) if custom_risk_pct is not None else prof["risk_pct"]
    lev = max(1, int(prof["leverage"] or 100))

    symbol = instruments.normalize(sig["symbol"])
    direction = str(sig.get("direction") or "BUY").upper()
    entry = float(sig["entry"])
    sl = float(sig.get("stop") or 0.0)
    sl_pips = abs(instruments.pips(symbol, entry, sl, row)) if sl > 0 else 0.0

    raw_lots = instruments.position_size(bal, rp, entry, sl, symbol, row) if sl > 0 else 0.01
    lots = round(max(0.01, min(raw_lots, 100.0)), 2)
    risk_usd = abs(instruments.pnl(symbol, direction, lots, entry, sl, row)) if sl > 0 else (bal * rp / 100.0)
    actual_risk_pct = (risk_usd / bal * 100.0) if bal > 0 else 0.0
    notional = instruments.notional_usd(symbol, lots, entry, row)
    margin_usd = notional / lev if lev > 0 else notional

    tp_profits = []
    for i, k in enumerate(("tp1", "tp2", "tp3"), start=1):
        lvl = float(sig.get(k) or 0.0)
        if lvl > 0:
            p = abs(instruments.pnl(symbol, direction, lots, entry, lvl, row))
            pp = abs(instruments.pips(symbol, entry, lvl, row))
            tp_profits.append({"idx": i, "price": lvl, "pips": round(pp, 1), "usd": round(p, 2)})

    return {
        "symbol": symbol,
        "direction": direction,
        "balance": bal,
        "risk_pct": rp,
        "actual_risk_pct": round(actual_risk_pct, 2),
        "leverage": lev,
        "lots": lots,
        "sl_pips": round(sl_pips, 1),
        "risk_usd": round(risk_usd, 2),
        "margin_usd": round(margin_usd, 2),
        "tp_profits": tp_profits,
        "profile": prof,
    }


def render_lot_calculator_card(user_id: int, sig: dict, row=None) -> str:
    """Luxury 1-Tap Lot Calculator HTML card for a specific signal."""
    calc = calculate_signal_lot(user_id, sig, row=row)
    dd = daily_drawdown_status(user_id)
    sym = calc["symbol"]
    lines = [
        f"🧮 <b><u>حاسبة اللوت الملكية — {esc(sym)} {dir_badge(calc['direction'])}</u></b>",
        "<blockquote>"
        f"▎💰 رصيد ملف المخاطرة: <b>{calc['balance']:,.2f} $</b> · الرافعة: <b>1:{calc['leverage']}</b>\n"
        f"▎⚖️ المخاطرة المطلوبة: <b>{calc['risk_pct']:g}%</b> · مسافة الوقف: <b>-{calc['sl_pips']:.1f} pips</b>\n"
        f"▎📦 <b>حجم اللوت الموصى به:</b> <code>{calc['lots']:.2f} lot</code>\n"
        f"▎🛑 الخسارة الدقيقة عند SL: <b>-{calc['risk_usd']:,.2f} $</b> ({calc['actual_risk_pct']:.2f}%)\n"
        f"▎🏦 الهامش التقريبي المطلوب: <b>{calc['margin_usd']:,.2f} $</b>"
        "</blockquote>",
    ]
    if calc["tp_profits"]:
        tp_lines = []
        for t in calc["tp_profits"]:
            tp_lines.append(
                f"▎🏁 <b>TP{t['idx']}</b> (<code>{instruments.fmt_price(sym, t['price'], row)}</code> · "
                f"+{t['pips']:.1f}p): <b>+{t['usd']:,.2f} $</b>"
            )
        lines.append("<blockquote>" + "\n".join(tp_lines) + "</blockquote>")
    if dd["breached"]:
        lines.append(
            f"\n🚨 <b>تحذير حارس السحب اليومي:</b> بلغت خسائر يومك <b>{dd['today_loss_pct']:.2f}%</b> "
            f"(الحد الأقصى: <b>{dd['max_daily_dd_pct']:g}%</b>) — يُنصح بالتوقف اليوم لحماية رأس مالك!"
        )
    lines.append("\n<i>💡 يمكنك تعديل رصيدك أو نسبة المخاطرة من «🛡 ملف المخاطرة» في أي وقت.</i>")
    return "\n".join(lines)


def compact_risk_snippet(user_id: int, sig: dict, row=None) -> str:
    """Compact 2-line real-account lot recommendation appended to delivered signal cards."""
    prof = get_risk_profile(user_id)
    if not prof["configured"]:
        return ""
    calc = calculate_signal_lot(user_id, sig, row=row)
    tp1_usd = calc["tp_profits"][0]["usd"] if calc["tp_profits"] else 0.0
    dd = daily_drawdown_status(user_id)
    out = (
        f"🛡 <b>ملف مخاطرتك الحقيقي ({calc['balance']:,.0f}$ · {calc['risk_pct']:g}%):</b> "
        f"اللوت المقترح <code>{calc['lots']:.2f} lot</code> "
        f"(SL: <b>-{calc['risk_usd']:,.2f}$</b> · TP1: <b>+{tp1_usd:,.2f}$</b>)"
    )
    if dd["breached"]:
        out += f"\n🚨 <b>حارس السحب اليومي:</b> تجاوزت حد خسارتك اليومي ({dd['today_loss_pct']:.1f}% / {dd['max_daily_dd_pct']:g}%)!"
    return out


# ---------------------------------------------------------------------------
# #23 & #24 Daily Drawdown Guard + Auto Trading Journal
# ---------------------------------------------------------------------------

def log_journal_action(user_id: int, sig: dict, action: str) -> dict:
    """Record user's 1-tap TAKEN or SKIPPED decision on a signal."""
    act = (action or "").upper()
    if act not in ("TAKEN", "SKIPPED"):
        return {"ok": False, "error": "invalid action"}
    sid = int(sig["id"])
    calc = calculate_signal_lot(user_id, sig)
    lots = calc["lots"] if act == "TAKEN" else 0.0
    risk_usd = calc["risk_usd"] if act == "TAKEN" else 0.0
    now_iso = config.now()

    # Check if signal is already closed -> compute realized outcome immediately
    status_j = "SKIPPED" if act == "SKIPPED" else "OPEN"
    realized_pips = 0.0
    realized_usd = 0.0
    closed_at = ""
    sig_st = str(sig.get("status") or "").upper()
    if act == "TAKEN" and sig_st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT") and (sig.get("closed_at") or ""):
        status_j = "CLOSED"
        realized_pips = float(sig.get("pips_result") or 0.0)
        realized_usd = round(instruments.usd_move(sig["symbol"], realized_pips, lots, px=float(sig.get("entry") or 0)), 2)
        closed_at = str(sig.get("closed_at") or now_iso)

    with db.conn() as c:
        c.execute(
            """INSERT INTO trade_journal(
                 user_id, signal_id, symbol, direction, action, lots, risk_usd,
                 realized_pips, realized_usd, status, created_at, closed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, signal_id) DO UPDATE SET
                 action=excluded.action,
                 lots=excluded.lots,
                 risk_usd=excluded.risk_usd,
                 realized_pips=excluded.realized_pips,
                 realized_usd=excluded.realized_usd,
                 status=excluded.status,
                 closed_at=excluded.closed_at""",
            (int(user_id), sid, sig["symbol"], sig["direction"], act, lots, risk_usd,
             realized_pips, realized_usd, status_j, now_iso, closed_at),
        )
    return {
        "ok": True,
        "action": act,
        "lots": lots,
        "risk_usd": risk_usd,
        "status": status_j,
        "drawdown": daily_drawdown_status(user_id),
    }


def sync_user_journal(user_id: int) -> int:
    """Sync OPEN journal rows against real closed outcomes in `signals`."""
    updated = 0
    with db.conn() as c:
        open_rows = c.execute(
            """SELECT j.id, j.signal_id, j.symbol, j.lots, s.status AS s_status,
                      s.pips_result, s.entry, s.closed_at
               FROM trade_journal j
               JOIN signals s ON s.id = j.signal_id
               WHERE j.user_id=? AND j.action='TAKEN' AND j.status='OPEN'
                 AND (
                   s.status IN ('TP3_HIT','SL_HIT','CANCELLED','EXPIRED')
                   OR (s.status IN ('TP1_HIT','TP2_HIT') AND s.closed_at IS NOT NULL AND s.closed_at!='')
                 )""",
            (int(user_id),),
        ).fetchall()
        for r in open_rows:
            pips = float(r["pips_result"] or 0.0)
            usd = round(instruments.usd_move(r["symbol"], pips, float(r["lots"] or 0.01), px=float(r["entry"] or 0)), 2)
            c.execute(
                "UPDATE trade_journal SET status='CLOSED', realized_pips=?, realized_usd=?, closed_at=? WHERE id=?",
                (pips, usd, r["closed_at"] or config.now(), r["id"]),
            )
            updated += 1
    return updated


def daily_drawdown_status(user_id: int) -> dict:
    """#23 Real-time Daily Drawdown Guard status from today's closed TAKEN journal trades."""
    sync_user_journal(user_id)
    prof = get_risk_profile(user_id)
    bal = max(1.0, prof["balance"])
    max_dd = prof["max_daily_dd_pct"]
    day_start = db.day_start_utc()
    with db.conn() as c:
        rows = c.execute(
            """SELECT realized_usd FROM trade_journal
               WHERE user_id=? AND action='TAKEN' AND status='CLOSED'
                 AND closed_at>=?""",
            (int(user_id), day_start),
        ).fetchall()
    net_today_usd = sum(float(r["realized_usd"] or 0.0) for r in rows)
    loss_usd = abs(min(0.0, net_today_usd))
    loss_pct = (loss_usd / bal) * 100.0
    return {
        "balance": bal,
        "max_daily_dd_pct": max_dd,
        "net_today_usd": round(net_today_usd, 2),
        "today_loss_usd": round(loss_usd, 2),
        "today_loss_pct": round(loss_pct, 2),
        "remaining_dd_pct": round(max(0.0, max_dd - loss_pct), 2),
        "breached": loss_pct >= max_dd,
        "trades_closed_today": len(rows),
    }


def journal_summary(user_id: int, days: int = 30) -> dict:
    """#24 Aggregate personal Trading Journal analytics over last `days`."""
    sync_user_journal(user_id)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    with db.conn() as c:
        rows = c.execute(
            """SELECT * FROM trade_journal
               WHERE user_id=? AND created_at>=?
               ORDER BY id DESC""",
            (int(user_id), cutoff),
        ).fetchall()
    taken = [dict(r) for r in rows if r["action"] == "TAKEN"]
    skipped = [dict(r) for r in rows if r["action"] == "SKIPPED"]
    closed = [r for r in taken if r["status"] == "CLOSED"]
    wins = [r for r in closed if float(r["realized_pips"] or 0) > 0]
    losses = [r for r in closed if float(r["realized_pips"] or 0) < 0]
    net_pips = sum(float(r["realized_pips"] or 0) for r in closed)
    net_usd = sum(float(r["realized_usd"] or 0) for r in closed)
    wr = (len(wins) / len(closed) * 100.0) if closed else 0.0
    return {
        "days": days,
        "taken_count": len(taken),
        "skipped_count": len(skipped),
        "open_count": sum(1 for r in taken if r["status"] == "OPEN"),
        "closed_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 1),
        "net_pips": round(net_pips, 1),
        "net_usd": round(net_usd, 2),
        "recent": taken[:6],
        "drawdown": daily_drawdown_status(user_id),
    }


def render_risk_profile_card(user_id: int) -> str:
    """Ultra-luxury Personal Risk Profile & Daily Drawdown Guard card."""
    p = get_risk_profile(user_id)
    dd = daily_drawdown_status(user_id)
    dd_badge = "🚨 تم تجاوز الحد اليومي!" if dd["breached"] else f"🟢 آمن (المتبقي {dd['remaining_dd_pct']:.2f}%)"
    return (
        "🛡 <b><u>ملف إدارة المخاطر الشخصي وحارس السحب اليومي</u></b>\n"
        "<blockquote>"
        f"▎💰 رصيد حسابك الحقيقي: <b>{p['balance']:,.2f} $</b>\n"
        f"▎⚖️ نسبة المخاطرة لكل صفقة: <b>{p['risk_pct']:g}%</b> (≈ <b>{p['balance'] * p['risk_pct'] / 100:,.2f} $</b>)\n"
        f"▎🏦 الرافعة المالية: <b>1:{p['leverage']}</b>\n"
        f"▎🚨 حد السحب اليومي الأقصى: <b>{p['max_daily_dd_pct']:g}%</b>\n"
        f"▎📊 حالة حارس السحب اليوم: <b>{dd_badge}</b> (خسارة اليوم: <b>{dd['today_loss_pct']:.2f}%</b>)"
        "</blockquote>\n\n"
        "<i>✨ كل إشارة تصلك ستحسب تلقائياً حجم اللوت المخصص لرصيدك ونسبة مخاطرتك بدقة مؤسسية.</i>"
    )


def render_journal_card(user_id: int, days: int = 30) -> str:
    """Ultra-luxury Personal Auto Trading Journal summary card."""
    s = journal_summary(user_id, days=days)
    dd = s["drawdown"]
    lines = [
        f"📓 <b><u>مفكرة التداول التلقائية — آخر {days} يوماً</u></b>",
        "<blockquote>"
        f"▎✅ صفقات دخلتها: <b>{s['taken_count']}</b> (مفتوحة: {s['open_count']} · مغلقة: {s['closed_count']})\n"
        f"▎❌ صفقات تجاهلتها: <b>{s['skipped_count']}</b>\n"
        f"▎🎯 نسبة فوزك الشخصية: <b>{s['win_rate']:.1f}%</b> ({s['wins']}✓ / {s['losses']}✗)  {bar(s['win_rate'], 100, 8)}\n"
        f"▎📈 صافي نقاطك: <b>{s['net_pips']:+.1f} pips</b>\n"
        f"▎💵 صافي ربحك/خسارتك المقدّرة: <b>{s['net_usd']:+,.2f} $</b>\n"
        f"▎🛡 حارس السحب اليوم: <b>{'🚨 متجاوز (' + str(dd['today_loss_pct']) + '%)' if dd['breached'] else '🟢 آمن (' + str(dd['today_loss_pct']) + '% / ' + str(dd['max_daily_dd_pct']) + '%)'}</b>"
        "</blockquote>",
    ]
    if s["recent"]:
        lines.append("\n<b>آخر الصفقات المسجلة في مفكرتك:</b>")
        for r in s["recent"]:
            st_ico = "🟢" if r["status"] == "OPEN" else ("🏆" if float(r["realized_pips"] or 0) > 0 else "🛑")
            res_s = f"{float(r['realized_pips']):+.1f}p ({float(r['realized_usd']):+,.2f}$)" if r["status"] == "CLOSED" else "جارية الآن"
            lines.append(
                f"  {st_ico} <b>{esc(r['symbol'])}</b> {r['direction']} · <code>{float(r['lots']):.2f} lot</code> · {res_s}"
            )
    else:
        lines.append("\n<i>✨ اضغط «✅ دخلت الصفقة» أو «❌ تجاهلتها» أسفل أي إشارة لتبني سجل أدائك التلقائي.</i>")
    return "\n".join(lines)
