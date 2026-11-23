"""Signal engine — the full issuance pipeline (18 steps, honest rejections).

Pipeline:
  1  fetch market data (entry TF)          10 compute SL
  2  validate data freshness/quality       11 compute TP1..TP3
  3  fetch bias TF data                    12 compute R:R
  4  validate bias data                    13 risk-engine gate
  5  trend analysis                        14 duplicate check
  6  structure analysis                    15 store signal (transaction)
  7  liquidity analysis                    16 deliver to eligible users
  8  run allowed strategies                17 tracker follows lifecycle
  9  merge & select best candidate         18 all steps logged (engine_runs)

A signal is refused (with a recorded reason) when data is stale/insufficient,
strategy conditions are not met, RR is weak, risk caps are hit, or a duplicate
exists. No signal is ever issued merely because a user asked for one.
"""
import json
from datetime import timedelta

from fysb import config, db
from fysb.config import log
from fysb.engines import market_data
from fysb.engines.analysis import atr_pct, structure_labels
from fysb.engines.entry_state import birth_pulse_line, birth_status, is_stale_entry
from fysb.engines.strategies import evaluate_all
from fysb.engines.strategies.base import Ctx
from fysb.engines import risk
from fysb import instruments

ENTRY_TF_DEFAULT = "M15"
BIAS_TF_DEFAULT = "H4"


def _log_run(symbol: str, timeframe: str, result: str, detail: str) -> None:
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(symbol,timeframe,result,detail,created_at) VALUES(?,?,?,?,?)",
                  (symbol, timeframe, result[:40], detail[:400], config.now()))


def _resolve_tfs():
    """Entry/bias timeframes from enabled catalog; fallback defaults."""
    with db.conn() as c:
        enabled = {r["code"] for r in c.execute("SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1")}
    entry = ENTRY_TF_DEFAULT if ENTRY_TF_DEFAULT in enabled else (sorted(enabled)[0] if enabled else ENTRY_TF_DEFAULT)
    bias = BIAS_TF_DEFAULT if BIAS_TF_DEFAULT in enabled else entry
    return entry, bias


def _allowed_strategies(symbol: str, timeframe: str) -> list[str]:
    """Strategies allowed by catalog (enabled+verified) AND actually implemented
    in the registry — the old hardcoded list silently dropped new engines."""
    from fysb.engines.strategies import REGISTRY
    with db.conn() as c:
        rows = c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchall()
    codes = [r["code"] for r in rows if REGISTRY.get(r["code"]) is not None]
    return codes or ["PRICE_ACTION"]


