"""Risk engine — server-side policy enforcement, not decoration.

Gates every signal before it is stored or delivered. All limits are settings
(admin-adjustable) and evaluated against REAL numbers (open signals, daily loss
on demo accounts, ATR% volatility).
"""
from dataclasses import dataclass

from fysb import db


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    metrics: dict = None

    def __post_init__(self):
        if self.metrics is None:
            self.metrics = {}


def _s(key: str, default: float) -> float:
    return db.get_setting_float(key, default)


_USD_QUOTE_FX = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}
_USD_BASE_FX = {"USDJPY", "USDCAD", "USDCHF"}
_JPY_CROSSES = {"EURJPY", "GBPJPY", "AUDJPY", "CADJPY"}
_METALS = {"XAUUSD", "XAGUSD", "XPTUSD"}
_CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD", "ADAUSD"}


def exposure_bucket(symbol: str, direction: str) -> str:
    """Directional correlation bucket for multi-market risk control (v6.20.0).
    BUY EURUSD + BUY GBPUSD + SELL USDJPY all map to 'USD_SHORT' so the engine
    never stacks hidden 3x+ identical currency exposure."""
    sym = (symbol or "").strip().upper().replace("/", "")
    d = (direction or "").strip().upper()
    if d not in ("BUY", "SELL"):
        return ""
    if sym in _USD_QUOTE_FX:
        return "USD_SHORT" if d == "BUY" else "USD_LONG"
    if sym in _USD_BASE_FX:
        return "USD_LONG" if d == "BUY" else "USD_SHORT"
    if sym in _JPY_CROSSES:
        return "JPY_SHORT" if d == "BUY" else "JPY_LONG"
    if sym in _METALS:
        return f"METALS_{d}"
    if sym in _CRYPTO:
        return f"CRYPTO_{d}"
    return ""


def daily_signal_pnl_pct(conn_obj=None) -> tuple[float, int]:
    """Realized net P&L (%) and SL count of today's closed public signals (UTC)."""
    risk_per = max(0.1, _s("risk_per_signal_pct", 1.0))
    day_start = db.day_start_utc()
    def _query(c):
        return c.execute(
            """SELECT * FROM signals
               WHERE COALESCE(private_user_id,0)=0 AND (source IS NULL OR UPPER(source) NOT IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED'))
                 AND closed_at>=? AND status IN ('SL_HIT','TP1_HIT','TP2_HIT','TP3_HIT','TP1_BE','TP2_BE')""",
            (day_start,),
        ).fetchall()
    if conn_obj is not None:
        rows = _query(conn_obj)
    else:
        with db.conn() as c:
            rows = _query(c)
    net_r = 0.0
    sl_hits = 0
    for r in rows:
        from fysb.engines.accounting import modeled_r
        value = modeled_r(dict(r))
        if value is not None:
            net_r += value
        if str(r["status"]).upper() == "SL_HIT" and int(r["hit_tp"] or 0) == 0:
            sl_hits += 1
    return round(net_r * risk_per, 2), sl_hits


