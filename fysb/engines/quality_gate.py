"""v6.20.6 — Institutional Quality, HTF Alignment & Market-Regime Gate.

Shared 100% identically between the LIVE signal engine (`signal_engine.py`)
and the walk-forward backtester (`backtest.py`) so historical reports match
live execution without discrepancy or repainting.

Guards enforced:
  1. Walk-forward causal HTF resampling (`resample_htf`) — zero lookahead.
  2. Kaufman Efficiency Ratio chop/whipsaw filter — blocks random range noise
     where trend/breakout strategies bleed.
  3. Hard Higher-Timeframe (HTF) directional bias alignment — blocks fighting
     a strong H1/H4 trend unless an extraordinary A++ structural reversal forms.
  4. Minimum institutional confluence score floor (`min_signal_score`, default 72).
  5. Thin rollover / dead-session protection on M5/M15 Gold & Forex.
"""
from __future__ import annotations

from datetime import datetime

from fysb import instruments
from fysb.engines.analysis import atr, ema, structure_labels

DEFAULT_MIN_SCORE = 72


def htf_factor_for(timeframe: str) -> int:
    """Bar aggregation factor from entry TF to Higher Timeframe."""
    tf = (timeframe or "M15").strip().upper()
    return {
        "M1": 15,
        "M5": 6,    # -> M30
        "M15": 4,   # -> H1
        "M30": 4,   # -> H2
        "H1": 4,    # -> H4
        "H4": 6,    # -> D1
    }.get(tf, 4)


def resample_htf(candles: list[dict], factor: int = 4) -> list[dict]:
    """UTC epoch-aligned COMPLETE buckets; no tail drift or LTF masquerade.

    Input timestamps are opening times of completed, regularly spaced bars.
    Missing constituents and partial edge buckets are omitted. A short series
    returns fewer HTF bars, NEVER the original lower-timeframe history.
    """
    from datetime import datetime, timezone
    from collections import Counter
    if not candles or factor <= 1:
        return list(candles or [])
    if len(candles) < 2:
        return []
    def stamp(c):
        dt = datetime.fromisoformat(c["datetime"].replace("Z", "+00:00"))
        return int((dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).timestamp())
    try:
        stamps = [stamp(c) for c in candles]
        diffs = [b-a for a, b in zip(stamps, stamps[1:])]
        if not diffs or min(diffs) <= 0:
            return []
        step = Counter(diffs).most_common(1)[0][0]
        width = step * factor
        groups = {}
        for t, c in zip(stamps, candles):
            groups.setdefault(t // width * width, []).append((t, c))
        out = []
        for start, members in groups.items():
            if [t for t, _ in members] != [start + i*step for i in range(factor)]:
                continue
            grp = [c for _, c in members]
            out.append({
                "datetime": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(grp[0]["open"]),
                "high": max(float(c["high"]) for c in grp),
                "low": min(float(c["low"]) for c in grp),
                "close": float(grp[-1]["close"]),
                "volume": sum(float(c.get("volume") or 0.0) for c in grp),
            })
        return out
    except (ValueError, TypeError, KeyError, OverflowError):
        return []


def efficiency_ratio(candles: list[dict], period: int = 20) -> float:
    """Kaufman Efficiency Ratio in [0.0, 1.0].
      * < 0.18 : severe whipsaw / random chop
      * 0.18–0.28 : mixed transition
      * >= 0.28 : clean directional movement
    """
    if not candles or len(candles) < period + 2:
        return 0.30  # neutral fallback when history is short
    sub = candles[-(period + 1):]
    net = abs(float(sub[-1]["close"]) - float(sub[0]["close"]))
    path = sum(abs(float(sub[i]["close"]) - float(sub[i - 1]["close"])) for i in range(1, len(sub)))
    if path <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, net / path))


def htf_bias_direction(bias_candles: list[dict]) -> tuple[str, float]:
    """Return (direction: 'UP'|'DOWN'|'NEUTRAL', strength_in_atr) from HTF bars."""
    if not bias_candles or len(bias_candles) < 35:
        return "NEUTRAL", 0.0
    closes = [float(c["close"]) for c in bias_candles[-80:]]
    e20 = ema(closes, 20)
    e50 = ema(closes, min(50, len(closes)))
    a = atr(bias_candles[-50:], 14)
    if a <= 1e-12:
        return "NEUTRAL", 0.0
    sep = (e20 - e50) / a
    struct = structure_labels(bias_candles[-60:])
    tr = struct.get("trend", "range")
    if sep >= 0.25 and tr != "down":
        return "UP", abs(sep)
    if sep <= -0.25 and tr != "up":
        return "DOWN", abs(sep)
    if sep >= 0.55:
        return "UP", abs(sep)
    if sep <= -0.55:
        return "DOWN", abs(sep)
    return "NEUTRAL", abs(sep)