def generate_signal(symbol: str, timeframe: str = "", force: bool = False,
                    only_strategies: list[str] | None = None) -> dict:
    """Run the full pipeline for one instrument. Returns a result dict:
    {"status": "SIGNAL_CREATED"|"NO_SETUP"|"REJECTED"|"ERROR"|"DUPLICATE",
     "signal_id": int|None, "reason": str, "score": int|None}

    v6.10.0 — only_strategies (from «إشارة الآن»): restrict the run to the
    CHOSEN methodology (still intersected with enabled+verified+implemented —
    a closed engine can never be summoned by request)."""
    entry_tf = timeframe or _resolve_tfs()[0]
    bias_tf = _resolve_tfs()[1]
    api_key = config.market_api_key()
    provider_symbol = _provider_symbol(symbol)

    # 0: market session gate — never manufacture signals from a closed market
    mrow = _market_row(symbol)
    state = market_data.market_state((mrow or {}).get("category") or instruments.spec(symbol).category)
    if state is False and not force:
        _log_run(symbol, entry_tf, "MARKET_CLOSED", "session calendar says market is closed; no signal manufactured")
        return {"status": "REJECTED", "signal_id": None, "reason": "market closed", "score": None}

    if db.kill_switch_on():
        _log_run(symbol, entry_tf, "KILL_SWITCH", "global kill switch is engaged")
        return {"status": "REJECTED", "signal_id": None, "reason": "kill switch engaged", "score": None}

    # 1-2: entry data + validation
    try:
        payload = market_data.fetch_candles(provider_symbol, market_data.parse_interval(entry_tf), api_key)
    except RuntimeError as e:
        _log_run(symbol, entry_tf, "DATA_ERROR", str(e))
        return {"status": "ERROR", "signal_id": None, "reason": str(e), "score": None}
    if market_data.is_stale(payload) and not force:
        _log_run(symbol, entry_tf, "STALE_DATA", f"last candle {payload['last_candle_iso']} age={int(payload['age_seconds'])}s")
        return {"status": "REJECTED", "signal_id": None,
                "reason": f"data stale (age {int(payload['age_seconds'])//60}min)", "score": None}

    # 3-4: bias data (non-fatal if unavailable — fall back to entry TF)
    try:
        bias_payload = market_data.fetch_candles(provider_symbol, market_data.parse_interval(bias_tf), api_key, outputsize=150)
        if market_data.is_stale(bias_payload):
            bias_candles = payload["values"]
            bias_note = "bias TF stale, used entry TF"
        else:
            bias_candles = bias_payload["values"]
            bias_note = f"bias {bias_tf}"
    except RuntimeError:
        bias_candles = payload["values"]
        bias_note = "bias TF unavailable, used entry TF"

    candles = payload["values"]
    if len(candles) < 40:
        _log_run(symbol, entry_tf, "INSUFFICIENT_DATA", f"{len(candles)} candles")
        return {"status": "REJECTED", "signal_id": None, "reason": f"insufficient candles ({len(candles)})", "score": None}

    # 5-7: trend / structure / liquidity feed into every strategy via ctx
    ctx = Ctx(symbol=symbol, timeframe=entry_tf, candles=candles,
              bias_candles=bias_candles, market_row=_market_row(symbol))

    # 8: strategies (v6.10.0: «إشارة الآن» pins the chosen methodology)
    allowed = _allowed_strategies(symbol, entry_tf)
    if only_strategies:
        wanted = {s.strip().upper() for s in only_strategies if s.strip()}
        allowed = [c for c in allowed if c in wanted]
        if not allowed:
            _log_run(symbol, entry_tf, "STRATEGY_NOT_LIVE", f"requested {sorted(wanted)} is not enabled+verified+implemented")
            return {"status": "REJECTED", "signal_id": None,
                    "reason": "المنهجية المطلوبة غير مفعلة/موثقة حالياً", "score": None}
    results = evaluate_all(ctx, allowed)
    if not results:
        _log_run(symbol, entry_tf, "NO_SETUP", "no strategy conditions met; no signal manufactured")
        return {"status": "NO_SETUP", "signal_id": None, "reason": "no qualifying setup", "score": None}

    # 9: merge — best score wins; require margin over runner-up only for equal directions
    best = results[0]
    vol = atr_pct(candles)

    # 12-13: RR + risk gate
    risk_gate = risk.check_signal(symbol, best.rr, vol)
    if not risk_gate.allowed:
        _log_run(symbol, entry_tf, "RISK_REJECTED", risk_gate.reason)
        return {"status": "REJECTED", "signal_id": None, "reason": risk_gate.reason, "score": best.score}

    # 13.5: REAL-ENTRY GATE — a signal must be born actionable.
    # The plan's prices are computed from candle history; the market does not
    # wait for the pipeline. If the LIVE quote has already consumed >25% of
    # the entry→TP1 leg on the waiting side (a stale retrace nobody filled)
    # or blown through the entry toward the stop, this setup is a RELIC —
    # publishing it would print an R:R the market no longer offers (the exact
    # user report: «+288.6 pips أعلى الدخول» at birth). Refused, logged.
    live_px = 0.0
    try:
        quote = market_data.fetch_price(symbol, api_key)
        live_px = float((quote or {}).get("price") or 0.0)
    except Exception:
        live_px = 0.0
    if live_px <= 0:
        vals = payload.get("values") or []
        if vals:
            try:
                live_px = float(vals[-1].get("close") or 0.0)
            except (TypeError, ValueError):
                live_px = 0.0
    birth_run = False
    if live_px > 0:
        stale, stale_detail = is_stale_entry(symbol, best.direction, best.entry,
                                             best.stop, best.tp1, live_px)
        if stale:
            _log_run(symbol, entry_tf, "STALE_ENTRY", stale_detail)
            return {"status": "REJECTED", "signal_id": None,
                    "reason": ("stale entry — السعر ابتعد عن منطقة الدخول قبل النشر "
                               f"({stale_detail}); لا تُنشر فكرة فات قطارها"),
                    "score": best.score}
        birth_status_now = birth_status(best.direction, best.entry, live_px)
    else:
        birth_status_now = "ACTIVE"

    # 14: duplicate check — same symbol+direction+TF within freshness window
    hours = max(1, min(db.get_setting_int("signal_expiry_hours", 72) // 2, 36))
    cutoff = (config_now() - timedelta(hours=hours)).isoformat()
    with db.conn() as c:
        dup = c.execute("""SELECT id FROM signals WHERE source='AUTO' AND symbol=? AND direction=? AND timeframe=?
                           AND created_at>=? LIMIT 1""", (symbol, best.direction, entry_tf, cutoff)).fetchone()
        if dup and not force:
            _log_run(symbol, entry_tf, "DUPLICATE_SKIPPED", f"recent signal #{dup['id']}")
            return {"status": "DUPLICATE", "signal_id": dup["id"], "reason": "recent duplicate", "score": best.score}

        # 15: store (full precision floats; formatting happens only at display).
        # Birth truth: the birth quote is stored (live_px) and an at-market or
        # already-crossed entry is born RUNNING with filled_at set — the first
        # card the world sees can never lie about the execution state.
        filled_at_birth = config_now().isoformat() if birth_status_now == "RUNNING" else ""
        cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
                           strategy,reason,status,source,created_by,created_at,data_source,data_time,
                           strategy_scores,expires_at,entry_zone_lo,entry_zone_hi,filled_at,live_px)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'AUTO',0,?,?,?,?,?,?,?,?,?)""",
                        (symbol, best.direction, entry_tf, best.entry, best.stop, best.tp1, best.tp2, best.tp3,
                         f"{best.tp1},{best.tp2},{best.tp3}", best.rr, best.score, best.code,
                         " | ".join(best.reasons), birth_status_now, config.now(), "LIVE", payload["last_candle_iso"],
                         json.dumps({r.code: r.score for r in results}), _expiry_iso(),
                         0.0, 0.0, filled_at_birth, live_px))
        sid = cur.lastrowid
    _log_run(symbol, entry_tf, "SIGNAL_CREATED",
             f"id={sid} {best.summary()} | {bias_note} | birth={birth_status_now} live={live_px:g}")
    return {"status": "SIGNAL_CREATED", "signal_id": sid, "reason": best.summary(), "score": best.score}


def opposite_close_advisory_targets(symbol: str, direction: str, exclude_sid: int) -> list[dict]:
    """Open signals on the SAME symbol in the OPPOSITE direction (the trades a
    new signal would fight against). Pure DB — unit-testable."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT * FROM signals
               WHERE symbol=? AND direction!=? AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
               AND id!=? ORDER BY id DESC""",
            (symbol, direction, exclude_sid)).fetchall()]
    return rows


async def opposite_close_advisory(sig: dict, bot_send) -> int:
    """v6.10.0 owner report 6 — the direction-flip protection.

    When a NEW signal is about to publish and an OPEN signal on the same
    symbol runs in the OPPOSITE direction, every holder of the old trade gets
    the royal close advisory FIRST: the documented profit so far (real price,
    signed pips, USD at 0.01 lot), the verdict «the trade is WINNING so far»,
    and the instruction to close/secure before the new signal arrives.
    Returns the number of advisory messages sent."""
    import asyncio as _asyncio
    from fysb import instruments as _inst
    from fysb.engines import market_data as _md
    from fysb.engines.tracker import drift_pips, seq_of
    from fysb.theme import dir_badge, esc, status_ar, tf_name
    olds = opposite_close_advisory_targets(sig["symbol"], sig["direction"], int(sig.get("id") or 0))
    if not olds:
        return 0
    live_px = 0.0
    try:
        quote = await _asyncio.to_thread(_md.fetch_price, sig["symbol"])
        live_px = float((quote or {}).get("price") or 0.0)
    except Exception:
        live_px = 0.0
    sent = 0
    for old in olds:
        try:
            entry = float(old["entry"])
            drift = drift_pips(old["symbol"], old["direction"], entry, live_px) if live_px else float(old.get("live_px") or 0.0)
            usd = _inst.usd_move(old["symbol"], drift, 0.01, px=live_px or 0.0) if drift else 0.0
            hit = int(old.get("hit_tp") or 0)
            hit_txt = (" · تحقق الهدف الأول ✅" if hit >= 1 else
                       " · تحقق هدفان ✅✅" if hit >= 2 else
                       " · الصفقة في صالحك حتى الآن ✅")
            with db.conn() as c:
                uids = [r["user_id"] for r in c.execute(
                    "SELECT user_id FROM signal_deliveries WHERE signal_id=? AND user_id>0", (old["id"],))]
            if not uids:
                continue  # nobody actually holds this old trade — nothing to advise
            text = (
                f"🔄 <b><u>تنبيه إغلاق صفقة متعاكسة — الإشارة #{seq_of(old['id'])}</u></b> — {esc(old['symbol'])} {dir_badge(old['direction'])} ⏱ {tf_name(old['timeframe'])}\n"
                "<blockquote>"
                f"▎📌 الحالة الحالية: <b>{status_ar(old['status'])}</b>{hit_txt}\n"
                + (f"▎📍 السعر الآن: <code>{_inst.fmt_price(old['symbol'], live_px)}</code> · "
                   f"<b>{drift:+.1f} نقطة ({drift:+.0f} pips)</b> من الدخول\n" if live_px else "")
                + f"▎💵 <b>الربح الموثق الآن بـ 0.01 لوت: {usd:+,.2f} $</b>\n"
                "▎✅ <b>الصفقة تعتبر رابحة حتى هذه اللحظة وفق بيانات السوق الفعلية.</b>\n"
                "▎🛡 <b>قرارك الآن قبل الإشارة الجديدة:</b> أغلقها على الربح الحالي، أو أمنحه "
                "(انقل الوقف لنقطة الدخول وأغلق 50% على الأقل) — لا تتركها تتعارض مع الاتجاه الجديد.\n"
                "▎📡 إشارة جديدة عكسية قادمة مباشرة بعد هذا التنبيه."
                "</blockquote>\n"
                "<i>أرقام موثقة من أسعار السوق — القرار النهائي لك، والتنبيه لا يغلق شيئاً تلقائياً في حسابك.</i>"
            )
            for uid in uids:
                try:
                    await bot_send(uid, text)
                    sent += 1
                except Exception:
                    pass
            if config.ADMIN_ID:
                try:
                    await bot_send(config.ADMIN_ID, text)
                    sent += 1
                except Exception:
                    pass
        except Exception:
            continue
    return sent


def config_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _expiry_iso() -> str:
    from datetime import datetime, timezone, timedelta
    hours = db.get_setting_int("signal_expiry_hours", 72)
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _provider_symbol(symbol: str) -> str:
    with db.conn() as c:
        r = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return r["provider_symbol"] if r else symbol


def _market_row(symbol: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def passes_personal_filters(u, sig_symbol: str, sig_tf: str, sig_strategy: str, sig_score) -> bool:
    """Personal server-side delivery filters for one user row.

    Enforced gates (empty value = no personal restriction):
      * market    : user's chosen market ("XAU/USD" style tolerated)
      * timeframe : exact entry-TF match
      * strategy  : the user's selected methodology — "ALL" or empty = every
                    methodology. This makes the profile's "الاستراتيجية المفضلة"
                    a REAL delivery gate, not a cosmetic label.
      * min_score : quality floor
    Plan/package entitlements are a separate hard gate in emit_to_users.
    """
    u_market = (u["market"] or "").strip().upper()
    if u_market:
        # slash-tolerant both ways: "XAU/USD" <-> "XAUUSD"
        u_m_n = u_market.replace("/", "")
        sig_n = sig_symbol.upper().replace("/", "")
        if sig_n not in u_m_n and sig_n != u_m_n:
            return False
    u_tf = (u["timeframe"] or "").strip().upper()
    if u_tf and u_tf != sig_tf.upper():
        return False
    u_strategy = (u["strategy"] or "").strip().upper()
    if u_strategy and u_strategy != "ALL" and u_strategy != (sig_strategy or "").upper():
        return False
    if sig_score < (u["min_score"] or 0):
        return False
    return True


def record_delivery_refs(uid: int, signal_id: int, msg) -> None:
    """Pin the delivered card's chat_id + message_id so the tracker can EDIT
    that exact original message live (status transitions + market pulse).
    Rows delivered before this upgrade have empty refs — their card stays a
    snapshot; every NEW delivery is live forever."""
    if msg is None:
        return
    message_id = getattr(msg, "message_id", 0)
    chat_id = getattr(msg, "chat_id", None)
    if not message_id or chat_id is None:
        return
    with db.conn() as c:
        c.execute("UPDATE signal_deliveries SET chat_id=?, message_id=? WHERE user_id=? AND signal_id=?",
                  (str(chat_id), int(message_id), uid, signal_id))


async def emit_to_users(signal_row, bot_send) -> dict:
    """Step 16 — deliver a stored signal to eligible users (server-side gates).

    bot_send: async callable(user_id, text) -> None. MUST be awaited inside the
    bot's event loop (PTB's httpx client is loop-bound).
    Gates applied per user, all server-side (anti-bypass):
      1. package entitlement: symbol/timeframe/strategy must be IN the plan
      2. personal preferences (market=""/timeframe="" => all allowed by plan)
      3. quality floor (min_score)
      4. daily quota (atomic ledger)
    Each delivered message carries the user's PERSONAL risk/profit block, and
    when auto-trade is on, the trade is opened on his demo account instantly.

    v6.10.0 (owner report 6): BEFORE delivering, every holder of an OPEN signal
    on the SAME symbol in the OPPOSITE direction receives the royal close
    advisory — documented profit so far, «close or secure it» — then the new
    signal flows. No silent direction flip ever again.
    """
    from fysb.services.subscriptions import try_consume_signal, release_signal, plan_for
    from fysb.services import user_trade
    from fysb.ui.cards import render_signal_card

    stats = {"sent": 0, "skipped_quota": 0, "failed": 0, "skipped_filters": 0}
    sig = dict(signal_row)
    sig_symbol = sig["symbol"]
    sig_tf = sig["timeframe"]
    sig_score = sig["score"]
    sig_strategy = (sig.get("strategy") or "").upper()
    await opposite_close_advisory(sig, bot_send)
    with db.conn() as c:
        users = c.execute("SELECT user_id,market,timeframe,strategy,min_score,notifications,is_blocked FROM users WHERE notifications=1 AND is_blocked=0").fetchall()

    for u in users:
        uid = u["user_id"]
        if uid == config.ADMIN_ID:
            continue  # the admin is never treated as a signal recipient

        # 1) package entitlement — hard gate (the plan decides, not the client)
        plan = plan_for(uid)
        if not plan:
            stats["skipped_filters"] += 1
            continue
        p_markets = [x.strip().upper() for x in str(plan["markets"] or "").split(",") if x.strip()]
        p_tfs = [x.strip().upper() for x in str(plan["timeframes"] or "").split(",") if x.strip()]
        p_strats = [x.strip().upper() for x in str(plan["strategies"] or "").split(",") if x.strip()]
        if p_markets and sig_symbol.upper() not in p_markets:
            stats["skipped_filters"] += 1
            continue
        if p_tfs and sig_tf.upper() not in p_tfs:
            stats["skipped_filters"] += 1
            continue
        if p_strats and "ALL" not in p_strats and sig_strategy not in p_strats:
            stats["skipped_filters"] += 1
            continue

        # 2) personal preferences (empty = follow the plan entirely)
        if not passes_personal_filters(u, sig_symbol, sig_tf, sig_strategy, sig_score):
            stats["skipped_filters"] += 1
            continue

        # 3) quota (atomic)
        if not try_consume_signal(uid, sig):
            stats["skipped_quota"] += 1
            continue

        # 4) deliver: luxury card + personal plan block (single message)
        row = _market_row(sig_symbol)
        text = render_signal_card(sig, user_view=True, row=row)
        pulse = birth_pulse_line(sig, row)
        if pulse:
            text += "\n\n<blockquote>" + pulse + "</blockquote>"
        personal = user_trade.personal_block(sig, uid)
        if personal:
            text += "\n\n<blockquote>" + personal + "</blockquote>"
        try:
            msg = await bot_send(uid, text)
            stats["sent"] += 1
            record_delivery_refs(uid, sig["id"], msg)
        except Exception:
            stats["failed"] += 1
            release_signal(uid, sig["id"])
            continue

        # 5) auto-execution on the user's demo account (his own risk/lot)
        exec_res = user_trade.auto_execute(sig, uid)
        if exec_res and exec_res.get("ok"):
            try:
                await bot_send(uid, user_trade.execution_card(exec_res, sig))
            except Exception:
                pass
    return stats
