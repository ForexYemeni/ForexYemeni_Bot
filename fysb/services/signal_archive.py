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

from fysb import config, db
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
    """Current Mecca calendar day YYYY-MM-DD (flips at 12:00am بتوقيت مكة)."""
    return config.mecca_today_iso()


def yesterday_iso() -> str:
    """Previous Mecca calendar day YYYY-MM-DD."""
    return (config.mecca_now() - timedelta(days=1)).strftime("%Y-%m-%d")


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
    """Half-open UTC ISO range [lo, hi) corresponding to the exact Mecca day
    (12:00am -> next 12:00am بتوقيت مكة), 100% PG & SQLite compatible."""
    return config.mecca_day_utc_bounds(day_iso)


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
        elif st == "TP3_HIT" or st == "LOCKED_50" or (st in ("TP1_HIT", "TP2_HIT") and closed):
            wins += 1  # v8.14.10 🏅: a royally locked trade is a real win
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
                  COALESCE(SUM(CASE WHEN status='TP3_HIT' OR status='LOCKED_50'
                                    OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='') THEN 1 ELSE 0 END), 0) AS wins_n,
                  COALESCE(SUM(CASE WHEN status='SL_HIT' THEN 1 ELSE 0 END), 0) AS losses_n,
                  COALESCE(SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT','LOCKED_50')
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


def _group_rows_by_mecca_day(raw_rows: list) -> list[dict]:
    """Bucket signal rows by exact Mecca calendar date (12:00am KSA boundary)."""
    buckets: dict[str, dict] = {}
    for r in raw_rows:
        day = config.mecca_date_of(r["created_at"])
        if not day or len(day) != 10:
            continue
        b = buckets.setdefault(day, {
            "day": day, "total_n": 0, "open_n": 0, "wins_n": 0, "losses_n": 0, "net_pips": 0.0,
        })
        st = (r["status"] or "").upper()
        closed_at = (r["closed_at"] or "").strip()
        pips = float(r["pips_result"] or 0.0)
        b["total_n"] += 1
        if st in _OPEN_STATUSES and not closed_at:
            b["open_n"] += 1
        elif st == "TP3_HIT" or (st in ("TP1_HIT", "TP2_HIT") and closed_at):
            b["wins_n"] += 1
        elif st == "SL_HIT":
            b["losses_n"] += 1
        if st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"):
            b["net_pips"] += pips
    return sorted(buckets.values(), key=lambda x: x["day"], reverse=True)


def admin_dates_page(page: int = 0, size: int = 10) -> tuple[int, list[dict]]:
    """Distinct Mecca calendar days with per-day totals, wins, losses, net pips."""
    with db.conn() as c:
        raw = c.execute(
            """SELECT created_at, status, closed_at, pips_result
               FROM signals
               WHERE created_at IS NOT NULL AND created_at!=''"""
        ).fetchall()
    grouped = _group_rows_by_mecca_day(raw)
    total = len(grouped)
    start = max(0, int(page)) * max(1, int(size))
    return total, grouped[start:start + max(1, int(size))]


PL_SECTORS = (
    ("metal", "🥇", "المعادن والسلع", "المعادن"),
    ("forex", "💱", "الفوركس والعملات", "الفوركس"),
    ("crypto", "₿", "العملات الرقمية", "الكريبتو"),
    ("index", "📈", "المؤشرات والأسهم", "المؤشرات"),
)

PL_SECTOR_META = {k: (icon, title, short) for k, icon, title, short in PL_SECTORS}


def sector_of_symbol(symbol: str, catalog_cats: dict[str, str] | None = None) -> str:
    """Resolve canonical market sector: metal | forex | crypto | index."""
    from fysb import instruments
    sym = instruments.normalize(symbol)
    raw = ""
    if catalog_cats and sym in catalog_cats:
        raw = (catalog_cats[sym] or "").strip().lower()
    if not raw:
        raw = (instruments.spec(sym).category or "").strip().lower()
    if raw in ("metal", "metals", "commodity", "commodities") or sym in ("XAUUSD", "XAGUSD", "USOIL", "UKOIL", "WTI", "BRENT"):
        return "metal"
    if raw in ("crypto", "cryptocurrency") or sym.startswith(("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "LTC", "TON")):
        return "crypto"
    if raw in ("index", "indices", "stock", "stocks", "equity") or sym in ("US30", "NAS100", "USTEC", "SPX500", "US500", "GER40", "UK100"):
        return "index"
    return "forex"


def format_pf(gross_profit: float, gross_loss: float) -> str:
    """Format institutional Profit Factor safely."""
    gp = max(0.0, float(gross_profit or 0.0))
    gl = abs(float(gross_loss or 0.0))
    if gp <= 0 and gl <= 0:
        return "—"
    if gl <= 1e-9:
        return "∞ (بلا خسارة)"
    return f"{gp / gl:.2f}x"


def _period_where(period: str) -> tuple[list[str], list]:
    where = ["status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT','LOCKED_50')"]
    args: list = []
    p = (period or "all").strip().lower()
    if p == "today":
        lo, hi = date_bounds(today_iso())
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    elif p == "yesterday":
        lo, hi = date_bounds(yesterday_iso())
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    elif p == "7d":
        lo_day = (config.mecca_now() - timedelta(days=7)).strftime("%Y-%m-%d")
        lo, _ = date_bounds(lo_day)
        where.append("created_at>=?")
        args.append(lo)
    elif is_valid_iso_date(p):
        lo, hi = date_bounds(p)
        where.append("created_at>=? AND created_at<?")
        args.extend([lo, hi])
    return where, args


def admin_pl_detailed(period: str = "all", sector: str = "", symbol: str = "") -> dict:
    """v6.20.5 — Institutional P/L accounting grouped cleanly by Market Sector
    (Metals / Forex / Crypto / Indices) and per-Instrument trade ledger.
    Uses ONE DB connection and 100% ANSI SQL (PostgreSQL + SQLite safe)."""
    from fysb import instruments
    where, args = _period_where(period)
    sym_filter = instruments.normalize(symbol) if symbol else ""
    if sym_filter:
        where.append("REPLACE(UPPER(symbol),'/','')=?")
        args.append(sym_filter)
    w_sql = " AND ".join(where)

    with db.conn() as c:
        cat_rows = c.execute("SELECT symbol, category, pip_size, digits, contract_size, spread_pips FROM market_catalog").fetchall()
        catalog_rows = {instruments.normalize(r["symbol"]): dict(r) for r in cat_rows}
        catalog_cats = {k: str(v.get("category") or "") for k, v in catalog_rows.items()}
        sig_rows = c.execute(
            f"""SELECT * FROM (
                  SELECT id, symbol, timeframe, direction, status, pips_result,
                         strategy, score, created_at, closed_at,
                         ROW_NUMBER() OVER (ORDER BY id) AS seq
                  FROM signals
                ) s
                WHERE {w_sql}
                ORDER BY s.id DESC""",
            args,
        ).fetchall()

    def _empty_bucket():
        return {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_usd": 0.0, "gross_profit_usd": 0.0, "gross_loss_usd": 0.0,
            "tp1_n": 0, "tp2_n": 0, "tp3_n": 0, "sl_n": 0, "locked_n": 0,
        }

    grand = _empty_bucket()
    sectors_map: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}

    sec_filter = (sector or "").strip().lower()

    for r in sig_rows:
        d = dict(r)
        sym = instruments.normalize(d["symbol"])
        sec = sector_of_symbol(sym, catalog_cats)
        if sec_filter and sec != sec_filter:
            continue
        pips = float(d.get("pips_result") or 0.0)
        mrow = catalog_rows.get(sym)
        usd = float(instruments.usd_move(sym, pips, 0.01, mrow))
        st = (d.get("status") or "").upper()
        is_win = pips > 0

        if sym not in by_symbol:
            b = _empty_bucket()
            b.update({
                "symbol": sym,
                "sector": sec,
                "net_pips": 0.0,
                "avg_pips": 0.0,
                "gross_profit_pips": 0.0,
                "gross_loss_pips": 0.0,
                "best_pips": None,
                "best_usd": None,
                "worst_pips": None,
                "worst_usd": None,
                "trades": [],
            })
            by_symbol[sym] = b

        if sec not in sectors_map:
            sb = _empty_bucket()
            sb.update({"sector": sec, "symbols": []})
            sectors_map[sec] = sb

        for bucket in (grand, sectors_map[sec], by_symbol[sym]):
            bucket["total"] += 1
            if is_win:
                bucket["wins"] += 1
                bucket["gross_profit_usd"] += max(0.0, usd)
            else:
                bucket["losses"] += 1
                bucket["gross_loss_usd"] += min(0.0, usd)
            bucket["net_usd"] += usd
            if st == "TP1_HIT":
                bucket["tp1_n"] += 1
            elif st == "TP2_HIT":
                bucket["tp2_n"] += 1
            elif st == "TP3_HIT":
                bucket["tp3_n"] += 1
            elif st == "SL_HIT":
                bucket["sl_n"] += 1
            elif st == "LOCKED_50":
                bucket["locked_n"] = bucket.get("locked_n", 0) + 1

        sb_sym = by_symbol[sym]
        sb_sym["net_pips"] += pips
        if is_win:
            sb_sym["gross_profit_pips"] += max(0.0, pips)
        else:
            sb_sym["gross_loss_pips"] += min(0.0, pips)
        if sb_sym["best_pips"] is None or pips > sb_sym["best_pips"]:
            sb_sym["best_pips"] = pips
            sb_sym["best_usd"] = usd
        if sb_sym["worst_pips"] is None or pips < sb_sym["worst_pips"]:
            sb_sym["worst_pips"] = pips
            sb_sym["worst_usd"] = usd
        d["usd"] = usd
        sb_sym["trades"].append(d)

    def _finalize(b: dict):
        n = max(1, b["total"])
        b["win_rate"] = (b["wins"] / n) * 100.0
        b["net_usd"] = round(b["net_usd"], 2)
        b["gross_profit_usd"] = round(b["gross_profit_usd"], 2)
        b["gross_loss_usd"] = round(b["gross_loss_usd"], 2)
        b["pf"] = format_pf(b["gross_profit_usd"], b["gross_loss_usd"])

    _finalize(grand)
    for sym_b in by_symbol.values():
        _finalize(sym_b)
        sym_b["net_pips"] = round(sym_b["net_pips"], 1)
        sym_b["avg_pips"] = round(sym_b["net_pips"] / max(1, sym_b["total"]), 1)
        sym_b["gross_profit_pips"] = round(sym_b["gross_profit_pips"], 1)
        sym_b["gross_loss_pips"] = round(sym_b["gross_loss_pips"], 1)
        sectors_map[sym_b["sector"]]["symbols"].append(sym_b)

    ordered_sectors = []
    for sec_key, _icon, _title, _short in PL_SECTORS:
        if sec_key in sectors_map:
            sb = sectors_map[sec_key]
            _finalize(sb)
            sb["symbols"].sort(key=lambda x: (-x["net_usd"], -x["total"]))
            ordered_sectors.append(sb)

    return {
        "grand": grand,
        "sectors": ordered_sectors,
        "by_symbol": by_symbol,
    }


def admin_pl_rows(period: str = "all") -> list[dict]:
    """100% PostgreSQL + SQLite portable P/L accounting per instrument.
    NEVER uses SUM(boolean) or ROUND(double, int)."""
    where, args = _period_where(period)
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
    """v6.23.0 — 100% Honest Delivered-Only User Signal Archive.

    A user sees ONLY signals that were genuinely delivered as real signal cards
    to their account (`signal_deliveries`):
      1) No undelivered signal ever appears in «📊 الإشارات».
      2) Newly registered users NEVER see old pre-join signals created before
         they joined, and can never be enrolled in pre-join tracker alerts.
      3) Every delivered signal stays permanently in their archive (never
         vanishes after closing on TP1/TP2/TP3/SL) and matches 1-to-1 with
         their live tracking updates.
    """
    sql = "s.id IN (SELECT signal_id FROM signal_deliveries WHERE user_id=?)"
    return sql, [int(uid)]


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
    """Distinct Mecca calendar dates containing signals visible to this user."""
    vis_sql, vis_args = _user_visibility_sql(uid, markets, timeframes, strategies, min_score)
    with db.conn() as c:
        raw = c.execute(
            f"""SELECT s.created_at, s.status, s.closed_at, s.pips_result
                FROM signals s
                WHERE {vis_sql} AND s.created_at IS NOT NULL AND s.created_at!=''""",
            vis_args,
        ).fetchall()
    grouped = _group_rows_by_mecca_day(raw)
    total = len(grouped)
    start = max(0, int(page)) * max(1, int(size))
    return total, grouped[start:start + max(1, int(size))]


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
