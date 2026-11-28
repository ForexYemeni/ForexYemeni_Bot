"""v8.14.19 — «دفتر الحقيقة» (the Truth Ledger) — the owner's decree:

ANY trade whose documented favorable excursion reached the lock threshold
(+50 gold pips by default — the owner-editable setting) is NEVER counted as
a loss, anywhere in the platform. Its REAL profit is the threshold pips,
100%. Mechanically this is the royal-lock contract (v8.14.10): a trade that
touched the threshold CANNOT close below it while the lock is armed — so any
SL_HIT row carrying profit_50_hit=1 is legacy/edge data whose truthful
settlement is the locked profit, not the stop.

The verdict is shared by EVERY consumer: the interactive filtered report,
the classic signals report, and the executive digest (caption + PNG).
"""
from __future__ import annotations

from fysb import db
from fysb.engines import honesty

WIN_ST = ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE", "CLOSED_WIN", "WIN", "LOCKED_50")
LOSS_ST = ("SL_HIT", "LOSS")


def closed_verdict(row: dict, lock_pips_v: int | None = None) -> tuple[str, float]:
    """Classify ONE closed signal row truthfully.

    Returns (verdict, effective_pips):
      ("win",  pips)  — banked TP / royal lock / the 50-pip rescue decree
      ("loss", pips)  — honest SL_HIT without a documented threshold touch
      ("skip", 0.0)   — not a closed row (caller filters first)

    lock_pips_v defaults to the owner's editable threshold setting.
    """
    if not isinstance(row, dict):
        row = dict(row)  # sqlite3.Row compatibility
    st = str(row.get("status") or "").upper()
    pips = float(row.get("pips_result") or 0.0)
    if lock_pips_v is None:
        lock_pips_v = honesty.lock_pips()
    if st in WIN_ST:
        return ("win", pips)
    if st in LOSS_ST:
        # v8.14.19 — the owner's decree: a documented +threshold touch means
        # the trade was (or would have been) LOCKED — its real profit IS the
        # threshold, never the stop. 100% mechanical, zero fabrication.
        if int(row.get("profit_50_hit") or 0) == 1:
            return ("win", float(lock_pips_v))
        return ("loss", pips)
    return ("skip", 0.0)


def effective_row(row, lock_pips_v: int | None = None) -> dict:
    """A copy of the row with its TRUTHFUL pips_result (for R-multiple math)."""
    verdict, pips = closed_verdict(row, lock_pips_v)
    if not isinstance(row, dict):
        row = dict(row)
    out = dict(row)
    if verdict == "win" and abs(float(row.get("pips_result") or 0.0) - pips) > 1e-9:
        out["pips_result"] = pips
    return out


def is_closed_row(row: dict) -> bool:
    """True when the row belongs to the CLOSED ledger (truth settlements only)."""
    from fysb.engines.accounting import is_closed
    return bool(is_closed(row))


def rescue_count(rows) -> int:
    """How many SL rows the decree rescued into documented wins."""
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            r = dict(r)
        st = str(r.get("status") or "").upper()
        if st == "SL_HIT" and int(r.get("profit_50_hit") or 0) == 1:
            n += 1
    return n
