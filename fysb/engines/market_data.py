"""Market data service — REAL-SPOT truth layer with hidden failover chain.

Hidden provider chain (never exposed to users — the bot always says
"بيانات السوق اللحظية"):

  SPOT-REF  gold-api.com (keyless)      real spot quotes: XAU (gold), XAG
                                        (silver), BTC, ETH — updated in
                                        real time, works 24/7.
  FX-REF    Yahoo Finance (keyless)     real spot OHLC + quotes for forex
                                        majors (EURUSD=X, GBPUSD=X, USDJPY=X,
                                        AUDUSD=X, USDCAD=X, USDCHF=X, NZDUSD=X).
  VENUE-1   Binance public REST (keyless)   crypto candles + PAXG gold candles
                                            (auto-calibrated to real spot).
  VENUE-2   Bybit  public REST v5 (keyless) crypto fallback.
  KEYED     TwelveData (optional .env key)  full catalogue, last resort.

Gold truth: the venue gold token (PAXG) trades at a venue premium that drifts
from real spot (observed +13..+18 USD). The engine learns the live offset
(spot − venue) on every fetch, applies it to every candle OHLC, and persists
it so the venue can still serve calibrated candles when the spot reference
hiccups. The headline price is ALWAYS the real spot reference, so analyses
match the actual market the trader sees.

Honesty rules enforced here:
  * Real OHLC candles and real quotes only — no synthetic/seed prices anywhere.
  * Per-(provider,symbol,interval) TTL cache + provider cooldown on errors.
  * If every provider fails, callers get a sanitized Arabic error — the bot
    then says "تعذر جلب بيانات لحظية" instead of inventing a price.
"""
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fysb import config, instruments
from fysb.config import log
from fysb.engines import crypto_ws, provider_health

BASE_URL_TD = "https://api.twelvedata.com/time_series"
QUOTE_URL_TD = "https://api.twelvedata.com/price"
# Venue endpoints are env-overridable (config.BINANCE_BASE_URL / BYBIT_BASE_URL)
# so a geo-blocked or firewalled runtime can point the same chain at a mirror
# without touching code. Call sites read config directly (always current).
BASE_URL_GOLD_API = "https://api.gold-api.com/price"
YAHOO_BASES = ("https://query1.finance.yahoo.com/v8/finance/chart/",
               "https://query2.finance.yahoo.com/v8/finance/chart/")

CACHE_TTL_SECONDS = 25           # short TTL — quotes must stay "لحظية"
QUOTE_CACHE_TTL_SECONDS = 8      # burst-protection cache for live quotes
PROVIDER_COOLDOWN_SECONDS = 45   # after a failure, pause that provider
MIN_CANDLES = 35
CALIB_MAX_RATIO = 0.02           # reject absurd calibration offsets (>2%)
CALIB_MAX_AGE = 48 * 3600        # persisted offset older than this is ignored

# Symbol maps (internal normalized symbol -> venue/reference symbol).
YAHOO_FX_MAP = {
    # 7 Forex Majors
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
    # v8.14.0 — REAL SPOT gold candles (no futures premium, no offset needed):
    # Yahoo serves XAUUSD=X intraday series. This gives the gold candle chain a
    # true-spot third source when the venue chain (futures+offset) and the keyed
    # provider both fail — the Friday 13:47 «تعذر جلب» incident's final fix.
    "XAUUSD": "XAUUSD=X",
    # All 21 G8 Crosses
    "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X", "CHFJPY": "CHFJPY=X", "NZDJPY": "NZDJPY=X",
    "EURGBP": "EURGBP=X", "EURAUD": "EURAUD=X", "EURCHF": "EURCHF=X",
    "EURCAD": "EURCAD=X", "EURNZD": "EURNZD=X",
    "GBPAUD": "GBPAUD=X", "GBPCHF": "GBPCHF=X", "GBPCAD": "GBPCAD=X",
    "GBPNZD": "GBPNZD=X",
    "AUDCAD": "AUDCAD=X", "AUDCHF": "AUDCHF=X", "AUDNZD": "AUDNZD=X",
    "CADCHF": "CADCHF=X", "NZDCAD": "NZDCAD=X", "NZDCHF": "NZDCHF=X",
    # Major Global Exotics
    "USDMXN": "USDMXN=X", "USDZAR": "USDZAR=X", "USDTRY": "USDTRY=X",
    "USDSGD": "USDSGD=X", "USDNOK": "USDNOK=X", "USDSEK": "USDSEK=X",
}
YAHOO_METAL_MAP = {
    "XAGUSD": "SI=F",
    "XPTUSD": "PL=F",
    "XPDUSD": "PA=F",
}
YAHOO_EQUITY_MAP = {
    # Industrial Metal (Copper futures)
    "XCUUSD": "HG=F",
    # Global Indices (10)
    "US30": "^DJI", "DJI": "^DJI",
    "NAS100": "^NDX", "NDX": "^NDX",
    "SPX500": "^GSPC", "SPX": "^GSPC",
    "US2000": "^RUT", "RUT": "^RUT",
    "GER40": "^GDAXI", "DAX": "^GDAXI",
    "UK100": "^FTSE", "FTSE": "^FTSE",
    "FRA40": "^FCHI", "FCHI": "^FCHI",
    "JPN225": "^N225", "N225": "^N225",
    "HK50": "^HSI", "HSI": "^HSI",
    "DXY": "DX-Y.NYB",
    # Global Commodities & Energy (5)
    "USOIL": "CL=F", "WTI": "CL=F",
    "UKOIL": "BZ=F", "BRENT": "BZ=F",
    "NGAS": "NG=F",
    "WHEAT": "ZW=F",
    "CORN": "ZC=F",
    # Top 22 Global Wall Street Stocks
    "AAPL": "AAPL", "TSLA": "TSLA", "NVDA": "NVDA", "MSFT": "MSFT",
    "AMZN": "AMZN", "META": "META", "GOOGL": "GOOGL", "NFLX": "NFLX",
    "AMD": "AMD", "INTC": "INTC", "ORCL": "ORCL", "AVGO": "AVGO",
    "CRM": "CRM", "PLTR": "PLTR", "COIN": "COIN",
    "JPM": "JPM", "V": "V", "MA": "MA", "BA": "BA",
    "DIS": "DIS", "KO": "KO", "BABA": "BABA",
}
BINANCE_MAP = {
    "XAUUSD": "PAXGUSDT",
    "BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT", "BNBUSD": "BNBUSDT", "ADAUSD": "ADAUSDT",
    "DOGEUSD": "DOGEUSDT", "AVAXUSD": "AVAXUSDT", "LINKUSD": "LINKUSDT",
    "DOTUSD": "DOTUSDT", "TONUSD": "TONUSDT", "LTCUSD": "LTCUSDT",
    "BCHUSD": "BCHUSDT", "TRXUSD": "TRXUSDT", "SHIBUSD": "SHIBUSDT",
    "SUIUSD": "SUIUSDT", "NEARUSD": "NEARUSDT", "ATOMUSD": "ATOMUSDT",
    "UNIUSD": "UNIUSDT", "APTUSD": "APTUSDT", "ARBUSD": "ARBUSDT",
    "OPUSD": "OPUSDT", "XLMUSD": "XLMUSDT", "HBARUSD": "HBARUSDT",
    "PEPEUSD": "PEPEUSDT",
}
BYBIT_MAP = {k: v for k, v in BINANCE_MAP.items() if k != "XAUUSD"}
GOLD_API_MAP = {   # real spot quote reference (no candles)
    "XAUUSD": "XAU", "XAGUSD": "XAG", "XPTUSD": "XPT", "XPDUSD": "XPD",
    "BTCUSD": "BTC", "ETHUSD": "ETH",
}
TWELVEDATA_MAP = {
    "XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD", "XPTUSD": "XPT/USD", "XPDUSD": "XPD/USD",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD",
}
FOREX_ONLY = set(YAHOO_FX_MAP.keys())


