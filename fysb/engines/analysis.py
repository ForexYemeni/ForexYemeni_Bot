"""Technical analysis core — indicators, market structure, liquidity zones.

All functions are pure and testable: they take plain candle lists and return
measurable values. Every strategy rule later references these primitives, so a
confidence score can be traced to concrete computed checks (no random scores).
"""
from datetime import datetime
from typing import Optional


# --- Basic indicators -------------------------------------------------------
def ema(values: list[float], period: int) -> float:
    """Exponential moving average of the last N values (seeded by SMA)."""
    if not values:
        return 0.0
    period = max(1, min(period, len(values)))
    seed = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    result = seed
    for v in values[period:]:
        result = alpha * v + (1 - alpha) * result
    return result


def rsi(closes: list[float], period: int = 14) -> float:
    """True Wilder-smoothed RSI (RMA) matching TradingView & MT5."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles: list[dict], period: int = 14) -> float:
    """True Wilder-smoothed Average True Range (RMA) matching TradingView & MT5."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    rma = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        rma = (rma * (period - 1) + trs[i]) / period
    return rma


def atr_pct(candles: list[dict], period: int = 14) -> float:
    """ATR as % of last close — the volatility filter input."""
    last = candles[-1]["close"] if candles else 0
    if last <= 0:
        return 0.0
    return atr(candles, period) / last * 100


# --- Market structure (SMC primitives) ---------------------------------------
def swings(candles: list[dict], lookback: int = 3) -> tuple[list[dict], list[dict]]:
    """Detect swing highs/lows (fractal: higher than `lookback` candles both sides).

    Fallback (documented): strictly monotonic series produce no strict fractal
    pivots; in that case chunked window extremes are used so structure remains
    computable (each chunk of 2*lookback bars contributes its extreme).
    Returns (highs, lows) as candle dicts with 'price' added.
    """
    highs, lows = [], []
    n = len(candles)
    for i in range(lookback, n - lookback):
        window = candles[i - lookback: i + lookback + 1]
        if candles[i]["high"] == max(w["high"] for w in window):
            highs.append({**candles[i], "price": candles[i]["high"], "index": i})
        if candles[i]["low"] == min(w["low"] for w in window):
            lows.append({**candles[i], "price": candles[i]["low"], "index": i})
    if len(highs) < 2 and n > lookback * 2:
        chunk = max(2, lookback * 2)
        for start in range(0, n - chunk + 1, chunk):
            seg = candles[start:start + chunk]
            hi = max(range(len(seg)), key=lambda j: seg[j]["high"])
            highs.append({**seg[hi], "price": seg[hi]["high"], "index": start + hi})
    if len(lows) < 2 and n > lookback * 2:
        chunk = max(2, lookback * 2)
        for start in range(0, n - chunk + 1, chunk):
            seg = candles[start:start + chunk]
            lo_i = min(range(len(seg)), key=lambda j: seg[j]["low"])
            lows.append({**seg[lo_i], "price": seg[lo_i]["low"], "index": start + lo_i})
    return highs, lows


def structure_labels(candles: list[dict]) -> dict:
    """Classify recent structure: HH/HL/LH/LL, BOS, CHOCH.

    Labels are chronological (ordered by bar index), so the recent tail is a
    faithful read of the latest market structure.
    Returns {"labels": [...], "bos": bool, "choch": bool, "trend": 'up'|'down'|'range'}
    """
    highs, lows = swings(candles, 3)
    marks: list[tuple[int, str]] = []
    for i in range(1, len(highs)):
        marks.append((int(highs[i]["index"]), "HH" if highs[i]["price"] > highs[i - 1]["price"] else "LH"))
    for i in range(1, len(lows)):
        marks.append((int(lows[i]["index"]), "HL" if lows[i]["price"] > lows[i - 1]["price"] else "LL"))
    marks.sort(key=lambda m: m[0])
    labels = [m[1] for m in marks]

    closes = [c["close"] for c in candles]
    trend = "range"
    if len(highs) >= 2 and len(lows) >= 2:
        up = highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]
        down = highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]
        trend = "up" if up else ("down" if down else "range")

    # BOS: close beyond the last confirmed swing in trend direction
    bos, choch, bos_dir = False, False, ""
    if highs and lows:
        last_high = max(h["price"] for h in highs[-3:])
        last_low = min(l["price"] for l in lows[-3:])
        if closes[-1] > last_high:
            bos = trend == "up" or trend == "range"
            choch = trend == "down"
            bos_dir = "up"
        elif closes[-1] < last_low:
            bos = trend == "down" or trend == "range"
            choch = trend == "up"
            bos_dir = "down"
    return {"labels": labels, "bos": bos, "choch": choch, "trend": trend, "bos_dir": bos_dir}


