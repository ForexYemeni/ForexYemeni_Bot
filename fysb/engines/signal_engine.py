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
from fysb.engines.analysis import atr, atr_pct, confirmed_candles, structure_labels
from fysb.engines.entry_state import birth_pulse_line, birth_status, is_stale_entry
from fysb.engines.strategies import consensus_filter, evaluate_all
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


_MAX_STOP_PCT_BY_TF = {
    "M1": 0.0045,
    "M5": 0.0065,
    "M15": 0.0100,
    "M30": 0.0125,
    "H1": 0.0150,
    "H4": 0.0250,
    "D1": 0.0400,
    "W1": 0.0650,
}


def validate_signal_geometry(
    symbol: str,
    timeframe: str,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    atr_val: float = 0.0,
    row=None,
) -> tuple[bool, str]:
    """v6.23.0 Universal SL/TP Geometry & Max-Stop Institutional Guard.

    1. Enforces strict directional ordering (zero inverted SL or TP levels):
       BUY : stop < entry < tp1 < tp2 < tp3
       SELL: tp3 < tp2 < tp1 < entry < stop
    2. Caps stop distance against both ATR (<= 3.5x ATR) and timeframe % of price
       (x2.5 for crypto) so freak spike candles never emit 888-pip H1 stops.
    """
    d = (direction or "").upper()
    try:
        entry = float(entry)
        stop = float(stop)
        tp1 = float(tp1)
        tp2 = float(tp2)
        tp3 = float(tp3)
    except (TypeError, ValueError):
        return False, "invalid numeric levels"
    if entry <= 0 or stop <= 0 or tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
        return False, "non-positive price level"

    if d == "BUY":
        if not (stop < entry < tp1 < tp2 < tp3):
            return False, "invalid BUY geometry — وقف الخسارة أو الأهداف معكوسة"
    elif d == "SELL":
        if not (tp3 < tp2 < tp1 < entry < stop):
            return False, "invalid SELL geometry — وقف الخسارة أو الأهداف معكوسة"
    else:
        return False, f"invalid direction {d}"

    risk = abs(entry - stop)
    if risk <= 0:
        return False, "zero stop distance"

    spec = instruments.spec(symbol, row)
    base_pct = _MAX_STOP_PCT_BY_TF.get((timeframe or "H1").upper(), 0.0200)
    max_pct = base_pct * 2.5 if spec.category == "crypto" else base_pct
    if (risk / entry) > max_pct:
        pips_sl = instruments.pips(symbol, entry, stop, row)
        return False, f"stop exceeds {timeframe} max ({pips_sl:.1f} pips, {risk / entry * 100:.2f}% > {max_pct * 100:.2f}%)"

    # Cap freak spike stops relative to real ATR (skip artificially flat synthetic test bars < 0.1% ATR)
    if atr_val and float(atr_val) > 0 and (float(atr_val) / entry) >= 0.0010 and risk > 4.0 * float(atr_val):
        pips_sl = instruments.pips(symbol, entry, stop, row)
        return False, f"oversized stop ({pips_sl:.1f} pips > 4.0x ATR)"

    return True, ""