def _yahoo_symbol_for(internal: str) -> str | None:
    return YAHOO_FX_MAP.get(internal) or YAHOO_METAL_MAP.get(internal) or YAHOO_EQUITY_MAP.get(internal)

_cache: dict[tuple, dict] = {}
_fail_streak: dict[str, tuple[float, int]] = {}
_last_provider: dict[str, str] = {}


def _norm(symbol: str) -> str:
    """Internal normalized key: 'XAU/USD' -> 'XAUUSD', 'paxgusdt' -> 'PAXGUSDT'."""
    return (symbol or "").upper().replace("/", "").replace("-", "").strip()


def _provider_key(internal: str) -> str | None:
    """Which hidden venue serves this symbol (None = not listed here)."""
    if internal in BINANCE_MAP:
        return BINANCE_MAP[internal]
    if internal.endswith("USDT") and len(internal) >= 6:
        return internal  # already a venue-style pair (admin-added crypto)
    return None


# --- provider chains (env-tunable order) -----------------------------------
# "goldapi" serves QUOTES only (no candles anywhere); it never enters a candle
# chain. "twelvedata" enters a chain only when TWELVEDATA_API_KEY is set.
_CANDLE_CHAINS = {
    # v8.14.1 — fx-ref (XAUUSD=X TRUE SPOT) FIRST: the traded instrument IS
    # spot gold, so spot candles need zero offset fiction and are execution-
    # grade by construction. The offset-futures venue becomes the FALLBACK
    # (its payloads stay PROXY/«للعرض فقط» — signal-grade never), keyed last.
    "gold":   ["fx-ref", "venue-gold", "twelvedata"],
    "metal":  ["fx-ref", "twelvedata"],
    "fx":     ["fx-ref", "twelvedata"],
    "equity": ["fx-ref", "twelvedata"],
    "crypto": ["binance", "bybit", "twelvedata"],
}
_QUOTE_CHAINS = {
    "gold":   ["spot-ref", "venue-gold", "twelvedata"],
    "metal":  ["spot-ref", "twelvedata"],
    "fx":     ["fx-ref", "twelvedata"],
    "equity": ["fx-ref", "twelvedata"],
    "crypto": ["binance", "bybit", "twelvedata"],
}
_PREFERRED_ALIASES = {
    "GOLD": "spot-ref", "GOLD_API": "spot-ref", "GOLDAPI": "spot-ref",
    "YAHOO": "fx-ref", "YAHOO_FINANCE": "fx-ref", "FX": "fx-ref",
    "PAXG": "venue-gold", "VENUE": "venue-gold", "BINANCE_PAXG": "venue-gold",
    "TD": "twelvedata",
}


def _class_of(internal: str) -> str | None:
    """Symbol class that selects the provider chain."""
    if internal == "XAUUSD":                # must win over the BINANCE_MAP check
        return "gold"
    if internal in BYBIT_MAP or _provider_key(internal):
        return "crypto"
    if internal in YAHOO_METAL_MAP or internal in GOLD_API_MAP:
        return "metal"
    if internal in YAHOO_FX_MAP:
        return "fx"
    if internal in YAHOO_EQUITY_MAP:
        return "equity"
    return None


def _ordered_chain(chain: list[str], preferred: str, has_key: bool) -> list[str]:
    """Default chain minus unavailable entries, preferred provider moved first.

    Preference REORDERS only — a provider the preferred value names but that
    cannot serve this chain (e.g. GOLDAPI for candles) is ignored, and the
    full failover net always stays. Unknown values change nothing (auto).
    """
    out = [p for p in chain if p != "twelvedata" or has_key]
    pref = _PREFERRED_ALIASES.get((preferred or "").upper(), (preferred or "").upper())
    if pref:
        pref_l = pref.lower()               # chains are lowercase, env value is upper
        if pref_l in out:
            out.remove(pref_l)
            out.insert(0, pref_l)
    return out


