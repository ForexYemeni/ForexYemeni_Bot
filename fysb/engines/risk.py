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


def check_signal(symbol: str, rr: float, atr_pct_value: float) -> RiskDecision:
    """Signal-issuance gate (signals only — no live exposure exists)."""
    metrics = {}
    if rr < _s("min_rr", 2.0):
        return RiskDecision(False, f"RR {rr:.2f} below minimum {_s('min_rr', 2.0):.1f}", metrics)
    metrics["rr"] = rr
    max_open = int(_s("max_open_signals", 3))
    with db.conn() as c:
        open_count = c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')").fetchone()["n"]
    metrics["open_signals"] = open_count
    if open_count >= max_open:
        return RiskDecision(False, f"open signals {open_count} reached cap {max_open}", metrics)
    if db.get_setting("volatility_filter", "1") == "1" and atr_pct_value > _s("atr_pct_max", 6.0):
        return RiskDecision(False, f"volatility {atr_pct_value:.2f}% above {_s('atr_pct_max', 6.0):.1f}%", metrics)
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
    if not (0.0 < risk_pct <= _s("risk_per_trade_pct", 1.0) * 3):
        return RiskDecision(False, f"risk per trade {risk_pct}% outside allowed band", metrics)
    return RiskDecision(True, "ok", metrics)


def position_sizing(balance: float, risk_pct: float, entry: float, sl: float, symbol: str, row=None) -> float:
    from fysb import instruments
    return instruments.position_size(balance, min(risk_pct, 5.0), entry, sl, symbol, row)
