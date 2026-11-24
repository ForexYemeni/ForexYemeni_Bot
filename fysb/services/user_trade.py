"""User trade personalization — the luxury personal block appended to every
delivered signal, computed from the user's OWN demo account settings.

Contents (all computed server-side from real numbers):
  * position size (auto-by-risk or user fixed lot)
  * money at risk ($) if SL is hit
  * projected profit ($) at TP1 / TP2 / TP3
  * account balance used for the calculation

Also owns the auto-execution handoff to the demo engine (open_from_signal).
"""
from fysb import config, db, instruments
from fysb.engines import demo as demo_engine
from fysb.theme import bar, esc, strategy_name, tf_name


def personal_block(sig: dict, user_id: int) -> str:
    """Return the personal risk/profit card lines for this user+signal."""
    try:
        symbol = instruments.normalize(sig["symbol"])
        row = None
        with db.conn() as c:
            r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
            if r:
                row = dict(r)
        acc = demo_engine.get_or_create_account(user_id)
        entry = float(sig["entry"])
        sl = float(sig["stop"] or 0)
        lots, risk_money, risk_pct = demo_engine.compute_lots(user_id, symbol, entry, sl, row)
        if sl:
            risk_money = abs(instruments.pnl(symbol, "BUY", lots, entry, sl, row))
        lines = [
            f"💼 <b>خطتك الشخصية على حسابك التجريبي:</b>",
            f"▎💰 الرصيد المستخدم: <b>{acc['balance']:,.2f} USD</b>",
            f"▎⚖️ المخاطرة المختارة: <b>{risk_pct:g}%</b> (<b>{risk_money:,.2f} $</b> عند ضرب الوقف)",
            f"▎📦 حجم الصفقة المحسوب: <b>{lots:g} lot</b>",
        ]
        for i, tp in enumerate((sig["tp1"], sig["tp2"], sig["tp3"]), start=1):
            if not tp:
                continue
            profit = abs(instruments.pnl(symbol, sig["direction"], lots, entry, float(tp), row))
            lines.append(f"▎🏁 ربح متوقع عند TP{i}: <b>{profit:+,.2f} $</b>")
        lines.append(f"▎🤖 التنفيذ التلقائي: <b>{'مفعّل — تُفتح الصفقة في حسابك تلقائياً' if acc.get('auto_trade') else 'متوقف — فعّله من إعدادات التجريبي'}</b>")
        return "\n".join(lines)
    except Exception:
        return ""


def auto_execute(sig: dict, user_id: int) -> dict | None:
    """Execute the signal on the user's demo account when auto-trade is ON.
    Returns the execution summary dict (or None when disabled/failed silently)."""
    try:
        acc = demo_engine.get_or_create_account(user_id)
        if not (acc.get("auto_trade") or 0):
            return None
        res = demo_engine.open_from_signal(user_id, sig, config.market_api_key())
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "unknown")}
        return res
    except Exception as e:
        from fysb.config import log
        log.warning("auto execute failed user=%s: %s", user_id, type(e).__name__)
        return None


def execution_card(exec_res: dict, sig: dict) -> str:
    """Luxury 'your trade was opened automatically' card."""
    symbol = exec_res.get("symbol", sig["symbol"])
    lots = exec_res.get("lots", 0)
    plan = exec_res.get("plan") or {}
    lines = [
        f"🤖 <b>نُفّذت الصفقة تلقائياً في حسابك التجريبي #{exec_res.get('position_id')}</b>",
        "",
        f"▎🌐 الأداة: <b>{esc(symbol)}</b> · <b>{'شراء' if sig['direction']=='BUY' else 'بيع'}</b> · ⏱ {tf_name(sig['timeframe'])}",
        f"▎📦 الحجم: <b>{lots:g} lot</b> · ⚖️ مخاطرة: <b>{exec_res.get('risk_pct', 0):g}%</b> (<b>{exec_res.get('risk_money', 0):,.2f} $</b>)",
        f"▎🎯 الدخول: <code>{instruments.fmt_price(symbol, exec_res.get('entry_ref', sig['entry']))}</code>",
    ]
    for i in (1, 2, 3):
        if plan.get(f"tp{i}"):
            lines.append(f"▎🏁 ربح متوقع عند TP{i}: <b>{plan[f'tp{i}']:+,.2f} $</b>")
    lines += [
        "",
        "▎💧 سيصلك تنبيه عند وصول السعر <b>نصف الطريق نحو الهدف الأول</b> — عندئذ يُغلق 50% من الصفقة تلقائياً لتحقيق الربح ويُنقل الوقف لنقطة الدخول.",
        "▎🧪 إدارة الصفقة والنتيجة تُحسب داخل تيليجرام بالكامل على أسعار السوق الحقيقية.",
    ]
    return "\n".join(lines)
