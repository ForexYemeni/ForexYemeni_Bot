"""v8.14.9 — «مفتاحا الصدق الملكيان» (the owner-controlled honesty laws).

Two institutional laws protect every generated signal:
  ⚖️ بوابة حد الصدق (القِدم)  — refuse any snapshot older than the frame's
     honesty limit (3× interval). THE owner decree: an archaeologically old
     provider failure is never served.
  🧊 قانون عدم إعادة الرسم    — compute strictly on CLOSED candles; the
     still-forming bar never enters indicators, gates or geometry.

Both laws default ON — the institutional contract is unchanged out of the box
(UPE: no silent behavior change). The OWNER may disarm either from
«النظام والأمان ← الإعدادات العامة»; every change is audit-logged, and every
disarmed bypass is stamped honestly on the user surface and logged STALE_BYPASS
in engine_runs for the owner alone. Nothing becomes silent, nothing fake.
"""
from __future__ import annotations

from fysb import db

STALE_GATE_KEY = "honesty_stale_gate"        # "1" = law active (default)
CLOSED_ONLY_KEY = "no_repaint_closed_only"   # "1" = law active (default)
LOCK50_KEY = "lock_50_enabled"               # "1" = royal lock armed (default)


def lock50_enabled() -> bool:
    """🏅 the royal +$5 lock (0.01 lot, every market) — ON by default: any
    open signal whose documented favorable excursion reaches $5 at 0.01 lot
    is LOCKED — it can never close below +$5, whatever the retrace."""
    return (db.get_setting(LOCK50_KEY, "1") or "1") == "1"


def stale_gate_enabled() -> bool:
    """⚖️ the age honesty gate — ON by default (the institutional contract)."""
    return (db.get_setting(STALE_GATE_KEY, "1") or "1") == "1"


def closed_candles_only() -> bool:
    """🧊 the closed-candles-only (no-repaint) law — ON by default."""
    return (db.get_setting(CLOSED_ONLY_KEY, "1") or "1") == "1"


def stale_bypass_note(payload: dict) -> str:
    """The royal honest stamp carried on every signal born on a snapshot that
    missed the honesty limit (deferred-honesty mode or owner force). Zero
    counters — the raw age lives in engine_runs for the owner alone."""
    if payload.get("feed_frozen"):
        return ("⚠️ وضع المؤجل الصادق: التغذية جامدة — الإشارة تتطلب تأكيد "
                "كوت حي قبل التنفيذ")
    return ("⚠️ وضع المؤجل الصادق: محسوبة على تغذية تجاوزت حد الصدق — الإشارة "
            "تتطلب تأكيد كوت حي قبل التنفيذ")


def states() -> dict[str, bool]:
    """Both laws' current state (settings page + health surfaces)."""
    return {STALE_GATE_KEY: stale_gate_enabled(),
            CLOSED_ONLY_KEY: closed_candles_only()}
