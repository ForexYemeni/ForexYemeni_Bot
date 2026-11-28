"""v8.14.12 — «عين الأزرار الملكية» (the Royal Button Eye).

The owner tests the product on PRODUCTION where the code has no eyes. When a
button press does nothing, opens nothing, or never moves to the next page,
the cause was invisible: the dispatcher logged to a file he cannot reach
while testing on his phone. This module makes EVERY silent press SPEAK:

  * FAIL          — a route raised: the real exception + traceback tail.
  * SILENT        — the press ended WITHOUT any page delivered (hang,
                    starvation, stuck route) — the exact «لا تفتح شي».
  * NOT_MODIFIED  — the render contract decided the page is byte-identical
                    and delivered nothing visually.

Everything is stored in `ui_events` (portable SQL) and surfaced in the admin
panel («🩺 عين الأزرار») so the next report carries the REAL cause — no
guessing, no repro hand-waving. Zero counters, zero fabrication: raw facts.
"""
from __future__ import annotations

import threading
import time

from fysb import config, db

# v8.14.14 — raised 300→600: the royal guard records a CLICK receipt for every
# arriving press (arrival proof per press), so the ring needs the headroom to
# keep FAIL/SILENT/TIMEOUT evidence alive alongside the receipts.
MAX_ROWS = 600


def _prune(c) -> None:
    c.execute("""DELETE FROM ui_events WHERE id NOT IN (
                     SELECT id FROM ui_events ORDER BY id DESC LIMIT ?)""", (MAX_ROWS,))


# v8.14.14 — «الحرس الملكي»: session arrivals live IN MEMORY (zero DB cost,
# zero lock contention). A per-press DB receipt would add a third concurrent
# writer on every press and can lock SQLite against the route's own write —
# a self-inflicted outage class caught by the v6.20.1 latency contract test.
# Deaths still persist as before; arrivals are session-scoped truth exposed
# via /btnfail and /ping alongside the persisted evidence kinds.
_arrival_lock = threading.Lock()
_arrivals = {"total": 0, "last_user": 0, "last_data": "", "last_at": ""}


def record_arrival(user_id: int, data: str) -> None:
    """Receipt at the door — pure memory, microsecond cost, zero I/O."""
    try:
        with _arrival_lock:
            _arrivals["total"] += 1
            _arrivals["last_user"] = int(user_id)
            _arrivals["last_data"] = str(data or "")[:60]
            _arrivals["last_at"] = config.now()
    except Exception:
        pass  # the receipt must never break the press it proves


def arrival_stats() -> dict:
    with _arrival_lock:
        return dict(_arrivals)


def record(kind: str, user_id: int, data: str, detail: str = "",
           duration_ms: float | None = None) -> None:
    try:
        with db.conn() as c:
            c.execute("""INSERT INTO ui_events(kind,user_id,data,detail,duration_ms,created_at)
                         VALUES(?,?,?,?,?,?)""",
                      (kind[:12], int(user_id), str(data)[:200],
                       str(detail or "")[:1800],
                       None if duration_ms is None else round(float(duration_ms)),
                       config.now()))
            _prune(c)
    except Exception:
        pass  # diagnostics must never break the press they diagnose


def record_failure(user_id: int, data: str, error: str, tb_tail: str,
                   duration_ms: float) -> None:
    record("FAIL", user_id, data, f"{error}\n{tb_tail}", duration_ms)


def record_silent(user_id: int, data: str, duration_ms: float) -> None:
    record("SILENT", user_id, data,
           "انتهت الضغطة دون تسليم أي صفحة — تعليق أو تجوّع أو مسار عالق",
           duration_ms)


def record_not_modified(user_id: int, data: str) -> None:
    record("NOT_MODIFIED", user_id, data,
           "الصفحة المطلوبة مطابقة حرفياً لما يُعرض — لم يتغير شيء بصرياً",
           None)


def recent(limit: int = 15) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("""SELECT * FROM ui_events ORDER BY id DESC LIMIT ?""",
                         (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def counts() -> dict:
    with db.conn() as c:
        rows = c.execute("SELECT kind, COUNT(*) n FROM ui_events GROUP BY kind").fetchall()
    out = {"FAIL": 0, "SILENT": 0, "NOT_MODIFIED": 0,
           "DROP": 0, "RAW": 0, "PROBE": 0}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out


def clear() -> int:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM ui_events").fetchone()["n"]
        c.execute("DELETE FROM ui_events")
    with _arrival_lock:
        _arrivals.update({"total": 0, "last_user": 0, "last_data": "", "last_at": ""})
    return int(n)


KIND_LABEL = {"FAIL": "💥 عطل حقيقي",
              "SILENT": "👁 ضغطة صامتة (بلا تسليم)",
              "NOT_MODIFIED": "🔁 صفحة مطابقة (لا تغيير بصري)",
              "DROP": "🚨 إسقاط في بوابة التوزيع",
              "RAW": "🧱 خلل في الطبقة الخارجية للحرس",
              "PROBE": "✅ اختبار الحرس الناجح"}
