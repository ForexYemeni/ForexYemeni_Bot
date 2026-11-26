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
    # --- المعادن (Metals — 5) ---
    "XAUUSD": Spec("XAUUSD", "metal", 0.1, 0.01, 2, 100, 2.5),
    "XAGUSD": Spec("XAGUSD", "metal", 0.01, 0.001, 3, 5000, 3.0),
    "XPTUSD": Spec("XPTUSD", "metal", 0.1, 0.01, 2, 50, 5.0),
    "XPDUSD": Spec("XPDUSD", "metal", 0.1, 0.01, 2, 100, 6.0),
    "XCUUSD": Spec("XCUUSD", "metal", 0.001, 0.0001, 4, 25000, 3.0),
    # --- الفوركس (Forex Majors, All 21 G8 Crosses, Exotics — 34) ---
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
    "EURNZD": Spec("EURNZD", "forex", 0.0001, 0.00001, 5, 100000, 2.5),
    "GBPAUD": Spec("GBPAUD", "forex", 0.0001, 0.00001, 5, 100000, 2.5),
    "GBPCHF": Spec("GBPCHF", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "GBPCAD": Spec("GBPCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.5),
    "GBPNZD": Spec("GBPNZD", "forex", 0.0001, 0.00001, 5, 100000, 3.0),
    "AUDCAD": Spec("AUDCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "AUDCHF": Spec("AUDCHF", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "AUDNZD": Spec("AUDNZD", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "CADCHF": Spec("CADCHF", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "NZDCAD": Spec("NZDCAD", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "NZDCHF": Spec("NZDCHF", "forex", 0.0001, 0.00001, 5, 100000, 2.2),
    "USDMXN": Spec("USDMXN", "forex", 0.001, 0.0001, 4, 100000, 3.5),
    "USDZAR": Spec("USDZAR", "forex", 0.001, 0.0001, 4, 100000, 4.0),
    "USDTRY": Spec("USDTRY", "forex", 0.001, 0.0001, 4, 100000, 5.0),
    "USDSGD": Spec("USDSGD", "forex", 0.0001, 0.00001, 5, 100000, 2.0),
    "USDNOK": Spec("USDNOK", "forex", 0.001, 0.0001, 4, 100000, 3.0),
    "USDSEK": Spec("USDSEK", "forex", 0.001, 0.0001, 4, 100000, 3.0),
    # --- العملات الرقمية (Crypto — 25) ---
    "BTCUSD": Spec("BTCUSD", "crypto", 1.0, 0.01, 2, 1, 15.0),
    "ETHUSD": Spec("ETHUSD", "crypto", 0.1, 0.01, 2, 1, 1.5),
    "SOLUSD": Spec("SOLUSD", "crypto", 0.01, 0.001, 3, 1, 1.0),
    "XRPUSD": Spec("XRPUSD", "crypto", 0.0001, 0.00001, 4, 1, 2.0),
    "BNBUSD": Spec("BNBUSD", "crypto", 0.1, 0.01, 2, 1, 1.0),
    "ADAUSD": Spec("ADAUSD", "crypto", 0.0001, 0.00001, 4, 1, 2.0),
    "DOGEUSD": Spec("DOGEUSD", "crypto", 0.0001, 0.00001, 5, 1, 2.0),
    "AVAXUSD": Spec("AVAXUSD", "crypto", 0.01, 0.001, 3, 1, 0.8),
    "LINKUSD": Spec("LINKUSD", "crypto", 0.01, 0.001, 3, 1, 0.8),
    "DOTUSD": Spec("DOTUSD", "crypto", 0.001, 0.0001, 3, 1, 0.8),
    "TONUSD": Spec("TONUSD", "crypto", 0.001, 0.0001, 3, 1, 0.8),
    "LTCUSD": Spec("LTCUSD", "crypto", 0.01, 0.001, 2, 1, 1.0),
    "BCHUSD": Spec("BCHUSD", "crypto", 0.1, 0.01, 2, 1, 1.5),
    "TRXUSD": Spec("TRXUSD", "crypto", 0.0001, 0.00001, 5, 1, 0.5),
    "SHIBUSD": Spec("SHIBUSD", "crypto", 0.0000001, 0.00000001, 8, 1, 1.0),
    "SUIUSD": Spec("SUIUSD", "crypto", 0.001, 0.0001, 4, 1, 0.8),
    "NEARUSD": Spec("NEARUSD", "crypto", 0.001, 0.0001, 3, 1, 0.8),
    "ATOMUSD": Spec("ATOMUSD", "crypto", 0.001, 0.0001, 3, 1, 0.8),
    "UNIUSD": Spec("UNIUSD", "crypto", 0.01, 0.001, 3, 1, 0.8),
    "APTUSD": Spec("APTUSD", "crypto", 0.01, 0.001, 3, 1, 0.8),
    "ARBUSD": Spec("ARBUSD", "crypto", 0.001, 0.0001, 4, 1, 0.8),
    "OPUSD": Spec("OPUSD", "crypto", 0.001, 0.0001, 4, 1, 0.8),
    "XLMUSD": Spec("XLMUSD", "crypto", 0.0001, 0.00001, 5, 1, 0.5),
    "HBARUSD": Spec("HBARUSD", "crypto", 0.0001, 0.00001, 5, 1, 0.5),
    "PEPEUSD": Spec("PEPEUSD", "crypto", 0.0000001, 0.00000001, 8, 1, 1.0),
    # --- الأسهم العالمية (Stocks — 22) ---
    "AAPL": Spec("AAPL", "stock", 0.01, 0.001, 2, 1, 1.0),
    "TSLA": Spec("TSLA", "stock", 0.01, 0.001, 2, 1, 2.0),
    "NVDA": Spec("NVDA", "stock", 0.01, 0.001, 2, 1, 1.5),
    "MSFT": Spec("MSFT", "stock", 0.01, 0.001, 2, 1, 1.0),
    "AMZN": Spec("AMZN", "stock", 0.01, 0.001, 2, 1, 1.2),
    "META": Spec("META", "stock", 0.01, 0.001, 2, 1, 1.5),
    "GOOGL": Spec("GOOGL", "stock", 0.01, 0.001, 2, 1, 1.2),
    "NFLX": Spec("NFLX", "stock", 0.01, 0.001, 2, 1, 2.0),
    "AMD": Spec("AMD", "stock", 0.01, 0.001, 2, 1, 1.2),
    "INTC": Spec("INTC", "stock", 0.01, 0.001, 2, 1, 1.0),
    "ORCL": Spec("ORCL", "stock", 0.01, 0.001, 2, 1, 1.2),
    "AVGO": Spec("AVGO", "stock", 0.01, 0.001, 2, 1, 1.5),
    "CRM": Spec("CRM", "stock", 0.01, 0.001, 2, 1, 1.5),
    "PLTR": Spec("PLTR", "stock", 0.01, 0.001, 2, 1, 1.2),
    "COIN": Spec("COIN", "stock", 0.01, 0.001, 2, 1, 2.0),
    "JPM": Spec("JPM", "stock", 0.01, 0.001, 2, 1, 1.2),
    "V": Spec("V", "stock", 0.01, 0.001, 2, 1, 1.2),
    "MA": Spec("MA", "stock", 0.01, 0.001, 2, 1, 1.5),
    "BA": Spec("BA", "stock", 0.01, 0.001, 2, 1, 1.5),
    "DIS": Spec("DIS", "stock", 0.01, 0.001, 2, 1, 1.2),
    "KO": Spec("KO", "stock", 0.01, 0.001, 2, 1, 1.0),
    "BABA": Spec("BABA", "stock", 0.01, 0.001, 2, 1, 1.5),
    # --- المؤشرات العالمية (Indices — 10) ---
    "US30": Spec("US30", "index", 1.0, 0.1, 2, 1, 3.0),
    "NAS100": Spec("NAS100", "index", 1.0, 0.1, 2, 1, 2.0),
    "SPX500": Spec("SPX500", "index", 1.0, 0.1, 2, 1, 1.5),
    "US2000": Spec("US2000", "index", 0.1, 0.01, 2, 1, 2.0),
    "GER40": Spec("GER40", "index", 1.0, 0.1, 2, 1, 2.5),
    "UK100": Spec("UK100", "index", 1.0, 0.1, 2, 1, 2.5),
    "FRA40": Spec("FRA40", "index", 1.0, 0.1, 2, 1, 2.5),
    "JPN225": Spec("JPN225", "index", 1.0, 0.1, 2, 1, 3.5),
    "HK50": Spec("HK50", "index", 1.0, 0.1, 2, 1, 4.0),
    "DXY": Spec("DXY", "index", 0.01, 0.001, 3, 1, 1.5),
    # --- السلع والطاقة (Commodities — 5) ---
    "USOIL": Spec("USOIL", "commodity", 0.01, 0.001, 2, 1000, 3.0),
    "UKOIL": Spec("UKOIL", "commodity", 0.01, 0.001, 2, 1000, 3.5),
    "NGAS": Spec("NGAS", "commodity", 0.001, 0.0001, 3, 10000, 3.0),
    "WHEAT": Spec("WHEAT", "commodity", 0.25, 0.05, 2, 50, 4.0),
    "CORN": Spec("CORN", "commodity", 0.25, 0.05, 2, 50, 4.0),
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


def signed_pips(symbol: str, direction: str, entry: float, level: float, row=None) -> float:
    """Directional signed PIPS from `entry` to `level` for a BUY or SELL trade.

    Profit-side levels (TP) return positive pips (+); risk-side levels (SL) return negative pips (-).
    """
    s = spec(symbol, row)
    if s.pip_size <= 0:
        return 0.0
    delta = (float(level) - float(entry)) if (direction or "").upper() == "BUY" else (float(entry) - float(level))
    return delta / s.pip_size


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


def typical_spread_pips(symbol: str, row=None) -> float:
    """v8.0.0: Realistic per-asset broker spread in pips (used by Tracker & Backtest)."""
    if row and row.get("spread_pips") is not None:
        try:
            sp = float(row["spread_pips"])
            if sp > 0:
                return sp
        except (TypeError, ValueError):
            pass
    s = spec(symbol, row)
    sym = s.symbol
    if sym == "XAUUSD":
        return 2.5
    if sym == "XAGUSD":
        return 2.0
    if s.category == "crypto":
        return 12.0 if sym in ("BTCUSD", "ETHUSD") else 4.0
    if sym in ("EURUSD", "USDJPY", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD"):
        return 1.0
    if s.category == "forex":
        return 1.8
    return 2.0


def tp_confirm_pips(symbol: str, row=None) -> float:
    """v8.0.0: Per-asset conservative TP confirmation buffer in pips (eliminates
    one-size-fits-all 1.5 pip distortion across Gold / Forex / Crypto)."""
    s = spec(symbol, row)
    sym = s.symbol
    if sym == "XAUUSD":
        return 2.0
    if sym == "XAGUSD":
        return 1.5
    if s.category == "crypto":
        return 8.0 if sym in ("BTCUSD", "ETHUSD") else 3.0
    return 1.0


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
