"""Signal lifecycle tracker — status updates from REAL price data only.

Rules:
  * Statuses: ACTIVE -> TP1_HIT -> TP2_HIT -> TP3_HIT (or SL_HIT / CANCELLED / EXPIRED)
  * A TP/SL is only recorded when provider candles confirm the price actually
    traded through the level. On gaps where a candle spans both SL and TP, the
    conservative rule applies (SL first) — documented, never optimistic.
  * Missing/stale data -> skip the check entirely; no fabricated outcomes.
  * Users who received the signal are notified on state transitions (once).
"""
import json

from fysb import config, db
from fysb.engines import market_data
from fysb.config import log

TERMINAL = ("SL_HIT", "TP3_HIT", "CANCELLED", "EXPIRED")


def _provider_symbol(symbol: str) -> str:
    with db.conn() as c:
        r = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return r["provider_symbol"] if r else symbol


def check_open_signals(api_key: str) -> dict:
    """Scan all open signals against recent candles; update states.

    Returns stats + a notifications list [(user_id, text), ...] and an
    admin_notice — the CALLER (inside the bot's event loop) sends them.
    """
    stats = {"checked": 0, "updated": 0, "skipped_stale": 0, "errors": 0}
    notifications: list[tuple[int, str]] = []
    admin_notice = ""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM signals WHERE status IN ('ACTIVE','TP1_HIT','TP2_HIT') ORDER BY id").fetchall()]
    for sig in rows:
        stats["checked"] += 1
        symbol = sig["symbol"]
        try:
            payload = market_data.fetch_candles(_provider_symbol(symbol), "5min", api_key, outputsize=60)
        except RuntimeError:
            try:
                payload = market_data.fetch_candles(_provider_symbol(symbol), "15min", api_key, outputsize=60)
            except RuntimeError as e:
                stats["errors"] += 1
                _log(symbol, sig["timeframe"], "TRACK_ERROR", str(e)[:200])
                continue
        if market_data.is_stale(payload):
            stats["skipped_stale"] += 1
            continue
        # candles strictly AFTER signal creation (avoid counting the forming bar)
        candles = [c for c in payload["values"] if c["datetime"] >= sig["created_at"][:19]]
        if not candles:
            stats["skipped_stale"] += 1
            continue
        transition = _evaluate_signal(sig, candles)
        if transition:
            stats["updated"] += 1
            notes = _apply_transition(sig, transition, payload)
            notifications.extend(notes)
            admin_notice += f"\n📡 إشارة #{sig['id']} {sig['symbol']}: {transition} ({sig.get('pips_result') or 0:+.1f} pips)"
            continue
        # Half-TP1 secure-profit advisory (one-time per signal, data-verified)
        if not sig.get("half_notified") and sig["status"] == "ACTIVE":
            try:
                if halfway_reached(sig, candles):
                    with db.conn() as c:
                        c.execute("UPDATE signals SET half_notified=1 WHERE id=? AND half_notified=0", (sig["id"],))
                    notes = _halfway_notes(sig, candles)
                    notifications.extend(notes)
                    admin_notice += f"\n💧 إشارة #{sig['id']} {sig['symbol']}: وصل السعر نصف الطريق نحو الهدف الأول"
            except Exception:
                pass
    return {"stats": stats, "notifications": notifications, "admin_notice": admin_notice}