def _td_symbol(internal: str, raw_symbol: str) -> str:
    if "/" in (raw_symbol or ""):
        return raw_symbol  # catalog already carries TwelveData vocabulary
    return TWELVEDATA_MAP.get(internal, raw_symbol)


# --- cooldown bookkeeping ----------------------------------------------------
def _ck(provider: str, symbol: str, interval: str) -> str:
    return f"{provider}|{symbol}|{interval}"


def in_cooldown(provider: str, symbol: str, interval: str) -> bool:
    if provider_health.is_open(provider):
        return True
    entry = _fail_streak.get(_ck(provider, symbol, interval))
    if not entry:
        return False
    until, _streak = entry
    return time.time() < until


def _mark_failure(provider: str, symbol: str, interval: str, err: str = "") -> None:
    key = _ck(provider, symbol, interval)
    streak = _fail_streak.get(key, (0.0, 0))[1] + 1
    _fail_streak[key] = (time.time() + PROVIDER_COOLDOWN_SECONDS * min(streak, 4), streak)
    provider_health.record_failure(provider, err)


def _mark_success(provider: str, symbol: str, interval: str, latency_ms: float = 0.0) -> None:
    _fail_streak.pop(_ck(provider, symbol, interval), None)
    provider_health.record_success(provider, latency_ms)


def _http_json(url: str, timeout: int = 15, headers: dict | None = None) -> dict:
    base_headers = {"User-Agent": f"FOREXYEMENI/{config.VERSION}",
                    "Accept": "application/json"}
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, headers=base_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_interval(code: str) -> str:
    """Catalog code M15 -> neutral interval vocabulary used across providers."""
    mapping = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
               "H1": "1h", "H4": "4h", "D1": "1day", "W1": "1week"}
    return mapping.get((code or "").upper(), "")


