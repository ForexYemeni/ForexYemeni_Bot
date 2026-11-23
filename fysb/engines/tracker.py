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


def _utc_cutoff(iso: str) -> str:
    """Normalize ANY stored timestamp to 'YYYY-MM-DD HH:MM:SS' (UTC) so it can
    be compared with candle datetimes of the same format.

    The pipeline stores created_at as config.now() -> ISO with a 'T' separator
    ("2026-11-23T00:41:33+00:00"), while every provider candle carries a
    'YYYY-MM-DD HH:MM:SS' string with a plain space. A raw string comparison
    (' ' < 'T') silently excluded EVERY candle newer than the signal — signals
    then stayed ACTIVE forever. Both sides are normalized here; timestamps are
    stored UTC-only platform-wide, and tz-aware values are converted to UTC.
    """
    s = (iso or "").strip()
    if not s:
        return "0000-01-01 00:00:00"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return s.replace("T", " ")[:19]


def _provider_symbol(symbol: str) -> str:
    with db.conn() as c:
        r = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return r["provider_symbol"] if r else symbol


def _tracking_payload(provider_symbol: str, api_key: str) -> dict:
    """Fetch a candle window LONG ENOUGH to cover the signal's whole lifetime.

    A young signal needs fine granularity; an old one needs history. The chain
    walks 5min -> 15min -> 1h and returns the first FRESH payload (each covers
    16h / 30h / 100h respectively). Raises RuntimeError when everything fails.
    """
    last_err: Exception | None = None
    for interval, size in (("5min", 200), ("15min", 120), ("1h", 100)):
        try:
            payload = market_data.fetch_candles(provider_symbol, interval, api_key, outputsize=size)
        except RuntimeError as e:
            last_err = e
            continue
        if market_data.is_stale(payload):
            last_err = RuntimeError(f"stale {interval} payload")
            continue
        return payload
    raise last_err or RuntimeError("تعذر جلب شموع التتبع — كل المزودين فشلوا")


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
            payload = _tracking_payload(_provider_symbol(symbol), api_key)
        except RuntimeError as e:
            stats["errors"] += 1
            _log(symbol, sig["timeframe"], "TRACK_ERROR", str(e)[:200])
            continue
        # candles strictly AFTER signal creation (avoid counting the forming bar)
        cutoff = _utc_cutoff(sig["created_at"])
        candles = [c for c in payload["values"] if c["datetime"] >= cutoff]
        if not candles:
            stats["skipped_stale"] += 1
            continue
        transition = _evaluate_signal(sig, candles)
        if transition:
            stats["updated"] += 1
            notes, result_pips = _apply_transition(sig, transition, payload)
            notifications.extend(notes)
            admin_notice += f"\n📡 إشارة #{sig['id']} {sig['symbol']}: {transition} ({result_pips:+.1f} pips)"
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

    Chronological, order-honest walk:
      * A stop-loss touch BEFORE any target progress kills the trade ->
        SL_HIT immediately (a trade stopped out first must never be re-branded
        as a winner just because price later crossed a target).
      * Once a target is banked, the remaining position keeps running toward
        the next ones (best-progress semantics — unchanged from the royal
        baseline); later stop touches on the original stop do not downgrade a
        partially-secured trade.
      * Gap candles that span both levels resolve to the banked target of the
        conservative model already in place.
    """
    direction = sig["direction"]
    sl, tp1, tp2, tp3 = sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"]
    hit_tp = int(sig["hit_tp"] or 0)

    progress_tp = hit_tp
    for c in candles:
        hi, lo = c["high"], c["low"]
        if direction == "BUY":
            sl_now = lo <= sl
            new_tp = 0
            if progress_tp == 0 and hi >= tp1:
                new_tp = 1
            if progress_tp <= 1 and hi >= tp2:
                new_tp = max(new_tp, 2)
            if progress_tp <= 2 and hi >= tp3:
                new_tp = max(new_tp, 3)
        else:
            sl_now = hi >= sl
            new_tp = 0
            if progress_tp == 0 and lo <= tp1:
                new_tp = 1
            if progress_tp <= 1 and lo <= tp2:
                new_tp = max(new_tp, 2)
            if progress_tp <= 2 and lo <= tp3:
                new_tp = max(new_tp, 3)
        if sl_now and progress_tp == hit_tp and new_tp == 0:
            return "SL_HIT"                       # stopped out before any gain
        if new_tp > progress_tp:
            progress_tp = new_tp                  # target banked; keep running
    if progress_tp > hit_tp:
        if progress_tp == 3:
            return "TP3_HIT"
        return {1: "TP1_HIT", 2: "TP2_HIT", 3: "TP3_HIT"}[progress_tp]
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


def _apply_transition(sig: dict, new_status: str, payload: dict) -> tuple[list[tuple[int, str]], float]:
    """Persist the transition (idempotent); return ([(user_id, text)], pips_result)
    notifications for receivers + the admin. The caller sends them inside the
    bot's event loop."""
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
    return notes, pips_result


def expire_stale_signals() -> int:
    """Mark ACTIVE signals past their expiry window as EXPIRED (no price claims).

    Two sweeps, both honest (no fabricated outcomes):
      * expires_at set and already passed;
      * expires_at EMPTY but the signal is older than the configured expiry
        window — legacy rows must never haunt the board as ACTIVE forever.
    """
    from datetime import datetime, timedelta, timezone
    hours = max(1, db.get_setting_int("signal_expiry_hours", 72))
    legacy_cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.conn() as c:
        cur = c.execute("""UPDATE signals SET status='EXPIRED', closed_at=?
                           WHERE status IN ('ACTIVE','TP1_HIT','TP2_HIT')
                           AND ((expires_at!='' AND expires_at<=?)
                                OR (expires_at='' AND created_at<=?))""",
                        (config.now(), config.now(), legacy_cutoff))
        return cur.rowcount


def _log(symbol, tf, result, detail):
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(symbol,timeframe,result,detail,created_at) VALUES(?,?,?,?,?)",
                  (symbol, tf, result[:40], detail[:400], config.now()))