def _halfway_notes(sig: dict, candles: list[dict]) -> list[tuple[int, str]]:
    """Luxury half-way secure-profit advisory for every receiver + admin."""
    from fysb import instruments
    from fysb.theme import dir_badge, esc, tf_name
    from fysb import config as cfg
    symbol = sig["symbol"]
    entry = float(sig["entry"])
    tp1 = float(sig["tp1"])
    dist = abs(tp1 - entry)
    secured_half_price = entry + (dist / 2.0) if sig["direction"] == "BUY" else entry - (dist / 2.0)
    text = (
        f"💧 <b>تحديث إدارة الصفقة</b> — {esc(symbol)} {dir_badge(sig['direction'])}\n"
        f"<blockquote>"
        f"▎✨ وصل السعر <b>نصف الطريق</b> نحو الهدف الأول (TP1).\n"
        f"▎🎯 الهدف الأول: <code>{instruments.fmt_price(symbol, tp1)}</code>\n"
        f"▎🛡 <b>توصية الحماية:</b> انقل وقف الخسارة إلى نقطة الدخول "
        f"<code>{instruments.fmt_price(symbol, entry)}</code> وأمّن جزءاً من الربح (إغلاق 50%).\n"
        f"▎⏱ الإطار: <b>{tf_name(sig['timeframe'])}</b> · الحالة: <b>نشطة — تحرك لصالحك</b>\n"
        f"</blockquote>\n"
        f"<i>تحديث مُتحقق من بيانات السعر الفعلية — وليس تنفيذاً تلقائياً لحسابك الحقيقي.</i>"
    )
    with db.conn() as c:
        uids = [r["user_id"] for r in c.execute("SELECT user_id FROM signal_deliveries WHERE signal_id=?", (sig["id"],))]
    notes = [(uid, text) for uid in uids]
    if cfg.ADMIN_ID:
        notes.append((cfg.ADMIN_ID, f"💧 إشارة #{sig['id']} {symbol}: نصف الطريق نحو TP1"))
    return notes


def _evaluate_signal(sig: dict, candles: list[dict]) -> str | None:
    """Return the next status if price data justifies it, else None.

    Conservative gap rule: when a single candle spans both SL and a TP,
    SL wins (assumed worst case first).
    """
    direction = sig["direction"]
    sl, tp1, tp2, tp3 = sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"]
    hit_tp = int(sig["hit_tp"] or 0)

    # Walk candles in order; track best progress.
    progress_tp = hit_tp
    sl_touched = False
    for c in candles:
        hi, lo = c["high"], c["low"]
        if direction == "BUY":
            if lo <= sl:
                sl_touched = True
            if progress_tp == 0 and hi >= tp1:
                progress_tp = 1
            if progress_tp == 1 and hi >= tp2:
                progress_tp = 2
            if progress_tp == 2 and hi >= tp3:
                progress_tp = 3
        else:
            if hi >= sl:
                sl_touched = True
            if progress_tp == 0 and lo <= tp1:
                progress_tp = 1
            if progress_tp == 1 and lo <= tp2:
                progress_tp = 2
            if progress_tp == 2 and lo <= tp3:
                progress_tp = 3

    if sl_touched and progress_tp == hit_tp:
        return "SL_HIT"
    if progress_tp > hit_tp:
        if progress_tp == 3 and (sl_touched or True):
            return "TP3_HIT"
        return {1: "TP1_HIT", 2: "TP2_HIT", 3: "TP3_HIT"}[progress_tp]
    if sl_touched:
        return "SL_HIT"
    return None


def halfway_reached(sig: dict, candles: list[dict]) -> bool:
    """Did price already cover >=50% of the entry->TP1 distance (in trade
    direction) at any point AFTER the signal, without hitting SL?"""
    direction = sig["direction"]
    entry, tp1, sl = float(sig["entry"]), float(sig["tp1"]), float(sig["stop"])
    dist = abs(tp1 - entry)
    if dist <= 0:
        return False
    half = entry + (dist / 2.0) if direction == "BUY" else entry - (dist / 2.0)
    for c in candles:
        if direction == "BUY":
            if c["low"] <= sl:
                return False            # invalidated first — no celebration
            if c["high"] >= half:
                return True
        else:
            if c["high"] >= sl:
                return False
            if c["low"] <= half:
                return True
    return False


