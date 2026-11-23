"""Signal lifecycle tracker — status updates from REAL price data only.

Rules:
  * Statuses: ACTIVE -> RUNNING -> TP1_HIT -> TP2_HIT -> TP3_HIT (or SL_HIT /
    CANCELLED / EXPIRED). RUNNING = candle data confirms price actually traded
    through the entry level — the signal is IN the market, no longer «بانتظار
    التنفيذ». A signal whose entry was never touched stays ACTIVE honestly.
  * A TP/SL is only recorded when provider candles confirm the price actually
    traded through the level. On gaps where a candle spans both SL and TP, the
    conservative rule applies (SL first) — documented, never optimistic.
  * Missing/stale data -> skip the check entirely; no fabricated outcomes.
  * Users who received the signal are notified on state transitions (once).
"""
import json

from fysb import config, db, instruments
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


def entry_filled(sig: dict, candles: list[dict]) -> bool:
    """Candle-confirmed entry execution: a SELL fills when price trades at or
    ABOVE the entry level; a BUY fills when price trades at or BELOW it."""
    entry = float(sig["entry"])
    if sig["direction"] == "BUY":
        return any(c["low"] <= entry for c in candles)
    return any(c["high"] >= entry for c in candles)


def check_open_signals(api_key: str) -> dict:
    """Scan all open signals against recent candles; update states.

    Returns stats + notifications [(user_id, text), ...] + LIVE-CARD edits
    [(user_id, chat_id, message_id, text), ...] — the caller edits the user's
    ORIGINAL signal message in place so the first card never goes stale.
    """
    stats = {"checked": 0, "updated": 0, "refreshed": 0, "skipped_stale": 0, "errors": 0}
    notifications: list[tuple[int, str]] = []
    edits: list[tuple[int, str, int, str]] = []
    admin_notice = ""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') ORDER BY id").fetchall()]
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
        live_px = _payload_close(payload)
        transition = _evaluate_signal(sig, candles)
        if transition:
            stats["updated"] += 1
            notes, result_pips, new_edits = _apply_transition(sig, transition, payload, live_px)
            notifications.extend(notes)
            edits.extend(new_edits)
            continue
        # Entry-fill activation: the moment candles confirm price traded through
        # the entry level, the signal is IN the market — «بانتظار التنفيذ» must
        # never outlive an executed trade (the exact user-reported bug).
        activated_now = False
        if sig["status"] == "ACTIVE" and entry_filled(sig, candles):
            stats["updated"] += 1
            activated_now = True
            notes, _, new_edits = _apply_transition(sig, "RUNNING", payload, live_px)
            notifications.extend(notes)
            edits.extend(new_edits)
            sig["status"] = "RUNNING"
        # Half-TP1 secure-profit advisory (one-time per signal, data-verified)
        if not sig.get("half_notified") and sig["status"] in ("ACTIVE", "RUNNING"):
            try:
                if halfway_reached(sig, candles):
                    with db.conn() as c:
                        c.execute("UPDATE signals SET half_notified=1 WHERE id=? AND half_notified=0", (sig["id"],))
                    notes, new_edits = _halfway_notes(sig, candles, live_px)
                    notifications.extend(notes)
                    if not activated_now:      # the activation edit already refreshes the card
                        edits.extend(new_edits)
            except Exception:
                pass
        # Live-card pulse: re-render the ORIGINAL delivered card while the price
        # moved meaningfully — the user's first message is the live board.
        if not activated_now and live_px and _should_refresh(sig, live_px):
            refs = _delivery_refs(sig["id"])
            if refs:
                edits.extend(_card_refresh_edits(sig, live_px, refs))
                with db.conn() as c:
                    c.execute("UPDATE signals SET live_px=?, data_time=? WHERE id=?",
                              (live_px, payload["last_candle_iso"], sig["id"]))
                stats["refreshed"] += 1
    return {"stats": stats, "notifications": notifications,
            "admin_notice": admin_notice, "edits": edits}


def _halfway_notes(sig: dict, candles: list[dict], live_px: float = 0.0) -> tuple[list[tuple[int, str]], list]:
    """Luxury half-way secure-profit advisory for every receiver + admin
    (+ live-card edits so the original message shows the current state)."""
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
    edits = _card_refresh_edits(sig, live_px, _delivery_refs(sig["id"]))
    return notes, edits


