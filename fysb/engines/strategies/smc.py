"""SMC — project-specific BOS/CHOCH and swing-derived supply/demand model.

A BOS/CHOCH determines direction. Demand/supply uses an intact-side swing and
an ATR-width zone, not measured resting orders. HTF alignment is a score bonus,
not a mandatory rule inside this module. Entry uses zone edge clipped to close.
Stop: protective swing +/- 0.85 ATR, widened if tight; targets 2R/3R/4.5R.
Weights: labels14, BOS12, CHOCH10, zone14, HTF12, volatility4, RR6
(total72). Minimum score58; minimum40 bars.

Checklist merit is normalized to42..95, NOT a win probability. Callers must
supply correctly labelled completed candles and enforce issuance/risk policy.
"""
from fysb.engines.analysis import atr, atr_pct, equal_extremes, structure_labels, swings, support_resistance
from fysb.engines.strategies.base import (
    diagnosis_record, diagnosis_refuse,
    Ctx, StrategyResult, cap_stop, ensure_min_tp_rr, normalized_score, checked_strategy,
)


@checked_strategy
def evaluate(ctx: Ctx) -> StrategyResult | None:
    candles = ctx.candles
    if len(candles) < 40:
        diagnosis_refuse("SMC", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
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

    # v8.0.0 FIX (SIG-1): On CHOCH, `bos_dir` gives the NEW reversal break direction;
    # NEVER use the pre-reversal `struct["trend"]` which points the OPPOSITE way!
    direction = ""
    if struct["bos"] and struct.get("bos_dir"):
        direction = "BUY" if struct["bos_dir"] == "up" else "SELL"
    elif struct["choch"]:
        if struct.get("bos_dir") in ("up", "down"):
            direction = "BUY" if struct["bos_dir"] == "up" else "SELL"
        elif struct["trend"] == "down":
            direction = "BUY"
        elif struct["trend"] == "up":
            direction = "SELL"
        else:
            direction = "BUY" if close >= float(candles[-1]["open"]) else "SELL"
    if not direction:
        diagnosis_refuse("SMC", "لا اتجاه مؤسسي واضح للنموذج")
        return None

    # --- Demand / Supply zone from the last INTACT opposite swing (v6.20.0) --
    # Skip broken swings where price already closed beyond the zone boundary.
    highs, lows = swings(candles, 3)
    a = atr(candles)
    if a <= 0:
        diagnosis_refuse("SMC", "التقلب غير قابل للقياس الآن")
        return None
    zone = None
    if direction == "BUY" and lows:
        for base in reversed(lows):
            if close >= base["price"]:
                zone = (base["price"], base["price"] + 1.2 * a)
                break
        if zone is None:
            diagnosis_refuse("SMC", "لا منطقة عرض/طلب سليمة للنموذج")
            return None  # All demand zones violated — structure is broken
        entry = min(zone[1], close)
    elif direction == "SELL" and highs:
        for base in reversed(highs):
            if close <= base["price"]:
                zone = (base["price"] - 1.2 * a, base["price"])
                break
        if zone is None:
            diagnosis_refuse("SMC", "لا منطقة عرض/طلب سليمة للنموذج")
            return None  # All supply zones violated — structure is broken
        entry = max(zone[0], close)
    else:
        entry = close

    # --- Stop beyond the protective swing (v6.20.6: 0.85 ATR breathing room) --
    if direction == "BUY":
        stop = (zone[0] - 0.85 * a) if zone else entry - 1.8 * a
    else:
        stop = (zone[1] + 0.85 * a) if zone else entry + 1.8 * a
    stop = cap_stop(entry, stop, direction, a, min_atr=1.15, fallback_atr=1.8, max_atr=2.8, cap_atr=2.4)

    risk = abs(entry - stop)
    if risk <= 0:
        diagnosis_refuse("SMC", "حساب الوقف غير سليم (مخاطرة صفرية)")
        return None
    tp1, tp2, tp3, rr = ensure_min_tp_rr(
        entry, stop,
        entry + risk * 2.0 if direction == "BUY" else entry - risk * 2.0,
        entry + risk * 3.0 if direction == "BUY" else entry - risk * 3.0,
        entry + risk * 4.5 if direction == "BUY" else entry - risk * 4.5,
        direction,
    )

    # --- Normalized Fair Score (v8.0.0) ------------------------------------
    earned = 0.0
    max_pts = 72.0
    checks = {}
    checks["labels"] = (direction == "BUY" and up_count > down_count) or (direction == "SELL" and down_count > up_count)
    earned += 14 if checks["labels"] else 0
    checks["bos"] = struct["bos"]
    earned += 12 if checks["bos"] else 0
    checks["choch"] = struct["choch"]
    earned += 10 if checks["choch"] else 0
    checks["zone"] = zone is not None
    earned += 14 if zone else 0
    checks["htf_bias"] = (bias["trend"] == "up" and direction == "BUY") or (bias["trend"] == "down" and direction == "SELL")
    earned += 12 if checks["htf_bias"] else 0
    vol = atr_pct(candles)
    checks["vol_ok"] = 0.05 < vol < 6.0
    earned += 4 if checks["vol_ok"] else 0
    checks["rr_ok"] = rr >= 2.0
    earned += 6 if checks["rr_ok"] else 0

    # v8.12.0 — institutional anchor confluence: the SMC zone landing on a
    # classic daily pivot / opening anchor is institutional-grade supply/demand.
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
    base_candle = None
    zone_origin_pool = None
    zone_mitigated = None
    intact_layers = 0
    fixed_zone_touch = False
    inducement = False
    pool_swept = False
    if zone is not None:
        # locate the base swing candle for fixed-width/freshness reads
        base_pts = lows if direction == "BUY" else highs
        for base in reversed(base_pts):
            bpx = float(base["price"])
            within = (abs(bpx - zone[0]) <= 1.3 * a) if direction == "BUY" else (abs(bpx - zone[1]) <= 1.3 * a)
            if within and base.get("index") is not None:
                base_candle = candles[int(base["index"])]
                break

        # 1) school-standard FIXED-WIDTH zone: the base candle's wick-to-body.
        if base_candle is not None:
            o0, c0, h0, l0 = (float(base_candle["open"]), float(base_candle["close"]),
                              float(base_candle["high"]), float(base_candle["low"]))
            if direction == "BUY":
                fixed = (l0, c0 if c0 < o0 else o0)      # wick-to-body demand
            else:
                fixed = (c0 if c0 > o0 else o0, h0)      # wick-to-body supply
            if fixed[1] > fixed[0]:
                fixed_zone_touch = fixed[0] - 0.25 * a <= entry <= fixed[1] + 0.25 * a

        # 2) freshness: price revisited the zone since its base candle formed?
        bidx = int(base_candle["index"]) if (base_candle is not None and base_candle.get("index") is not None) else 0
        post = candles[bidx + 1:-1] if bidx + 1 < len(candles) - 1 else []
        if post:
            if direction == "BUY":
                zone_mitigated = any(float(c["low"]) <= zone[1] for c in post)
            else:
                zone_mitigated = any(float(c["high"]) >= zone[0] for c in post)
        else:
            zone_mitigated = False

        # 3) progression: how many INTACT same-side zones stack behind this one
        chosen_base = zone[0] if direction == "BUY" else zone[1]
        pts = lows if direction == "BUY" else highs
        for base in pts:
            bpx = float(base["price"])
            bidx2 = int(base.get("index") or 0)
            tail = candles[bidx2 + 1:]
            if not tail:
                continue
            violated = any((float(c["close"]) < bpx) for c in tail) if direction == "BUY" \
                else any((float(c["close"]) > bpx) for c in tail)
            if not violated and abs(bpx - chosen_base) > 1e-12:
                intact_layers += 1

        # 4) inducement: an UNSWEPT minor pivot sits between price and the zone
        minor_h, minor_l = swings(candles, 1)
        if direction == "BUY" and close > zone[1]:
            for m in minor_h:
                mp = float(m["price"])
                if close > mp > zone[1]:
                    post_m = candles[int(m["index"]) + 1:]
                    if post_m and not any(float(c["high"]) > mp for c in post_m):
                        inducement = True
                        break
        elif direction == "SELL" and close < zone[0]:
            for m in minor_l:
                mp = float(m["price"])
                if close < mp < zone[0]:
                    post_m = candles[int(m["index"]) + 1:]
                    if post_m and not any(float(c["low"]) < mp for c in post_m):
                        inducement = True
                        break

    # 5) EQH/EQL pools: zone origin at a pool, or a recent sweep of a pool
    # in the trade direction (smart money filled the draw).
    pools = equal_extremes(candles, a)
    if zone is not None:
        for lvl in (pools["eql"] if direction == "BUY" else pools["eqh"]):
            if abs(lvl - (zone[0] if direction == "BUY" else zone[1])) <= 0.25 * a:
                zone_origin_pool = lvl
                break
    if direction == "BUY":
        for lvl in pools["eql"]:
            if any(float(c["low"]) <= lvl <= float(c["high"]) + 0.3 * a for c in candles[-8:-1]):
                pool_swept = True
                break
    else:
        for lvl in pools["eqh"]:
            if any(float(c["high"]) >= lvl >= float(c["low"]) - 0.3 * a for c in candles[-8:-1]):
                pool_swept = True
                break

    checks["fixed_zone_touch"] = fixed_zone_touch
    checks["zone_mitigated"] = zone_mitigated
    checks["intact_zone_layers"] = intact_layers
    checks["inducement_unswept"] = inducement
    checks["pool_origin"] = zone_origin_pool
    checks["pool_swept"] = pool_swept
    earned += 4 if fixed_zone_touch else 0
    earned -= 3 if zone_mitigated else 0
    earned += min(intact_layers, 2) * 2
    earned -= 4 if inducement else 0
    earned += 3 if zone_origin_pool else 0
    earned += 3 if pool_swept else 0

    score = normalized_score(earned, max_pts)
    if score < 58:
        diagnosis_record("SMC", checks, earned, max_pts)
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
    # v8.13.0 — gap-fill disclosures (only what actually fired)
    if fixed_zone_touch:
        reasons.append("entry inside the school-standard fixed wick-to-body zone")
    if zone_mitigated:
        reasons.append("zone mitigated since formation — downgraded honestly")
    if intact_layers:
        reasons.append(f"{intact_layers} intact same-side zone layers behind (progression)")
    if inducement:
        reasons.append("unswept minor inducement between price and zone — downgraded")
    if zone_origin_pool:
        reasons.append(f"zone origin on an equal-extreme pool {zone_origin_pool:.5g}")
    if pool_swept:
        reasons.append(f"{'EQL' if direction == 'BUY' else 'EQH'} liquidity pool swept before entry")
    return StrategyResult(
        code="SMC", direction=direction, entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, rr=round(rr, 2),
        score=score, reasons=reasons, checks=checks,
    )