def open_caps_snapshot() -> dict:
    """v8.11.0 (P5) — live visibility of the hidden issuance ceiling.

    The risk caps silently block NEW signals when full (12 open public signals
    by default, 3 per symbol, 3 correlated, daily-loss breaker). This snapshot
    exposes counts vs caps for the admin diagnostics panel so a full ceiling is
    never an invisible mystery again. Public AUTO rows only — the same query
    scope check_signal() enforces.
    """
    from collections import Counter
    from datetime import datetime, timezone
    max_per_sym = max(1, int(_s("max_open_per_symbol", 3)))
    max_open = max(1, int(_s("max_open_signals", 12)))
    max_corr = max(1, int(_s("max_correlated_signals", 3)))
    max_daily_loss = abs(_s("max_daily_loss_pct", 5.0))
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT symbol, direction, created_at FROM signals "
            "WHERE COALESCE(private_user_id,0)=0 AND (source IS NULL OR UPPER(source) NOT IN "
            "('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED')) "
            "AND (closed_at IS NULL OR closed_at='') "
            "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')"
        ).fetchall()]
        daily_pct, _sl_today = daily_signal_pnl_pct(c)
    per_symbol = Counter(
        (r["symbol"] or "").strip().upper().replace("/", "") for r in rows)
    buckets = Counter(
        exposure_bucket(r["symbol"], r["direction"]) for r in rows)
    oldest_h = 0.0
    now_dt = datetime.now(timezone.utc)
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["created_at"] or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            oldest_h = max(oldest_h, (now_dt - dt).total_seconds() / 3600.0)
        except (ValueError, TypeError):
            continue
    worst_sym, worst_sym_n = (per_symbol.most_common(1)[0] if per_symbol else ("", 0))
    worst_bucket, worst_bucket_n = (buckets.most_common(1)[0] if buckets else ("", 0))
    worst_bucket = worst_bucket or ""
    open_rows = []
    for r in rows[:12]:
        hours = 0.0
        try:
            dt = datetime.fromisoformat(str(r["created_at"] or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hours = max(0.0, round((now_dt - dt).total_seconds() / 3600.0, 1))
        except (ValueError, TypeError):
            pass
        open_rows.append({"symbol": (r["symbol"] or ""), "direction": (r["direction"] or ""),
                          "hours": hours})
    return {
        "open": len(rows),
        "cap_open": max_open,
        "ceiling_full": len(rows) >= max_open,
        "cap_per_symbol": max_per_sym,
        "worst_symbol": worst_sym,
        "worst_symbol_open": worst_sym_n,
        "symbol_full": worst_sym_n >= max_per_sym,
        "cap_correlated": max_corr,
        "worst_bucket": worst_bucket,
        "worst_bucket_open": worst_bucket_n,
        "correlated_full": bool(worst_bucket) and worst_bucket_n >= max_corr,
        "daily_pnl_pct": daily_pct,
        "max_daily_loss_pct": max_daily_loss,
        "daily_breached": daily_pct <= -max_daily_loss,
        "oldest_open_hours": round(oldest_h, 1),
        "open_rows": open_rows,
    }


def _parse_ok(ts) -> bool:
    try:
        datetime.fromisoformat(str(ts or "").replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def check_signal(symbol: str, rr: float, atr_pct_value: float,
                 source: str = "AUTO", created_by: int = 0,
                 direction: str = "") -> RiskDecision:
    """Signal-issuance gate (signals only — no live exposure exists).

    v6.20.0 Institutional Multi-Market Risk Gates:
      * max_open_per_symbol (default 3): caps open signals on the SAME symbol.
      * max_open_signals (default 12): global ceiling across all markets.
      * max_correlated_signals (default 3): prevents stacking hidden identical
        currency/asset exposure (e.g. BUY EURUSD + BUY GBPUSD + SELL USDJPY).
      * Daily realized loss circuit breaker (max_daily_loss_pct): pauses new
        automatic signals if today's realized net signal drawdown breaches the
        configured daily loss cap.
      * ADMIN_SIGNAL_NOW: Admin's on-demand «⚡ إشارة الآن» is EXEMPT from open,
        correlation, and daily-loss caps (still enforces RR & volatility).
      * SIGNAL_NOW: regular user's private «⚡ إشارة الآن» is isolated from
        the global AUTO engine and checked only against that user's own open
        SIGNAL_NOW rows.
    """
    metrics = {}
    if rr < _s("min_rr", 2.0):
        return RiskDecision(False, f"نسبة العائد/المخاطرة {rr:.2f} أقل من الحد الأدنى {_s('min_rr', 2.0):.1f}", metrics)
    metrics["rr"] = rr
    src = (source or "AUTO").upper()
    sym_norm = (symbol or "").strip().upper().replace("/", "")
    max_per_sym = max(1, int(_s("max_open_per_symbol", 3)))
    max_open = max(1, int(_s("max_open_signals", 12)))
    max_corr = max(1, int(_s("max_correlated_signals", 3)))
    max_daily_loss = abs(_s("max_daily_loss_pct", 5.0))

    if src == "ADMIN_SIGNAL_NOW":
        metrics["open_signals"] = 0
        metrics["open_symbol_signals"] = 0
        metrics["cap_exempt"] = True
    else:
        with db.conn() as c:
            if src in ("SIGNAL_NOW", "TRUE_ANALYSIS_INSTANT", "TRUE_ANALYSIS_CONFIRMED") and int(created_by or 0) > 0:
                open_sym = c.execute(
                    "SELECT COUNT(*) n FROM signals WHERE (created_by=? OR private_user_id=?) "
                    "AND REPLACE(UPPER(symbol),'/','')=? "
                    "AND (closed_at IS NULL OR closed_at='') "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                    (int(created_by), int(created_by), sym_norm),
                ).fetchone()["n"]
                open_count = c.execute(
                    "SELECT COUNT(*) n FROM signals WHERE (created_by=? OR private_user_id=?) "
                    "AND (closed_at IS NULL OR closed_at='') "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                    (int(created_by), int(created_by)),
                ).fetchone()["n"]
                corr_open = 0
                daily_pct = 0.0
            else:
                open_rows = c.execute(
                    "SELECT symbol, direction FROM signals "
                    "WHERE COALESCE(private_user_id,0)=0 AND (source IS NULL OR UPPER(source) NOT IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED')) "
                    "AND (closed_at IS NULL OR closed_at='') "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')"
                ).fetchall()
                open_count = len(open_rows)
                open_sym = sum(
                    1 for r in open_rows
                    if (r["symbol"] or "").strip().upper().replace("/", "") == sym_norm
                )
                bucket = exposure_bucket(sym_norm, direction)
                corr_open = (
                    sum(1 for r in open_rows if exposure_bucket(r["symbol"], r["direction"]) == bucket)
                    if bucket else 0
                )
                daily_pct, _sl_today = daily_signal_pnl_pct(c)
        metrics["open_symbol_signals"] = open_sym
        metrics["open_signals"] = open_count
        metrics["correlated_signals"] = corr_open
        metrics["daily_pnl_pct"] = daily_pct
        if open_sym >= max_per_sym:
            return RiskDecision(
                False,
                f"سقف السوق {sym_norm}: وصلت الإشارات المفتوحة على هذا السوق إلى الحد الأقصى ({open_sym}/{max_per_sym})",
                metrics,
            )
        if open_count >= max_open:
            return RiskDecision(
                False,
                f"السقف الكلي للمنصة: وصلت الإشارات المفتوحة إلى الحد الأقصى ({open_count}/{max_open})",
                metrics,
            )
        bucket = exposure_bucket(sym_norm, direction)
        if bucket and corr_open >= max_corr:
            return RiskDecision(
                False,
                f"حماية الترابط ({bucket}): وصلت الصفقات المفتوحة بنفس التعرض الاتجاهي للحد الأقصى ({corr_open}/{max_corr})",
                metrics,
            )
        if src != "SIGNAL_NOW" and max_daily_loss > 0 and daily_pct <= -max_daily_loss:
            return RiskDecision(
                False,
                f"قاطع الخسارة اليومية: وصل صافي خسارة إشارات اليوم ({daily_pct:.1f}%) إلى الحد الأقصى (-{max_daily_loss:.1f}%)",
                metrics,
            )

    if db.get_setting("volatility_filter", "1") == "1" and atr_pct_value > _s("atr_pct_max", 6.0):
        return RiskDecision(False, f"التقلب {atr_pct_value:.2f}% أعلى من الحد المسموح {_s('atr_pct_max', 6.0):.1f}%", metrics)
    return RiskDecision(True, "ok", metrics)


def check_demo_trade(user_id: int, risk_pct: float) -> RiskDecision:
    """Demo position-opens gate — enforces daily loss + open positions caps."""
    max_open = int(_s("demo_max_open", 5))
    with db.conn() as c:
        acc = c.execute("SELECT * FROM demo_accounts WHERE user_id=?", (user_id,)).fetchone()
        if not acc:
            return RiskDecision(False, "no demo account", {})
        open_pos = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE account_id=? AND status='OPEN'", (acc["id"],)).fetchone()["n"]
        day_start = db.day_start_utc()
        closed = c.execute("SELECT COALESCE(SUM(pnl),0) s FROM demo_positions WHERE account_id=? AND status!='OPEN' AND closed_at>=?",
                           (acc["id"], day_start)).fetchone()["s"]
    metrics = {"open_pos": open_pos, "daily_pnl": closed}
    if open_pos >= max_open:
        return RiskDecision(False, f"open demo positions {open_pos} reached cap {max_open}", metrics)
    daily_loss_limit = -abs(acc["balance"]) * _s("max_daily_loss_pct", 5.0) / 100.0
    if closed <= daily_loss_limit:
        return RiskDecision(False, f"daily demo loss {closed:.2f} breached limit {daily_loss_limit:.2f}", metrics)
    max_risk_band = max(_s("risk_per_trade_pct", 1.0) * 3.0, 5.0)
    if not (0.0 < risk_pct <= max_risk_band):
        return RiskDecision(False, f"risk per trade {risk_pct:.2f}% outside allowed band (<= {max_risk_band:.1f}%)", metrics)
    return RiskDecision(True, "ok", metrics)


def position_sizing(balance: float, risk_pct: float, entry: float, sl: float, symbol: str, row=None) -> float:
    from fysb import instruments
    return instruments.position_size(balance, min(risk_pct, 5.0), entry, sl, symbol, row)
