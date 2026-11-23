"""Real-Entry protocol — birth-state truth for every signal.

The user's verdict on v6.6.2: «الإشارات ترسل بعد المرور من نقطة الدخول بشكل
كبير جداً ... الدخول ليس حقيقياً». A signal published while the market sits
hundreds of pips beyond its entry is not a signal — it is a relic.

This module defines ONE shared geometry used by BOTH the issuance engine
(publish gate) and the lifecycle tracker (fill semantics):

  BUY  · entry below the plan is fill territory (a limit buy fills when price
         trades DOWN through it). Price above entry = waiting for the retrace.
  SELL · entry above the plan is fill territory (a limit sell fills when price
         trades UP through it). Price below entry = waiting for the retrace.

Two honest quantities, per side:
  pending side  consumed = how much of the entry→TP1 leg the market has
                already burned while the entry was never touched. A setup whose
                first-target leg is half-consumed at birth (the user's signal
                #5: chase 288.6 pips of a 582-pip leg) is STALE — refused.
  passed side   consumed = how far beyond the entry (toward the stop) price
                already is. A small overshoot = born RUNNING («قيد التنفيذ»)
                with an honest drift; a blown-through toward the stop = STALE.
"""
from fysb import instruments

# A signal may be published while the market already consumed at most this
# fraction of the relevant leg (entry→TP1 on the pending side, entry→SL on
# the passed side). 25% keeps normal retrace entries publishable while
# killing the «+288 pips من الدخول» relic class.
CHASE_MAX_RATIO = 0.25

# Absolute floor (pips) — tight setups must not be rejected over spread noise.
STALE_ABS_FLOOR_PIPS = 8.0


def entry_geometry(direction: str, entry: float, stop: float, tp1: float,
                   px: float) -> dict:
    """Where does the live price sit relative to a not-yet-delivered plan?

    Returns {"side": "pending"|"passed", "consumed": float(0..n), "abs_move":
    float(price distance)} — `consumed` is the fraction of the relevant leg
    already travelled, `abs_move` the raw |px - entry| distance.
    """
    eps = 1e-9
    direction = (direction or "").upper()
    if direction == "BUY":
        if px >= entry:
            leg = max(abs(float(tp1) - entry), eps)
            return {"side": "pending", "consumed": (px - entry) / leg,
                    "abs_move": px - entry}
        leg = max(abs(entry - float(stop)), eps)
        return {"side": "passed", "consumed": (entry - px) / leg,
                "abs_move": entry - px}
    if px <= entry:
        leg = max(abs(float(tp1) - entry), eps)
        return {"side": "pending", "consumed": (entry - px) / leg,
                "abs_move": entry - px}
    leg = max(abs(float(stop) - entry), eps)
    return {"side": "passed", "consumed": (px - entry) / leg,
            "abs_move": px - entry}


def is_stale_entry(symbol: str, direction: str, entry: float, stop: float,
                   tp1: float, px: float) -> tuple[bool, str]:
    """(stale, human detail) — BOTH thresholds must be exceeded: the leg
    fraction beyond CHASE_MAX_RATIO AND the raw distance beyond the absolute
    pips floor. Keeps tight setups alive and relics dead."""
    geo = entry_geometry(direction, entry, stop, tp1, px)
    try:
        abs_pips = instruments.pips(symbol, entry, px)
    except Exception:
        abs_pips = abs(px - entry)
    stale = geo["consumed"] > CHASE_MAX_RATIO and abs_pips > STALE_ABS_FLOOR_PIPS
    detail = (f"live={px:g} entry={entry:g} side={geo['side']} "
              f"consumed={geo['consumed'] * 100:.0f}% ({abs_pips:.1f} pips)")
    return stale, detail


def birth_status(direction: str, entry: float, px: float) -> str:
    """Status at birth: price already on/past the entry = the trade IS live
    from the first second — «بانتظار التنفيذ» must never be printed for an
    executed trade. Price still on the waiting side = honest pending plan."""
    direction = (direction or "").upper()
    if direction == "BUY":
        return "RUNNING" if px <= entry else "ACTIVE"
    return "RUNNING" if px >= entry else "ACTIVE"


def birth_pulse_line(sig: dict, row=None) -> str:
    """The market-truth line appended to the DELIVERED card — from the birth
    quote stored with the signal (zero extra network calls, deterministic)."""
    px = float(sig.get("live_px") or 0)
    if px <= 0:
        return ""
    from fysb.theme import esc
    symbol = sig["symbol"]
    entry = float(sig["entry"])
    try:
        drift = instruments.pips(symbol, entry, px)
    except Exception:
        drift = abs(px - entry)
    fav = (px <= entry) if (sig.get("direction") or "").upper() == "SELL" \
        else (px >= entry)
    signed = drift if fav else -drift
    px_txt = instruments.fmt_price(symbol, px, row)
    if sig.get("status") == "RUNNING":
        side = "أعلى الدخول" if px > entry else ("عند الدخول" if px == entry
                                                 else "أدنى الدخول")
        return (f"💰 <b>سعر السوق عند الإصدار:</b> <code>{esc(px_txt)}</code>"
                f" · {side} · <b>{signed:+.1f} pips</b> — الإشارة قيد التنفيذ")
    away = abs(signed)
    return (f"💰 <b>سعر السوق عند الإصدار:</b> <code>{esc(px_txt)}</code>"
            f" · بانتظار وصول السعر لمنطقة الدخول (يبعد <b>{away:.1f} pips</b>)"
            f" — تُتابع لحظياً وتُلغى تلقائياً إن فات الدخول")