def liquidity_sweep(candles: list[dict], lookback: int = 20) -> dict:
    """Detect recent liquidity sweeps: wick beyond prior extreme, close back inside.

    Returns {"buy_side": bool, "sell_side": bool, "level": float|None, "bars_ago": int}
      buy_side sweep  = high pierced a prior high (BSL taken) then closed below it
      sell_side sweep = low pierced a prior low (SSL taken) then closed above it
    """
    out = {"buy_side": False, "sell_side": False, "level": None, "bars_ago": None}
    n = len(candles)
    if n < lookback + 2:
        return out
    scan = min(10, n - lookback - 1)
    for i in range(n - 1, n - 1 - scan, -1):
        c = candles[i]
        prior_highs = [k["high"] for k in candles[max(0, i - lookback):i]]
        prior_lows = [k["low"] for k in candles[max(0, i - lookback):i]]
        if not prior_highs or not prior_lows:
            continue
        ph, pl = max(prior_highs), min(prior_lows)
        if c["high"] > ph and c["close"] < ph:
            out.update(buy_side=True, level=ph, bars_ago=n - 1 - i)
            break
        if c["low"] < pl and c["close"] > pl:
            out.update(sell_side=True, level=pl, bars_ago=n - 1 - i)
            break
    return out


def fair_value_gaps(candles: list[dict], limit: int = 3) -> list[dict]:
    """FVG: 3-candle imbalance — gap between candle1 high and candle3 low (bullish)
    or candle1 low and candle3 high (bearish). Newest first, limited."""
    gaps = []
    for i in range(len(candles) - 2, 0, -1):
        a, _b, c = candles[i - 1], candles[i], candles[i + 1]
        if c["low"] > a["high"]:
            gaps.append({"kind": "bullish", "lo": a["high"], "hi": c["low"], "index": i + 1})
        elif c["high"] < a["low"]:
            gaps.append({"kind": "bearish", "lo": c["high"], "hi": a["low"], "index": i + 1})
        if len(gaps) >= limit:
            break
    return gaps


def order_blocks(candles: list[dict], limit: int = 3) -> list[dict]:
    """Order blocks: last opposite candle before a displacement move.

    Bullish OB = last bearish candle before strong bullish displacement;
    Bearish OB = last bullish candle before strong bearish displacement.
    Displacement = candle range > 1.5 * average range of previous 10.
    """
    obs = []
    n = len(candles)
    for i in range(n - 2, 2, -1):
        c = candles[i + 1]          # displacement candidate
        base = candles[i]           # potential OB
        avg_range = sum(k["high"] - k["low"] for k in candles[i - 10:i]) / max(1, min(10, i))
        if avg_range <= 0:
            continue
        disp = (c["high"] - c["low"]) > 1.5 * avg_range
        if not disp:
            continue
        if c["close"] > c["open"] and base["close"] < base["open"]:
            obs.append({"kind": "bullish", "lo": base["low"], "hi": max(base["open"], base["close"]), "index": i})
        elif c["close"] < c["open"] and base["close"] > base["open"]:
            obs.append({"kind": "bearish", "lo": min(base["open"], base["close"]), "hi": base["high"], "index": i})
        if len(obs) >= limit:
            break
    return obs


