"""v6.20.4 — Luxury Date-Grouped Signals Archive & Accounting Service.

Provides unified, 100% ANSI-SQL (PostgreSQL + SQLite identical) queries and
royal Arabic UI formatting to separate signals cleanly into:
  * 📅 إشارات اليوم (Today)
  * 📆 إشارات أمس (Yesterday)
  * 🗓 حسب التاريخ (By Date — calendar journal with per-day KPIs)
  * 🟢 المفتوحة الآن / 🗂 الكل

Used by BOTH Admin (`a:signals`) and User (`u:signals`) so previous signals
NEVER vanish once closed, while keeping real global sequential #IDs (`seq`)
and strict user plan/privacy isolation (`SIGNAL_NOW` stays private to owner).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fysb import db
from fysb.theme import esc, is_signal_closed

_AR_WEEKDAYS = {
    0: "الإثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}

_OPEN_STATUSES = ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT")


def today_iso() -> str:
    """Current UTC calendar day YYYY-MM-DD (matches human_dt and created_at)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def yesterday_iso() -> str:
    """Previous UTC calendar day YYYY-MM-DD."""
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def is_valid_iso_date(s: str) -> bool:
    if not s or len(s) != 10:
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def resolve_scope_date(scope: str) -> str:
    """Resolve 'today' | 'yesterday' | 'YYYY-MM-DD' -> 'YYYY-MM-DD', else ''."""
    s = (scope or "").strip().lower()
    if s == "today":
        return today_iso()
    if s == "yesterday":
        return yesterday_iso()
    if is_valid_iso_date(s):
        return s
    return ""


def date_bounds(day_iso: str) -> tuple[str, str]:
    """Half-open UTC string range [day_iso, next_day_iso) — works on both
    'YYYY-MM-DDTHH:MM:SS+00:00' and 'YYYY-MM-DD HH:MM:SS' in PG and SQLite."""
    dt = datetime.strptime(day_iso, "%Y-%m-%d")
    nxt = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return day_iso, nxt


def ar_date_label(day_iso: str) -> str:
    """Luxury Arabic date label with weekday name."""
    if not day_iso:
        return "🗂 جميع التواريخ"
    if day_iso == today_iso():
        return f"📅 إشارات اليوم · {day_iso}"
    if day_iso == yesterday_iso():
        return f"📆 إشارات أمس · {day_iso}"
    try:
        wd = _AR_WEEKDAYS.get(datetime.strptime(day_iso, "%Y-%m-%d").weekday(), "")
        return f"🗓 {wd} · {day_iso}"
    except ValueError:
        return f"🗓 {day_iso}"


def ar_date_short(day_iso: str) -> str:
    """Compact luxury date label for inline calendar buttons."""
    if day_iso == today_iso():
        return f"📅 اليوم ({day_iso})"
    if day_iso == yesterday_iso():
        return f"📆 أمس ({day_iso})"
    try:
        wd = _AR_WEEKDAYS.get(datetime.strptime(day_iso, "%Y-%m-%d").weekday(), "")
        return f"🗓 {wd} {day_iso}"
    except ValueError:
        return f"🗓 {day_iso}"


def summarize_rows(rows: list[dict]) -> dict:
    """Compute honest live/closed KPIs over a set of signal dicts."""
    total = len(rows)
    open_n = 0
    wins = 0
    losses = 0
    net_pips = 0.0
    for r in rows:
        st = (r.get("status") or "").upper()
        closed = is_signal_closed(r)
        if not closed and st in _OPEN_STATUSES:
            open_n += 1
        elif st == "TP3_HIT" or (st in ("TP1_HIT", "TP2_HIT") and closed):
            wins += 1
            net_pips += float(r.get("pips_result") or 0.0)
        elif st == "SL_HIT":
            losses += 1
            net_pips += float(r.get("pips_result") or 0.0)
        elif st in ("TP1_HIT", "TP2_HIT"):
            # Open runner that already banked TP1/TP2 profit
            net_pips += float(r.get("pips_result") or 0.0)
    return {
        "total": total,
        "open": open_n,
        "wins": wins,
        "losses": losses,
        "net_pips": round(net_pips, 1),
    }


