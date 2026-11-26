"""ICT — project-specific price-pattern model, not institutional order-flow data.

Direction uses HTF structure with sweep/structure context. A trigger needs a
matching sweep OR a directional zone + gap-width displacement + local structure.
FVG/OB selection checks the latest close against the zone, not a complete zone
mitigation ledger. Entry is zone midpoint clipped to close, or close without zone.
Stop: recent eight-bar extreme/zone edge +/- 0.85 ATR, widened if too tight.
Targets: planned 2R/3R/4.5R, not proof of reachable liquidity.
Weights: HTF12, sweep14, displacement10, zone12, OB8, PD10, MSS8,
kill-zone6, volatility4, RR6 (total90). Minimum score58; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import (
    atr, atr_pct, fair_value_gaps, kill_zone, liquidity_sweep, order_blocks,
    premium_discount, structure_labels,
)
from fysb.engines.strategies.base import (
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        return None
    last = candles[-1]
    close = last["close"]

    # --- Higher timeframe bias -------------------------------------------
    bias = structure_labels(ctx.bias_candles)
    bias_up, bias_down = bias["trend"] == "up", bias["trend"] == "down"

    # --- Liquidity sweep ---------------------------------------------------
    sweep = liquidity_sweep(candles)
    sweep_dir = "BUY" if sweep["buy_side"] else ("SELL" if sweep["sell_side"] else "")
    sweep_scored = sweep_dir in ("BUY", "SELL")

    # --- Displacement / FVG / OB ------------------------------------------
    obs = order_blocks(candles, 2)
    fvgs = fair_value_gaps(candles, 3)
    displacement = any(abs(f["hi"] - f["lo"]) > atr(candles) for f in fvgs)

    # --- Premium/Discount ---------------------------------------------------
    pd = premium_discount(candles)

    # --- Decide direction: bias first, then sweep confirmation --------------
    mss = structure_labels(candles)
    direction = ""
    if bias_up and sweep_dir in ("", "SELL"):
        direction = "BUY"          # HTF up + (no sweep or SSL taken) = bullish context
    elif bias_down and sweep_dir in ("", "BUY"):
        direction = "SELL"
    elif sweep_dir == "SELL" and (mss["choch"] or mss["bos"]) and mss["trend"] != "down":
        direction = "BUY"          # SSL sweep + structure shift up = reversal continuation
    elif sweep_dir == "BUY" and (mss["choch"] or mss["bos"]) and mss["trend"] != "up":
        direction = "SELL"
    if not direction:
        return None

    # --- Entry: retrace into the nearest INTACT FVG/OB in trade direction ---
    # v6.19.0: skip zones already broken against the trade direction (BUY with
    # close below zone low, or SELL with close above zone high) and never place
    # a limit entry already in drawdown against current close.
    entry_zone = None
    for f in fvgs:
        if f["kind"] == ("bullish" if direction == "BUY" else "bearish"):
            if direction == "BUY" and close < f["lo"]:
                continue
            if direction == "SELL" and close > f["hi"]:
                continue
            entry_zone = (f["lo"], f["hi"])
            break
    if entry_zone is None:
        for ob in obs:
            if ob["kind"] == ("bullish" if direction == "BUY" else "bearish"):
                if direction == "BUY" and close < ob["lo"]:
                    continue
                if direction == "SELL" and close > ob["hi"]:
                    continue
                entry_zone = (ob["lo"], ob["hi"])
                break
    if entry_zone:
        z_mid = (entry_zone[0] + entry_zone[1]) / 2
        entry = min(z_mid, close) if direction == "BUY" else max(z_mid, close)
    else:
        entry = close

    # v6.20.6: True ICT trigger guard — never fire blindly on every bar merely
    # because HTF bias is up/down. Require EITHER a matching liquidity sweep
    # (SSL for BUY / BSL for SELL) OR an intact FVG/OB zone backed by
    # displacement + matching structure. Also require the trigger candle not
    # to close strongly against our entry direction without a sweep.
    has_matching_sweep = (sweep["sell_side"] and direction == "BUY") or (sweep["buy_side"] and direction == "SELL")
    has_zone_trigger = (entry_zone is not None) and displacement and (
        (direction == "BUY" and mss["trend"] != "down" and close >= float(last["open"]))
        or (direction == "SELL" and mss["trend"] != "up" and close <= float(last["open"]))
    )
    if not (has_matching_sweep or has_zone_trigger):
        return None

    # --- Stop: beyond the swept liquidity level / recent extreme -----------
    a = atr(candles)
    if a <= 0:
        return None
    if direction == "BUY":
        swing_low = min(c["low"] for c in candles[-8:])
        stop = min(entry_zone[0] if entry_zone else swing_low, swing_low) - 0.85 * a
    else:
        swing_high = max(c["high"] for c in candles[-8:])
        stop = max(entry_zone[1] if entry_zone else swing_high, swing_high) + 0.85 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.15, fallback_atr=1.6, max_atr=2.8, cap_atr=2.4)

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5,
        direction,
    )

    # --- Normalized Fair Score (v8.0.0) + FIX checks["mss"] (SIG-6) --------
    earned = 0.0
    max_pts = 90.0
    checks = {}
    checks["htf_bias"] = (bias_up and direction == "BUY") or (bias_down and direction == "SELL")
    earned += 12 if checks["htf_bias"] else 0
    checks["sweep"] = (sweep["sell_side"] and direction == "BUY") or (sweep["buy_side"] and direction == "SELL")
    earned += 14 if checks["sweep"] else 0
    checks["displacement"] = displacement
    earned += 10 if displacement else 0
    checks["fvg_zone"] = entry_zone is not None
    earned += 12 if entry_zone else 0
    checks["ob_confluence"] = any(o["kind"] == ("bullish" if direction == "BUY" else "bearish") for o in obs)
    earned += 8 if checks["ob_confluence"] else 0
    if pd.get("high") and pd.get("low") and pd["high"] > pd["low"]:
        entry_pos = (entry - pd["low"]) / (pd["high"] - pd["low"])
        checks["pd_align"] = (direction == "BUY" and entry_pos <= 0.75) or (direction == "SELL" and entry_pos >= 0.25)
    else:
        checks["pd_align"] = pd["zone"] in ("discount", "equilibrium", "premium")
    earned += 10 if checks["pd_align"] else 0
    checks["mss"] = bool(mss["choch"] or mss["bos"])
    earned += 8 if checks["mss"] else 0
    kz = ctx.kill_zone or (kill_zone(candles[-1]["datetime"]) or "")
    checks["kill_zone"] = bool(kz)
    earned += 6 if checks["kill_zone"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        return None

    reasons = [
        f"HTF bias={bias['trend']}",
        f"liquidity sweep {'SSL' if direction=='BUY' else 'BSL'} @ {sweep['level']:.5g}" if checks["sweep"] else "no matching sweep",
        "displacement leg confirmed" if displacement else "no displacement",
        f"entry retraces FVG/OB zone {entry_zone[0]:.5g}-{entry_zone[1]:.5g}" if entry_zone else "entry at market (no zone)",
        f"premium/discount={pd['zone']} (pos={pd['pos']:.2f})",
        f"kill zone: {kz}" if kz else "outside kill zones",
        f"RR(TP1)=1:{rr:.2f}",
    ]
    return StrategyResult(
        code="ICT", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
