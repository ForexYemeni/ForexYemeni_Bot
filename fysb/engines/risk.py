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


def check_signal(symbol: str, rr: float, atr_pct_value: float,
                 source: str = "AUTO", created_by: int = 0) -> RiskDecision:
    """Signal-issuance gate (signals only — no live exposure exists).

    v6.18.0 Multi-Market Luxury Caps:
      * max_open_per_symbol (default 2): caps open signals on the SAME symbol
        so one active market (e.g. Gold) never chokes BTCUSD/EURUSD/etc.
      * max_open_signals (default 12): global ceiling across all markets.
      * ADMIN_SIGNAL_NOW: Admin's on-demand «⚡ إشارة الآن» is EXEMPT from the
        automatic engine's open-signals caps (and never steals AUTO slots),
        while still enforcing RR, volatility, stale-entry & duplicate gates.
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

    if src == "ADMIN_SIGNAL_NOW":
        metrics["open_signals"] = 0
        metrics["open_symbol_signals"] = 0
        metrics["cap_exempt"] = True
    else:
        with db.conn() as c:
            if src == "SIGNAL_NOW" and int(created_by or 0) > 0:
                open_sym = c.execute(
                    "SELECT COUNT(*) n FROM signals WHERE source='SIGNAL_NOW' AND created_by=? "
                    "AND REPLACE(UPPER(symbol),'/','')=? "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                    (int(created_by), sym_norm),
                ).fetchone()["n"]
                open_count = c.execute(
                    "SELECT COUNT(*) n FROM signals WHERE source='SIGNAL_NOW' AND created_by=? "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                    (int(created_by),),
                ).fetchone()["n"]
            else:
                open_sym = c.execute(
                    "SELECT COUNT(*) n FROM signals "
                    "WHERE (source IS NULL OR source NOT IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW')) "
                    "AND REPLACE(UPPER(symbol),'/','')=? "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')",
                    (sym_norm,),
                ).fetchone()["n"]
                open_count = c.execute(
                    "SELECT COUNT(*) n FROM signals "
                    "WHERE (source IS NULL OR source NOT IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW')) "
                    "AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')"
                ).fetchone()["n"]
        metrics["open_symbol_signals"] = open_sym
        metrics["open_signals"] = open_count
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
