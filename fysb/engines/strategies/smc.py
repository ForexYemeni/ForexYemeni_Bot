"""Smart Money Concepts strategy — BOS/CHOCH, HH/HL/LH/LL, supply & demand.

Entry thesis: a market-structure shift confirms the smart-money direction; price
then returns to a freshly-formed supply (bearish) or demand (bullish) zone.

Score weights (documented, deterministic):
  Structure labels HH/HL (up) or LH/LL (down) ... +14
  BOS in trade direction ........................ +12
  CHOCH confirming reversal ..................... +10
  Supply/Demand zone retrace entry .............. +14
  HTF bias alignment ............................ +12
  ATR regime normal ............................. +4
  RR >= 2.0 ..................................... +6
Base 50. Cap 95.
"""
from fysb.engines.analysis import atr, atr_pct, structure_labels, swings, support_resistance
from fysb.engines.strategies.base import Ctx, StrategyResult, clamp_score


def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    close = candles[-1]["close"]
    struct = structure_labels(candles)
    bias = structure_labels(ctx.bias_candles)

    labels = struct["labels"]
    # Recent-structure read: only the last few labels drive the direction so an
    # old long trend in history cannot outvote the fresh BOS/CHOCH.
    recent = labels[-6:]
    hh = recent.count("HH")
    hl = recent.count("HL")
    lh = recent.count("LH")
    ll = recent.count("LL")
    up_count, down_count = (hh + hl), (lh + ll)

    direction = ""
    if struct["bos"] and struct.get("bos_dir"):
        # A fresh Break of Structure defines the smart-money direction itself.
        direction = "BUY" if struct["bos_dir"] == "up" else "SELL"
    elif struct["choch"]:
        direction = "BUY" if struct["trend"] == "up" else "SELL"
    if not direction:
        return None

    # --- Demand / Supply zone from the last INTACT opposite swing (v6.20.0) --
    # Skip broken swings where price already closed beyond the zone boundary.
    highs, lows = swings(candles, 3)
    a = atr(candles)
    if a <= 0:
        return None
    zone = None
    if direction == "BUY" and lows:
        for base in reversed(lows):
            if close >= base["price"]:
                zone = (base["price"], base["price"] + 1.2 * a)
                break
        if zone is None:
            return None  # All demand zones violated — structure is broken
        entry = min(zone[1], close)
    elif direction == "SELL" and highs:
        for base in reversed(highs):
            if close <= base["price"]:
                zone = (base["price"] - 1.2 * a, base["price"])
                break
        if zone is None:
            return None  # All supply zones violated — structure is broken
        entry = max(zone[0], close)
    else:
        entry = close

    # --- Stop beyond the protective swing ----------------------------------
    if direction == "BUY":
        stop = (zone[0] - 0.6 * a) if zone else entry - 1.8 * a
        if stop >= entry:
            stop = entry - 1.8 * a
    else:
        stop = (zone[1] + 0.6 * a) if zone else entry + 1.8 * a
        if stop <= entry:
            stop = entry + 1.8 * a

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tps = tuple(entry + risk * m if direction == "BUY" else entry - risk * m for m in (2.0, 3.0, 4.5))
    rr = abs(tps[0] - entry) / risk

    # --- Score -------------------------------------------------------------
    score = 50.0
    checks = {}
    checks["labels"] = (direction == "BUY" and up_count > down_count) or (direction == "SELL" and down_count > up_count)
    score += 14 if checks["labels"] else 0
    checks["bos"] = struct["bos"]
    score += 12 if checks["bos"] else 0
    checks["choch"] = struct["choch"]
    score += 10 if checks["choch"] else 0
    checks["zone"] = zone is not None
    score += 14 if zone else 0
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    score += 12 if checks["htf_bias"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    score += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    score += 6 if checks["rr_ok"] else 0

    if score < 55:
        return None

    sr = support_resistance(candles)
    reasons = [
        f"structure labels: {','.join(labels[-6:]) or 'n/a'}",
        f"BOS={'yes' if struct['bos'] else 'no'} / CHOCH={'yes' if struct['choch'] else 'no'}",
        f"entry retraces {'demand' if direction=='BUY' else 'supply'} zone {zone[0]:.5g}-{zone[1]:.5g}" if zone else "entry at market",
        f"HTF bias={bias['trend']}",
        f"S/R: support={sr['support'] if sr['support'] is None else format(sr['support'], '.5g')}, resistance={sr['resistance'] if sr['resistance'] is None else format(sr['resistance'], '.5g')}",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="SMC", direction=direction, entry=entry, stop=stop,
        tp1=tps[0], tp2=tps[1], tp3=tps[2], rr=round(rr, 2),
        score=clamp_score(score), reasons=reasons, checks=checks,
    )
