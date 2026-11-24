"""Signal lifecycle tracker — status updates from REAL price data only.

Rules:
  * Statuses: ACTIVE -> RUNNING -> TP1_HIT -> TP2_HIT -> TP3_HIT (or SL_HIT /
    CANCELLED / EXPIRED). RUNNING = candle data confirms price actually traded
    through the entry level — the signal is IN the market, no longer «بانتظار
    التنفيذ». A signal whose entry was never touched stays ACTIVE honestly.
  * REAL-ENTRY DISCIPLINE (v6.6.4): an UNFILLED (ACTIVE) signal earns NO
    lifecycle rewards — no TP banking, no halfway celebration. Price reaching
    TP1 without ever touching the entry KILLS the idea (CANCELLED — «فات
    الدخول»), it never gets re-branded as a winner. Targets are evaluated
    strictly FROM the fill candle onward (filled_at cutoff), so a pre-fill
    visit to TP1 can never be banked after a late retrace fill.
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

# v6.8.0 — CONSERVATIVE target confirmation: a target is banked only when the
# provider candle PENETRATES the level by this margin, not merely grazes it.
# Asymmetric by design, like every other rule here: SL stays EXACT (never
# optimistic on the loss side), TP requires real penetration (never optimistic
# on the win side). This kills single-tick feed-noise wicks from banked profits
# — the live complaint: «TP1 verified (+582.0 pips)» while the chart showed the
# high at 3637 vs TP1 3644 — a feed-noise touch must never read as a goal.
TP_CONFIRM_PIPS = 2.0


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


def _tracking_intervals_for(since_iso: str) -> tuple[tuple[str, int], ...]:
    """Pick the finest candle interval whose window covers `since_iso`.

    5min x 200 covers ~16.6h; 15min x 120 covers ~30h; 1h x 100 covers ~100h.
    Always retains all three intervals as a failover chain.
    """
    if not since_iso:
        return (("5min", 200), ("15min", 120), ("1h", 100))
    try:
        from datetime import datetime, timezone
        cut = _utc_cutoff(since_iso)
        dt = datetime.strptime(cut, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
        if age_hours > 28.0:
            return (("1h", 100), ("15min", 120), ("5min", 200))
        if age_hours > 14.0:
            return (("15min", 120), ("1h", 100), ("5min", 200))
    except Exception:
        pass
    return (("5min", 200), ("15min", 120), ("1h", 100))


def _tracking_payload(provider_symbol: str, api_key: str, since_iso: str = "") -> dict:
    """Fetch a candle window LONG ENOUGH to cover the signal's whole lifetime.

    A young signal needs fine granularity (5min); an older one needs deeper
    history (15min -> 1h) so fills/TPs beyond 16h are never missed. Raises
    RuntimeError when everything fails.
    """
    last_err: Exception | None = None
    for interval, size in _tracking_intervals_for(since_iso):
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


def fill_candle(sig: dict, candles: list[dict]) -> dict | None:
    """Candle-confirmed entry fill: the FIRST candle that actually traded
    through the entry level (a SELL fills when price trades at or ABOVE the
    entry; a BUY fills when price trades at or BELOW it). Returns that candle
    — its datetime becomes `filled_at`, the anchor for all TP/SL evaluation."""
    entry = float(sig["entry"])
    if sig["direction"] == "BUY":
        for c in candles:
            if float(c["low"]) <= entry:
                return c
        return None
    for c in candles:
        if float(c["high"]) >= entry:
            return c
    return None


def entry_filled(sig: dict, candles: list[dict]) -> bool:
    """Boolean form of fill_candle (kept for external callers)."""
    return fill_candle(sig, candles) is not None


def missed_target(sig: dict, candles: list[dict]) -> bool:
    """The entry was NEVER touched but price already reached TP1 — the idea is
    dead (the market ran away without giving the plan its fill). No result is
    credited: an unentered trade cannot win.
    Uses the same conservative TP confirmation buffer as _evaluate_signal."""
    tp1 = float(sig["tp1"])
    buf = TP_CONFIRM_PIPS * max(instruments.spec(sig["symbol"]).pip_size, 1e-9)
    if sig["direction"] == "BUY":
        return any(float(c["high"]) >= tp1 + buf for c in candles)
    return any(float(c["low"]) <= tp1 - buf for c in candles)


def _legacy_active(sig: dict) -> bool:
    """Signals born BEFORE the Real-Entry protocol carry no birth quote and no
    filled_at — an at-market birth under the old pipeline is invisible to the
    candle-touch check (price left the entry forever and never came back: the
    exact «بانتظار التنفيذ للأبد» bug). Rows created after the protocol epoch
    have an exact birth status and are NEVER touched by the heuristic."""
    if sig.get("filled_at"):
        return False
    epoch = db.get_setting("real_entry_protocol_since", "")
    if not epoch:
        return True          # epoch not seeded yet — treat as legacy
    return (sig.get("created_at") or "") < epoch


def _legacy_birth_fill(sig: dict, candles: list[dict]) -> dict | None:
    """Salvage proof for legacy at-market births: the first tracked candle's
    OPEN is the closest recorded witness of the birth price. If it sits ON the
    fill side or within a small at-market band (10% of the risk leg, min 5
    pips), the trade filled at birth. Pending-retrace rows (open far on the
    waiting side) are never touched."""
    if not candles:
        return None
    first = candles[0]
    entry = float(sig["entry"])
    risk = abs(entry - float(sig["stop"]))
    pip = max(instruments.spec(sig["symbol"]).pip_size, 1e-9)
    band = max(0.10 * risk, 5 * pip)
    try:
        o = float(first.get("open") or first.get("close") or 0.0)
    except (TypeError, ValueError):
        return None
    if sig["direction"] == "BUY":
        return first if o <= entry + band else None
    return first if o >= entry - band else None


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
            "SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') "
            "AND (closed_at IS NULL OR closed_at='') ORDER BY id").fetchall()]
    catalog_map = _load_market_catalog_map() if rows else {}
    for sig in rows:
        stats["checked"] += 1
        symbol = sig["symbol"]
        mkt_row = catalog_map.get(symbol)
        prov_sym = (mkt_row["provider_symbol"] if mkt_row and mkt_row.get("provider_symbol") else symbol)
        try:
            since = sig.get("filled_at") or sig.get("created_at") or ""
            payload = _tracking_payload(prov_sym, api_key, since_iso=since)
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

        # ---- UNFILLED signals: fill watch or honest death — nothing else ----
        if sig["status"] == "ACTIVE":
            fill_c = fill_candle(sig, candles)
            if fill_c is None and _legacy_active(sig):
                fill_c = _legacy_birth_fill(sig, candles)
            if fill_c:
                stats["updated"] += 1
                sig["filled_at"] = fill_c["datetime"]
                # Evaluate targets from the fill candle onward FIRST: if the
                # tracked window already contains a stop-out or a banked
                # target, that truth is applied directly (one honest
                # transition) instead of announcing RUNNING for a trade that
                # already ended.
                transition = _evaluate_signal(sig, candles, cutoff=fill_c["datetime"])
                if transition:
                    notes, _, new_edits = _apply_transition(
                        sig, transition, payload, live_px, filled_at=fill_c["datetime"])
                    notifications.extend(notes)
                    edits.extend(new_edits)
                    sig["status"] = transition
                    continue
                notes, _, new_edits = _apply_transition(
                    sig, "RUNNING", payload, live_px, filled_at=fill_c["datetime"])
                notifications.extend(notes)
                edits.extend(new_edits)
                sig["status"] = "RUNNING"
                # Same-pass half-TP1 check (post-fill candles only) — mirrors
                # the standing pass below; the activation edit already
                # refreshed the card, so only notes are extended.
                if not sig.get("half_notified"):
                    try:
                        post = [c for c in candles
                                if c["datetime"] >= _utc_cutoff(sig["filled_at"])]
                        if post and halfway_reached(sig, post):
                            with db.conn() as c:
                                c.execute("UPDATE signals SET half_notified=1 WHERE id=? AND half_notified=0",
                                          (sig["id"],))
                            notes, _ = _halfway_notes(sig, post, live_px)
                            notifications.extend(notes)
                    except Exception:
                        pass
                continue
            if missed_target(sig, candles):
                stats["updated"] += 1
                notes, _, new_edits = _apply_transition(sig, "CANCELLED", payload, live_px)
                notifications.extend(notes)
                edits.extend(new_edits)
                _log(symbol, sig["timeframe"], "MISSED_ENTRY",
                     f"signal #{sig['id']} reached TP1 without ever touching the entry")
                continue
            # Still a live pending plan: keep the card pulsing with the real
            # price, but NEVER bank targets or celebrate halfway for a trade
            # the market has not given us yet.
            if live_px and _should_refresh(sig, live_px):
                refs = _delivery_refs(sig["id"])
                if refs:
                    edits.extend(_card_refresh_edits(sig, live_px, refs))
                    with db.conn() as c:
                        c.execute("UPDATE signals SET live_px=?, data_time=? WHERE id=?",
                                  (live_px, payload["last_candle_iso"], sig["id"]))
                    stats["refreshed"] += 1
            continue

        # ---- FILLED signals: full lifecycle from the fill candle onward ----
        transition = _evaluate_signal(sig, candles, live_px=live_px)
        if transition:
            stats["updated"] += 1
            notes, result_pips, new_edits = _apply_transition(sig, transition, payload, live_px)
            notifications.extend(notes)
            edits.extend(new_edits)
            continue
        # Half-TP1 secure-profit advisory (one-time per signal, data-verified,
        # strictly while RUNNING — never fires retroactively after TP1/TP2 is already banked)
        if not sig.get("half_notified") and sig["status"] == "RUNNING":
            try:
                fill_cut = _utc_cutoff(sig.get("filled_at") or sig["created_at"])
                post = [c for c in candles if c["datetime"] >= fill_cut]
                if post and halfway_reached(sig, post):
                    with db.conn() as c:
                        c.execute("UPDATE signals SET half_notified=1 WHERE id=? AND half_notified=0", (sig["id"],))
                    notes, new_edits = _halfway_notes(sig, post, live_px)
                    notifications.extend(notes)
                    edits.extend(new_edits)
            except Exception:
                pass
        # Live-card pulse: re-render the ORIGINAL delivered card while the price
        # moved meaningfully — the user's first message is the live board.
        if live_px and _should_refresh(sig, live_px):
            refs = _delivery_refs(sig["id"])
            if refs:
                edits.extend(_card_refresh_edits(sig, live_px, refs))
                with db.conn() as c:
                    c.execute("UPDATE signals SET live_px=?, data_time=? WHERE id=?",
                              (live_px, payload["last_candle_iso"], sig["id"]))
                stats["refreshed"] += 1
    return {"stats": stats, "notifications": notifications,
            "admin_notice": admin_notice, "edits": edits}


def _documented_extreme(sig: dict, payload: dict) -> tuple[float, float]:
    """(extreme_price, signed_pips) — the furthest documented price travel in
    the trade's favour within the tracked window AFTER the fill anchor.
    This is the number the notification can PROVE from provider candles; the
    card shows it next to any achieved target so every claim carries its own
    documented witness (v6.8.0 honesty)."""
    try:
        entry = float(sig["entry"])
        cut = _utc_cutoff(sig.get("filled_at") or sig.get("created_at") or "")
        post = [c for c in (payload.get("values") or []) if c.get("datetime", "") >= cut]
        if not post:
            return 0.0, 0.0
        if sig["direction"] == "BUY":
            ext = max(float(c["high"]) for c in post)
        else:
            ext = min(float(c["low"]) for c in post)
        return ext, drift_pips(sig["symbol"], sig["direction"], entry, ext)
    except Exception:
        return 0.0, 0.0


def _alert_targets(sig: dict, text: str) -> list[tuple]:
    """Build deduplicated push-alert targets for a signal.

    * Respects private SIGNAL_NOW isolation (does NOT spam ADMIN_ID on a
      subscriber's private on-demand query).
    * Deduplicates targets so ADMIN_ID never receives 2 copies if already
      present in signal_deliveries.
    * Includes broadcast channels (user_id <= 0 routed via chat_id).
    """
    from fysb.engines.signal_engine import admin_accepts_market
    with db.conn() as c:
        refs = [dict(r) for r in c.execute(
            "SELECT user_id, chat_id FROM signal_deliveries WHERE signal_id=?", (sig["id"],))]
    notes: list[tuple] = []
    seen: set[str] = set()
    for r in refs:
        duid = int(r["user_id"] or 0)
        target = r["chat_id"] if duid <= 0 else duid
        if target in (None, "", 0):
            continue
        key = str(target)
        if key not in seen:
            seen.add(key)
            notes.append((target, text))
    sig_src = (sig.get("source") or "AUTO").upper()
    if (sig_src != "SIGNAL_NOW" and config.ADMIN_ID
            and str(config.ADMIN_ID) not in seen
            and admin_accepts_market(sig["symbol"])):
        notes.append((config.ADMIN_ID, text))
    return notes


def _halfway_notes(sig: dict, candles: list[dict], live_px: float = 0.0) -> tuple[list[tuple[int, str]], list]:
    """LUXURY half-way secure-profit advisory (v6.8.0) — for every receiver AND
    the admin (the old one-liner «💧 إشارة #N … نصف الطريق» is gone; admin sees
    the same royal card with REAL numbers: documented travel, progress toward
    TP1, USD at 0.01 lot for any pair, and the money-management protocol).
    (+ live-card edits so the original message shows the current state)."""
    from fysb import instruments
    from fysb.theme import bar, dir_badge, esc, tf_name
    symbol = sig["symbol"]
    entry = float(sig["entry"])
    tp1 = float(sig["tp1"])
    dist = abs(tp1 - entry)
    px = float(live_px or 0.0)
    drift = drift_pips(symbol, sig["direction"], entry, px) if px else 0.0
    progress = min(100.0, max(0.0, (drift / instruments.pips(symbol, entry, tp1) * 100.0)
                              if instruments.pips(symbol, entry, tp1) > 0 else 0.0))
    remaining = instruments.pips(symbol, entry, tp1) - max(0.0, drift)
    usd_now = instruments.usd_move(symbol, drift, 0.01, px=px) if px else 0.0
    usd_tp1 = instruments.usd_move(symbol, instruments.pips(symbol, entry, tp1), 0.01, px=tp1)
    tp2_val = float(sig["tp2"] or tp1)
    tp3_val = float(sig["tp3"] or tp1)
    usd_tp2 = instruments.usd_move(symbol, instruments.pips(symbol, entry, tp2_val), 0.01, px=tp2_val)
    usd_tp3 = instruments.usd_move(symbol, instruments.pips(symbol, entry, tp3_val), 0.01, px=tp3_val)
    text = (
        f"💧 <b>إدارة الصفقة الملكية</b> — {esc(symbol)} {dir_badge(sig['direction'])} ⏱ {tf_name(sig['timeframe'])}\n"
        f"<blockquote>"
        f"▎✨ وصل السعر <b>نصف الطريق</b> نحو الهدف الأول.\n"
        f"▎✅ <b>الصفقة تعتبر رابحة حتى الآن</b> — حققت <b>{drift:+.1f} نقطة</b> من الدخول"
        + (f" و<b>{usd_now:+,.2f} $</b> بـ 0.01 لوت.\n" if px else "\n")
        + f"▎📍 السعر الآن: <code>{instruments.fmt_price(symbol, px)}</code>"
        f" · <b>{drift:+.1f} pips</b> من الدخول"
        + (f" · تقدم {progress:.0f}% نحو TP1 {bar(progress, 100)}\n" if px else "\n")
        + (f"▎🏁 المتبقي نحو TP1: <b>{max(0.0, remaining):.1f} pips</b> (الهدف <code>{instruments.fmt_price(symbol, tp1)}</code>)\n"
           if px else f"▎🎯 الهدف الأول: <code>{instruments.fmt_price(symbol, tp1)}</code>\n")
        + f"▎💵 <b>الربح الحقيقي الآن بـ 0.01 لوت:</b> <b>{usd_now:+,.2f} $</b>"
          f" · وعند TP1: <b>{usd_tp1:+,.2f} $</b>\n"
        + f"▎📈 مسار الربح الكامل بـ 0.01 لوت: TP1 <b>{usd_tp1:+,.2f} $</b> · TP2 <b>{usd_tp2:+,.2f} $</b> · TP3 <b>{usd_tp3:+,.2f} $</b>\n"
        + f"▎🛡 <b>بروتوكول التأمين (نصيحة مالية):</b> انقل وقف الخسارة الآن إلى نقطة الدخول "
          f"<code>{instruments.fmt_price(symbol, entry)}</code> وأغلق <b>50%</b> من الحجم — "
          f"تصبح الصفقة المتبقية خالية من المخاطرة تماماً.\n"
        + f"▎⏱ الحالة: <b>نشطة — متابعة لحظية حتى الأهداف أو الوقف</b>\n"
        + f"</blockquote>\n"
        + f"<i>أرقام موثقة من أسعار السوق الفعلية — إدارة صفقتك بيدك، والتنبيه لا ينفذ في حسابك تلقائياً إلا إذا فعّلت التنفيذ الآلي في حسابك التجريبي.</i>"
    )
    notes = _alert_targets(sig, text)
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


def seq_of(signal_id: int) -> int:
    """REAL sequential number — the SAME number the signals center displays.

    The center numbers signals chronologically via ROW_NUMBER (deleting any
    signal makes the following ones take the freed number). The rank of id X
    among all signals ordered by id equals COUNT(id <= X), so every alert can
    show the exact number the admin sees — never a stale internal id like the
    live «إشارة #12» complaint after deletions (v6.8.1 numbering integrity)."""
    try:
        with db.conn() as c:
            r = c.execute("SELECT COUNT(*) n FROM signals WHERE id<=?", (signal_id,)).fetchone()
        return int(r["n"]) if r else signal_id
    except Exception:
        return signal_id


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


def _load_market_catalog_map() -> dict[str, dict]:
    """Batch-load market_catalog once per tracker pass (eliminates N+1 queries)."""
    with db.conn() as c:
        return {r["symbol"]: dict(r) for r in c.execute("SELECT * FROM market_catalog").fetchall()}


def _load_demo_accounts_map(uids: list[int]) -> dict[int, dict]:
    """Batch-load demo_accounts for a signal's recipients in 1 SQL query."""
    uniq = sorted({int(u) for u in uids if int(u or 0) > 0})
    if not uniq:
        return {}
    out: dict[int, dict] = {}
    with db.conn() as c:
        for i in range(0, len(uniq), 400):
            chunk = uniq[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in c.execute(f"SELECT * FROM demo_accounts WHERE user_id IN ({ph})", tuple(chunk)).fetchall():
                out[int(r["user_id"])] = dict(r)
    return out


def _base_live_card_text(sig: dict, px: float, row=None) -> str:
    """Build the invariant portion of the live signal card ONCE per signal.

    v6.20.3 honesty: once a signal is CLOSED (including TP1_HIT / TP2_HIT closed
    at breakeven with closed_at stamped), NEVER append a live drifting market
    price below entry — instead display the final secured-profit close summary.
    """
    from fysb.theme import is_signal_closed
    from fysb.ui.cards import render_signal_card
    if row is None:
        row = _market_row(sig["symbol"])
    text = render_signal_card(sig, user_view=True, row=row)
    closed = is_signal_closed(sig)
    if closed:
        st = (sig.get("status") or "").upper()
        if st in ("TP1_HIT", "TP2_HIT"):
            banked = float(sig.get("pips_result") or 0.0)
            text += (
                f"\n\n<blockquote>🔒 <b>الصفقة مغلقة نهائياً على ربح محقق ({banked:+.1f} pips)</b> — "
                f"أُغلق الجزء المتبقي عند نقطة التأمين (الدخول) وانتهت متابعة هذه الإشارة؛ "
                f"المحرك يبحث الآن عن الفرص الجديدة.</blockquote>"
            )
        return text
    if px:
        entry = float(sig["entry"])
        side = "أعلى الدخول" if px > entry else ("عند الدخول" if px == entry else "أدنى الدخول")
        text += (f"\n\n<blockquote>💰 <b>سعر السوق الآن:</b> "
                 f"<code>{instruments.fmt_price(sig['symbol'], px, row)}</code>"
                 f" · {side} · <b>{drift_pips(sig['symbol'], sig['direction'], entry, px):+.1f} pips</b> من الدخول</blockquote>")
    return text


def _live_card_text(sig: dict, uid: int, px: float, row=None, acc=None, base_text: str | None = None) -> str:
    """Rebuild the EXACT luxury card the user first received — current status,
    fresh market pulse, personal block. This text REPLACES the original
    message body via edit_message_text, so the first card is the live board."""
    from fysb.services import user_trade
    if row is None:
        row = _market_row(sig["symbol"])
    text = base_text if base_text is not None else _base_live_card_text(sig, px, row=row)
    if int(uid or 0) > 0:
        personal = user_trade.personal_block(sig, uid, row=row, acc=acc)
        if personal:
            text += "\n\n<blockquote>" + personal + "</blockquote>"
    else:
        text += f"\n\n📡 <b>{config.BOT_SIGNATURE}</b>"
    return text


def _card_refresh_edits(sig: dict, live_px: float, refs: list[dict], market_row=None) -> list:
    """Edits for every delivered card — builds the base card ONCE and
    batch-loads all recipients' demo accounts in 1 SQL query (O(1) instead of 3N)."""
    if not refs:
        return []
    row = market_row if market_row is not None else _market_row(sig["symbol"])
    base_text = _base_live_card_text(sig, live_px, row=row)
    acc_map = _load_demo_accounts_map([r["user_id"] for r in refs])
    return [(r["user_id"], r["chat_id"], int(r["message_id"]),
             _live_card_text(sig, r["user_id"], live_px, row=row,
                             acc=acc_map.get(int(r["user_id"] or 0)), base_text=base_text))
            for r in refs]


def _evaluate_signal(sig: dict, candles: list[dict], cutoff: str = "", live_px: float = 0.0) -> str | None:
    """Return the next status if price data justifies it, else None.

    Only candles AT/AFTER the fill anchor are walked — `cutoff` (or the
    signal's filled_at, or its creation time as last resort). A retrace fill
    that arrives AFTER price already visited TP1 can never bank that stale
    touch: the visit happened before the trade existed.

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
    entry = float(sig["entry"])
    sl, tp1, tp2, tp3 = sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"]
    hit_tp = int(sig["hit_tp"] or 0)

    cut = _utc_cutoff(cutoff or sig.get("filled_at") or sig.get("created_at") or "")
    walked = [c for c in candles if c["datetime"] >= cut]
    if not walked:
        return None
    pip = max(instruments.spec(sig["symbol"]).pip_size, 1e-9)
    buf = TP_CONFIRM_PIPS * pip
    progress_tp = hit_tp
    # Track the bar index where the already-banked target (hit_tp) was reached
    # so the fill bar itself (at entry) never false-triggers a breakeven stop.
    banked_bar_idx = -1
    be_stopped = False
    for idx, c in enumerate(walked):
        hi, lo = c["high"], c["low"]
        if direction == "BUY":
            sl_now = lo <= sl
            be_now = lo <= entry
            if hit_tp == 1 and banked_bar_idx < 0 and hi >= tp1 + buf:
                banked_bar_idx = idx
            elif hit_tp == 2 and banked_bar_idx < 0 and hi >= tp2 + buf:
                banked_bar_idx = idx
            new_tp = 0
            if progress_tp == 0 and hi >= tp1 + buf:
                new_tp = 1
            if progress_tp <= 1 and hi >= tp2 + buf:
                new_tp = max(new_tp, 2)
            if progress_tp <= 2 and hi >= tp3 + buf:
                new_tp = max(new_tp, 3)
        else:
            sl_now = hi >= sl
            be_now = hi >= entry
            if hit_tp == 1 and banked_bar_idx < 0 and lo <= tp1 - buf:
                banked_bar_idx = idx
            elif hit_tp == 2 and banked_bar_idx < 0 and lo <= tp2 - buf:
                banked_bar_idx = idx
            new_tp = 0
            if progress_tp == 0 and lo <= tp1 - buf:
                new_tp = 1
            if progress_tp <= 1 and lo <= tp2 - buf:
                new_tp = max(new_tp, 2)
            if progress_tp <= 2 and lo <= tp3 - buf:
                new_tp = max(new_tp, 3)
        if sl_now and progress_tp == 0:
            # Conservative SL-first rule: if a candle hits SL before any target
            # was banked (including gap candles spanning both SL and TP), record
            # SL_HIT — never optimistically credit a target on a gap candle.
            return "SL_HIT"
        if new_tp > progress_tp:
            progress_tp = new_tp                  # target banked; keep running
            if progress_tp == 3:
                break
        # v6.20.0 Breakeven stop-out for already-banked TP1_HIT / TP2_HIT runners:
        # only triggers strictly AFTER the banked target candle (or on post-fill
        # bars if the TP candle predates the current candle window).
        if hit_tp in (1, 2) and progress_tp == hit_tp and be_now:
            if (banked_bar_idx >= 0 and idx > banked_bar_idx) or (
                banked_bar_idx < 0 and c["datetime"] > cut
            ):
                be_stopped = True
                break
    if progress_tp > hit_tp:
        if progress_tp == 3:
            return "TP3_HIT"
        return {1: "TP1_HIT", 2: "TP2_HIT", 3: "TP3_HIT"}[progress_tp]
    # Fallback live-quote breakeven check for already-banked TP1/TP2 runners:
    # if the current live price is already back at/past entry on the adverse side,
    # close immediately at breakeven so the trade NEVER hangs open.
    if not be_stopped and hit_tp in (1, 2) and live_px > 0:
        if (direction == "BUY" and live_px <= entry) or (direction == "SELL" and live_px >= entry):
            be_stopped = True
    if be_stopped and hit_tp in (1, 2):
        return f"TP{hit_tp}_BE"
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
                      live_px: float = 0.0, filled_at: str = "") -> tuple[list[tuple[int, str]], float, list]:
    """Persist the transition (idempotent); return (notifications, pips_result,
    live-card edits). The caller sends/edits inside the bot's event loop.
    RUNNING transitions anchor `filled_at` (the fill candle's datetime) so all
    later target evaluation starts from the true fill moment."""
    from fysb.theme import dir_badge, esc, status_ar, strategy_name, tf_name
    is_be_close = new_status in ("TP1_BE", "TP2_BE")
    status = {"TP1_BE": "TP1_HIT", "TP2_BE": "TP2_HIT"}.get(new_status, new_status)
    hit_tp = {"TP1_HIT": 1, "TP2_HIT": 2, "TP3_HIT": 3,
              "SL_HIT": int(sig["hit_tp"] or 0)}.get(status, int(sig["hit_tp"] or 0))
    pips_result = 0.0
    from fysb import instruments
    if status == "SL_HIT":
        pips_result = -instruments.pips(sig["symbol"], sig["entry"], sig["stop"])
    elif status.startswith("TP"):
        tp_map = {1: sig["tp1"], 2: sig["tp2"], 3: sig["tp3"]}
        pips_result = instruments.pips(sig["symbol"], sig["entry"], tp_map[hit_tp])
    if status == "RUNNING":
        filled_at = filled_at or sig.get("filled_at") or config.now()
    closed_at = config.now() if (status in TERMINAL or is_be_close) else ""
    half_done = 1 if (status.startswith("TP") or status in TERMINAL or is_be_close) else int(sig.get("half_notified") or 0)
    with db.tx(immediate=True) as c:
        r = c.execute("SELECT status, closed_at FROM signals WHERE id=?", (sig["id"],)).fetchone()
        if not r or r["status"] in TERMINAL or (r["closed_at"] or ""):
            return [], 0.0, []  # already closed/terminal — never double-update
        c.execute("""UPDATE signals SET status=?, hit_tp=?, closed_at=?, pips_result=?,
                     data_time=?, live_px=?, filled_at=?, half_notified=?
                     WHERE id=? AND status=? AND (closed_at IS NULL OR closed_at='')""",
                  (status, hit_tp, closed_at, pips_result, payload["last_candle_iso"],
                   live_px, filled_at or sig.get("filled_at") or "", half_done, sig["id"], sig["status"]))
    _log(sig["symbol"], sig["timeframe"], f"TRANSITION:{new_status}",
         f"signal #{sig['id']} pips={pips_result:.1f} filled_at={filled_at or sig.get('filled_at') or '-'}")
    if status in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"):
        try:
            from fysb.services import strategy_stats
            strategy_stats.invalidate_leaderboard_cache()
        except Exception:
            pass

    # The card the user holds is re-rendered with the NEW status immediately —
    # «بانتظار التنفيذ» never outlives the candle that ended it.
    sig_now = dict(sig)
    sig_now.update(status=status, hit_tp=hit_tp, pips_result=pips_result,
                   closed_at=closed_at, data_time=payload["last_candle_iso"],
                   filled_at=filled_at or sig.get("filled_at") or "",
                   half_notified=half_done)
    edits = _card_refresh_edits(sig_now, live_px, _delivery_refs(sig["id"]))

    tp_map = {1: sig["tp1"], 2: sig["tp2"], 3: sig["tp3"]}
    detail = ""
    result_line = f"▎📈 النتيجة حتى الآن: <b>{pips_result:+.1f} pips</b>"
    ext_px, ext_pips = _documented_extreme(sig, payload)
    if ext_px:
        result_line += (f" · أعلى تلامس موثق: <code>{instruments.fmt_price(sig['symbol'], ext_px)}</code> "
                        f"({ext_pips:+.1f} pips)")
    result_line += "\n"
    if status == "RUNNING":
        detail = ("▎🚀 <b>عُبر سعر الدخول ببيانات سوق فعلية — الإشارة الآن قيد التنفيذ "
                  "وتُتابع لحظياً حتى الأهداف أو الوقف.</b>\n")
        if live_px:
            side_pips = drift_pips(sig["symbol"], sig["direction"], float(sig["entry"]), live_px)
            side = "أعلى الدخول" if live_px > float(sig["entry"]) else (
                "عند الدخول" if live_px == float(sig["entry"]) else "أدنى الدخول")
            result_line = (f"▎📍 السعر عند التفعيل: <code>{instruments.fmt_price(sig['symbol'], live_px)}</code>"
                           f" · {side} · <b>{side_pips:+.1f} pips</b> من الدخول"
                           + (f" · 💵 <b>{instruments.usd_move(sig['symbol'], side_pips, 0.01, px=live_px):+,.2f} $</b> بـ 0.01 لوت" if side_pips else "")
                           + "\n")
    elif status == "CANCELLED":
        detail = ("▎⛔ <b>فات الدخول:</b> لم يصل السعر إلى منطقة الدخول إطلاقاً وبلغ الهدف الأول دونها — "
                  "فكرة فات وقتها تُغلق بشرف: لا تُحتسب نتيجة ولا يُفتح ما لم يُنفّذ.\n")
        result_line = ""
    if is_be_close:
        tp_px = float(tp_map[hit_tp])
        detail = (
            f"▎🏁 الهدف المحقق سابقاً: <code>{instruments.fmt_price(sig['symbol'], tp_px)}</code>"
            f" · 💵 <b>{instruments.usd_move(sig['symbol'], pips_result, 0.01, px=tp_px):+,.2f} $</b> لكل 0.01 لوت\n"
            f"▎🛡 <b>إغلاق مؤمّن عند نقطة الدخول (Breakeven):</b> عاد السعر إلى منطقة الدخول "
            f"<code>{instruments.fmt_price(sig['symbol'], float(sig['entry']))}</code> بعد تحقق الهدف — "
            f"تُغلق متابعة الجزء المتبقي على ربح مضمون دون أي خسارة.\n"
        )
    elif status.startswith("TP"):
        tp_px = float(tp_map[hit_tp])
        detail = (f"▎🏁 الهدف المتحقق: <code>{instruments.fmt_price(sig['symbol'], tp_px)}</code>"
                  f" · 💵 <b>{instruments.usd_move(sig['symbol'], pips_result, 0.01, px=tp_px):+,.2f} $</b> لكل 0.01 لوت\n")
        if hit_tp == 1:
            detail += ("▎🛡 <b>إدارة الصفقة:</b> انقل وقف الخسارة إلى نقطة الدخول (حماية كاملة) "
                       "واُترك الجزء المتبقي يعمل نحو الهدفين التاليين.\n")
        if status == "TP3_HIT":
            detail += "▎👑 <b>صفقة مكتملة بكل الأهداف — أداء ممتاز.</b>\n"
    if status == "SL_HIT":
        usd_sl = instruments.usd_move(sig["symbol"], pips_result, 0.01, px=float(sig["stop"]))
        detail = (f"▎🛡 الخسارة محدودة بمقدار وقف الخسارة المخطط"
                  + (f" · 💵 <b>{usd_sl:+,.2f} $</b> لكل 0.01 لوت" if usd_sl else "")
                  + " — التزم بإدارة المخاطر في الفكرة القادمة.\n")
    # v6.8.1 — numbering integrity: every alert shows the SAME sequential
    # number the signals center displays (after deletions the following
    # signals take the freed numbers), never the raw internal id.
    seq = seq_of(sig["id"])
    status_label = f"{status_ar(status)} · إغلاق مؤمّن عند الدخول 🛡" if is_be_close else status_ar(status)
    text = (
        f"📡 <b><u>تحديث الإشارة الملكية #{seq}</u></b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])} ⏱ {tf_name(sig['timeframe'])}\n"
        f"<blockquote>"
        f"▎📌 الحالة الجديدة: <b>{status_label}</b>\n"
        f"{result_line}"
        f"{detail}"
        f"▎🧠 المنهجية: <b>{strategy_name(sig['strategy'])}</b> · ⭐ الجودة: <code>{int(sig['score'] or 0)}/100</code>\n"
        f"</blockquote>\n"
        f"<i>تحديث قائم على بيانات السعر الفعلية من السوق اللحظي — تتابع لحظي حتى الإغلاق.</i>"
    )
    notes = _alert_targets(sig, text)
    return notes, pips_result, edits


def expire_stale_signals() -> int:
    """Mark unbanked signals past their expiry window as EXPIRED, and close
    expired TP1_HIT/TP2_HIT trades while preserving their banked winning status.

    Two sweeps, both honest (no fabricated outcomes):
      * expires_at set and already passed;
      * expires_at EMPTY but the signal is older than the configured expiry
        window — legacy rows must never haunt the board as ACTIVE forever.
    """
    from datetime import datetime, timedelta, timezone
    hours = max(1, db.get_setting_int("signal_expiry_hours", 72))
    legacy_cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    now_iso = config.now()
    with db.conn() as c:
        # Preserve banked TP1/TP2 winners: stamp closed_at so tracking stops,
        # without erasing their winning status to EXPIRED.
        c.execute("""UPDATE signals SET closed_at=?
                     WHERE status IN ('TP1_HIT','TP2_HIT')
                     AND (closed_at IS NULL OR closed_at='')
                     AND ((expires_at!='' AND expires_at<=?)
                          OR (expires_at='' AND created_at<=?))""",
                  (now_iso, now_iso, legacy_cutoff))
        cur = c.execute("""UPDATE signals SET status='EXPIRED', closed_at=?
                           WHERE status IN ('ACTIVE','RUNNING')
                           AND ((expires_at!='' AND expires_at<=?)
                                OR (expires_at='' AND created_at<=?))""",
                        (now_iso, now_iso, legacy_cutoff))
        return cur.rowcount


def expire_with_alerts() -> tuple[int, list, list]:
    """The expiry sweep PLUS live-card edits AND push notifications (v6.20.0).

    A retired signal's original message stops claiming «بانتظار التنفيذ» AND
    every holder (users + channels + admin for non-private signals) receives
    an explicit EXPIRED push notification so no trade vanishes silently."""
    from datetime import datetime, timedelta, timezone
    from fysb.theme import dir_badge, esc, status_ar, strategy_name, tf_name
    hours = max(1, db.get_setting_int("signal_expiry_hours", 72))
    legacy_cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    now_iso = config.now()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING')
               AND ((expires_at!='' AND expires_at<=?)
                    OR (expires_at='' AND created_at<=?))""",
            (now_iso, legacy_cutoff)).fetchall()]
    edits: list = []
    notifications: list[tuple] = []
    for sig in rows:
        prev_status = sig["status"]
        sig["status"] = "EXPIRED"
        edits.extend(_card_refresh_edits(sig, 0.0, _delivery_refs(sig["id"])))
        seq = seq_of(sig["id"])
        why = ("لم يلمس السعر منطقة الدخول خلال نافذة الصلاحية."
               if prev_status == "ACTIVE"
               else "انتهت نافذة المتابعة الزمنية للإشارة دون بلوغ الأهداف أو الوقف.")
        text = (
            f"⌛ <b><u>انتهاء صلاحية الإشارة #{seq}</u></b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])} ⏱ {tf_name(sig['timeframe'])}\n"
            f"<blockquote>"
            f"▎📌 الحالة النهائية: <b>{status_ar('EXPIRED')}</b>\n"
            f"▎📋 السبب: {why}\n"
            f"▎🧠 المنهجية: <b>{strategy_name(sig['strategy'])}</b>\n"
            f"▎🛡 <b>الإجراء:</b> أُغلقت المتابعة لهذه الفكرة؛ لا تدخل عليها بعد انتهاء الصلاحية."
            f"</blockquote>"
        )
        notifications.extend(_alert_targets(sig, text))
    with db.conn() as c:
        c.execute("""UPDATE signals SET closed_at=?
                     WHERE status IN ('TP1_HIT','TP2_HIT')
                     AND (closed_at IS NULL OR closed_at='')
                     AND ((expires_at!='' AND expires_at<=?)
                          OR (expires_at='' AND created_at<=?))""",
                  (now_iso, now_iso, legacy_cutoff))
        cur = c.execute("""UPDATE signals SET status='EXPIRED', closed_at=?
                           WHERE status IN ('ACTIVE','RUNNING')
                           AND ((expires_at!='' AND expires_at<=?)
                                OR (expires_at='' AND created_at<=?))""",
                        (now_iso, now_iso, legacy_cutoff))
    return max(cur.rowcount, 0), edits, notifications


def expire_with_card_edits() -> tuple[int, list]:
    """Backward-compatible 2-tuple wrapper around expire_with_alerts()."""
    count, edits, _notes = expire_with_alerts()
    return count, edits


def _log(symbol, tf, result, detail):
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(symbol,timeframe,result,detail,created_at) VALUES(?,?,?,?,?)",
                  (symbol, tf, result[:40], detail[:400], config.now()))
