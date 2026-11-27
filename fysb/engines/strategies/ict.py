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
    premium_discount, structure_labels, swings,
)
from fysb.engines.strategies.base import (
    diagnosis_record,
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

    # v8.12.0 — institutional anchor confluence (classic daily pivot / session
    # opening anchor within 0.35 ATR on the entry's side): scored quality factor.
    try:
        from fysb.engines.intelligence import detect_anchor_confluence
        _anch = detect_anchor_confluence(candles, entry, direction, atr_value=a)
        checks["anchor_confluence"] = bool(_anch["detected"])
        max_pts += 6
        earned += 6 if _anch["detected"] else 0
    except Exception:
        checks["anchor_confluence"] = None

    # ------------------------------------------------------------------
    # v8.13.0 — gap-fill suite (ALL bonus-only: earned rises, max_pts fixed)
    # ------------------------------------------------------------------

    # 1) DOL — Draw on Liquidity: the nearest untested swing pool BEYOND entry
    # in the trade direction. Room to the draw >= 2R earns the bonus; the
    # level is disclosed as the plan's draw target (a swing pool, NOT a map
    # of real orders).
    highs_d, lows_d = swings(candles, 3)
    dol = 0.0
    if direction == "BUY":
        for h in reversed(highs_d):
            if float(h["price"]) > close + 0.5 * a:
                dol = float(h["price"])
                break
    else:
        for l in reversed(lows_d):
            if float(l["price"]) < close - 0.5 * a:
                dol = float(l["price"])
                break
    checks["dol"] = dol if dol else None
    if dol:
        room_ok = abs(dol - entry) >= 2.0 * risk
        checks["dol_room"] = room_ok
        earned += 5 if room_ok else 2

    # 2) Breaker / Mitigation — a violated opposite block now retested from
    # the other side acts as a breaker in trade direction (+bonus); a OWN-side
    # block already revisited since formation is mitigated (weaker, -penalty).
    breaker = False
    mitigated = False
    for ob in obs:
        if ob["kind"] == ("bearish" if direction == "BUY" else "bullish"):
            viol = (float(ob["hi"]) < close) if direction == "BUY" else (float(ob["lo"]) > close)
            if viol:
                breaker = True
    if entry_zone:
        z0, z1 = entry_zone
        formed_idx = None
        for f in fvgs:
            if (f["lo"], f["hi"]) == entry_zone:
                formed_idx = int(f["index"])
                break
        if formed_idx is not None and formed_idx < len(candles) - 2:
            post = candles[formed_idx + 1:-1]
            touched = any((float(c["low"]) <= z1 for c in post) if direction == "BUY"
                          else (float(c["high"]) >= z0 for c in post))
            mitigated = touched
    checks["breaker_confluence"] = breaker
    checks["zone_mitigated"] = mitigated
    earned += 5 if breaker else 0
    earned -= 4 if mitigated else 0

    # 3) Formal OTE — the last impulse leg retraced into the 0.618–0.79 band
    # with the 0.705 sweet spot graded higher.
    ote = None
    if direction == "BUY":
        leg_lo, leg_hi = None, None
        for l in reversed(lows_d):
            leg_lo = float(l["price"])
            break
        for h in reversed(highs_d):
            if float(h["price"]) > close:
                leg_hi = float(h["price"])
                break
        if leg_lo is not None and leg_hi:
            depth = (leg_hi - close) / (leg_hi - leg_lo)
    else:
        leg_hi, leg_lo = None, None
        for h in reversed(highs_d):
            leg_hi = float(h["price"])
            break
        for l in reversed(lows_d):
            if float(l["price"]) < close:
                leg_lo = float(l["price"])
                break
        if leg_hi is not None and leg_lo:
            depth = (close - leg_lo) / (leg_hi - leg_lo)
    try:
        if 0.618 <= depth <= 0.79:
            ote = round(depth, 3)
    except NameError:
        ote = None
    checks["ote_depth"] = ote
    if ote is not None:
        earned += 4 + (2 if abs(ote - 0.705) <= 0.03 else 0)

    # 4) PD-Array stack — distinct ICT arrays stacked at the entry price
    # (FVG / OB / breaker / OTE band): depth of confluence, disclosed.
    stack = 0
    if entry_zone or ote is not None:
        tol = 0.5 * a
        near = (lambda zone: zone[0] - tol <= entry <= zone[1] + tol)
        if entry_zone and near(entry_zone):
            stack += 1
        if any(o["kind"] == ("bullish" if direction == "BUY" else "bearish")
               and o["lo"] - tol <= entry <= o["hi"] + tol for o in obs):
            stack += 1
        if breaker:
            stack += 1
        if ote is not None:
            stack += 1
    checks["pd_stack"] = stack
    earned += min(stack, 3) * 2

    # 5) SMT divergence — correlated symbol made the OPPOSITE swing structure
    # (symbol lower low while correlated higher low = smart-money split).
    # Skipped HONESTLY when no correlated series was supplied by the caller.
    smt = None
    if ctx.correlated_candles:
        try:
            ch, cl = swings(ctx.correlated_candles, 2)
            if direction == "BUY" and len(lows_d) >= 2 and len(cl) >= 2:
                sym_ll = float(lows_d[-1]["price"]) < float(lows_d[-2]["price"])
                corr_hl = float(cl[-1]["price"]) > float(cl[-2]["price"])
                if sym_ll and corr_hl:
                    smt = "BUY"
            elif direction == "SELL" and len(highs_d) >= 2 and len(ch) >= 2:
                sym_hh = float(highs_d[-1]["price"]) > float(highs_d[-2]["price"])
                corr_lh = float(ch[-1]["price"]) < float(ch[-2]["price"])
                if sym_hh and corr_lh:
                    smt = "SELL"
        except Exception:
            smt = None
    checks["smt"] = smt
    earned += 5 if smt == direction else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("ICT", checks, earned, max_pts)
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
    # v8.13.0 — gap-fill disclosures (only what actually fired)
    if dol:
        reasons.append(f"DOL draw target {dol:.5g} (untested swing pool, room {'ok' if checks.get('dol_room') else 'tight'})")
    if breaker:
        reasons.append("breaker block: violated opposite OB retested in trade direction")
    if mitigated:
        reasons.append("zone mitigated since formation — downgraded honestly")
    if ote is not None:
        reasons.append(f"formal OTE depth {ote} (0.618–0.79 band, sweet 0.705)")
    if stack:
        reasons.append(f"PD-array stack at entry: {stack} layers")
    if smt:
        reasons.append(f"SMT divergence confirmed on the correlated series ({smt})")
    elif ctx.correlated_candles is None:
        reasons.append("SMT skipped — no correlated series supplied (honest)")
    return StrategyResult(
        code="ICT", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
