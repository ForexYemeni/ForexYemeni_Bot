"""Institutional anchors — session VWAP, opening-range anchors, classic pivots.

Everything here is computed ONLY from supplied OHLCV candles: no network, no
fabrication, no look-ahead (each anchor is derived from candles the strategy
already has). Honest-by-construction rules:

  * Session VWAP — cumulative Σ(typical × volume) / Σ(volume) over the CURRENT
    UTC day. When every supplied candle lacks real volume, VWAP is undefined:
    the module returns kind="SESSION_MEAN" (unweighted mean of typical price)
    and volume_used=False — it NEVER labels a mean as VWAP.
  * Opening anchors — NY Midnight Open (00:00 America/New_York), London Open
    (first candle of 08:00 Europe/London hour), NY Equities Open (09:30 → the
    09:00-hour candle on coarse timeframes, labelled "opening hour"), Weekly
    Open (open of the first candle of the current ISO week, UTC).
  * Classic daily pivots (P, S1..S3, R1..R3) from the PREVIOUS UTC day H/L/C.

Anchor dicts carry the ISO timestamp they were derived from so callers can
display honest provenance; missing data returns None entries, never guesses.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_LDN = ZoneInfo("Europe/London")


def _utc(dt_iso: str) -> datetime:
    dt = datetime.strptime(dt_iso, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def _last_day(candles: list[dict]):
    """(last_candle_utc, its UTC date)."""
    return _utc(candles[-1]["datetime"]), _utc(candles[-1]["datetime"]).date()


def session_vwap(candles: list[dict]) -> dict | None:
    """Current UTC day's VWAP (with real volume) or unweighted session mean."""
    if not candles:
        return None
    day = _last_day(candles)[0].date()
    todays = [c for c in candles if _utc(c["datetime"]).date() == day]
    if not todays:
        return None
    typs = [(float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0 for c in todays]
    vols = [float(c.get("volume") or 0.0) for c in todays]
    vol_sum = sum(vols)
    if vol_sum > 0:
        return {
            "level": sum(t * v for t, v in zip(typs, vols)) / vol_sum,
            "kind": "VWAP",
            "volume_used": True,
            "day_iso": day.isoformat(),
            "bars": len(todays),
        }
    return {
        "level": sum(typs) / len(typs),
        "kind": "SESSION_MEAN",
        "volume_used": False,
        "day_iso": day.isoformat(),
        "bars": len(todays),
    }


def _first_open_of(candles: list[dict], pred) -> dict | None:
    for c in candles:
        if pred(c):
            return {"price": float(c["open"]), "iso": c["datetime"]}
    return None


def opening_anchors(candles: list[dict]) -> dict:
    """NY Midnight / London / NY-equities / Weekly opening anchors.

    Each anchor = the OPEN of the first candle of that session boundary in the
    current session day; None when the supplied history does not reach it.
    """
    out: dict = {"midnight_ny": None, "london": None, "ny_open": None, "weekly": None}
    if not candles:
        return out
    last_dt = _utc(candles[-1]["datetime"])
    last_ny_date = last_dt.astimezone(_NY).date()
    last_ldn_date = last_dt.astimezone(_LDN).date()
    for c in candles:
        dt = _utc(c["datetime"])
        if out["midnight_ny"] is None:
            ny = dt.astimezone(_NY)
            if ny.date() == last_ny_date and ny.hour == 0:
                out["midnight_ny"] = {"price": float(c["open"]), "iso": c["datetime"]}
        if out["london"] is None:
            ldn = dt.astimezone(_LDN)
            if ldn.date() == last_ldn_date and ldn.hour == 8:
                out["london"] = {"price": float(c["open"]), "iso": c["datetime"]}
        if out["ny_open"] is None:
            ny = dt.astimezone(_NY)
            if ny.date() == last_ny_date and ny.hour == 9:
                out["ny_open"] = {"price": float(c["open"]), "iso": c["datetime"]}
    week_start = (last_dt - timedelta(days=last_dt.weekday())).date()
    out["weekly"] = _first_open_of(candles, lambda c: _utc(c["datetime"]).date() == week_start)
    return out


def classic_pivots(candles: list[dict]) -> dict | None:
    """Classic floor-trader pivots from the PREVIOUS UTC day's H/L/C."""
    if not candles:
        return None
    prev_day = (_last_day(candles)[0] - timedelta(days=1)).date()
    prev = [c for c in candles if _utc(c["datetime"]).date() == prev_day]
    if not prev:
        return None
    h = max(float(c["high"]) for c in prev)
    l = min(float(c["low"]) for c in prev)
    cl = float(prev[-1]["close"])
    p = (h + l + cl) / 3.0
    return {
        "P": p,
        "R1": 2 * p - l, "S1": 2 * p - h,
        "R2": p + (h - l), "S2": p - (h - l),
        "R3": h + 2 * (p - l), "S3": l - 2 * (h - p),
        "day_iso": prev_day.isoformat(),
    }


def nearest_anchor_distance(price: float, anchors: dict) -> tuple[str, float] | None:
    """(anchor_name, |price − anchor|) for the closest available anchor."""
    best = None
    for name, a in (anchors or {}).items():
        if not a or not isinstance(a, dict) or not a.get("price"):
            continue
        d = abs(float(price) - float(a["price"]))
        if best is None or d < best[1]:
            best = (name, d)
    return best


def pivot_confluence(price: float, pivots: dict, tol_atr: float) -> str | None:
    """Name of the nearest classic pivot level within `tol_atr` (None otherwise)."""
    if not pivots:
        return None
    best_name, best_d = None, None
    for k in ("P", "R1", "S1", "R2", "S2", "R3", "S3"):
        d = abs(float(price) - float(pivots[k]))
        if best_d is None or d < best_d:
            best_name, best_d = k, d
    if best_d is not None and best_d <= tol_atr:
        return best_name
    return None


def opening_gaps(candles: list[dict], a: float) -> dict:
    """NWOG / NDOG — opening gaps computed from the candle timeline itself.

    NDOG (New Day Opening Gap): today's first open vs yesterday's last close
    (UTC dates, requires both sessions present in the series).
    NWOG (New Week Opening Gap): this ISO week's first open vs the last close
    of the previous ISO week (requires both weeks present).
    A gap exists when |open - close| >= 0.3 * ATR. Returns honest empties when
    the timeline does not cover the boundaries. Pure arithmetic; a gap is a
    price hole, not a known order cluster.
    """
    out = {"ndog": None, "nwog": None}
    if len(candles) < 10 or a <= 0:
        return out
    by_date: dict[str, list[dict]] = {}
    by_week: dict[str, list[dict]] = {}
    for c in candles:
        d = str(c["datetime"])[:10]
        by_date.setdefault(d, []).append(c)
        y, m, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
        iso = datetime(y, m, dd).isocalendar()
        by_week.setdefault(f"{iso[0]}-W{iso[1]:02d}", []).append(c)
    dates = sorted(by_date)
    weeks = sorted(by_week)
    if len(dates) >= 2:
        prev_last = by_date[dates[-2]][-1]
        cur_first = by_date[dates[-1]][0]
        gap = float(cur_first["open"]) - float(prev_last["close"])
        if abs(gap) >= 0.3 * a:
            out["ndog"] = {"level": float(cur_first["open"]), "gap": gap,
                           "dir": "BUY" if gap > 0 else "SELL", "date": dates[-1]}
    if len(weeks) >= 2:
        prev_last = by_week[weeks[-2]][-1]
        cur_first = by_week[weeks[-1]][0]
        gap = float(cur_first["open"]) - float(prev_last["close"])
        if abs(gap) >= 0.3 * a:
            out["nwog"] = {"level": float(cur_first["open"]), "gap": gap,
                           "dir": "BUY" if gap > 0 else "SELL", "week": weeks[-1]}
    return out
