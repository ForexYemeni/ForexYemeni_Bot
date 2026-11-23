"""Instrument specifications — Pip vs Point vs Price, per-symbol correctness.

Different asset classes use different conventions. Never apply one rule to all:
  Forex (5-digit)     pip = 0.0001, point = 0.00001
  Forex JPY (3-digit) pip = 0.01,   point = 0.001
  Metals (Gold)       pip = 0.1,    point = 0.01,  contract = 100 oz
  Crypto              pip = 1.0,    point = 0.01,  contract = 1 unit
Full precision is kept in calculations & DB; formatting rounds for display only.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    symbol: str
    category: str          # forex | metal | crypto | index | commodity
    pip_size: float
    point_size: float
    digits: int
    contract_size: float
    spread_pips: float     # typical spread in pips (used by the demo simulator)


_DEFAULTS = {
    "XAUUSD": Spec("XAUUSD", "metal", 0.1, 0.01, 2, 100, 2.5),
    "EURUSD": Spec("EURUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.0),
    "GBPUSD": Spec("GBPUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.5),
    "USDJPY": Spec("USDJPY", "forex", 0.01, 0.001, 3, 100000, 1.2),
    "BTCUSD": Spec("BTCUSD", "crypto", 1.0, 0.01, 2, 1, 15),
    "ETHUSD": Spec("ETHUSD", "crypto", 0.1, 0.01, 2, 1, 1.5),
}


def spec(symbol: str, from_db_row=None) -> Spec:
    """Resolve a symbol spec from DB catalog row if provided, else defaults."""
    if from_db_row is not None:
        try:
            pip = float(from_db_row["pip_size"] or 0)
            digits = int(from_db_row["digits"] or 0)
            contract = float(from_db_row["contract_size"] or 0)
            cat = str(from_db_row["category"] or "forex")
            spread = float(from_db_row["spread_pips"] or 0)
            if pip > 0 and digits > 0 and contract > 0:
                point = pip / 10.0 if digits >= 2 else pip
                return Spec(str(from_db_row["symbol"]), cat, pip, point, digits, contract, spread or pip)
        except (KeyError, TypeError, ValueError):
            pass
    key = (symbol or "").upper().replace("/", "")
    return _DEFAULTS.get(key, Spec(key or "?", "forex", 0.0001, 0.00001, 5, 100000, 1.5))


def normalize(symbol: str) -> str:
    return (symbol or "").upper().replace("/", "").strip()


def fmt_price(symbol: str, price: float, row=None) -> str:
    """Display formatting only — DB always stores the full float."""
    s = spec(symbol, row)
    return f"{price:,.{s.digits}f}"


def pips(symbol: str, price_a: float, price_b: float, row=None) -> float:
    """Distance between two prices expressed in PIPS."""
    s = spec(symbol, row)
    if s.pip_size <= 0:
        return 0.0
    return abs(price_a - price_b) / s.pip_size


def points(symbol: str, price_a: float, price_b: float, row=None) -> float:
    """Distance between two prices expressed in POINTS."""
    s = spec(symbol, row)
    if s.point_size <= 0:
        return 0.0
    return abs(price_a - price_b) / s.point_size


def position_size(balance: float, risk_pct: float, entry: float, sl: float, symbol: str, row=None) -> float:
    """Lot size so that hitting SL loses exactly risk_pct% of balance.

    loss = distance_price * contract_size * lots  ->  lots = risk_money / (distance*contract)
    """
    s = spec(symbol, row)
    risk_money = balance * max(0.0, risk_pct) / 100.0
    distance = abs(entry - sl)
    if distance <= 0 or s.contract_size <= 0:
        return 0.0
    lots = risk_money / (distance * s.contract_size)
    return round(max(lots, 0.0), 2)


def pnl(symbol: str, direction: str, lots: float, entry: float, exit_price: float, row=None) -> float:
    """Absolute P&L in account currency for a simulated position."""
    s = spec(symbol, row)
    diff = (exit_price - entry) if direction.upper() == "BUY" else (entry - exit_price)
    return diff * float(lots) * s.contract_size


def rr_ratio(entry: float, sl: float, tp: float, direction: str) -> float:
    """Risk/Reward for a given target. Returns 0.0 when invalid."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0 or reward <= 0:
        return 0.0
    # Direction sanity: the target must sit in the trade direction.
    if direction.upper() == "BUY" and tp <= entry:
        return 0.0
    if direction.upper() == "SELL" and tp >= entry:
        return 0.0
    return reward / risk


def usd_move(symbol: str, signed_pips: float, lots: float, row=None, px: float = 0.0) -> float:
    """USD value of a SIGNED pips move at `lots` — quote-currency aware (v6.8.0).

    XXXUSD instruments (metals, forex majors, crypto): the quote IS USD, so
    value = move * contract * lots exactly.
    USDXXX pairs (e.g. USDJPY): the quote is XXX — the conversion needs the
    current price; without it the function returns 0.0 HONESTLY rather than
    fabricating a dollar number. Never prints a made-up profit.
    """
    s = spec(symbol, row)
    if s.pip_size <= 0 or s.contract_size <= 0:
        return 0.0
    sym = (s.symbol or "").upper()
    if sym.startswith("USD") and len(sym) == 6 and sym.endswith(("JPY", "CHF", "CAD", "NOK", "SEK")):
        if px <= 0:
            return 0.0            # no quote available — honest zero, no invention
        return (signed_pips * s.pip_size * s.contract_size * lots) / px
    return signed_pips * s.pip_size * s.contract_size * lots