def _payload_close(payload: dict) -> float:
    """Latest confirmed close price from the tracking payload (0.0 if absent)."""
    vals = payload.get("values") or []
    if vals and vals[-1].get("close"):
        try:
            return float(vals[-1]["close"])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def drift_pips(symbol: str, direction: str, entry: float, px: float) -> float:
    """Signed pips from entry — positive means IN the trade's favour.

    instruments.pips() is a bare absolute distance; a card that shows «+60
    pips» on a SELL trading ABOVE its entry lies to the user. Signed drift
    keeps every live line honest (the user compares against his real P/L)."""
    d = instruments.pips(symbol, entry, px)
    favourable = (px <= entry) if (direction or "").upper() == "SELL" else (px >= entry)
    return d if favourable else -d


def _delivery_refs(signal_id: int) -> list[dict]:
    """Deliveries that carry an editable original card (chat_id + message_id).
    Rows delivered before v6.7.0 have empty refs — nothing to edit for them."""
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT user_id, chat_id, message_id FROM signal_deliveries "
            "WHERE signal_id=? AND message_id!=0 AND chat_id!=''", (signal_id,)).fetchall()]


REFRESH_PIPS = 3.0   # re-render the original card after this much price travel


def _should_refresh(sig: dict, live_px: float) -> bool:
    prev = float(sig.get("live_px") or 0)
    if prev <= 0:
        return True                                  # first pulse ever
    return abs(live_px - prev) >= REFRESH_PIPS * max(instruments.spec(sig["symbol"]).pip_size, 1e-9)


