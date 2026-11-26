"""Institutional Market Intelligence Suite (v6.25.0 — 100% Free & Real).

Provides six measurable, non-hallucinated institutional layers:
  #3  Adaptive Strategy Weighting   — dynamic score boost/trim from real 30d P/L
  #4  Institutional Confluence Matrix — multi-strategy + HTF agreement grading
  #5  Live DXY / USD Correlation    — real DX-Y.NYB bias vs USD-sensitive pairs
  #6  Killzone Session Intelligence — London / NY / Overlap liquidity bonuses
  #7  Liquidity Sweep & Stop-Hunt   — wick-sweep + reclaim close confirmation
  #12 Currency Strength Meter (8)   — live USD/EUR/GBP/JPY/CHF/CAD/AUD/NZD rank
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.config import log
from fysb.theme import bar, esc

_LOCK = threading.Lock()
_DXY_CACHE: dict = {"ts": 0.0, "trend": "NEUTRAL", "pct": 0.0}
_STRENGTH_CACHE: dict = {"ts": 0.0, "rows": []}

_DXY_TTL = 600       # 10 min
_STRENGTH_TTL = 300  # 5 min

# USD-quoted assets move INVERSELY to DXY; USD-base pairs move WITH DXY
_USD_QUOTE_SYMS = {
    "XAUUSD", "XAGUSD", "XPTUSD", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD", "ADAUSD", "DOGEUSD",
    "US30", "NAS100", "SPX500",
}
_USD_BASE_SYMS = {"USDJPY", "USDCAD", "USDCHF"}

_MAJORS_8 = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
_CCY_META = {
    "USD": ("🇺🇸", "الدولار الأمريكي"),
    "EUR": ("🇪🇺", "اليورو"),
    "GBP": ("🇬🇧", "الجنيه الإسترليني"),
    "JPY": ("🇯🇵", "الين الياباني"),
    "CHF": ("🇨🇭", "الفرنك السويسري"),
    "CAD": ("🇨🇦", "الدولار الكندي"),
    "AUD": ("🇦🇺", "الدولار الأسترالي"),
    "NZD": ("🇳🇿", "الدولار النيوزيلندي"),
}
# Yahoo symbols vs USD: (yahoo_ticker, base, quote)
_STRENGTH_PAIRS = (
    ("EURUSD=X", "EUR", "USD"),
    ("GBPUSD=X", "GBP", "USD"),
    ("AUDUSD=X", "AUD", "USD"),
    ("NZDUSD=X", "NZD", "USD"),
    ("USDJPY=X", "USD", "JPY"),
    ("USDCAD=X", "USD", "CAD"),
    ("USDCHF=X", "USD", "CHF"),
)


def reset_caches() -> None:
    with _LOCK:
        _DXY_CACHE.update(ts=0.0, trend="NEUTRAL", pct=0.0)
        _STRENGTH_CACHE.update(ts=0.0, rows=[])


# ---------------------------------------------------------------------------
# #3 Adaptive Strategy Weighting & Auto-Quarantine (v8.8.0 — R-Multiple Exact)
# ---------------------------------------------------------------------------

def _signal_r_multiple(row) -> float:
    """Compute real R-Multiple for a closed signal row (immune to cross-asset pip scale!)."""
    from fysb import instruments
    pips = float(row["pips_result"] or 0.0)
    st = str(row["status"] or "").upper()
    try:
        entry = float(row["entry"] or 0.0)
        stop = float(row["stop"] or 0.0)
        sym = str(row["symbol"] or "XAUUSD")
        sl_pips = abs(instruments.pips(sym, entry, stop)) if (entry > 0 and stop > 0 and entry != stop) else 0.0
    except Exception:
        sl_pips = 0.0
    if sl_pips > 1e-6 and pips != 0.0:
        return max(-2.0, min(10.0, pips / sl_pips))
    if st == "TP3_HIT":
        return 4.5
    if st in ("TP2_HIT", "TP2_BE"):
        return 3.0
    if st in ("TP1_HIT", "TP1_BE"):
        return 2.0
    if st in ("SL_HIT", "LOSS"):
        return -1.0
    return 0.0


def strategy_quarantine_status(
    strategy_code: str,
    symbol: str = "",
    lookback_days: int = 14,
) -> dict:
    """v8.8.0 Institutional Auto-Quarantine Guard.

    Audits real closed official signals over the trailing `lookback_days`.
    If a strategy has >= 6 closed trades AND Win Rate < 35% AND R-Profit-Factor < 0.85,
    it is automatically quarantined from AUTO publishing until performance recovers
    or the window ages out — stopping bleeding strategies from wrecking live results.
    """
    code = (strategy_code or "").upper().strip()
    if not code:
        return {"quarantined": False, "trades": 0, "win_rate": 0.0, "pf": 0.0, "net_r": 0.0, "reason": ""}
    if (db.get_setting("strategy_auto_quarantine", "1") or "1") != "1":
        return {"quarantined": False, "trades": 0, "win_rate": 0.0, "pf": 0.0, "net_r": 0.0, "reason": ""}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(7, int(lookback_days)))).isoformat()
    try:
        with db.conn() as c:
            rows = c.execute(
                """SELECT symbol, entry, stop, status, pips_result FROM signals
                   WHERE UPPER(strategy)=?
                     AND (source IS NULL OR UPPER(source) != 'SIGNAL_NOW')
                     AND status IN ('TP1_HIT','TP2_HIT','TP3_HIT','TP1_BE','TP2_BE','SL_HIT')
                     AND closed_at!='' AND closed_at>=?""",
                (code, cutoff),
            ).fetchall()
    except Exception:
        return {"quarantined": False, "trades": 0, "win_rate": 0.0, "pf": 0.0, "net_r": 0.0, "reason": ""}

    n = len(rows)
    if n < 6:
        return {"quarantined": False, "trades": n, "win_rate": 0.0, "pf": 0.0, "net_r": 0.0, "reason": ""}

    wins = 0
    gross_win_r = 0.0
    gross_loss_r = 0.0
    for r in rows:
        r_mult = _signal_r_multiple(r)
        st = str(r["status"] or "").upper()
        if st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE") and r_mult >= 0:
            wins += 1
            gross_win_r += max(r_mult, 0.5)
        else:
            gross_loss_r += max(abs(r_mult), 1.0)

    wr = (wins / n) * 100.0
    pf = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 3.0
    net_r = round(gross_win_r - gross_loss_r, 2)
    quarantined = (wr < 35.0 and pf < 0.85)
    reason = (
        f"حماية ذكية (Auto-Quarantine): استراتيجية {code} موقوفة مؤقتاً "
        f"(نجاح {wr:.0f}% · PF {pf:.2f} · صافي {net_r:+.1f}R من {n} صفقات)"
        if quarantined else ""
    )
    return {
        "quarantined": quarantined,
        "strategy": code,
        "trades": n,
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "net_r": net_r,
        "reason": reason,
    }


def list_quarantined_strategies(lookback_days: int = 14) -> list[dict]:
    """Return all currently auto-quarantined strategies in the trailing window."""
    if (db.get_setting("strategy_auto_quarantine", "1") or "1") != "1":
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(7, int(lookback_days)))).isoformat()
    try:
        with db.conn() as c:
            codes = [
                str(r["s"]).upper()
                for r in c.execute(
                    """SELECT DISTINCT UPPER(strategy) AS s FROM signals
                       WHERE (source IS NULL OR UPPER(source) != 'SIGNAL_NOW')
                         AND status IN ('TP1_HIT','TP2_HIT','TP3_HIT','TP1_BE','TP2_BE','SL_HIT')
                         AND closed_at!='' AND closed_at>=?""",
                    (cutoff,),
                ).fetchall()
                if r["s"]
            ]
    except Exception:
        return []
    out = []
    for code in sorted(codes):
        st = strategy_quarantine_status(code, lookback_days=lookback_days)
        if st.get("quarantined"):
            out.append(st)
    return out


def adaptive_strategy_weight(strategy_code: str, symbol: str = "", lookback_days: int = 30) -> dict:
    """Compute bounded [-8.0 .. +7.0] score adjustment from real 30-day R-Multiple results.

    Uses true R-Multiples (so BTCUSD pip scale can never distort Forex/Gold evaluation).
    Requires >= 5 closed trades for statistical relevance; otherwise neutral (0.0).
    """
    code = (strategy_code or "").upper().strip()
    if not code:
        return {"delta": 0.0, "trades": 0, "win_rate": 0.0, "pf": 0.0, "note": ""}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(7, lookback_days))).isoformat()
    try:
        with db.conn() as c:
            if symbol:
                rows = c.execute(
                    """SELECT symbol, entry, stop, status, pips_result FROM signals
                       WHERE UPPER(strategy)=? AND REPLACE(UPPER(symbol),'/','')=?
                         AND (source IS NULL OR UPPER(source) != 'SIGNAL_NOW')
                         AND status IN ('TP1_HIT','TP2_HIT','TP3_HIT','TP1_BE','TP2_BE','SL_HIT')
                         AND closed_at!='' AND closed_at>=?""",
                    (code, symbol.upper().replace("/", ""), cutoff),
                ).fetchall()
            else:
                rows = []
            if len(rows) < 5:
                rows = c.execute(
                    """SELECT symbol, entry, stop, status, pips_result FROM signals
                       WHERE UPPER(strategy)=?
                         AND (source IS NULL OR UPPER(source) != 'SIGNAL_NOW')
                         AND status IN ('TP1_HIT','TP2_HIT','TP3_HIT','TP1_BE','TP2_BE','SL_HIT')
                         AND closed_at!='' AND closed_at>=?""",
                    (code, cutoff),
                ).fetchall()
    except Exception:
        return {"delta": 0.0, "trades": 0, "win_rate": 0.0, "pf": 0.0, "note": ""}

    n = len(rows)
    if n < 5:
        return {"delta": 0.0, "trades": n, "win_rate": 0.0, "pf": 0.0, "note": ""}

    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    for r in rows:
        rm = _signal_r_multiple(r)
        st = str(r["status"] or "").upper()
        if st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE") and rm >= 0:
            wins += 1
            gross_win += max(rm, 0.5)
        else:
            gross_loss += max(abs(rm), 1.0)

    wr = wins / n
    pf = gross_win / gross_loss if gross_loss > 0 else (3.0 if gross_win > 0 else 1.0)
    raw_delta = (wr - 0.50) * 14.0 + max(-3.5, min(3.5, (pf - 1.25) * 2.2))
    delta = round(max(-8.0, min(7.0, raw_delta)), 1)
    note = f"adaptive({code}: WR {wr*100:.0f}%, PF {pf:.2f}R, n={n} => {delta:+.1f})"
    return {"delta": delta, "trades": n, "win_rate": round(wr * 100, 1), "pf": round(pf, 2), "note": note}


# ---------------------------------------------------------------------------
# #4 Institutional Confluence Matrix
# ---------------------------------------------------------------------------

def build_confluence_summary(
    results: list,
    winning_direction: str,
    entry_tf: str = "M15",
    bias_trend: str = "",
) -> dict:
    """Count agreeing strategies + HTF alignment and assign institutional grade."""
    d = (winning_direction or "").upper()
    aligned = [r for r in (results or []) if getattr(r, "direction", "").upper() == d]
    aligned.sort(key=lambda r: float(getattr(r, "score", 0)), reverse=True)
    codes = [str(getattr(r, "code", "")).upper() for r in aligned]
    cnt = len(codes)
    bt = (bias_trend or "").lower()
    htf_ok = (d == "BUY" and bt == "up") or (d == "SELL" and bt == "down")

    if cnt >= 3 and htf_ok:
        grade = "A+ ملكي"
        bonus = 4.0
    elif cnt >= 2 and htf_ok:
        grade = "A قوي"
        bonus = 2.5
    elif cnt >= 2 or htf_ok:
        grade = "B+ متوازن"
        bonus = 1.0
    else:
        grade = "B جيد"
        bonus = 0.0

    htf_ar = "متوافق مع الفريم الأعلى ✅" if htf_ok else "محايد مع الفريم الأعلى"
    label = f"توافق {cnt} استراتيجية ({', '.join(codes[:4])}) · {grade} · {htf_ar}"
    return {
        "count": cnt,
        "codes": codes,
        "htf_aligned": htf_ok,
        "grade": grade,
        "bonus": bonus,
        "label": label,
    }


def format_confluence_from_scores(strategy_scores_raw: str, winning_code: str = "") -> str:
    """Render a compact Confluence Matrix badge on signal cards from stored JSON."""
    if not strategy_scores_raw:
        return ""
    try:
        data = json.loads(strategy_scores_raw) if isinstance(strategy_scores_raw, str) else dict(strategy_scores_raw)
    except Exception:
        return ""
    if not isinstance(data, dict) or not data:
        return ""
    ranked = sorted(data.items(), key=lambda kv: float(kv[1] or 0), reverse=True)
    cnt = len(ranked)
    top_names = " · ".join(str(k) for k, _ in ranked[:4])
    if cnt >= 4:
        badge = "🏆 A+ ملكي"
    elif cnt >= 2:
        badge = "💎 A قوي"
    else:
        badge = "✅ B موثّق"
    return f"🧩 <b>التوافق المؤسسي:</b> <b>{badge}</b> ({cnt}/13: <code>{esc(top_names)}</code>)"


# ---------------------------------------------------------------------------
# #5 Live DXY / USD Correlation Filter
# ---------------------------------------------------------------------------

def _fetch_yahoo_day_pct(yahoo_ticker: str) -> float | None:
    """Fetch 24h percentage change for a Yahoo Finance ticker (keyless, fast)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}?range=2d&interval=1h"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"FOREXYEMENI/{config.VERSION}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return None
    meta = res[0].get("meta") or {}
    px = float(meta.get("regularMarketPrice") or 0.0)
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0)
    if px > 0 and prev > 0:
        return ((px - prev) / prev) * 100.0
    closes = [c for c in (((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if c]
    if len(closes) >= 2 and closes[0] > 0:
        return ((float(closes[-1]) - float(closes[0])) / float(closes[0])) * 100.0
    return None


def set_dxy_snapshot(pct: float, ts: float | None = None) -> None:
    """Inject a DXY % change directly (used in deterministic tests)."""
    trend = "UP" if pct >= 0.15 else ("DOWN" if pct <= -0.15 else "NEUTRAL")
    with _LOCK:
        _DXY_CACHE.update(ts=time.time() if ts is None else float(ts), trend=trend, pct=float(pct))


def get_dxy_snapshot() -> dict:
    now_t = time.time()
    with _LOCK:
        if _DXY_CACHE["ts"] > 0 and (now_t - _DXY_CACHE["ts"]) < _DXY_TTL:
            return dict(_DXY_CACHE)
    try:
        pct = _fetch_yahoo_day_pct("DX-Y.NYB")
        if pct is not None:
            set_dxy_snapshot(pct, now_t)
            with _LOCK:
                return dict(_DXY_CACHE)
    except Exception as exc:
        log.debug("DXY fetch unavailable (neutral fallback): %s", exc)
    with _LOCK:
        _DXY_CACHE["ts"] = now_t - _DXY_TTL + 180
        return dict(_DXY_CACHE)


def dxy_bias_check(symbol: str, direction: str) -> dict:
    """Compare live DXY trend against a BUY/SELL candidate on a USD pair."""
    sym = (symbol or "").upper().replace("/", "").replace("-", "")
    d = (direction or "").upper()
    snap = get_dxy_snapshot()
    trend = snap.get("trend", "NEUTRAL")
    pct = float(snap.get("pct") or 0.0)
    if trend == "NEUTRAL":
        return {"aligned": None, "delta": 0.0, "dxy_pct": pct, "note": ""}

    if sym in _USD_QUOTE_SYMS:
        # Inverse to DXY: DXY DOWN supports BUY, DXY UP supports SELL
        aligned = (d == "BUY" and trend == "DOWN") or (d == "SELL" and trend == "UP")
    elif sym in _USD_BASE_SYMS:
        # Direct with DXY: DXY UP supports BUY, DXY DOWN supports SELL
        aligned = (d == "BUY" and trend == "UP") or (d == "SELL" and trend == "DOWN")
    else:
        return {"aligned": None, "delta": 0.0, "dxy_pct": pct, "note": ""}

    delta = 3.0 if aligned else -3.0
    arrow = "صاعد" if trend == "UP" else "هابط"
    note = (
        f"DXY {arrow} ({pct:+.2f}%) {'يدعم' if aligned else 'يعاكس'} {d} على {sym}"
    )
    return {"aligned": aligned, "delta": delta, "dxy_pct": pct, "note": note}


# ---------------------------------------------------------------------------
# #6 Killzone Session Intelligence
# ---------------------------------------------------------------------------

def current_killzone(now_utc: datetime | None = None) -> dict:
    """Institutional UTC Killzone session classification + quality bonus."""
    dt = now_utc or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    h = dt.hour
    if 12 <= h < 16:
        return {"code": "LONDON_NY_OVERLAP", "label": "🔥 تقاطع لندن–نيويورك (ذروة السيولة)", "bonus": 4.0}
    if 7 <= h < 12:
        return {"code": "LONDON", "label": "🇬🇧 جلسة لندن (Killzone)", "bonus": 3.0}
    if 16 <= h < 20:
        return {"code": "NEW_YORK", "label": "🇺🇸 جلسة نيويورك (Killzone)", "bonus": 3.0}
    if 0 <= h < 6:
        return {"code": "ASIA", "label": "🇯🇵 جلسة آسيا", "bonus": 0.0}
    return {"code": "OFF_SESSION", "label": "🌙 خارج ذروة السيولة", "bonus": -1.0}


# ---------------------------------------------------------------------------
# #7 Liquidity Sweep & Stop-Hunt Detector
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(candles: list[dict], lookback: int = 20) -> dict:
    """Detect a genuine stop-hunt sweep on the most recent confirmed candle.

    * Bullish sweep (`BUY`): candle wick pierces below prior `lookback` swing low
      AND closes decisively back above that swing low (bullish reclaim).
    * Bearish sweep (`SELL`): candle wick pierces above prior `lookback` swing high
      AND closes decisively back below that swing high (bearish reclaim).
    """
    if not candles or len(candles) < lookback + 2:
        return {"detected": False, "direction": "", "level": 0.0, "bonus": 0.0, "note": ""}
    prior = candles[-(lookback + 1):-1]
    last = candles[-1]
    prior_hi = max(float(c["high"]) for c in prior)
    prior_lo = min(float(c["low"]) for c in prior)
    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c_px = float(last["close"])
    rng = max(h - l, 1e-9)

    # Bullish stop-hunt below prior low + strong reclaim close
    if l < prior_lo and c_px > prior_lo and ((c_px - l) / rng) >= 0.55:
        return {
            "detected": True,
            "direction": "BUY",
            "level": prior_lo,
            "bonus": 3.5,
            "note": f"سحب سيولة صاعد (Stop-Hunt أسفل {prior_lo:.5g} وإغلاق داخلي قوي)",
        }
    # Bearish stop-hunt above prior high + strong reclaim close
    if h > prior_hi and c_px < prior_hi and ((h - c_px) / rng) >= 0.55:
        return {
            "detected": True,
            "direction": "SELL",
            "level": prior_hi,
            "bonus": 3.5,
            "note": f"سحب سيولة هابط (Stop-Hunt أعلى {prior_hi:.5g} وإغلاق داخلي قوي)",
        }
    return {"detected": False, "direction": "", "level": 0.0, "bonus": 0.0, "note": ""}


# ---------------------------------------------------------------------------
# #12 Live Currency Strength Meter (8 Major Currencies)
# ---------------------------------------------------------------------------

def compute_strength_from_pairs(pairs_pct: dict[str, float]) -> list[dict]:
    """Pure math: compute normalized 0..100 strength score for currencies backed by REAL data.

    `pairs_pct` maps pair key like `"EURUSD"`, `"USDJPY"` -> 24h % change.
    Currencies with zero fetched pairs are excluded from ranking rather than
    silently scored as 0.0% (preventing artificial distortion when a pair fails).
    """
    raw: dict[str, list[float]] = {c: [] for c in _MAJORS_8}
    for _tk, base, quote in _STRENGTH_PAIRS:
        key = f"{base}{quote}"
        if key not in pairs_pct:
            continue
        pct = float(pairs_pct[key])
        raw[base].append(pct)
        raw[quote].append(-pct)

    present = [c for c in _MAJORS_8 if raw[c]]
    if not present:
        return []
    avgs = {c: (sum(raw[c]) / len(raw[c])) for c in present}
    vals = list(avgs.values())
    lo = min(vals)
    hi = max(vals)
    span = max(hi - lo, 0.20)

    ranked: list[dict] = []
    for c in present:
        avg = avgs[c]
        score = int(round(max(5, min(98, ((avg - lo) / span) * 85.0 + 8.0))))
        flag, name_ar = _CCY_META[c]
        ranked.append({
            "code": c,
            "flag": flag,
            "name_ar": name_ar,
            "score": score,
            "delta_pct": round(avg, 3),
        })
    ranked.sort(key=lambda r: (r["score"], r["delta_pct"]), reverse=True)
    return ranked


def _fetch_pair_day_pct_resilient(ticker: str, base: str, quote: str) -> tuple[str, float | None]:
    """Fetch 24h % move via Yahoo Finance with real fallback to `market_data.fetch_candles`."""
    pair_key = f"{base}{quote}"
    try:
        pct = _fetch_yahoo_day_pct(ticker)
        if pct is not None:
            return pair_key, pct
    except Exception:
        pass
    # Real fallback: TwelveData / market_data failover chain (24 hourly candles)
    try:
        from fysb.engines import market_data as _md
        payload = _md.fetch_candles(f"{base}/{quote}", "1h", api_key=config.market_api_key(), outputsize=26, use_cache=True)
        vals = list((payload or {}).get("values") or [])
        if len(vals) >= 2:
            c0 = float(vals[0].get("open") or vals[0].get("close") or 0.0)
            c1 = float(vals[-1].get("close") or 0.0)
            if c0 > 0 and c1 > 0:
                return pair_key, ((c1 - c0) / c0) * 100.0
    except Exception:
        pass
    return pair_key, None


def currency_strength_meter(force_refresh: bool = False) -> list[dict]:
    """Fetch live 24h % moves across the 7 USD majors in parallel and rank currencies."""
    from concurrent.futures import ThreadPoolExecutor
    now_t = time.time()
    with _LOCK:
        if not force_refresh and _STRENGTH_CACHE["rows"] and (now_t - _STRENGTH_CACHE["ts"]) < _STRENGTH_TTL:
            return list(_STRENGTH_CACHE["rows"])
    pairs_pct: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=len(_STRENGTH_PAIRS)) as pool:
        futs = [pool.submit(_fetch_pair_day_pct_resilient, tk, b, q) for tk, b, q in _STRENGTH_PAIRS]
        for fut in futs:
            try:
                k, pct = fut.result(timeout=10)
                if pct is not None:
                    pairs_pct[k] = pct
            except Exception:
                continue
    if not pairs_pct:
        with _LOCK:
            return list(_STRENGTH_CACHE["rows"])
    ranked = compute_strength_from_pairs(pairs_pct)
    with _LOCK:
        _STRENGTH_CACHE.update(ts=now_t, rows=ranked, pairs_count=len(pairs_pct))
    return list(ranked)


def render_strength_card(rows: list[dict] | None = None, *, force_refresh: bool = False) -> str:
    """Render an ultra-luxury Arabic 8-Currency Strength Meter board."""
    data = currency_strength_meter(force_refresh=force_refresh) if rows is None else rows
    if not data:
        return (
            "🧭 <b><u>مؤشر قوة العملات اللحظي (Currency Strength)</u></b>\n"
            "<blockquote>▎⚠️ تعذر الاتصال بمزود أسعار الفوركس اللحظية الآن (انقطاع مؤقت في المصدر) — اضغط «تحديث فوري» بعد لحظات.</blockquote>"
        )
    with _LOCK:
        p_cnt = int(_STRENGTH_CACHE.get("pairs_count") or len(_STRENGTH_PAIRS))
    medals = ("🥇", "🥈", "🥉")
    lines = [
        f"🧭 <b><u>مؤشر قوة العملات اللحظي ({len(data)} عملات رئيسية)</u></b>",
        "<blockquote>",
    ]
    for i, r in enumerate(data):
        m = medals[i] if i < 3 else ("🔻" if i >= len(data) - 2 else "•")
        b = bar(r["score"], 100, 8)
        lines.append(
            f"▎{m} {r['flag']} <b>{r['code']}</b>  {b}  <b>{r['score']}/100</b>  <i>({r['delta_pct']:+.2f}%)</i>"
        )
    strongest = data[0]
    weakest = data[-1]
    from fysb import config as _cfg
    now_mecca_s = _cfg.mecca_time_12h()
    lines.append("</blockquote>")
    lines.append(
        f"\n🎯 <b>أقوى تقاطع مؤسسي الآن:</b> "
        f"شراء {strongest['flag']} <b>{strongest['code']}</b> مقابل بيع {weakest['flag']} <b>{weakest['code']}</b>\n"
        f"<i>📡 محسوب رياضياً من حركة {p_cnt}/7 أزواج رئيسية حقيقية خلال 24 ساعة · آخر تحديث: {now_mecca_s}</i>"
    )
    return "\n".join(lines)