# --- provider: SPOT-REF (gold-api.com — real spot, keyless, 24/7) -------------
def _fetch_goldapi_price(ticker: str) -> dict:
    raw = _http_json(f"{BASE_URL_GOLD_API}/{urllib.parse.quote(ticker)}", timeout=10)
    price = float(raw["price"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("bad spot price")
    updated = str(raw.get("updatedAt") or "")
    return {"price": price, "updated": updated}


# --- provider: FX-REF (Yahoo Finance — real spot forex, keyless) --------------
def _yahoo_interval(interval: str) -> tuple[str, str]:
    """(yahoo interval, range). 4h is aggregated from 60m candles."""
    table = {"1min": ("1m", "5d"), "5min": ("5m", "1mo"), "15min": ("15m", "1mo"),
             "30min": ("30m", "1mo"), "1h": ("60m", "3mo"), "4h": ("60m", "1y"),
             "1day": ("1d", "2y"), "1week": ("1wk", "5y")}
    return table.get(interval, ("15m", "1mo"))


def _fetch_yahoo_candles(yahoo_symbol: str, interval: str, outputsize: int) -> list[dict]:
    yi, rng = _yahoo_interval(interval)
    q = urllib.parse.urlencode({"interval": yi, "range": rng,
                                "includePrePost": "false"})
    payload, last_err = None, None
    for base in YAHOO_BASES:
        try:
            payload = _http_json(base + urllib.parse.quote(yahoo_symbol) + "?" + q,
                                 timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            break
        except Exception as exc:            # try the mirror host
            last_err = exc
    if payload is None:
        raise RuntimeError(f"yahoo unreachable: {type(last_err).__name__}")
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("yahoo: empty chart result")
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [None])[0] or {}
    opens, highs, lows, closes = (quote.get(k) or [] for k in ("open", "high", "low", "close"))
    out = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if o is None or h is None or l is None or c is None:
                continue
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            item = {"datetime": dt, "open": float(o), "high": float(h),
                    "low": float(l), "close": float(c)}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) \
                    and item["high"] >= item["low"]:
                out.append(item)
        except (IndexError, TypeError, ValueError):
            continue
    if interval == "4h":
        out = _aggregate_utc(out, 4 * 3600)
    if outputsize and len(out) > outputsize:
        out = out[-outputsize:]
    if len(out) < MIN_CANDLES:
        raise RuntimeError(f"yahoo: insufficient candles ({len(out)})")
    return out


def _fetch_yahoo_quote(yahoo_symbol: str) -> dict:
    payload, last_err = None, None
    for base in YAHOO_BASES:
        try:
            payload = _http_json(base + urllib.parse.quote(yahoo_symbol) + "?interval=1m&range=1d",
                                 timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            break
        except Exception as exc:
            last_err = exc
    if payload is None:
        raise RuntimeError(f"yahoo unreachable: {type(last_err).__name__}")
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("yahoo: empty chart result")
    meta = result.get("meta") or {}
    price = float(meta.get("regularMarketPrice") or 0)
    ts = int(meta.get("regularMarketTime") or 0)
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("yahoo: bad quote")
    return {"price": price,
            "updated": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}


def _aggregate_utc(values: list[dict], bucket_seconds: int) -> list[dict]:
    """Aggregate smaller candles into UTC-aligned buckets (e.g. 60m -> 4h)."""
    buckets: dict[int, dict] = {}
    for c in values:
        try:
            ts = int(datetime.strptime(c["datetime"], "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        b = ts - (ts % bucket_seconds)
        cur = buckets.get(b)
        if cur is None:
            buckets[b] = {"datetime": datetime.fromtimestamp(b, tz=timezone.utc)
                          .strftime("%Y-%m-%d %H:%M:%S"),
                          "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
    return [buckets[k] for k in sorted(buckets)]


# --- gold calibration (venue premium -> real spot) -----------------------------
def _calib_path() -> Path:
    return config.DB_FILE.parent / "spot_calibration.json"


def _load_calib() -> dict:
    try:
        with open(_calib_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_calib(data: dict) -> None:
    try:
        with open(_calib_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def _learn_gold_offset(internal: str, spot_price: float, venue_price: float) -> float | None:
    """Learn offset = real spot − venue. Reject absurd values; persist."""
    if not (math.isfinite(spot_price) and math.isfinite(venue_price)) or venue_price <= 0:
        return None
    offset = spot_price - venue_price
    if abs(offset) > CALIB_MAX_RATIO * venue_price:
        return None
    data = _load_calib()
    data[internal] = {"offset": round(offset, 6), "ts": time.time()}
    _save_calib(data)
    return offset


def _persisted_offset(internal: str) -> float | None:
    data = _load_calib().get(internal)
    if not data:
        return None
    if time.time() - float(data.get("ts") or 0) > CALIB_MAX_AGE:
        return None
    off = float(data.get("offset") or 0)
    return off if math.isfinite(off) else None


def _apply_offset(values: list[dict], offset: float) -> list[dict]:
    if not offset:
        return values
    out = []
    for c in values:
        out.append({"datetime": c["datetime"],
                    "open": c["open"] + offset, "high": c["high"] + offset,
                    "low": c["low"] + offset, "close": c["close"] + offset})
    return out


def _fetch_venue_gold_price(venue_symbol: str) -> float:
    """Fetch PAXG venue quote from Binance, failing over to Bybit if Binance is
    unreachable or geo-blocked."""
    try:
        return _fetch_binance_price(venue_symbol)
    except Exception:
        return _fetch_bybit_price(venue_symbol)


def _fetch_venue_gold_candles(venue_symbol: str, interval: str, outputsize: int) -> list[dict]:
    """Fetch PAXG venue candles from Binance, failing over to Bybit if Binance
    is unreachable or geo-blocked."""
    try:
        vals = _fetch_binance_candles(venue_symbol, interval, outputsize)
        if len(vals) >= MIN_CANDLES:
            return vals
    except Exception as exc:
        log.debug("binance gold candles fallback to bybit: %s", exc)
    return _fetch_bybit_candles(venue_symbol, interval, outputsize)


def _gold_offset_now(internal: str, venue_symbol: str) -> float | None:
    """Live offset from the real spot reference vs the venue gold token."""
    spot_ticker = GOLD_API_MAP.get(internal)
    if not spot_ticker:
        return None
    try:
        spot = _fetch_goldapi_price(spot_ticker)["price"]
        venue = _fetch_venue_gold_price(venue_symbol)
        return _learn_gold_offset(internal, spot, venue)
    except Exception:
        return _persisted_offset(internal)


# --- provider: VENUE-1 (Binance) ------------------------------------------------
def _binance_interval(interval: str) -> str:
    return {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
            "1h": "1h", "4h": "4h", "1day": "1d", "1week": "1w"}.get(interval, "15m")


def _fetch_binance_candles(venue_symbol: str, interval: str, outputsize: int) -> list[dict]:
    q = urllib.parse.urlencode({"symbol": venue_symbol, "interval": _binance_interval(interval),
                                "limit": max(40, min(outputsize, 1000))})
    raw = _http_json(config.BINANCE_BASE_URL + "/api/v3/klines?" + q)
    if not isinstance(raw, list):
        msg = raw.get("msg", "unexpected payload") if isinstance(raw, dict) else "unexpected payload"
        raise RuntimeError(f"binance klines error: {str(msg)[:120]}")
    out = []
    for row in raw:
        try:
            ts_raw = int(row[0])
            ts_sec = ts_raw / 1000.0 if ts_raw > 1e11 else float(ts_raw)
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            item = {"datetime": dt, "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4])}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) and item["high"] >= item["low"]:
                out.append(item)
        except (IndexError, TypeError, ValueError, OverflowError):
            continue
    return out


def _fetch_binance_price(venue_symbol: str) -> float:
    raw = _http_json(config.BINANCE_BASE_URL + "/api/v3/ticker/price?" + urllib.parse.urlencode({"symbol": venue_symbol}), timeout=10)
    if not isinstance(raw, dict) or "price" not in raw:
        raise RuntimeError("binance: bad price payload")
    price = float(raw["price"])
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("binance: non-positive price")
    return price


# --- provider: VENUE-2 (Bybit) ---------------------------------------------------
def _bybit_interval(interval: str) -> str:
    return {"1min": "1", "5min": "5", "15min": "15", "30min": "30",
            "1h": "60", "4h": "240", "1day": "D", "1week": "W"}.get(interval, "15")


def _fetch_bybit_candles(venue_symbol: str, interval: str, outputsize: int) -> list[dict]:
    q = urllib.parse.urlencode({"category": "spot", "symbol": venue_symbol,
                                "interval": _bybit_interval(interval), "limit": max(40, min(outputsize, 1000))})
    raw = _http_json(config.BYBIT_BASE_URL + "/v5/market/kline?" + q)
    if not isinstance(raw, dict):
        raise RuntimeError("bybit: unexpected kline payload")
    if int(raw.get("retCode", 0) or 0) != 0:
        raise RuntimeError(f"bybit kline error: {str(raw.get('retMsg', ''))[:120]}")
    rows = ((raw.get("result") or {}).get("list") or [])
    if not isinstance(rows, list):
        raise RuntimeError("bybit: empty kline list")
    out = []
    for row in reversed(rows):  # newest-first -> oldest-first
        try:
            # Bybit v5 startTime is in MILLISECONDS (e.g. "1700000000000");
            # handle both ms and s safely so timestamps never overflow.
            ts_raw = int(row[0])
            ts_sec = ts_raw / 1000.0 if ts_raw > 1e11 else float(ts_raw)
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            item = {"datetime": dt, "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4])}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) and item["high"] >= item["low"]:
                out.append(item)
        except (IndexError, TypeError, ValueError, OverflowError):
            continue
    return out


def _fetch_bybit_price(venue_symbol: str) -> float:
    raw = _http_json(config.BYBIT_BASE_URL + "/v5/market/tickers?" + urllib.parse.urlencode({"category": "spot", "symbol": venue_symbol}), timeout=10)
    if not isinstance(raw, dict) or int(raw.get("retCode", 0) or 0) != 0:
        raise RuntimeError(f"bybit ticker error: {str((raw or {}).get('retMsg', ''))[:120]}")
    items = ((raw.get("result") or {}).get("list") or [])
    if not items:
        raise RuntimeError("bybit: empty ticker list")
    price = float(items[0]["lastPrice"])
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("bybit: non-positive price")
    return price


# --- provider: KEYED (TwelveData, optional) ---------------------------------------
def _fetch_td_candles(td_symbol: str, interval: str, api_key: str, outputsize: int) -> list[dict]:
    params = urllib.parse.urlencode({"symbol": td_symbol, "interval": interval,
                                     "outputsize": outputsize, "apikey": api_key, "format": "JSON"})
    payload = _http_json(BASE_URL_TD + "?" + params, timeout=20)
    if payload.get("status") == "error" or "values" not in payload:
        raise RuntimeError(str(payload.get("message", "no values"))[:200])
    out = []
    for row in payload.get("values", []):
        try:
            item = {"datetime": str(row["datetime"]), "open": float(row["open"]),
                    "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) and item["high"] >= item["low"]:
                out.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _fetch_td_price(td_symbol: str, api_key: str) -> float:
    params = urllib.parse.urlencode({"symbol": td_symbol, "apikey": api_key})
    payload = _http_json(QUOTE_URL_TD + "?" + params, timeout=15)
    if payload.get("status") == "error" or "price" not in payload:
        raise RuntimeError(str(payload.get("message", "no price"))[:200])
    return float(payload["price"])


# --- unified facade ------------------------------------------------------------
def _assemble(internal: str, interval: str, values: list[dict], source: str, use_cache: bool,
              cache_key, raw_symbol: str, provider_name: str = "") -> dict:
    values.sort(key=lambda x: x["datetime"])
    if len(values) < MIN_CANDLES:
        raise RuntimeError(f"insufficient valid candles: {len(values)} (need at least {MIN_CANDLES})")
    last_iso = values[-1]["datetime"]
    age = _candle_age_seconds(last_iso, interval)
    result = {
        "symbol": raw_symbol, "interval": interval, "values": values,
        "fetched_at": config.now(), "fetched_epoch": time.time(),
        "last_candle_iso": last_iso, "age_seconds": age, "source": source,
        "provider": provider_name or source,
        "instrument_kind": "PROXY" if provider_name == "venue-gold" else "PROVIDER_MARKET",
        "is_proxy": provider_name == "venue-gold",
    }
    _mark_success(provider_name or source, internal, interval)
    _last_provider[internal] = provider_name or source
    if use_cache:
        _cache[cache_key] = {"t": time.time(), "payload": result}
        if len(_cache) > 200:
            for k, _v in sorted(_cache.items(), key=lambda kv: kv[1]["t"])[:50]:
                _cache.pop(k, None)
    return result


def fetch_candles(symbol: str, interval: str, api_key: str = "", outputsize: int = 120,
                  use_cache: bool = True) -> dict:
    """Fetch OHLC candles through the hidden failover chain.

    Chain per symbol class (config.PRICE_PROVIDER may reorder it — preference
    first, everything else stays as the fallback net):
      * gold   : VENUE gold candles (Binance -> Bybit PAXG) CALIBRATED to real spot -> KEYED -> error
      * metal  : KEYED only (silver has no venue candles; quotes use SPOT-REF)
      * forex  : FX-REF real spot candles -> KEYED -> error
      * crypto : VENUE-1 -> VENUE-2 -> KEYED -> error
      * custom : KEYED (if TWELVEDATA_API_KEY configured) -> error
    Raises RuntimeError with SANITIZED Arabic messages when everything fails —
    callers must never fabricate prices.

    v8.11.0 (P2): freshness-aware failover — a provider answering with STALE
    candles no longer blocks the remaining chain; the freshest stale payload is
    returned only as an honest last resort (never cached as fresh evidence).
    """
    internal = _norm(symbol)
    interval = interval or "15min"
    cache_key = (internal, interval, outputsize)
    if use_cache:
        hit = _cache.get(cache_key)
        if hit and time.time() - hit["t"] < CACHE_TTL_SECONDS:
            return hit["payload"]

    errors: list[str] = []
    cls = _class_of(internal)
    base_chain = _CANDLE_CHAINS.get(cls, ["twelvedata"] if cls is None else [])
    chain = _ordered_chain(base_chain, config.PRICE_PROVIDER, config.has_market_api())
    venue = _provider_key(internal) if internal != "XAUUSD" else None
    # v8.11.0 (P2): a SUCCESSFUL but STALE provider response no longer stops the
    # failover chain. The freshest stale payload is kept only as a last resort
    # and returned honestly (callers still reject it by their own freshness
    # gates) — it is NEVER cached, so the next attempt re-tries the chain live.
    stale_fallback: dict | None = None

    # v8.13.1 — TWO-PASS chain: pass 1 respects cooldowns; if nothing fresh
    # came back, pass 2 retries EVERY provider ignoring cooldowns (a provider
    # that recovered seconds ago must never lose to a 24h-old stale snapshot).
    fresh_payload: dict | None = None
    for attempt in (0, 1):
        bypass_cooldown = attempt == 1
        for provider in chain:
            if not bypass_cooldown and in_cooldown(provider, internal, interval):
                errors.append(f"{provider}:cooldown")
                continue
            try:
                if provider == "venue-gold":
                    v = BINANCE_MAP[internal]
                    # v8.0.0 (SIG-3): Prefer real CME Gold Futures (`GC=F`) via Yahoo first
                    # (zero crypto PAXG depeg!), then fail over to PAXG + GoldAPI spot offset.
                    used_futures = False
                    values = []
                    try:
                        yvals = _fetch_yahoo_candles("GC=F", interval, outputsize)
                        if len(yvals) >= MIN_CANDLES:
                            values = yvals
                            used_futures = True
                    except Exception as y_exc:
                        log.debug("yahoo GC=F gold candles unavailable, failing over to PAXG: %s", y_exc)
                    if not used_futures:
                        values = _fetch_venue_gold_candles(v, interval, outputsize)
                        offset = _gold_offset_now(internal, v)
                        if offset is None:
                            offset = _persisted_offset(internal) or 0.0
                        values = _apply_offset(values, offset)
                    else:
                        offset_applied = False
                        last_c = float(values[-1]["close"]) if values else 0.0
                        try:
                            spot_t = GOLD_API_MAP.get(internal)
                            if spot_t and values:
                                spot_px = _fetch_goldapi_price(spot_t)["price"]
                                if spot_px > 0 and last_c > 0 and abs(spot_px - last_c) <= 120.0:
                                    values = _apply_offset(values, spot_px - last_c)
                                    offset_applied = True
                                    try:
                                        _learn_gold_offset(internal, spot_px, last_c)
                                    except Exception:
                                        pass
                        except Exception as s_exc:
                            log.debug("gold futures spot calibration skipped: %s", s_exc)
                        if not offset_applied:
                            # v8.13.9 — fall back to the FRESH PERSISTED offset
                            # (learned ≤48h ago from live spot-vs-venue
                            # calibration, magnitude-capped ≤2% of the venue
                            # price, age-checked) before failing: a momentary
                            # gold-api hiccup must not wipe the whole gold chain
                            # into «تعذر جلب» while the market is OPEN (the
                            # Friday 13:47 incident). RAW futures (no documented
                            # offset at all) are STILL never served — the #46
                            # guarantee holds.
                            off = _persisted_offset(internal)
                            if off and last_c and abs(off) <= CALIB_MAX_RATIO * abs(last_c):
                                values = _apply_offset(values, off)
                                offset_applied = True
                            else:
                                raise RuntimeError("unoffsetted-futures")
                elif provider == "fx-ref":
                    ysym = _yahoo_symbol_for(internal)
                    if not ysym:
                        continue
                    values = _fetch_yahoo_candles(ysym, interval, outputsize)
                elif provider == "binance":
                    if not venue:
                        continue
                    values = _fetch_binance_candles(venue, interval, outputsize)
                elif provider == "bybit":
                    bybit_sym = BYBIT_MAP.get(internal) or venue
                    if not bybit_sym:
                        continue
                    values = _fetch_bybit_candles(bybit_sym, interval, outputsize)
                elif provider == "twelvedata":
                    values = _fetch_td_candles(_td_symbol(internal, symbol), interval,
                                               config.market_api_key(), outputsize)
                else:
                    continue
                # v8.13.1 — wrong-series guard BEFORE assembly: a provider serving
                # the wrong timestep (daily bars for a 15min request) is treated as
                # a provider failure so the chain keeps looking for the real series.
                if _series_interval_mismatch(values, interval):
                    _mark_failure(provider, internal, interval, "interval-mismatch")
                    errors.append(f"{provider}:interval-mismatch")
                    continue
                # v8.11.0 (P2): freshness-aware chain traversal — fresh data wins
                # immediately; stale data never blocks the remaining alternatives.
                payload = _assemble(internal, interval, values, "LIVE", False, cache_key, symbol,
                                    provider_name=provider)
                if not is_stale(payload):
                    _FEED_SNAPSHOTS.pop(cache_key, None)   # fresh again — reset freeze tracking
                    if use_cache:
                        _cache[cache_key] = {"t": time.time(), "payload": payload}
                        if len(_cache) > 200:
                            for k, _v in sorted(_cache.items(), key=lambda kv: kv[1]["t"])[:50]:
                                _cache.pop(k, None)
                    return payload
                # v8.13.6 — ancient-payload guard: a provider answering a 15min
                # request with a snapshot older than 3× the staleness limit
                # (cached CDN page / frozen mirror / throttled API serving old
                # pages) is a FAILED provider, never a "freshest stale"
                # candidate — a 31-hour zombie must not be the platform's best
                # answer for an M15 request (the 1898-minute incident).
                if payload["age_seconds"] > 3 * staleness_limit(interval):
                    _mark_failure(provider, internal, interval, "ancient-snapshot")
                    errors.append(f"{provider}:ancient(age={int(payload['age_seconds']) // 60}min)")
                    continue
                if stale_fallback is None or payload["age_seconds"] < stale_fallback["age_seconds"]:
                    stale_fallback = payload
                errors.append(f"{provider}:stale(age={int(payload['age_seconds']) // 60}min)")
            except Exception as exc:
                _mark_failure(provider, internal, interval)
                errors.append(f"{provider}:" + type(exc).__name__)
        # pass 1 returning nothing fresh falls through to pass 2 (cooldown
        # bypass); a fresh return inside the loop exits the function directly.

    # v8.11.0 (P2): every live alternative failed or was stale — the freshest
    # stale payload is returned HONESTLY as a last resort (never cached, never
    # presented as fresh). Callers apply their own staleness gates and speak
    # the sanitized Arabic reason — no fabricated freshness, no silent halt.
    # v8.13.1: the per-provider failure chain is attached for ADMIN surfaces
    # (engine_runs) so a frozen feed is traceable without exposing names to users.
    if stale_fallback is not None:
        stale_fallback["chain_errors"] = errors[:]
        _feed_freeze_note(cache_key, stale_fallback)
        return stale_fallback

    if internal in FOREX_ONLY and not config.has_market_api():
        raise RuntimeError("تعذر جلب شموع الفوركس من المرجع اللحظي — أعد المحاولة بعد قليل")

    raise RuntimeError("تعذر جلب بيانات لحظية لهذه الأداة الآن — أعد المحاولة بعد قليل")


def _store_quote(internal: str, provider: str, payload: dict, use_cache: bool) -> dict:
    payload = {**payload, "provider": provider, "instrument_kind": "PROXY" if provider == "venue-gold" else "PROVIDER_MARKET", "is_proxy": provider == "venue-gold"}
    _mark_success(provider, internal, "quote")
    _last_provider[internal] = provider
    if use_cache:
        _cache[("quote", internal)] = {"t": time.time(), "payload": payload}
        if len(_cache) > 250:
            for k, _v in sorted(_cache.items(), key=lambda kv: kv[1]["t"])[:50]:
                _cache.pop(k, None)
    return payload


def cached_price(symbol: str, max_age_seconds: float = 120.0) -> float:
    """Return a cached live price in 0ms without hitting any external API (0.0 if absent/stale)."""
    internal = _norm(symbol)
    hit = _cache.get(("quote", internal))
    if hit and (time.time() - hit["t"]) <= max_age_seconds:
        try:
            return float((hit.get("payload") or {}).get("price") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def fetch_price(symbol: str, api_key: str = "", use_cache: bool = True) -> dict:
    """Latest REAL quote — headline prices must match the actual market."""
    internal = _norm(symbol)
    cls = _class_of(internal)
    # v6.24.0: sub-second free live crypto WebSocket tick fast-path
    if use_cache and cls == "crypto":
        ws_px = crypto_ws.get_live_price(internal)
        if ws_px and ws_px > 0:
            _last_provider[internal] = "crypto-ws"
            return {
                "symbol": symbol,
                "price": ws_px,
                "fetched_at": config.now(),
                "source": "LIVE",
                "ws": True,
            }
    if use_cache:
        qhit = _cache.get(("quote", internal))
        if qhit and time.time() - qhit["t"] < QUOTE_CACHE_TTL_SECONDS:
            out = dict(qhit["payload"])
            out["symbol"] = symbol
            return out

    base_chain = _QUOTE_CHAINS.get(cls, ["twelvedata"] if cls is None else [])
    chain = _ordered_chain(base_chain, config.PRICE_PROVIDER, config.has_market_api())
    venue = _provider_key(internal) if internal != "XAUUSD" else None

    for provider in chain:
        if in_cooldown(provider, internal, "quote"):
            continue
        t0 = time.monotonic()
        try:
            if provider == "spot-ref":
                if internal not in GOLD_API_MAP:
                    continue
                ref = _fetch_goldapi_price(GOLD_API_MAP[internal])
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": ref["price"],
                                     "fetched_at": config.now(), "observed_at": ref.get("updated", ""), "source": "LIVE", "spot_ref": True},
                                    use_cache)
            if provider == "fx-ref":
                ysym = _yahoo_symbol_for(internal)
                if not ysym:
                    continue
                ref = _fetch_yahoo_quote(ysym)
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": ref["price"],
                                     "fetched_at": config.now(), "observed_at": ref.get("updated", ""), "source": "LIVE", "spot_ref": True},
                                    use_cache)
            if provider == "venue-gold":
                v = BINANCE_MAP[internal]
                venue_price = _fetch_venue_gold_price(v)
                offset = _gold_offset_now(internal, v)
                if offset is None:
                    offset = _persisted_offset(internal)
                if offset:
                    venue_price = venue_price + offset
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": venue_price,
                                     "fetched_at": config.now(), "source": "LIVE", "spot_ref": bool(offset)},
                                    use_cache)
            if provider == "binance":
                if not venue:
                    continue
                price = _fetch_binance_price(venue)
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": price,
                                     "fetched_at": config.now(), "source": "LIVE"},
                                    use_cache)
            if provider == "bybit":
                bybit_sym = BYBIT_MAP.get(internal) or venue
                if not bybit_sym:
                    continue
                price = _fetch_bybit_price(bybit_sym)
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": price,
                                     "fetched_at": config.now(), "source": "LIVE"},
                                    use_cache)
            if provider == "twelvedata":
                price = _fetch_td_price(_td_symbol(internal, symbol), config.market_api_key())
                provider_health.record_success(provider, (time.monotonic() - t0) * 1000.0)
                return _store_quote(internal, provider,
                                    {"symbol": symbol, "price": price,
                                     "fetched_at": config.now(), "source": "LIVE"},
                                    use_cache)
        except Exception as exc:
            _mark_failure(provider, internal, "quote", str(exc))
            continue

    if internal in FOREX_ONLY and not config.has_market_api():
        raise RuntimeError("تعذر جلب سعر لحظي من مرجع الفوركس — أعد المحاولة بعد قليل")

    raise RuntimeError("تعذر جلب سعر لحظي لهذه الأداة الآن — أعد المحاولة بعد قليل")


def live_provider_hint(internal: str) -> str:
    """Honest internal hint — used ONLY in admin logs, never shown to users."""
    return _last_provider.get(internal, "")


def _candle_age_seconds(last_iso: str, interval: str) -> float:
    """Age of the last closed candle, accounting for the interval duration."""
    try:
        dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        span = _interval_seconds(interval)
        return max(0.0, (datetime.now(timezone.utc) - (dt + timedelta(seconds=span))).total_seconds())
    except Exception:
        return 1e9


# v8.13.2 — feed-freeze detection bookkeeping: when the SAME last-candle
# snapshot is served again ≥10 minutes later while the session calendar says
# the market is OPEN, the feed is not merely stale — it is FROZEN. Marked on
# the payload for admin surfaces (engine_runs + live probe); never user-facing.
_FEED_SNAPSHOTS: dict = {}
FEED_FROZEN_MIN_GAP_SECONDS = 600.0


def _feed_freeze_note(cache_key: tuple, payload: dict) -> None:
    """Attach feed_frozen/frozen_repeats to a stale payload when the snapshot
    is byte-identical to a previous stale answer ≥10 minutes apart on an OPEN
    market. Honest detection — a changed iso (real data moving) resets it, a
    closed market never marks it."""
    iso = str(payload.get("last_candle_iso") or "")
    now_t = time.time()
    snap = _FEED_SNAPSHOTS.get(cache_key)
    try:
        open_now = market_state(instruments.spec(cache_key[0]).category)
    except Exception:
        open_now = None
    if open_now is True and snap and snap["iso"] == iso:
        if (now_t - snap["t"]) >= FEED_FROZEN_MIN_GAP_SECONDS:
            repeats = int(snap["repeats"]) + 1
            _FEED_SNAPSHOTS[cache_key] = {"iso": iso, "t": now_t, "repeats": repeats}
            payload["feed_frozen"] = True
            payload["frozen_repeats"] = repeats
        # else: same frozen snapshot within the window — keep the original timer
        return
    _FEED_SNAPSHOTS[cache_key] = {"iso": iso, "t": now_t, "repeats": 0}


def _series_interval_mismatch(values: list[dict], wanted_interval: str) -> bool:
    """v8.13.1 — wrong-series guard: a provider answering a 15min request with
    daily/1h bars poisons freshness math (a single 1-day candle reads ~24h
    stale and the whole analysis dies on honest-but-misleading data). The
    median timestep must match the requested interval within 2x; weekend holes
    are fine because the MEDIAN ignores isolated gaps."""
    try:
        if len(values) < 3:
            return False          # too few to judge — downstream gates handle it
        want = _interval_seconds(wanted_interval)
        deltas: list[float] = []
        prev = None
        for c in values:
            t = datetime.fromisoformat(str(c["datetime"]).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if prev is not None:
                deltas.append(abs((t - prev).total_seconds()))
            prev = t
        if not deltas:
            return False
        deltas.sort()
        median = deltas[len(deltas) // 2]
        return median > 2.0 * want
    except Exception:
        return False


def _interval_seconds(interval: str) -> int:
    table = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800,
             "1h": 3600, "4h": 14400, "1day": 86400, "1week": 604800}
    return table.get(interval, 900)


def staleness_limit(interval: str) -> int:
    """Max acceptable age per interval — weekend/holiday aware (markets closed)."""
    limit = _interval_seconds(interval) * 3
    now = datetime.now(timezone.utc)
    if now.weekday() == 5:                      # Saturday
        return max(limit, 3 * 86400)
    if now.weekday() == 6 and now.hour < 22:    # Sunday before reopen
        return max(limit, 2 * 86400)
    return limit


def is_stale(payload: dict) -> bool:
    return payload.get("age_seconds", 1e9) > staleness_limit(payload.get("interval", "15min"))


def market_state(category: str) -> bool | None:
    """Is the market OPEN right now (UTC-based session calendar)?

    True = open, False = closed, None = unknown category.
    Crypto venues trade 24/7 — always open. Gold/silver spot quotes come from
    a 24/7 reference, but the classic session calendar still gates SIGNAL
    issuance for forex/metals categories.
    """
    cat = (category or "").lower()
    if cat == "crypto":
        return True
    if cat in ("forex", "metal", "index", "commodity"):
        now = datetime.now(timezone.utc)
        wd, h = now.weekday(), now.hour
        if wd == 5:                       # Saturday
            return False
        if wd == 6 and h < 22:            # Sunday before reopen
            return False
        if wd == 4 and h >= 22:           # Friday after close
            return False
        if h == 22 and wd != 6:           # daily settlement pause
            return False
        return True
    return None


def provider_status() -> dict:
    """Real status from the last cache interactions — no fabricated states."""
    status = {"chain": ["SPOT-REF", "FX-REF", "VENUE-1", "VENUE-2"]
              + (["KEYED"] if config.has_market_api() else [])}
    if not _cache:
        status.update({"state": "IDLE", "cached": 0})
        return status
    newest = max(v["t"] for v in _cache.values())
    state = "OK" if time.time() - newest < 3600 else "NO_RECENT_DATA"
    status.update({"state": state, "cached": len(_cache), "last_fetch_epoch": newest})
    return status


def execution_quote(symbol: str, api_key: str = "", max_age: float = 120.0) -> dict:
    """Freshness from PROVIDER observation time, never HTTP fetch time.

    Adapters without an observation timestamp remain usable for indicative
    display, NOT proof for a broker dispatch. Proxy quotes are never eligible.
    """
    quote = dict(fetch_price(symbol, api_key))
    quote["ok"] = False
    try:
        observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
        age = (datetime.now(timezone.utc)-observed).total_seconds()
        quote["observation_age_seconds"] = age
        price = float(quote.get("price") or 0)
        quote["ok"] = (not quote.get("is_proxy") and bool(quote.get("provider"))
                       and math.isfinite(price) and price > 0 and -5 <= age <= max_age)
    except (TypeError, ValueError, OverflowError):
        quote["reason"] = "provider observation timestamp missing/invalid"
    return quote
