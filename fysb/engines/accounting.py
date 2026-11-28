"""Versioned signal-model accounting, NEVER broker cash reconciliation.

Old stored price-distance results are left untouched and explicitly legacy.
New PARTIAL_50_25_25 signals bank 50% at TP1, 25% at TP2, 25% at TP3;
the remainder exits at entry after target protection. Fees/slippage are absent.
"""
import math

PARTIAL_MODEL = "PARTIAL_50_25_25"


def is_private(row: dict) -> bool:
    return bool(int(row.get("private_user_id") or 0)) or str(row.get("source") or "").upper() in (
        "SIGNAL_NOW", "ADMIN_SIGNAL_NOW", "TRUE_ANALYSIS_INSTANT", "TRUE_ANALYSIS_CONFIRMED")


def is_closed(row: dict) -> bool:
    st = str(row.get("status") or "").upper()
    return st in ("TP3_HIT", "SL_HIT", "TP1_BE", "TP2_BE", "WIN", "LOSS", "CLOSED_WIN") or (
        st in ("TP1_HIT", "TP2_HIT") and bool(row.get("closed_at")))


def modeled_r(row: dict, hit_tp: int | None = None, status: str | None = None) -> float | None:
    """Closed or banked modeled R; None means unavailable, not a made-up R.

    Legacy rows use their recorded distance/risk ratio (not retrofitted partials).
    Callers must label aggregates as modeled and disclose legacy observations.
    """
    try:
        risk = abs(float(row["entry"])-float(row["stop"]))
        if not math.isfinite(risk) or risk <= 0:
            return None
        st = status or str(row.get("status") or "").upper()
        if st == "LOCKED_50":
            # 🏅 the royal lock: the REAL documented locked pips over planned risk
            lp = float(row.get("locked_pips") or 0.0)
            if lp > 0:
                from fysb import instruments as _ins
                rp = _ins.pips(row["symbol"], row["entry"], row["stop"])
                return (lp / rp) if rp else None
            return None
        if st == "SL_HIT" and not int(row.get("hit_tp") or 0):
            return -1.0
        if row.get("accounting_model") != PARTIAL_MODEL:
            from fysb import instruments
            rp = instruments.pips(row["symbol"], row["entry"], row["stop"])
            val = float(row.get("pips_result") or 0) / rp
        else:
            hit = int(row.get("hit_tp") or 0) if hit_tp is None else hit_tp
            st = status or str(row.get("status") or "").upper()
            if hit == 0:
                return -1.0 if st == "SL_HIT" else 0.0
            weights = (.5, .25, .25)
            val = sum(weights[i-1]*abs(float(row[f"tp{i}"])-float(row["entry"]))/risk
                      for i in range(1, min(hit, 3)+1))
        return val if math.isfinite(val) else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