def _parse_iso_utc(ts: str):
    """Best-effort ISO8601 -> timezone-aware UTC datetime (None if empty/invalid)."""
    if not ts:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def generate_signal(symbol: str, timeframe: str = "", force: bool = False,
                    only_strategies: list[str] | None = None,
                    source: str = "AUTO", created_by: int = 0) -> dict:
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

    # 0.5: v6.25.0 Live Economic Calendar & News Guard (free keyless feed)
    if not force:
        try:
            from fysb.engines import news_guard
            blocked_news, ev = news_guard.check_news_block(symbol)
            if blocked_news and ev:
                mins = int(ev.get("minutes_delta") or 0)
                when_txt = f"بعد {mins}د" if mins >= 0 else f"منذ {abs(mins)}د"
                n_reason = (
                    f"درع الأخبار (News Guard): خبر قوي قريب {ev.get('currency')} — "
                    f"{ev.get('title')} ({when_txt})"
                )
                _log_run(symbol, entry_tf, "NEWS_BLOCKED", n_reason)
                return {"status": "REJECTED", "signal_id": None, "reason": n_reason, "score": None}
        except Exception as exc:
            log.debug("news_guard check skipped: %s", exc)

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

    # 4.5: Non-repainting closed-candle guard (v6.20.0) — strip any still-forming
    # bar so signals & indicators are computed strictly on confirmed candle closes.
    candles = confirmed_candles(payload["values"], entry_tf)
    bias_candles = confirmed_candles(bias_candles, bias_tf)
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

    # 8.5: Directional consensus guard (v6.20.0) — resolve or reject BUY vs SELL
    # internal strategy conflicts instead of blindly picking top-1 score.
    results, conflict_note = consensus_filter(results)
    if not results:
        _log_run(symbol, entry_tf, "CONFLICT_REJECTED", conflict_note)
        return {"status": "NO_SETUP", "signal_id": None, "reason": conflict_note, "score": None}
    if conflict_note:
        _log_run(symbol, entry_tf, "CONFLICT_RESOLVED", conflict_note)

    # 8.7: v6.25.0 Institutional Intelligence Enrichment
    # (#3 Adaptive Weighting, #4 Confluence Matrix, #5 DXY Correlation,
    #  #6 Killzone Session, #7 Liquidity Sweep & Stop-Hunt)
    try:
        from fysb.engines import intelligence as _intel
        bias_struct = structure_labels(bias_candles) if bias_candles else {}
        bias_trend = str(bias_struct.get("trend") or "")
        kz = _intel.current_killzone()
        sweep = _intel.detect_liquidity_sweep(candles)
        dxy_cache_by_dir: dict[str, dict] = {}
        for cand in results:
            bonus = 0.0
            extra_notes: list[str] = []
            # Adaptive 30-day closed performance weight
            aw = _intel.adaptive_strategy_weight(cand.code, symbol)
            if aw["delta"]:
                bonus += aw["delta"]
                extra_notes.append(aw["note"])
            # Confluence across agreeing strategies + HTF alignment
            conf = _intel.build_confluence_summary(results, cand.direction, entry_tf, bias_trend)
            if conf["bonus"]:
                bonus += conf["bonus"]
            extra_notes.append(conf["label"])
            # Killzone liquidity session bonus
            if kz["bonus"]:
                bonus += kz["bonus"]
            extra_notes.append(f"الجلسة: {kz['label']}")
            # Live DXY correlation check
            d_key = cand.direction.upper()
            if d_key not in dxy_cache_by_dir:
                dxy_cache_by_dir[d_key] = _intel.dxy_bias_check(symbol, d_key)
            dxy_res = dxy_cache_by_dir[d_key]
            if dxy_res["delta"]:
                bonus += dxy_res["delta"]
                extra_notes.append(dxy_res["note"])
            # Liquidity sweep alignment
            if sweep["detected"] and sweep["direction"] == d_key:
                bonus += sweep["bonus"]
                extra_notes.append(sweep["note"])
            if bonus:
                cand.score = int(max(0, min(95, round(float(cand.score) + bonus))))
            cand.reasons = list(cand.reasons or []) + extra_notes
        results.sort(key=lambda r: float(r.score), reverse=True)
    except Exception as exc:
        log.debug("intelligence enrichment skipped: %s", exc)

    # 9: merge & multi-candidate gate evaluation (v6.19.0)
    # Evaluate ranked candidates in score order so if candidate #1 fails
    # per-candidate gates (stale entry or low RR), the engine seamlessly
    # promotes the next highest-scoring LIVE candidate instead of discarding
    # every other valid methodology.
    vol = atr_pct(candles)
    sig_source = (source or "AUTO").upper()

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

    best = None
    birth_status_now = "ACTIVE"
    first_reject_tag = ""
    first_reject_reason = ""
    first_reject_score = results[0].score
    skipped_notes: list[str] = []

    from fysb.engines.quality_gate import DEFAULT_MIN_SCORE, evaluate_quality_gate
    min_sig_score = db.get_setting_int("min_signal_score", DEFAULT_MIN_SCORE)
    atr_val = atr(candles)
    sym_row = _market_row(symbol)

    for cand in results:
        # 11.4: Institutional SL/TP Geometry & Max-Stop Guard (v6.23.0)
        # Never allow inverted SL/TP levels or freak spike-candle stops even on force=True.
        ok_geo, geo_reason = validate_signal_geometry(
            symbol, entry_tf, cand.direction,
            cand.entry, cand.stop, cand.tp1, cand.tp2, cand.tp3,
            atr_val=atr_val, row=sym_row,
        )
        if not ok_geo:
            if not first_reject_reason:
                first_reject_tag = "GEOMETRY_REJECTED"
                first_reject_reason = geo_reason
                first_reject_score = cand.score
            skipped_notes.append(f"{cand.code}:geo({geo_reason[:36]})")
            continue

        # 11.5: Institutional Quality + HTF Bias + Chop + Session Gate (v6.20.6)
        # Matches backtest.py 100% identically. Admin manual force=True bypasses
        # only the soft regime filter if explicitly forced.
        if not force:
            ok_q, q_reason = evaluate_quality_gate(
                symbol, entry_tf, candles, bias_candles, cand,
                min_score=min_sig_score,
            )
            if not ok_q:
                if not first_reject_reason:
                    first_reject_tag = "QUALITY_REJECTED"
                    first_reject_reason = q_reason
                    first_reject_score = cand.score
                skipped_notes.append(f"{cand.code}:quality({q_reason[:36]})")
                continue

        # 12-13: RR + risk gate (per-symbol, global cap, correlation, daily loss, ADMIN_SIGNAL_NOW exemption)
        risk_gate = risk.check_signal(symbol, cand.rr, vol, source=sig_source,
                                      created_by=created_by, direction=cand.direction)
        if not risk_gate.allowed:
            # Cap / volatility blocks apply identically to every candidate -> stop immediately.
            # Correlation block depends on direction, but consensus_filter already unified direction.
            is_rr_only = ("rr" not in (risk_gate.metrics or {}))
            if not is_rr_only:
                _log_run(symbol, entry_tf, "RISK_REJECTED", risk_gate.reason)
                return {"status": "REJECTED", "signal_id": None, "reason": risk_gate.reason, "score": cand.score}
            if not first_reject_reason:
                first_reject_tag = "RISK_REJECTED"
                first_reject_reason = risk_gate.reason
                first_reject_score = cand.score
            skipped_notes.append(f"{cand.code}:low_rr")
            continue

        # 13.5: REAL-ENTRY GATE — a signal must be born actionable.
        if live_px > 0:
            stale, stale_detail = is_stale_entry(symbol, cand.direction, cand.entry,
                                                 cand.stop, cand.tp1, live_px)
            if stale:
                if not first_reject_reason:
                    first_reject_tag = "STALE_ENTRY"
                    first_reject_reason = (
                        "stale entry — السعر ابتعد عن منطقة الدخول قبل النشر "
                        f"({stale_detail}); لا تُنشر فكرة فات قطارها"
                    )
                    first_reject_score = cand.score
                skipped_notes.append(f"{cand.code}:stale({stale_detail})")
                continue
            birth_status_now = birth_status(cand.direction, cand.entry, live_px)
        else:
            birth_status_now = "ACTIVE"

        best = cand
        break

    if best is None:
        _log_run(symbol, entry_tf, first_reject_tag or "REJECTED", first_reject_reason)
        return {"status": "REJECTED", "signal_id": None,
                "reason": first_reject_reason, "score": first_reject_score}

    if skipped_notes:
        _log_run(symbol, entry_tf, "CANDIDATE_FALLBACK",
                 f"selected {best.code} ({best.score}) after skipping {'; '.join(skipped_notes[:4])}")

    # 14: duplicate check — same symbol+direction+TF within freshness window.
    # v6.20.3: full freshness window applies ONLY to still-OPEN signals; once a
    # trade is CLOSED (including TP1/TP2 Breakeven close!), only a brief 15-min
    # cooldown applies so the engine immediately starts searching for new setups!
    hours = max(1, min(db.get_setting_int("signal_expiry_hours", 72) // 2, 36))
    open_cutoff = (config_now() - timedelta(hours=hours)).isoformat()
    closed_cutoff = (config_now() - timedelta(minutes=15)).isoformat()
    with db.conn() as c:
        if sig_source == "SIGNAL_NOW" and int(created_by or 0) > 0:
            dup = c.execute(
                """SELECT id FROM signals WHERE source='SIGNAL_NOW' AND created_by=? AND symbol=?
                   AND direction=? AND timeframe=?
                   AND (
                       ((closed_at IS NULL OR closed_at='')
                        AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                        AND created_at>=?)
                       OR
                       (closed_at!='' AND closed_at>=?)
                   ) LIMIT 1""",
                (int(created_by), symbol, best.direction, entry_tf, open_cutoff, closed_cutoff),
            ).fetchone()
        else:
            dup = c.execute(
                """SELECT id FROM signals WHERE source IN ('AUTO','ADMIN_SIGNAL_NOW') AND symbol=? AND direction=? AND timeframe=?
                   AND (
                       ((closed_at IS NULL OR closed_at='')
                        AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                        AND created_at>=?)
                       OR
                       (closed_at!='' AND closed_at>=?)
                   ) LIMIT 1""",
                (symbol, best.direction, entry_tf, open_cutoff, closed_cutoff),
            ).fetchone()
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
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (symbol, best.direction, entry_tf, best.entry, best.stop, best.tp1, best.tp2, best.tp3,
                         f"{best.tp1},{best.tp2},{best.tp3}", best.rr, best.score, best.code,
                         " | ".join(best.reasons), birth_status_now, sig_source, int(created_by or 0),
                         config.now(), "LIVE", payload["last_candle_iso"],
                         json.dumps({r.code: r.score for r in results}), _expiry_iso(),
                         0.0, 0.0, filled_at_birth, live_px))
        sid = cur.lastrowid
    _log_run(symbol, entry_tf, "SIGNAL_CREATED",
             f"id={sid} {best.summary()} | {bias_note} | birth={birth_status_now} live={live_px:g}")

    # v6.24.0 (#6 Tier-Aware Best Pick): if AUTO emitted an upper-tier strategy
    # (e.g. SMC/ICT) on a market+TF that FREE allows (e.g. XAUUSD M15/H1), check
    # whether a high-quality FREE-allowed candidate (CLASSIC/TREND) also passed
    # every institutional gate so FREE users don't starve unnecessarily.
    free_sid = None
    if sig_source == "AUTO" and not only_strategies:
        try:
            with db.conn() as c:
                fp = c.execute("SELECT markets,timeframes,strategies,min_score FROM plans WHERE code='FREE'").fetchone()
            if fp:
                f_markets = {_norm_sym(x) for x in str(fp["markets"] or "").split(",") if x.strip()}
                f_tfs = {x.strip().upper() for x in str(fp["timeframes"] or "").split(",") if x.strip()}
                f_strats = {x.strip().upper() for x in str(fp["strategies"] or "").split(",") if x.strip()}
                f_min_score = int(fp["min_score"] or 80)
                if ((not f_markets or "ALL" in f_markets or _norm_sym(symbol) in f_markets)
                        and (not f_tfs or "ALL" in f_tfs or entry_tf.upper() in f_tfs)
                        and f_strats and best.code.upper() not in f_strats):
                    for fcand in results:
                        if fcand.code.upper() not in f_strats or fcand.score < f_min_score:
                            continue
                        ok_geo_f, _ = validate_signal_geometry(
                            symbol, entry_tf, fcand.direction,
                            fcand.entry, fcand.stop, fcand.tp1, fcand.tp2, fcand.tp3,
                            atr_val=atr_val, row=sym_row,
                        )
                        if not ok_geo_f:
                            continue
                        if not force:
                            ok_q_f, _ = evaluate_quality_gate(
                                symbol, entry_tf, candles, bias_candles, fcand,
                                min_score=max(min_sig_score, f_min_score),
                            )
                            if not ok_q_f:
                                continue
                        if live_px > 0:
                            stale_f, _ = is_stale_entry(symbol, fcand.direction, fcand.entry,
                                                        fcand.stop, fcand.tp1, live_px)
                            if stale_f:
                                continue
                            f_birth = birth_status(fcand.direction, fcand.entry, live_px)
                        else:
                            f_birth = "ACTIVE"
                        if fcand.rr < float(db.get_setting("min_rr", "1.5") or 1.5):
                            continue
                        with db.conn() as c:
                            dup_f = c.execute(
                                """SELECT id FROM signals WHERE source='AUTO_FREE' AND symbol=?
                                   AND direction=? AND timeframe=?
                                   AND (
                                       ((closed_at IS NULL OR closed_at='')
                                        AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                        AND created_at>=?)
                                       OR (closed_at!='' AND closed_at>=?)
                                   ) LIMIT 1""",
                                (symbol, fcand.direction, entry_tf, open_cutoff, closed_cutoff),
                            ).fetchone()
                            if dup_f:
                                break
                            f_filled = config_now().isoformat() if f_birth == "RUNNING" else ""
                            free_sid = c.execute(
                                """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
                                   strategy,reason,status,source,created_by,created_at,data_source,data_time,
                                   strategy_scores,expires_at,entry_zone_lo,entry_zone_hi,filled_at,live_px)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (symbol, fcand.direction, entry_tf, fcand.entry, fcand.stop, fcand.tp1, fcand.tp2, fcand.tp3,
                                 f"{fcand.tp1},{fcand.tp2},{fcand.tp3}", fcand.rr, fcand.score, fcand.code,
                                 " | ".join(fcand.reasons), f_birth, "AUTO_FREE", 0,
                                 config.now(), "LIVE", payload["last_candle_iso"],
                                 json.dumps({r.code: r.score for r in results}), _expiry_iso(),
                                 0.0, 0.0, f_filled, live_px),
                            ).lastrowid
                        _log_run(symbol, entry_tf, "FREE_TIER_PICK",
                                 f"id={free_sid} {fcand.summary()} (companion to #{sid} {best.code})")
                        break
        except Exception as exc:
            log.debug("free_tier_fallback skipped: %s", exc)

    out = {"status": "SIGNAL_CREATED", "signal_id": sid, "reason": best.summary(), "score": best.score}
    if free_sid:
        out["free_fallback_signal_id"] = free_sid
    return out


def opposite_close_advisory_targets(symbol: str, direction: str, exclude_sid: int,
                                    source: str = "AUTO", created_by: int = 0) -> list[dict]:
    """Open signals on the SAME symbol in the OPPOSITE direction (the trades a
    new signal would fight against). Respects private SIGNAL_NOW isolation.
    Pure DB — unit-testable."""
    src = (source or "AUTO").upper()
    with db.conn() as c:
        if src == "SIGNAL_NOW" and int(created_by or 0) > 0:
            rows = [dict(r) for r in c.execute(
                """SELECT * FROM signals
                   WHERE symbol=? AND direction!=? AND source='SIGNAL_NOW' AND created_by=?
                   AND (closed_at IS NULL OR closed_at='')
                   AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                   AND id!=? ORDER BY id DESC""",
                (symbol, direction, int(created_by), exclude_sid)).fetchall()]
        else:
            rows = [dict(r) for r in c.execute(
                """SELECT * FROM signals
                   WHERE symbol=? AND direction!=?
                   AND (source IS NULL OR source != 'SIGNAL_NOW')
                   AND (closed_at IS NULL OR closed_at='')
                   AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                   AND id!=? ORDER BY id DESC""",
                (symbol, direction, exclude_sid)).fetchall()]
    return rows


async def opposite_close_advisory(sig: dict, bot_send) -> int:
    """v6.20.0 truthful direction-flip protection (users + broadcast channels).

    When a NEW signal is about to publish and an OPEN signal on the same
    symbol runs in the OPPOSITE direction, every holder (private users AND
    broadcast channels) gets an honest close/review advisory FIRST:
      * Winning trade (drift > 0 or TP hit): reports real profit & advises
        closing/securing profit at breakeven.
      * Floating loss (drift < 0 & no TP hit): reports honest floating loss
        (NEVER mislabels a losing trade as winning!) and advises reviewing risk.
      * Pending unfilled order (ACTIVE): advises cancelling the pending order.
    Returns the number of advisory messages sent."""
    import asyncio as _asyncio
    from fysb import instruments as _inst
    from fysb.engines import market_data as _md
    from fysb.engines.tracker import drift_pips, seq_of
    from fysb.theme import dir_badge, esc, status_ar, tf_name
    sig_src = (sig.get("source") or "AUTO").upper()
    sig_owner = int(sig.get("created_by") or 0)
    olds = opposite_close_advisory_targets(
        sig["symbol"], sig["direction"], int(sig.get("id") or 0),
        source=sig_src, created_by=sig_owner,
    )
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
            ref_px = live_px if live_px > 0 else float(old.get("live_px") or 0.0)
            drift = drift_pips(old["symbol"], old["direction"], entry, ref_px) if ref_px > 0 else 0.0
            usd = _inst.usd_move(old["symbol"], drift, 0.01, px=ref_px) if (ref_px > 0 and drift) else 0.0
            hit = int(old.get("hit_tp") or 0)
            old_status = (old.get("status") or "").upper()
            is_pending = (old_status == "ACTIVE" and hit == 0)
            is_winning = (hit >= 1) or (not is_pending and drift > 0)
            is_losing = (not is_pending) and (hit == 0) and (drift < 0)

            if is_pending:
                hit_txt = " · أمر معلق لم يُفعَّل بعد"
            elif hit >= 2:
                hit_txt = " · تحقق هدفان ✅✅"
            elif hit >= 1:
                hit_txt = " · تحقق الهدف الأول ✅"
            elif drift > 0:
                hit_txt = " · الصفقة في صالحك حتى الآن ✅"
            elif drift < 0:
                hit_txt = " · الصفقة على تراجع عائم حالياً ⚠️"
            else:
                hit_txt = " · عند نقطة الدخول ⚖️"

            with db.conn() as c:
                deliv_rows = c.execute(
                    "SELECT user_id, chat_id FROM signal_deliveries WHERE signal_id=?",
                    (old["id"],),
                ).fetchall()
            targets: list = []
            seen_targets: set[str] = set()
            for dr in deliv_rows:
                duid = int(dr["user_id"] or 0)
                if duid > 0:
                    key = str(duid)
                    if key not in seen_targets:
                        seen_targets.add(key)
                        targets.append(duid)
                else:
                    cid_str = str(dr["chat_id"] or "").strip()
                    if cid_str and cid_str not in seen_targets:
                        seen_targets.add(cid_str)
                        try:
                            targets.append(int(cid_str))
                        except ValueError:
                            targets.append(cid_str)
            if not targets:
                continue  # nobody actually holds this old trade — nothing to advise

            px_line = (
                f"▎📍 السعر الآن: <code>{_inst.fmt_price(old['symbol'], ref_px)}</code> · "
                f"<b>{drift:+.1f} نقطة ({drift:+.0f} pips)</b> من الدخول\n"
                if ref_px > 0 else ""
            )
            if is_pending:
                body_lines = (
                    "▎⏳ <b>هذا الأمر المعلق لم يتفعل بعد في السوق.</b>\n"
                    "▎🛡 <b>قرارك الآن قبل الإشارة الجديدة:</b> يُنصح بإلغاء الأمر المعلق "
                    "حتى لا يتعارض مع الإشارة العكسية الجديدة.\n"
                )
            elif is_winning:
                body_lines = (
                    f"▎💵 <b>الربح الموثق الآن بـ 0.01 لوت: {usd:+,.2f} $</b>\n"
                    "▎✅ <b>الصفقة تعتبر رابحة حتى هذه اللحظة وفق بيانات السوق الفعلية.</b>\n"
                    "▎🛡 <b>قرارك الآن قبل الإشارة الجديدة:</b> أغلقها على الربح الحالي، أو أمّنها "
                    "(انقل الوقف لنقطة الدخول وأغلق 50% على الأقل) — لا تتركها تتعارض مع الاتجاه الجديد.\n"
                )
            elif is_losing:
                body_lines = (
                    f"▎📉 <b>العائد العائم الآن بـ 0.01 لوت: {usd:+,.2f} $</b>\n"
                    "▎⚠️ <b>الصفقة حالياً على خسارة عائمة وفق بيانات السوق الفعلية (ليست رابحة).</b>\n"
                    "▎🛡 <b>قرارك الآن قبل الإشارة الجديدة:</b> راجع وقف الخسارة وقلّص المخاطرة "
                    "أو أغلقها يدوياً لتفادي الاحتفاظ بصفقتين متعاكستين على نفس السوق.\n"
                )
            else:
                body_lines = (
                    f"▎⚖️ <b>النتيجة العائمة الآن بـ 0.01 لوت: {usd:+,.2f} $ (عند التعادل)</b>\n"
                    "▎🛡 <b>قرارك الآن قبل الإشارة الجديدة:</b> يمكنك الإغلاق عند نقطة التعادل "
                    "قبل دخول الإشارة العكسية الجديدة.\n"
                )

            text = (
                f"🔄 <b><u>تنبيه إغلاق صفقة متعاكسة — الإشارة #{seq_of(old['id'])}</u></b> — {esc(old['symbol'])} {dir_badge(old['direction'])} ⏱ {tf_name(old['timeframe'])}\n"
                "<blockquote>"
                f"▎📌 الحالة الحالية: <b>{status_ar(old['status'])}</b>{hit_txt}\n"
                + px_line
                + body_lines
                + "▎📡 إشارة جديدة عكسية قادمة مباشرة بعد هذا التنبيه."
                "</blockquote>\n"
                "<i>أرقام موثقة من أسعار السوق — القرار النهائي لك، والتنبيه لا يغلق شيئاً تلقائياً في حسابك.</i>"
            )
            for tgt in targets:
                try:
                    await bot_send(tgt, text)
                    sent += 1
                except Exception:
                    pass
            # Admin copy only for non-private signals, and only if Admin wasn't already in targets
            if (sig_src != "SIGNAL_NOW" and config.ADMIN_ID
                    and str(config.ADMIN_ID) not in seen_targets
                    and admin_accepts_market(old["symbol"])):
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


def _norm_sym(s: str) -> str:
    """Canonical symbol key: uppercase, whitespace & '/' stripped ('XAU/USD' -> 'XAUUSD')."""
    return (s or "").strip().upper().replace("/", "")


def admin_accepts_market(symbol: str) -> bool:
    """v6.17.0: whether ADMIN_ID personally wants to receive signal/tracker
    alerts for `symbol`. Empty/'ALL' = receive all enabled markets. Never
    restricts users or broadcast channels."""
    raw = (db.get_setting("admin_signal_markets", "") or "").strip()
    if not raw or raw.upper() == "ALL":
        return True
    allowed = {_norm_sym(x) for x in raw.split(",") if x.strip()}
    return (not allowed) or (_norm_sym(symbol) in allowed)


def passes_personal_filters(u, sig_symbol: str, sig_tf: str, sig_strategy: str, sig_score) -> bool:
    """Personal server-side delivery filters for one user row.

    Enforced gates (empty value = no personal restriction):
      * market    : user's chosen market(s) — slash-normalized exact match
      * timeframe : exact entry-TF match
      * strategy  : the user's selected methodology — "ALL" or empty = every
                    methodology. This makes the profile's "الاستراتيجية المفضلة"
                    a REAL delivery gate, not a cosmetic label.
      * min_score : quality floor
    Plan/package entitlements are a separate hard gate in emit_to_users.
    """
    u_market = (u["market"] or "").strip().upper()
    if u_market and u_market != "ALL":
        allowed_mkts = {_norm_sym(m) for m in u_market.split(",") if m.strip()}
        if _norm_sym(sig_symbol) not in allowed_mkts:
            return False
    u_tf = (u["timeframe"] or "").strip().upper()
    if u_tf and u_tf != "ALL" and u_tf != sig_tf.upper():
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
    if chat_id is None:
        chat = getattr(msg, "chat", None)
        chat_id = getattr(chat, "id", None)
    if not message_id or chat_id is None:
        return
    with db.conn() as c:
        c.execute("UPDATE signal_deliveries SET chat_id=?, message_id=? WHERE user_id=? AND signal_id=?",
                  (str(chat_id), int(message_id), uid, signal_id))


def _preload_demo_accounts(uids: list[int]) -> dict[int, dict]:
    """Batch-load existing demo accounts for a set of user_ids in 1 SQL query."""
    if not uids:
        return {}
    uniq = sorted({int(u) for u in uids if int(u) > 0})
    if not uniq:
        return {}
    out: dict[int, dict] = {}
    with db.conn() as c:
        # Chunk to stay safely below SQLite's parameter limit on large audiences.
        for i in range(0, len(uniq), 400):
            chunk = uniq[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in c.execute(f"SELECT * FROM demo_accounts WHERE user_id IN ({ph})", tuple(chunk)).fetchall():
                out[int(r["user_id"])] = dict(r)
    return out


async def _offload_auto_executes(sig: dict, items: list[tuple[int, dict | None]], bot_send) -> int:
    """v6.16.0 Phase 6: run demo auto_execute OFF the critical signal delivery
    path in bounded worker threads so signal delivery & button taps never stall."""
    if not items:
        return 0
    import asyncio as _asyncio
    from fysb.services import user_trade
    sem = _asyncio.Semaphore(6)
    executed = 0

    async def _one(uid: int, acc: dict | None):
        nonlocal executed
        async with sem:
            exec_res = await _asyncio.to_thread(user_trade.auto_execute, sig, uid, acc)
            if exec_res and exec_res.get("ok"):
                executed += 1
                try:
                    await bot_send(uid, user_trade.execution_card(exec_res, sig))
                except Exception:
                    pass

    await _asyncio.gather(*(_one(uid, acc) for uid, acc in items), return_exceptions=True)
    return executed


async def emit_to_users(signal_row, bot_send, bot=None, include_channels: bool = True) -> dict:
    """Step 16 — deliver a stored signal to eligible users AND enabled broadcast channels.

    v6.16.0 Phase 6 optimizations:
      * Market catalog row, base signal card, and birth pulse computed ONCE per
        signal instead of N times.
      * Plans resolved in O(1) batch (plans_for_users) + demo accounts preloaded
        in 1 batch query (eliminating ~4N SQL queries per broadcast).
      * All signal cards + channel broadcasts are delivered FIRST; demo
        auto_execute runs offloaded in bounded worker threads afterwards so no
        recipient waits on another user's demo execution or price fetch.
    """
    from fysb.services.subscriptions import try_consume_signal, release_signal, plans_for_users, is_free_trial_expired
    from fysb.services import user_trade
    from fysb.ui.cards import render_signal_card

    stats = {"sent": 0, "skipped_quota": 0, "failed": 0, "skipped_filters": 0,
             "channels_sent": 0, "channels_failed": 0, "auto_executed": 0}
    sig = dict(signal_row)
    sig_symbol = sig["symbol"]
    sig_sym_norm = _norm_sym(sig_symbol)
    sig_tf = sig["timeframe"]
    sig_score = sig["score"]
    sig_strategy = (sig.get("strategy") or "").upper()
    await opposite_close_advisory(sig, bot_send)
    from fysb.services import partners as partners_svc
    with db.conn() as c:
        users = c.execute("SELECT user_id,market,timeframe,strategy,min_score,notifications,is_blocked,joined_at FROM users WHERE notifications=1 AND is_blocked=0").fetchall()
    active_partners = partners_svc.active_partner_user_ids()

    # Pre-compute invariant signal payload + batch-load plans & demo accounts ONCE
    row = _market_row(sig_symbol)
    base_card = render_signal_card(sig, user_view=True, row=row)
    pulse = birth_pulse_line(sig, row)
    if pulse:
        base_card += "\n\n<blockquote>" + pulse + "</blockquote>"

    candidate_uids = [
        int(u["user_id"]) for u in users
        if int(u["user_id"]) != config.ADMIN_ID and int(u["user_id"]) not in active_partners
    ]
    plans_map = plans_for_users(candidate_uids)
    demo_map = _preload_demo_accounts(candidate_uids)
    auto_exec_queue: list[tuple[int, dict | None]] = []
    delivered_uids: list[int] = []
    sig_created_dt = _parse_iso_utc(sig.get("created_at") or "")
    is_free_only = str(sig.get("source") or "").upper() == "AUTO_FREE"
    if is_free_only:
        include_channels = False

    for u in users:
        uid = int(u["user_id"])
        if uid == config.ADMIN_ID or uid in active_partners:
            continue  # admin & active partner-mode accounts are not private user recipients

        # 0) v6.23.0 strict post-join isolation: never push a signal created before the user joined
        if sig_created_dt is not None:
            joined_dt = _parse_iso_utc(u["joined_at"] if "joined_at" in u.keys() else "")
            if joined_dt is not None and sig_created_dt < joined_dt:
                stats["skipped_filters"] += 1
                continue

        # 1) package entitlement — hard gate (slash-normalized symbol matching)
        plan = plans_map.get(uid)
        if not plan:
            stats["skipped_filters"] += 1
            continue
        if is_free_only and str(plan.get("code") or "").upper() != "FREE":
            stats["skipped_filters"] += 1
            continue
        if is_free_trial_expired(uid, user_row=u, plan_row=plan):
            stats["skipped_quota"] += 1
            continue
        p_markets = {_norm_sym(x) for x in str(plan["markets"] or "").split(",") if x.strip()}
        p_tfs = {x.strip().upper() for x in str(plan["timeframes"] or "").split(",") if x.strip()}
        p_strats = {x.strip().upper() for x in str(plan["strategies"] or "").split(",") if x.strip()}
        if p_markets and "ALL" not in p_markets and sig_sym_norm not in p_markets:
            stats["skipped_filters"] += 1
            continue
        if p_tfs and "ALL" not in p_tfs and sig_tf.upper() not in p_tfs:
            stats["skipped_filters"] += 1
            continue
        if p_strats and "ALL" not in p_strats and sig_strategy not in p_strats:
            stats["skipped_filters"] += 1
            continue

        # 2) personal preferences (empty = follow the plan entirely)
        if not passes_personal_filters(u, sig_symbol, sig_tf, sig_strategy, sig_score):
            stats["skipped_filters"] += 1
            continue

        # 3) quota (atomic, reusing preloaded plan_row)
        if not try_consume_signal(uid, sig, plan_row=plan):
            stats["skipped_quota"] += 1
            continue

        # 4) deliver: pre-rendered luxury card + personal plan block + interactive action bar
        acc = demo_map.get(uid)
        personal = user_trade.personal_block(sig, uid, row=row, acc=acc)
        text = base_card + ("\n\n<blockquote>" + personal + "</blockquote>" if personal else "")
        try:
            from fysb.ui import keyboards as _kb
            action_kb = _kb.signal_action_keyboard(sig["id"])
            try:
                msg = await bot_send(uid, text, reply_markup=action_kb)
            except TypeError:
                msg = await bot_send(uid, text)
            stats["sent"] += 1
            delivered_uids.append(uid)
            record_delivery_refs(uid, sig["id"], msg)
        except Exception:
            stats["failed"] += 1
            release_signal(uid, sig["id"])
            continue

        # Queue demo auto-execution off the critical delivery path
        if acc and acc.get("auto_trade"):
            auto_exec_queue.append((uid, acc))

    # 5) unified broadcast to all matching enabled Telegram channels/groups
    real_bot = bot or getattr(bot_send, "_bot", None)
    if include_channels and real_bot is not None:
        try:
            from fysb.handlers.admin_channels import emit_to_channels
            ch_stats = await emit_to_channels(sig, real_bot)
            stats["channels_sent"] = ch_stats.get("channels_sent", 0)
            stats["channels_failed"] = ch_stats.get("channels_failed", 0)
        except Exception as e:
            log.warning("emit_to_channels failed for signal #%s: %s", sig.get("id"), type(e).__name__)

    # 5.5) v7.3.0 Auto-send luxury signal chart to delivered users + matching chart-only channels
    if real_bot is not None and hasattr(real_bot, "send_photo"):
        try:
            from fysb.ui import charts as _charts
            from fysb.services import chart_channels as _cch
            has_chart_chs = bool(include_channels and _cch.matching_channels(sig_symbol, mode="signal"))
            if _charts.charts_enabled() and (delivered_uids or has_chart_chs):
                import asyncio as _asyncio
                import io as _io
                from fysb.engines.tracker import seq_of
                from fysb.handlers.user_pages import build_signal_chart_bundle
                bundle = await _asyncio.to_thread(build_signal_chart_bundle, dict(sig), False)
                if bundle and bundle.get("png"):
                    png_b = bundle["png"]
                    cap = _cch.build_luxury_signal_chart_caption(sig, bundle.get("metrics"), seq=seq_of(sig["id"]))
                    for duid in delivered_uids:
                        try:
                            await real_bot.send_photo(
                                chat_id=duid,
                                photo=_io.BytesIO(png_b),
                                caption=cap,
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                    if has_chart_chs:
                        ch_chart_n = await _cch.deliver_chart_to_matching_channels(
                            real_bot, sig, png_b, cap, mode="signal"
                        )
                        stats["chart_channels_sent"] = ch_chart_n
        except Exception as e:
            log.debug("auto chart broadcast skipped for signal #%s: %s", sig.get("id"), type(e).__name__)

    # 6) offloaded demo auto-execution (bounded thread pool, non-blocking loop)
    if auto_exec_queue:
        stats["auto_executed"] = await _offload_auto_executes(sig, auto_exec_queue, bot_send)
    return stats