def luxury_day_header(
    heading: str,
    scope_label: str,
    summary: dict,
    page: int,
    total_pages: int,
    extra_lines: list[str] | None = None,
) -> str:
    """Render a royal luxury blockquote header card for Admin/User signals."""
    lines = [
        f"📡 <b><u>{heading}</u></b>",
        f"✨ <b>{esc(scope_label)}</b>",
    ]
    card_lines = [
        f"▎📊 الإجمالي: <b>{summary['total']}</b> · "
        f"🟢 مفتوحة: <b>{summary['open']}</b> · "
        f"🏆 رابحة: <b>{summary['wins']}</b> · "
        f"🛑 وقف: <b>{summary['losses']}</b>",
    ]
    if summary["wins"] or summary["losses"] or summary["net_pips"] != 0:
        pips_sign = "📈" if summary["net_pips"] >= 0 else "📉"
        card_lines.append(
            f"▎{pips_sign} صافي النقاط المحققة: <b>{summary['net_pips']:+.1f} pips</b>"
        )
    if total_pages > 1:
        card_lines.append(f"▎📄 صفحة <b>{page + 1}</b> من <b>{total_pages}</b>")
    if extra_lines:
        card_lines.extend(extra_lines)
    lines.append("<blockquote>" + "\n".join(card_lines) + "</blockquote>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ADMIN QUERIES (100% ANSI SQL — PostgreSQL & SQLite identical)
# ---------------------------------------------------------------------------

def admin_scope_counts() -> dict:
    """ONE single DB trip for Today / Yesterday / Open / All / Distinct Days."""
    t_lo, t_hi = date_bounds(today_iso())
    y_lo, y_hi = date_bounds(yesterday_iso())
    with db.conn() as c:
        row = c.execute(
            """SELECT
                 COUNT(*) AS total_n,
                 COALESCE(SUM(CASE WHEN created_at>=? AND created_at<? THEN 1 ELSE 0 END), 0) AS today_n,
                 COALESCE(SUM(CASE WHEN created_at>=? AND created_at<? THEN 1 ELSE 0 END), 0) AS yesterday_n,
                 COALESCE(SUM(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                   AND (closed_at IS NULL OR closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                 COUNT(DISTINCT SUBSTR(created_at, 1, 10)) AS days_n
               FROM signals""",
            (t_lo, t_hi, y_lo, y_hi),
        ).fetchone()
    return {
        "total": int(row["total_n"] or 0),
        "today": int(row["today_n"] or 0),
        "yesterday": int(row["yesterday_n"] or 0),
        "open": int(row["open_n"] or 0),
        "days": int(row["days_n"] or 0),
    }


def admin_signals_page(
    scope: str = "today",
    statuses: tuple[str, ...] = (),
    page: int = 0,
    size: int = 8,
) -> tuple[int, list[dict], dict]:
    """Fetch paginated admin signals with REAL global seq + full scope KPIs.
    scope: 'today' | 'yesterday' | 'YYYY-MM-DD' | 'open' | 'all'."""
    where = ["1=1"]
    args: list = []

    day_iso = resolve_scope_date(scope)
    if day_iso:
        lo, hi = date_bounds(day_iso)
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    elif scope == "open":
        where.append("status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')")

    if statuses:
        marks = ",".join("?" for _ in statuses)
        where.append(f"status IN ({marks})")
        args.extend(statuses)

    w_sql = " AND ".join(where)
    with db.conn() as c:
        agg = c.execute(
            f"""SELECT
                  COUNT(*) AS total_n,
                  COALESCE(SUM(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                    AND (closed_at IS NULL OR closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                  COALESCE(SUM(CASE WHEN status='TP3_HIT'
                                    OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='') THEN 1 ELSE 0 END), 0) AS wins_n,
                  COALESCE(SUM(CASE WHEN status='SL_HIT' THEN 1 ELSE 0 END), 0) AS losses_n,
                  COALESCE(SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                                    THEN pips_result ELSE 0 END), 0) AS net_pips
                FROM signals WHERE {w_sql}""",
            args,
        ).fetchone()
        rows = c.execute(
            f"""SELECT * FROM (
                  SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq FROM signals
                ) s
                WHERE {w_sql}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            [*args, size, page * size],
        ).fetchall()

    total = int(agg["total_n"] or 0)
    summary = {
        "total": total,
        "open": int(agg["open_n"] or 0),
        "wins": int(agg["wins_n"] or 0),
        "losses": int(agg["losses_n"] or 0),
        "net_pips": round(float(agg["net_pips"] or 0.0), 1),
    }
    return total, [dict(r) for r in rows], summary


def admin_dates_page(page: int = 0, size: int = 10) -> tuple[int, list[dict]]:
    """Distinct calendar days with per-day totals, wins, losses, net pips."""
    with db.conn() as c:
        total = int(
            c.execute("SELECT COUNT(DISTINCT SUBSTR(created_at, 1, 10)) n FROM signals").fetchone()["n"] or 0
        )
        rows = c.execute(
            """SELECT
                 SUBSTR(created_at, 1, 10) AS day,
                 COUNT(*) AS total_n,
                 COALESCE(SUM(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                   AND (closed_at IS NULL OR closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                 COALESCE(SUM(CASE WHEN status='TP3_HIT'
                                   OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='') THEN 1 ELSE 0 END), 0) AS wins_n,
                 COALESCE(SUM(CASE WHEN status='SL_HIT' THEN 1 ELSE 0 END), 0) AS losses_n,
                 COALESCE(SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                                   THEN pips_result ELSE 0 END), 0) AS net_pips
               FROM signals
               WHERE created_at IS NOT NULL AND created_at!=''
               GROUP BY SUBSTR(created_at, 1, 10)
               ORDER BY day DESC
               LIMIT ? OFFSET ?""",
            (size, page * size),
        ).fetchall()
    return total, [dict(r) for r in rows]


def admin_pl_rows(period: str = "all") -> list[dict]:
    """100% PostgreSQL + SQLite portable P/L accounting per instrument.
    NEVER uses SUM(boolean) or ROUND(double, int)."""
    where = ["status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')"]
    args: list = []
    if period == "today":
        lo, hi = date_bounds(today_iso())
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    elif period == "yesterday":
        lo, hi = date_bounds(yesterday_iso())
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    elif period == "7d":
        lo = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        where.append("created_at>=?")
        args.append(lo)
    elif is_valid_iso_date(period):
        lo, hi = date_bounds(period)
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])

    w_sql = " AND ".join(where)
    with db.conn() as c:
        rows = c.execute(
            f"""SELECT
                  symbol,
                  COUNT(*) AS n,
                  COALESCE(SUM(CASE WHEN pips_result > 0 THEN 1 ELSE 0 END), 0) AS wins,
                  COALESCE(SUM(CASE WHEN pips_result <= 0 THEN 1 ELSE 0 END), 0) AS losses,
                  COALESCE(SUM(pips_result), 0) AS net_pips,
                  COALESCE(AVG(pips_result), 0) AS avg_pips
                FROM signals
                WHERE {w_sql}
                GROUP BY symbol
                ORDER BY net_pips DESC""",
            args,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["avg_pips"] = round(float(d.get("avg_pips") or 0.0), 1)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# USER QUERIES — Previous signals NEVER vanish; plan + delivery aware!
# ---------------------------------------------------------------------------

def _user_visibility_sql(
    uid: int,
    markets: list[str],
    timeframes: list[str],
    strategies: list[str],
    min_score: int,
) -> tuple[str, list]:
    """Signals visible to a user:
      1) ANY signal ALREADY delivered to this user (NEVER vanishes, even after closing!)
      2) OR any non-cancelled/non-expired signal matching their plan
         (excluding other users' private SIGNAL_NOW requests).
    """
    m_list = markets or ["XAUUSD"]
    t_list = timeframes or ["M15"]
    s_list = strategies or ["ICT"]
    marks_m = ",".join("?" for _ in m_list)
    marks_t = ",".join("?" for _ in t_list)
    marks_s = ",".join("?" for _ in s_list)
    sql = (
        "(s.id IN (SELECT signal_id FROM signal_deliveries WHERE user_id=?) "
        "OR ("
        "s.status NOT IN ('CANCELLED','EXPIRED') "
        "AND (s.source!='SIGNAL_NOW' OR s.created_by=?) "
        f"AND REPLACE(s.symbol,'/','') IN ({marks_m}) "
        f"AND s.timeframe IN ({marks_t}) "
        f"AND s.strategy IN ({marks_s}) "
        "AND s.score>=?"
        "))"
    )
    args = [int(uid), int(uid), *m_list, *t_list, *s_list, int(min_score)]
    return sql, args


def user_scope_counts(
    uid: int,
    markets: list[str],
    timeframes: list[str],
    strategies: list[str],
    min_score: int,
) -> dict:
    """ONE single DB trip for user's Today / Yesterday / Open / All / Days counts."""
    vis_sql, vis_args = _user_visibility_sql(uid, markets, timeframes, strategies, min_score)
    t_lo, t_hi = date_bounds(today_iso())
    y_lo, y_hi = date_bounds(yesterday_iso())
    with db.conn() as c:
        row = c.execute(
            f"""SELECT
                  COUNT(*) AS total_n,
                  COALESCE(SUM(CASE WHEN s.created_at>=? AND s.created_at<? THEN 1 ELSE 0 END), 0) AS today_n,
                  COALESCE(SUM(CASE WHEN s.created_at>=? AND s.created_at<? THEN 1 ELSE 0 END), 0) AS yesterday_n,
                  COALESCE(SUM(CASE WHEN s.status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                    AND (s.closed_at IS NULL OR s.closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                  COUNT(DISTINCT SUBSTR(s.created_at, 1, 10)) AS days_n
                FROM signals s
                WHERE {vis_sql}""",
            [t_lo, t_hi, y_lo, y_hi, *vis_args],
        ).fetchone()
    return {
        "total": int(row["total_n"] or 0),
        "today": int(row["today_n"] or 0),
        "yesterday": int(row["yesterday_n"] or 0),
        "open": int(row["open_n"] or 0),
        "days": int(row["days_n"] or 0),
    }


def user_signals_page(
    uid: int,
    markets: list[str],
    timeframes: list[str],
    strategies: list[str],
    min_score: int,
    scope: str = "today",
    page: int = 0,
    size: int = 8,
) -> tuple[int, list[dict], dict, set[int]]:
    """Paginated user signals for Today / Yesterday / YYYY-MM-DD / Open / All,
    with REAL global seq, scope KPIs, and already-delivered ID set in ONE conn."""
    vis_sql, vis_args = _user_visibility_sql(uid, markets, timeframes, strategies, min_score)
    where = [vis_sql]
    args = list(vis_args)

    day_iso = resolve_scope_date(scope)
    if day_iso:
        lo, hi = date_bounds(day_iso)
        where.append("s.created_at>=? AND s.created_at<?")
        args.extend([lo, hi])
    elif scope == "open":
        where.append(
            "s.status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') "
            "AND (s.closed_at IS NULL OR s.closed_at='')"
        )

    w_sql = " AND ".join(where)
    with db.conn() as c:
        agg = c.execute(
            f"""SELECT
                  COUNT(*) AS total_n,
                  COALESCE(SUM(CASE WHEN s.status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                    AND (s.closed_at IS NULL OR s.closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                  COALESCE(SUM(CASE WHEN s.status='TP3_HIT'
                                    OR (s.status IN ('TP1_HIT','TP2_HIT') AND s.closed_at!='') THEN 1 ELSE 0 END), 0) AS wins_n,
                  COALESCE(SUM(CASE WHEN s.status='SL_HIT' THEN 1 ELSE 0 END), 0) AS losses_n,
                  COALESCE(SUM(CASE WHEN s.status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                                    THEN s.pips_result ELSE 0 END), 0) AS net_pips
                FROM signals s WHERE {w_sql}""",
            args,
        ).fetchone()
        rows = c.execute(
            f"""SELECT * FROM (
                  SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq FROM signals
                ) s
                WHERE {w_sql}
                ORDER BY s.id DESC LIMIT ? OFFSET ?""",
            [*args, size, page * size],
        ).fetchall()
        row_dicts = [dict(r) for r in rows]
        delivered_ids: set[int] = set()
        if row_dicts:
            sids = [int(r["id"]) for r in row_dicts]
            marks = ",".join("?" for _ in sids)
            drows = c.execute(
                f"SELECT signal_id FROM signal_deliveries WHERE user_id=? AND signal_id IN ({marks})",
                [int(uid), *sids],
            ).fetchall()
            delivered_ids = {int(r["signal_id"]) for r in drows}

    total = int(agg["total_n"] or 0)
    summary = {
        "total": total,
        "open": int(agg["open_n"] or 0),
        "wins": int(agg["wins_n"] or 0),
        "losses": int(agg["losses_n"] or 0),
        "net_pips": round(float(agg["net_pips"] or 0.0), 1),
    }
    return total, row_dicts, summary, delivered_ids


def user_dates_page(
    uid: int,
    markets: list[str],
    timeframes: list[str],
    strategies: list[str],
    min_score: int,
    page: int = 0,
    size: int = 10,
) -> tuple[int, list[dict]]:
    """Distinct calendar dates containing signals visible to this user."""
    vis_sql, vis_args = _user_visibility_sql(uid, markets, timeframes, strategies, min_score)
    with db.conn() as c:
        total = int(
            c.execute(
                f"""SELECT COUNT(DISTINCT SUBSTR(s.created_at, 1, 10)) n
                    FROM signals s WHERE {vis_sql} AND s.created_at IS NOT NULL AND s.created_at!=''""",
                vis_args,
            ).fetchone()["n"] or 0
        )
        rows = c.execute(
            f"""SELECT
                  SUBSTR(s.created_at, 1, 10) AS day,
                  COUNT(*) AS total_n,
                  COALESCE(SUM(CASE WHEN s.status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                    AND (s.closed_at IS NULL OR s.closed_at='') THEN 1 ELSE 0 END), 0) AS open_n,
                  COALESCE(SUM(CASE WHEN s.status='TP3_HIT'
                                    OR (s.status IN ('TP1_HIT','TP2_HIT') AND s.closed_at!='') THEN 1 ELSE 0 END), 0) AS wins_n,
                  COALESCE(SUM(CASE WHEN s.status='SL_HIT' THEN 1 ELSE 0 END), 0) AS losses_n,
                  COALESCE(SUM(CASE WHEN s.status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                                    THEN s.pips_result ELSE 0 END), 0) AS net_pips
                FROM signals s
                WHERE {vis_sql} AND s.created_at IS NOT NULL AND s.created_at!=''
                GROUP BY SUBSTR(s.created_at, 1, 10)
                ORDER BY day DESC
                LIMIT ? OFFSET ?""",
            [*vis_args, size, page * size],
        ).fetchall()
    return total, [dict(r) for r in rows]


def format_date_journal_button(row: dict) -> str:
    """Build a crisp luxury calendar row button label for one date."""
    day = row["day"]
    total = int(row.get("total_n") or 0)
    wins = int(row.get("wins_n") or 0)
    losses = int(row.get("losses_n") or 0)
    open_n = int(row.get("open_n") or 0)
    net = float(row.get("net_pips") or 0.0)
    parts = [f"{total} إشارة"]
    if open_n:
        parts.append(f"🟢{open_n}")
    if wins:
        parts.append(f"🏆{wins}")
    if losses:
        parts.append(f"🛑{losses}")
    if wins or losses or net != 0:
        parts.append(f"{net:+.0f}p")
    return f"{ar_date_short(day)} · {' · '.join(parts)}"
