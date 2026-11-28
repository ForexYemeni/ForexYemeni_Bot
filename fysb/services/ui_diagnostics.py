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

import time

from fysb import config, db

MAX_ROWS = 300


def _prune(c) -> None:
    c.execute("""DELETE FROM ui_events WHERE id NOT IN (
                     SELECT id FROM ui_events ORDER BY id DESC LIMIT ?)""", (MAX_ROWS,))


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
    out = {"FAIL": 0, "SILENT": 0, "NOT_MODIFIED": 0}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out


def clear() -> int:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM ui_events").fetchone()["n"]
        c.execute("DELETE FROM ui_events")
    return int(n)


KIND_LABEL = {"FAIL": "💥 عطل حقيقي",
              "SILENT": "👁 ضغطة صامتة (بلا تسليم)",
              "NOT_MODIFIED": "🔁 صفحة مطابقة (لا تغيير بصري)"}