def _market_row(symbol: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def _live_card_text(sig: dict, uid: int, px: float) -> str:
    """Rebuild the EXACT luxury card the user first received — current status,
    fresh market pulse, personal block. This text REPLACES the original
    message body via edit_message_text, so the first card is the live board."""
    from fysb.services import user_trade
    from fysb.ui.cards import render_signal_card
    row = _market_row(sig["symbol"])
    text = render_signal_card(sig, user_view=True, row=row)
    if px:
        entry = float(sig["entry"])
        side = "أعلى الدخول" if px > entry else ("عند الدخول" if px == entry else "أدنى الدخول")
        text += (f"\n\n<blockquote>💰 <b>سعر السوق الآن:</b> "
                 f"<code>{instruments.fmt_price(sig['symbol'], px, row)}</code>"
                 f" · {side} · <b>{drift_pips(sig['symbol'], sig['direction'], entry, px):+.1f} pips</b> من الدخول</blockquote>")
    personal = user_trade.personal_block(sig, uid)
    if personal:
        text += "\n\n<blockquote>" + personal + "</blockquote>"
    return text


def _card_refresh_edits(sig: dict, live_px: float, refs: list[dict]) -> list:
    """Edits for every delivered card. A status change deserves an edit even
    without a price (expiry/terminal states) — the price line appears only
    when a real close is available."""
    if not refs:
        return []
    return [(r["user_id"], r["chat_id"], int(r["message_id"]),
             _live_card_text(sig, r["user_id"], live_px)) for r in refs]


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


def _apply_transition(sig: dict, new_status: str, payload: dict,
                      live_px: float = 0.0) -> tuple[list[tuple[int, str]], float, list]:
    """Persist the transition (idempotent); return (notifications, pips_result,
    live-card edits). The caller sends/edits inside the bot's event loop."""
    from fysb.theme import dir_badge, esc, status_ar, tf_name
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
            return [], 0.0, []  # already terminal — never double-update
        closed_at = config.now() if status in TERMINAL else ""
        c.execute("""UPDATE signals SET status=?, hit_tp=?, closed_at=?, pips_result=?, data_time=?, live_px=?
                     WHERE id=? AND status=?""",
                  (status, hit_tp, closed_at, pips_result, payload["last_candle_iso"], live_px, sig["id"], sig["status"]))
    _log(sig["symbol"], sig["timeframe"], f"TRANSITION:{status}", f"signal #{sig['id']} pips={pips_result:.1f}")

    # The card the user holds is re-rendered with the NEW status immediately —
    # «بانتظار التنفيذ» never outlives the candle that ended it.
    sig_now = dict(sig)
    sig_now.update(status=status, hit_tp=hit_tp, pips_result=pips_result,
                   closed_at=closed_at, data_time=payload["last_candle_iso"])
    edits = _card_refresh_edits(sig_now, live_px, _delivery_refs(sig["id"]))

    tp_map = {1: sig["tp1"], 2: sig["tp2"], 3: sig["tp3"]}
    detail = ""
    result_line = f"▎📈 النتيجة حتى الآن: <b>{pips_result:+.1f} pips</b>\n"
    if status == "RUNNING":
        detail = ("▎🚀 <b>عُبر سعر الدخول ببيانات سوق فعلية — الإشارة الآن قيد التنفيذ "
                  "وتُتابع لحظياً حتى الأهداف أو الوقف.</b>\n")
        if live_px:
            side_pips = drift_pips(sig["symbol"], sig["direction"], float(sig["entry"]), live_px)
            side = "أعلى الدخول" if live_px > float(sig["entry"]) else (
                "عند الدخول" if live_px == float(sig["entry"]) else "أدنى الدخول")
            result_line = (f"▎📍 السعر عند التفعيل: <code>{instruments.fmt_price(sig['symbol'], live_px)}</code>"
                           f" · {side} · <b>{side_pips:+.1f} pips</b> من الدخول\n")
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
        f"{result_line}"
        f"{detail}"
        f"</blockquote>\n"
        f"<i>تحديث قائم على بيانات السعر الفعلية من السوق اللحظي.</i>"
    )
    with db.conn() as c:
        uids = [r["user_id"] for r in c.execute("SELECT user_id FROM signal_deliveries WHERE signal_id=?", (sig["id"],))]
    notes = [(uid, text) for uid in uids]
    if config.ADMIN_ID:
        # Admin sees the SAME truth the users see — Arabic status + real numbers,
        # never a raw machine token like «RUNNING (+0.0 pips)».
        if status == "RUNNING" and live_px:
            side_pips = drift_pips(sig["symbol"], sig["direction"], float(sig["entry"]), live_px)
            admin = (f"🚀 إشارة #{sig['id']} {sig['symbol']}: {status_ar(status)} — "
                     f"السعر <code>{instruments.fmt_price(sig['symbol'], live_px)}</code> "
                     f"({side_pips:+.1f} pips من الدخول)")
        elif pips_result:
            admin = f"📡 إشارة #{sig['id']} {sig['symbol']}: {status_ar(status)} ({pips_result:+.1f} pips)"
        else:
            admin = f"📡 إشارة #{sig['id']} {sig['symbol']}: {status_ar(status)}"
        notes.append((config.ADMIN_ID, admin))
    return notes, pips_result, edits


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
                           WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                           AND ((expires_at!='' AND expires_at<=?)
                                OR (expires_at='' AND created_at<=?))""",
                        (config.now(), config.now(), legacy_cutoff))
        return cur.rowcount


def expire_with_card_edits() -> tuple[int, list]:
    """The expiry sweep PLUS live-card edits: a retired signal's own message
    must stop claiming «بانتظار التنفيذ» — the card is re-rendered with the
    EXPIRED badge in place (no price line; expiry makes no price claims)."""
    from datetime import datetime, timedelta, timezone
    hours = max(1, db.get_setting_int("signal_expiry_hours", 72))
    legacy_cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
               AND ((expires_at!='' AND expires_at<=?)
                    OR (expires_at='' AND created_at<=?))""",
            (config.now(), legacy_cutoff)).fetchall()]
    edits: list = []
    for sig in rows:
        sig["status"] = "EXPIRED"
        edits.extend(_card_refresh_edits(sig, 0.0, _delivery_refs(sig["id"])))
    with db.conn() as c:
        cur = c.execute("""UPDATE signals SET status='EXPIRED', closed_at=?
                           WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                           AND ((expires_at!='' AND expires_at<=?)
                                OR (expires_at='' AND created_at<=?))""",
                        (config.now(), config.now(), legacy_cutoff))
    return max(cur.rowcount, 0), edits


def _log(symbol, tf, result, detail):
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(symbol,timeframe,result,detail,created_at) VALUES(?,?,?,?,?)",
                  (symbol, tf, result[:40], detail[:400], config.now()))
