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

from fysb import config
from fysb.config import log

BASE_URL_TD = "https://api.twelvedata.com/time_series"
QUOTE_URL_TD = "https://api.twelvedata.com/price"
# Venue endpoints are env-overridable (config.BINANCE_BASE_URL / BYBIT_BASE_URL)
# so a geo-blocked or firewalled runtime can point the same chain at a mirror
# without touching code. Call sites read config directly (always current).
BASE_URL_GOLD_API = "https://api.gold-api.com/price"
YAHOO_BASES = ("https://query1.finance.yahoo.com/v8/finance/chart/",
               "https://query2.finance.yahoo.com/v8/finance/chart/")

CACHE_TTL_SECONDS = 25           # short TTL — quotes must stay "لحظية"
PROVIDER_COOLDOWN_SECONDS = 45   # after a failure, pause that provider
MIN_CANDLES = 35
CALIB_MAX_RATIO = 0.02           # reject absurd calibration offsets (>2%)
CALIB_MAX_AGE = 48 * 3600        # persisted offset older than this is ignored

# Symbol maps (internal normalized symbol -> venue/reference symbol).
YAHOO_FX_MAP = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
}
BINANCE_MAP = {
    "XAUUSD": "PAXGUSDT",
    "BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT", "ADAUSD": "ADAUSDT", "DOGEUSD": "DOGEUSDT",
}
BYBIT_MAP = {
    "BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT", "ADAUSD": "ADAUSDT", "DOGEUSD": "DOGEUSDT",
}
GOLD_API_MAP = {   # real spot quote reference (no candles)
    "XAUUSD": "XAU", "XAGUSD": "XAG", "BTCUSD": "BTC", "ETHUSD": "ETH",
}
TWELVEDATA_MAP = {
    "XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD", "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "BTCUSD": "BTC/USD",
    "ETHUSD": "ETH/USD",
}
FOREX_ONLY = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}

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
    "gold":   ["venue-gold", "twelvedata"],
    "metal":  ["twelvedata"],
    "fx":     ["fx-ref", "twelvedata"],
    "crypto": ["binance", "bybit", "twelvedata"],
}
_QUOTE_CHAINS = {
    "gold":   ["spot-ref", "venue-gold", "twelvedata"],
    "metal":  ["spot-ref", "twelvedata"],
    "fx":     ["fx-ref", "twelvedata"],
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
    if internal in GOLD_API_MAP:          # XAGUSD — real spot quote, no venue candles
        return "metal"
    if internal in YAHOO_FX_MAP:
        return "fx"
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
    entry = _fail_streak.get(_ck(provider, symbol, interval))
    if not entry:
        return False
    until, _streak = entry
    return time.time() < until


def _mark_failure(provider: str, symbol: str, interval: str) -> None:
    key = _ck(provider, symbol, interval)
    streak = _fail_streak.get(key, (0.0, 0))[1] + 1
    _fail_streak[key] = (time.time() + PROVIDER_COOLDOWN_SECONDS * min(streak, 4), streak)


def _mark_success(provider: str, symbol: str, interval: str) -> None:
    _fail_streak.pop(_ck(provider, symbol, interval), None)


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


def _gold_offset_now(internal: str, venue_symbol: str) -> float | None:
    """Live offset from the real spot reference vs the venue gold token."""
    spot_ticker = GOLD_API_MAP.get(internal)
    if not spot_ticker:
        return None
    try:
        spot = _fetch_goldapi_price(spot_ticker)["price"]
        venue = _fetch_binance_price(venue_symbol)
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
    out = []
    for row in raw:
        try:
            dt = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            item = {"datetime": dt, "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4])}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) and item["high"] >= item["low"]:
                out.append(item)
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _fetch_binance_price(venue_symbol: str) -> float:
    raw = _http_json(config.BINANCE_BASE_URL + "/api/v3/ticker/price?" + urllib.parse.urlencode({"symbol": venue_symbol}), timeout=10)
    return float(raw["price"])


# --- provider: VENUE-2 (Bybit) ---------------------------------------------------
def _bybit_interval(interval: str) -> str:
    return {"1min": "1", "5min": "5", "15min": "15", "30min": "30",
            "1h": "60", "4h": "240", "1day": "D", "1week": "W"}.get(interval, "15")


def _fetch_bybit_candles(venue_symbol: str, interval: str, outputsize: int) -> list[dict]:
    q = urllib.parse.urlencode({"category": "spot", "symbol": venue_symbol,
                                "interval": _bybit_interval(interval), "limit": max(40, min(outputsize, 1000))})
    raw = _http_json(config.BYBIT_BASE_URL + "/v5/market/kline?" + q)
    rows = ((raw.get("result") or {}).get("list") or [])
    out = []
    for row in reversed(rows):  # newest-first -> oldest-first
        try:
            dt = datetime.fromtimestamp(int(row[0]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            item = {"datetime": dt, "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4])}
            if all(math.isfinite(item[k]) for k in ("open", "high", "low", "close")) and item["high"] >= item["low"]:
                out.append(item)
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _fetch_bybit_price(venue_symbol: str) -> float:
    raw = _http_json(config.BYBIT_BASE_URL + "/v5/market/tickers?" + urllib.parse.urlencode({"category": "spot", "symbol": venue_symbol}), timeout=10)
    return float(raw["result"]["list"][0]["lastPrice"])


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
              cache_key, raw_symbol: str) -> dict:
    values.sort(key=lambda x: x["datetime"])
    if len(values) < MIN_CANDLES:
        raise RuntimeError(f"insufficient valid candles: {len(values)} (need at least {MIN_CANDLES})")
    last_iso = values[-1]["datetime"]
    age = _candle_age_seconds(last_iso, interval)
    result = {
        "symbol": raw_symbol, "interval": interval, "values": values,
        "fetched_at": config.now(), "fetched_epoch": time.time(),
        "last_candle_iso": last_iso, "age_seconds": age, "source": source,
    }
    _mark_success(source, internal, interval)
    _last_provider[internal] = source
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
      * gold   : VENUE gold candles CALIBRATED to real spot -> KEYED -> error
      * metal  : KEYED only (silver has no venue candles; quotes use SPOT-REF)
      * forex  : FX-REF real spot candles -> KEYED -> error
      * crypto : VENUE-1 -> VENUE-2 -> KEYED -> error
    Raises RuntimeError with SANITIZED Arabic messages when everything fails —
    callers must never fabricate prices.
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
    chain = _ordered_chain(_CANDLE_CHAINS.get(cls, []), config.PRICE_PROVIDER,
                           config.has_market_api())
    venue = _provider_key(internal) if internal != "XAUUSD" else None

    for provider in chain:
        if in_cooldown(provider, internal, interval):
            errors.append(f"{provider}:cooldown")
            continue
        try:
            if provider == "venue-gold":
                v = BINANCE_MAP[internal]
                values = _fetch_binance_candles(v, interval, outputsize)
                offset = _gold_offset_now(internal, v)
                if offset is None:
                    offset = _persisted_offset(internal) or 0.0
                values = _apply_offset(values, offset)
            elif provider == "fx-ref":
                values = _fetch_yahoo_candles(YAHOO_FX_MAP[internal], interval, outputsize)
            elif provider == "binance":
                values = _fetch_binance_candles(venue, interval, outputsize)
            elif provider == "bybit":
                values = _fetch_bybit_candles(BYBIT_MAP[internal], interval, outputsize)
            elif provider == "twelvedata":
                values = _fetch_td_candles(_td_symbol(internal, symbol), interval,
                                           config.market_api_key(), outputsize)
            else:
                continue
            return _assemble(internal, interval, values, "LIVE", use_cache, cache_key, symbol)
        except Exception as exc:
            _mark_failure(provider, internal, interval)
            errors.append(f"{provider}:" + type(exc).__name__)

    if internal in FOREX_ONLY and not config.has_market_api():
        raise RuntimeError("تعذر جلب شموع الفوركس من المرجع اللحظي — أعد المحاولة بعد قليل")

    raise RuntimeError("تعذر جلب بيانات لحظية لهذه الأداة الآن — أعد المحاولة بعد قليل")


def fetch_price(symbol: str, api_key: str = "") -> dict:
    """Latest REAL quote — headline prices must match the actual market."""
    internal = _norm(symbol)
    cls = _class_of(internal)
    chain = _ordered_chain(_QUOTE_CHAINS.get(cls, []), config.PRICE_PROVIDER,
                           config.has_market_api())
    venue = _provider_key(internal) if internal != "XAUUSD" else None

    for provider in chain:
        if in_cooldown(provider, internal, "quote"):
            continue
        try:
            if provider == "spot-ref":
                ref = _fetch_goldapi_price(GOLD_API_MAP[internal])
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": ref["price"], "fetched_at": config.now(),
                        "source": "LIVE", "spot_ref": True}
            if provider == "fx-ref":
                ref = _fetch_yahoo_quote(YAHOO_FX_MAP[internal])
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": ref["price"], "fetched_at": config.now(),
                        "source": "LIVE", "spot_ref": True}
            if provider == "venue-gold":
                v = BINANCE_MAP[internal]
                venue_price = _fetch_binance_price(v)
                offset = _gold_offset_now(internal, v)
                if offset is None:
                    offset = _persisted_offset(internal)
                if offset:
                    venue_price = venue_price + offset
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": venue_price, "fetched_at": config.now(),
                        "source": "LIVE", "spot_ref": bool(offset)}
            if provider == "binance":
                price = _fetch_binance_price(venue)
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": price, "fetched_at": config.now(), "source": "LIVE"}
            if provider == "bybit":
                price = _fetch_bybit_price(BYBIT_MAP[internal])
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": price, "fetched_at": config.now(), "source": "LIVE"}
            if provider == "twelvedata":
                price = _fetch_td_price(_td_symbol(internal, symbol), config.market_api_key())
                _last_provider[internal] = "LIVE"
                return {"symbol": symbol, "price": price, "fetched_at": config.now(), "source": "LIVE"}
        except Exception:
            _mark_failure(provider, internal, "quote")
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
