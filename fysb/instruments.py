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
    "XAGUSD": Spec("XAGUSD", "metal", 0.01, 0.001, 3, 5000, 3.0),
    "EURUSD": Spec("EURUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.0),
    "GBPUSD": Spec("GBPUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.5),
    "AUDUSD": Spec("AUDUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.2),
    "NZDUSD": Spec("NZDUSD", "forex", 0.0001, 0.00001, 5, 100000, 1.5),
    "USDCAD": Spec("USDCAD", "forex", 0.0001, 0.00001, 5, 100000, 1.5),
    "USDCHF": Spec("USDCHF", "forex", 0.0001, 0.00001, 5, 100000, 1.5),
    "USDJPY": Spec("USDJPY", "forex", 0.01, 0.001, 3, 100000, 1.2),
    "EURJPY": Spec("EURJPY", "forex", 0.01, 0.001, 3, 100000, 1.8),
    "GBPJPY": Spec("GBPJPY", "forex", 0.01, 0.001, 3, 100000, 2.2),
    "AUDJPY": Spec("AUDJPY", "forex", 0.01, 0.001, 3, 100000, 1.8),
    "CADJPY": Spec("CADJPY", "forex", 0.01, 0.001, 3, 100000, 2.0),
    "CHFJPY": Spec("CHFJPY", "forex", 0.01, 0.001, 3, 100000, 2.0),
    "NZDJPY": Spec("NZDJPY", "forex", 0.01, 0.001, 3, 100000, 2.0),
    "EURGBP": Spec("EURGBP", "forex", 0.0001, 0.00001, 5, 100000, 1.2),
    "EURAUD": Spec("EURAUD", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "EURCHF": Spec("EURCHF", "forex", 0.0001, 0.00001, 5, 100000, 1.6),
    "EURCAD": Spec("EURCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "GBPAUD": Spec("GBPAUD", "forex", 0.0001, 0.00001, 5, 100000, 2.5),
    "GBPCHF": Spec("GBPCHF", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "GBPCAD": Spec("GBPCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.5),
    "AUDCAD": Spec("AUDCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "AUDNZD": Spec("AUDNZD", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "BTCUSD": Spec("BTCUSD", "crypto", 1.0, 0.01, 2, 1, 15.0),
    "ETHUSD": Spec("ETHUSD", "crypto", 0.1, 0.01, 2, 1, 1.5),
    "SOLUSD": Spec("SOLUSD", "crypto", 0.01, 0.001, 3, 1, 1.0),
    "XRPUSD": Spec("XRPUSD", "crypto", 0.0001, 0.00001, 5, 1, 2.0),
    "ADAUSD": Spec("ADAUSD", "crypto", 0.0001, 0.00001, 5, 1, 2.0),
    "DOGEUSD": Spec("DOGEUSD", "crypto", 0.0001, 0.00001, 5, 1, 2.0),
}

_USD_QUOTE_SUFFIXES = ("JPY", "CHF", "CAD", "NOK", "SEK", "MXN", "ZAR", "TRY", "CNH", "SGD", "HKD")


def is_usd_base(symbol: str) -> bool:
    """True for USD-base FX pairs (USDJPY, USDCAD, USDCHF, ...) whose quote
    currency is NOT USD — P&L and position sizing must divide by the quote rate."""
    sym = normalize(symbol)
    return sym.startswith("USD") and len(sym) == 6 and sym.endswith(_USD_QUOTE_SUFFIXES)


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
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    if len(key) == 6 and key.endswith("JPY"):
        return Spec(key, "forex", 0.01, 0.001, 3, 100000, 1.8)
    return Spec(key or "?", "forex", 0.0001, 0.00001, 5, 100000, 1.5)


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
    """Lot size so that hitting SL loses exactly risk_pct% of balance (USD).

    XXXUSD: loss_usd = distance * contract_size * lots
    USDXXX: loss_usd = (distance * contract_size * lots) / entry
    """
    s = spec(symbol, row)
    risk_money = balance * max(0.0, risk_pct) / 100.0
    distance = abs(entry - sl)
    if distance <= 0 or s.contract_size <= 0:
        return 0.0
    risk_per_lot = distance * s.contract_size
    if is_usd_base(s.symbol):
        ref_px = float(entry or sl or 0.0)
        if ref_px <= 0:
            return 0.0
        risk_per_lot = risk_per_lot / ref_px
    lots = risk_money / risk_per_lot
    return round(max(lots, 0.0), 2)


def pnl(symbol: str, direction: str, lots: float, entry: float, exit_price: float,
        row=None, quote_usd_rate: float = 0.0) -> float:
    """Absolute P&L in USD account currency for a simulated position.

    Quote-currency aware (v6.15.0):
      * XXXUSD / metals / crypto -> quote IS USD: diff * lots * contract_size.
      * USDXXX (USDJPY, USDCAD, USDCHF, ...) -> quote is XXX, exit_price is
        USD/XXX, so USD P&L = (diff * lots * contract_size) / exit_price.
      * Cross pairs with explicit quote_usd_rate -> raw * quote_usd_rate.
    """
    s = spec(symbol, row)
    diff = (exit_price - entry) if direction.upper() == "BUY" else (entry - exit_price)
    raw = diff * float(lots) * s.contract_size
    if is_usd_base(s.symbol):
        conv = float(exit_price or entry or 0.0)
        return (raw / conv) if conv > 0 else 0.0
    if quote_usd_rate and quote_usd_rate > 0:
        return raw * float(quote_usd_rate)
    return raw


def notional_usd(symbol: str, lots: float, price: float, row=None) -> float:
    """Position notional value expressed in USD (used for leverage caps).

    For USDXXX pairs (e.g. USDJPY), the base currency IS USD so 1 standard lot
    has notional = 100,000 USD (NOT 150 * 100,000 = $15M!).
    """
    s = spec(symbol, row)
    if is_usd_base(s.symbol):
        return abs(float(lots)) * s.contract_size
    return abs(float(price)) * abs(float(lots)) * s.contract_size


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
    """USD value of a SIGNED pips move at `lots` — quote-currency aware (v6.8.0+).

    XXXUSD instruments (metals, forex majors, crypto): the quote IS USD, so
    value = move * contract * lots exactly.
    USDXXX pairs (e.g. USDJPY, USDCAD, USDCHF): the quote is XXX — conversion
    divides by the price; without a valid price returns 0.0 HONESTLY rather
    than fabricating a dollar number.
    """
    s = spec(symbol, row)
    if s.pip_size <= 0 or s.contract_size <= 0:
        return 0.0
    if is_usd_base(s.symbol):
        if px <= 0:
            return 0.0            # no quote available — honest zero, no invention
        return (signed_pips * s.pip_size * s.contract_size * lots) / px
    return signed_pips * s.pip_size * s.contract_size * lots