def is_thin_session(last_candle: dict, symbol: str, timeframe: str) -> bool:
    """True during thin rollover / late-Asian illiquidity (21:00–05:59 UTC)
    on intraday LTF charts (M5/M15) for non-crypto instruments."""
    tf = (timeframe or "M15").strip().upper()
    if tf not in ("M1", "M5", "M15"):
        return False
    cat = (instruments.spec(symbol).category or "forex").lower()
    if cat == "crypto":
        return False
    raw = str((last_candle or {}).get("datetime") or "").strip()
    if len(raw) < 13:
        return False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
        hr = dt.hour
        return hr >= 21 or hr < 6
    except ValueError:
        return False


def evaluate_quality_gate(
    symbol: str,
    timeframe: str,
    candles: list[dict],
    bias_candles: list[dict],
    cand,
    *,
    min_score: int = DEFAULT_MIN_SCORE,
    strict_htf: bool = True,
    strict_chop: bool = True,
    strict_session: bool = True,
) -> tuple[bool, str]:
    """Institutional quality, HTF alignment, chop & session gate.

    Returns (allowed, rejection_reason). Empty reason when allowed=True.
    """
    score = float(getattr(cand, "score", 0) or 0)
    direction = (getattr(cand, "direction", "") or "").upper()
    checks = getattr(cand, "checks", None) or {}

    # 1) Minimum institutional confluence score
    if score < min_score:
        return False, f"جودة أقل من الحد المؤسسي ({score:.0f} < {min_score})"

    # 2) Thin rollover / dead-session protection on M5/M15 Gold & Forex
    if strict_session and candles and is_thin_session(candles[-1], symbol, timeframe):
        req = min(92, min_score + 6)
        if score < req:
            return False, f"جلسة سيولة منخفضة (21-06 UTC) — مطلوب جودة ≥ {req} (الحالي {score:.0f})"

    # 3) Chop / whipsaw regime filter (Kaufman Efficiency Ratio)
    if strict_chop and len(candles) >= 25:
        er = efficiency_ratio(candles, 20)
        if er < 0.18:
            # Only extraordinary multi-confluence structural sweeps may trade inside deep chop
            strong_reversal = bool(checks.get("sweep") or checks.get("choch")) and score >= min(92, min_score + 12)
            if not strong_reversal:
                return False, f"سوق متذبذب/عشوائي (ER={er:.2f} < 0.18) — تجنّب صفقات المصيدة"

    # 4) Hard Higher-Timeframe (HTF) directional alignment — NEVER fight a clear
    # H1/H4 trend (removes the #1 cause of repeated stop-outs in trending markets).
    if strict_htf and bias_candles and len(bias_candles) >= 35:
        htf_dir, htf_str = htf_bias_direction(bias_candles)
        counter_trend = (htf_dir == "UP" and direction == "SELL") or (htf_dir == "DOWN" and direction == "BUY")
        if counter_trend and htf_str >= 0.30:
            return False, f"معاكس لاتجاه الفريم الأعلى (HTF={htf_dir}, قوة {htf_str:.2f} ATR)"

    # 5) LTF Execution Alignment — NEVER catch a falling knife or short a rocket.
    # Even if HTF bias is still lagging bullish during a reversal, block BUY
    # entries when LTF EMA9 is strongly below EMA21 and the trigger bar is red
    # (and symmetrically for SELL).
    if len(candles) >= 25:
        ltf_closes = [float(c["close"]) for c in candles[-40:]]
        e9 = ema(ltf_closes, 9)
        e21 = ema(ltf_closes, 21)
        a_ltf = atr(candles[-30:], 14)
        if a_ltf > 1e-12:
            ltf_sep = (e9 - e21) / a_ltf
            last_op = float(candles[-1]["open"])
            last_cl = float(candles[-1]["close"])
            if direction == "BUY" and ltf_sep <= -0.35 and last_cl <= last_op:
                return False, f"هبوط حاد على الفريم التنفيذي (EMA9<EMA21, {ltf_sep:.2f} ATR) — منع التقاط السكين الساقطة"
            if direction == "SELL" and ltf_sep >= 0.35 and last_cl >= last_op:
                return False, f"صعود حاد على الفريم التنفيذي (EMA9>EMA21, {ltf_sep:+.2f} ATR) — منع البيع ضد الزخم"

    # 6) v8.8.0 HTF Liquidity Obstruction Guard — NEVER enter directly into a
    # major HTF wall (PDH/PDL or HTF swing) blocking the path to TP1 (2.0R)
    # unless the setup carries extraordinary A++ multi-confluence conviction.
    if checks.get("clear_path_to_2r") is False:
        req_clear = min(92, min_score + 12)
        if score < req_clear:
            return False, f"عائق سيولة على الفريم الأعلى قبل TP1 (مطلوب جودة ≥ {req_clear}، الحالي {score:.0f})"

    return True, ""