def premium_discount(candles: list[dict]) -> dict:
    """Dealing range from the last significant swing high/low; current zone."""
    highs, lows = swings(candles, 3)
    if not highs or not lows:
        return {"zone": "unknown", "pos": 0.5}
    hi = max(h["price"] for h in highs[-5:])
    lo = min(l["price"] for l in lows[-5:])
    if hi <= lo:
        return {"zone": "unknown", "pos": 0.5}
    pos = (candles[-1]["close"] - lo) / (hi - lo)
    zone = "premium" if pos > 0.55 else ("discount" if pos < 0.45 else "equilibrium")
    return {"zone": zone, "pos": pos, "high": hi, "low": lo}


def support_resistance(candles: list[dict], tolerance_pct: float = 0.15) -> dict:
    """Cluster recent swing levels into S/R zones around current price."""
    highs, lows = swings(candles, 3)
    levels = [h["price"] for h in highs] + [l["price"] for l in lows]
    if not levels or not candles:
        return {"support": None, "resistance": None}
    last = candles[-1]["close"]
    below = [x for x in levels if x < last]
    above = [x for x in levels if x > last]
    sup = max(below) if below else None
    res = min(above) if above else None
    return {"support": sup, "resistance": res}


# --- Candlestick patterns ----------------------------------------------------
def candle_patterns(candles: list[dict], limit: int = 3) -> list[str]:
    """Detect classic patterns on the most recent candles."""
    if len(candles) < 3:
        return []
    out = []
    for i in range(len(candles) - min(limit, len(candles) - 1), len(candles)):
        c, p = candles[i], candles[i - 1]
        body = abs(c["close"] - c["open"])
        range_ = max(c["high"] - c["low"], 1e-12)
        upper_wick = c["high"] - max(c["close"], c["open"])
        lower_wick = min(c["close"], c["open"]) - c["low"]
        if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] > p["open"] and c["open"] < p["close"]:
            out.append("ENGULFING_BULL")
        if c["close"] < c["open"] and p["close"] > p["open"] and c["close"] < p["open"] and c["open"] > p["close"]:
            out.append("ENGULFING_BEAR")
        if lower_wick > 2 * body and upper_wick < body and c["close"] >= c["open"]:
            out.append("PIN_BAR_BULL")
        if upper_wick > 2 * body and lower_wick < body and c["close"] <= c["open"]:
            out.append("PIN_BAR_BEAR")
        if body / range_ < 0.1:
            out.append("DOJI")
    return out


def breakout_retest(candles: list[dict]) -> dict:
    """Breakout with honest retest: close beyond N-bar extreme; retest requires
    a bar that touched/probed the broken level AND HELD on the breakout side."""
    n = len(candles)
    if n < 25:
        return {"breakout": None, "retest": False}
    window = candles[-15:-1]
    prior_high = max(k["high"] for k in window)
    prior_low = min(k["low"] for k in window)
    a = atr(candles)
    tol = 0.15 * a if a > 0 else 0.0
    last = candles[-1]
    out = {"breakout": None, "retest": False, "level": None}
    if last["close"] > prior_high:
        out.update(breakout="UP", level=prior_high)
        # Check whether the current or recent breakout bar retested and held above prior_high
        for k in reversed(candles[-5:]):
            if k["low"] <= prior_high + tol and k["close"] >= prior_high:
                out["retest"] = True
                break
    elif last["close"] < prior_low:
        out.update(breakout="DOWN", level=prior_low)
        for k in reversed(candles[-5:]):
            if k["high"] >= prior_low - tol and k["close"] <= prior_low:
                out["retest"] = True
                break
    return out


_BAR_SECONDS = {
    "M1": 60, "1MIN": 60,
    "M5": 300, "5MIN": 300,
    "M15": 900, "15MIN": 900,
    "M30": 1800, "30MIN": 1800,
    "H1": 3600, "1H": 3600,
    "H4": 14400, "4H": 14400,
    "D1": 86400, "1DAY": 86400,
}