def _apply_transition(sig: dict, new_status: str, payload: dict) -> list[tuple[int, str]]:
    """Persist the transition (idempotent); return [(user_id, text)] notifications
    for receivers + the admin. The caller sends them inside the event loop."""
    from fysb.theme import dir_badge, esc
    status = new_status
    hit_tp = {"TP1_HIT": 1, "TP2_HIT": 2, "TP3_HIT": 3, "SL_HIT": int(sig["hit_tp"] or 0)}.get(status, int(sig["hit_tp"] or 0))
    pips_result = 0.0
    from fysb import instruments
    if status == "SL_HIT":
        pips_result = -instruments.pips(sig["symbol"], sig["entry"], sig["stop"])
    elif status.startswith("TP"):
        tp_map = {1: sig["tp1"], 2: sig["tp2"], 3: sig["tp3"]}
        pips_result = instruments.pips(sig["symbol"], sig["entry"], tp_map[hit_tp])
    with db.tx(immediate=True) as c:
        r = c.execute("SELECT status FROM signals WHERE id=?", (sig["id"],)).fetchone()
        if not r or r["status"] in TERMINAL:
            return []  # already terminal — never double-update
        closed_at = config.now() if status in TERMINAL else ""
        c.execute("""UPDATE signals SET status=?, hit_tp=?, closed_at=?, pips_result=?, data_time=?
                     WHERE id=? AND status=?""",
                  (status, hit_tp, closed_at, pips_result, payload["last_candle_iso"], sig["id"], sig["status"]))
    _log(sig["symbol"], sig["timeframe"], f"TRANSITION:{status}", f"signal #{sig['id']} pips={pips_result:.1f}")

    from fysb.theme import dir_badge, esc, status_ar, tf_name
    tp_map = {1: sig["tp1"], 2: sig["tp2"], 3: sig["tp3"]}
    detail = ""
    if status.startswith("TP"):
        detail = (f"▎🏁 الهدف المتحقق: <code>{instruments.fmt_price(sig['symbol'], tp_map[hit_tp])}</code>\n")
        if hit_tp == 1:
            detail += ("▎🛡 <b>إدارة الصفقة:</b> انقل وقف الخسارة إلى نقطة الدخول (حماية كاملة) "
                       "واُترك الجزء المتبقي يعمل نحو الهدفين التاليين.\n")
        if status == "TP3_HIT":
            detail += "▎👑 <b>صفقة مكتملة بكل الأهداف — أداء ممتاز.</b>\n"
    if status == "SL_HIT":
        detail = "▎🛡 الخسارة محدودة بمقدار وقف الخسارة المخطط — التزم بإدارة المخاطر في الفكرة القادمة.\n"
    text = (
        f"📡 <b>تحديث الإشارة #{sig['id']}</b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])}\n"
        f"<blockquote>"
        f"▎📌 الحالة الجديدة: <b>{status_ar(status)}</b>\n"
        f"▎⏱ الإطار: <b>{tf_name(sig['timeframe'])}</b>\n"
        f"▎📈 النتيجة حتى الآن: <b>{pips_result:+.1f} pips</b>\n"
        f"{detail}"
        f"</blockquote>\n"
        f"<i>تحديث قائم على بيانات السعر الفعلية من السوق اللحظي.</i>"
    )
    with db.conn() as c:
        uids = [r["user_id"] for r in c.execute("SELECT user_id FROM signal_deliveries WHERE signal_id=?", (sig["id"],))]
    notes = [(uid, text) for uid in uids]
    if config.ADMIN_ID:
        notes.append((config.ADMIN_ID, f"📡 إشارة #{sig['id']} {sig['symbol']}: {status} ({pips_result:+.1f} pips)"))
    return notes


def expire_stale_signals() -> int:
    """Mark ACTIVE signals past their expiry window as EXPIRED (no price claims)."""
    with db.conn() as c:
        cur = c.execute("UPDATE signals SET status='EXPIRED', closed_at=? WHERE status IN ('ACTIVE','TP1_HIT','TP2_HIT') AND expires_at!='' AND expires_at<=?", (config.now(), config.now()))
        return cur.rowcount


def _log(symbol, tf, result, detail):
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(symbol,timeframe,result,detail,created_at) VALUES(?,?,?,?,?)",
                  (symbol, tf, result[:40], detail[:400], config.now()))