def confirmed_candles(candles: list[dict], timeframe: str, now_utc: Optional[datetime] = None) -> list[dict]:
    """v6.20.0 Non-Repainting Guard: strip the trailing candle ONLY when it is
    genuinely still open/forming (open_time <= now < open_time + bar_duration).
    Already-closed candles and historical/backtest series are preserved intact."""
    if not candles or len(candles) < 2:
        return candles
    bar_sec = _BAR_SECONDS.get((timeframe or "").strip().upper(), 0)
    if bar_sec <= 0:
        return candles
    raw_dt = str(candles[-1].get("datetime") or "").strip()
    if not raw_dt:
        return candles
    try:
        from datetime import timezone
        bar_open = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
        if bar_open.tzinfo is None:
            bar_open = bar_open.replace(tzinfo=timezone.utc)
        else:
            bar_open = bar_open.astimezone(timezone.utc)
        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed = (now - bar_open).total_seconds()
        # Strictly forming bar: opened in the past, not yet closed (with 2s clock skew grace)
        if 0.0 <= elapsed < (bar_sec - 2.0):
            return candles[:-1]
    except Exception:
        pass
    return candles


def _parse_utc_dt(dt_iso: str) -> Optional[datetime]:
    if not dt_iso:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(str(dt_iso).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def ny_hour(dt_iso: str) -> Optional[int]:
    """v8.0.0: Exact DST-aware America/New_York hour (0..23) for any UTC candle timestamp."""
    dt_utc = _parse_utc_dt(dt_iso)
    if dt_utc is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("America/New_York")).hour
    except Exception:
        # Fallback approximation (EST = UTC-5) if system lacks tzdata
        return (dt_utc.hour - 5) % 24


def session_levels(candles: list[dict]) -> dict:
    """v8.0.0: Extract Previous Day High/Low (PDH/PDL) and Asian Session High/Low (00-06 UTC)."""
    if not candles or len(candles) < 10:
        return {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None}
    last_dt = _parse_utc_dt(candles[-1].get("datetime", ""))
    if last_dt is None:
        return {"pdh": None, "pdl": None, "asian_high": None, "asian_low": None}
    cur_day = last_dt.date()
    prev_day_bars = []
    asian_bars = []
    seen_prev_date = None
    for c in reversed(candles):
        dt = _parse_utc_dt(c.get("datetime", ""))
        if dt is None:
            continue
        d = dt.date()
        if d == cur_day and 0 <= dt.hour < 6:
            asian_bars.append(c)
        elif d < cur_day:
            if seen_prev_date is None:
                seen_prev_date = d
            if d == seen_prev_date:
                prev_day_bars.append(c)
            elif d < seen_prev_date:
                break
    return {
        "pdh": max(float(c["high"]) for c in prev_day_bars) if prev_day_bars else None,
        "pdl": min(float(c["low"]) for c in prev_day_bars) if prev_day_bars else None,
        "asian_high": max(float(c["high"]) for c in asian_bars) if len(asian_bars) >= 2 else None,
        "asian_low": min(float(c["low"]) for c in asian_bars) if len(asian_bars) >= 2 else None,
    }


# --- ICT session (kill zones) — DST-aware New York clock (v8.0.0) ------------
def kill_zone(dt_iso: str) -> Optional[str]:
    """Return the active ICT kill zone using DST-aware America/New_York time:
      London Killzone:   02:00–05:00 NY (07-10 UTC winter / 06-09 UTC summer)
      New York Killzone: 07:00–10:00 NY (12-15 UTC winter / 11-14 UTC summer)
    """
    h = ny_hour(dt_iso)
    if h is None:
        return None
    if 2 <= h < 5:
        return "LONDON"
    if 7 <= h < 10:
        return "NEW_YORK"
    return None

