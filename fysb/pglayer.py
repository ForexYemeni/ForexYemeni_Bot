"""PostgreSQL engine layer — real Supabase/PostgreSQL behind the same API.

Activated automatically when DATABASE_URL is set in .env (config.USE_PG).
Without it, the platform stays on the classic SQLite engine — zero change.

What this layer does transparently for the whole codebase:
  * Same SQL dialect the SQLite codebase already speaks: `?` placeholders,
    `INSERT OR IGNORE INTO`, `IS NOT <n>`, and cursor.lastrowid are translated
    at execution time (no call-site changes anywhere in handlers/services).
  * Rows behave exactly like sqlite3.Row: row["col"], row[0], dict(row),
    len(row), iteration over values.
  * A small in-process connection pool keeps latency low on hosted runtimes;
    a dead connection is reconnected and the statement retried once.
  * INSERTs into serial-id tables gain `RETURNING id` so lastrowid keeps
    working; conflicts under INSERT OR IGNORE still yield None.
  * Credentials live only in DATABASE_URL — never logged, never rendered.

Guarantees kept identical to the SQLite engine:
  * Explicit transaction discipline: commit on success, rollback on error.
  * No destructive operations; schema changes stay additive (db.init_db).
"""
import re
import threading
from collections import deque
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from fysb import config

# Unified exception surface (referrals.py and friends catch db.IntegrityError)
IntegrityError = psycopg.errors.IntegrityError
OperationalError = psycopg.errors.OperationalError

# ---------------------------------------------------------------------------
# Row — sqlite3.Row compatibility
# ---------------------------------------------------------------------------
class Row:
    """Mapping row with sqlite3.Row semantics: key + integer access, dict()."""

    __slots__ = ("_map", "_vals")

    def __init__(self, mapping):
        self._map = mapping
        self._vals = list(mapping.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._map[key]

    def get(self, key, default=None):
        return self._map.get(key, default)

    def keys(self):
        return list(self._map.keys())

    def __contains__(self, key):
        return key in self._map

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return f"Row({self._map!r})"


# ---------------------------------------------------------------------------
# Connection pool — bounded, thread-safe, self-healing
# ---------------------------------------------------------------------------
_pool: deque = deque()
_pool_lock = threading.Lock()
_POOL_MAX = 8


def _connect():
    return psycopg.connect(
        config.DATABASE_URL,
        connect_timeout=15,
        application_name="FOREXYEMENI",
        row_factory=dict_row,
    )


def _acquire():
    with _pool_lock:
        while _pool:
            c = _pool.popleft()
            if not c.closed:
                return c
    return _connect()


def _release(c):
    try:
        c.rollback()          # never hand a dirty session to the next borrower
    except Exception:
        return
    with _pool_lock:
        if len(_pool) < _POOL_MAX:
            _pool.append(c)
            return
    try:
        c.close()
    except Exception:
        pass


def close_pool() -> None:
    with _pool_lock:
        while _pool:
            try:
                _pool.popleft().close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# SQL translation — SQLite dialect -> PostgreSQL
# ---------------------------------------------------------------------------
_OR_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_IS_NOT_NUM_RE = re.compile(r"\bIS\s+NOT\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_AUTOINC_RE = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_INT_TYPE_RE = re.compile(r"\bINTEGER\b", re.IGNORECASE)
_INSERT_TBL_RE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)


def ddl(schema_sql: str) -> str:
    """DDL translation: AUTOINCREMENT -> BIGSERIAL, INTEGER -> BIGINT.

    Telegram user ids already exceed 32-bit range on modern accounts, so
    every integer column is widened to 64-bit on PostgreSQL (lossless)."""
    s = _AUTOINC_RE.sub("BIGSERIAL PRIMARY KEY", schema_sql)
    return _INT_TYPE_RE.sub("BIGINT", s)

_serial_tables: set[str] | None = None
_serial_lock = threading.Lock()


def reset_serial_cache() -> None:
    """Invalidate the serial-id table cache (call after schema creation)."""
    global _serial_tables
    with _serial_lock:
        _serial_tables = None


def _qmarks(sql: str, escape_percent: bool) -> str:
    """Placeholder + percent handling for psycopg client-side binding.

    psycopg's client-side formatter is quote-BLIND: with params present, ANY
    bare % (even inside string literals like LIKE '%ICT%') is a format token
    and must be doubled. Without params the query is sent as-is, so % must
    stay untouched. `?` -> `%s` stays quote-aware in both modes.
    """
    out = []
    in_q = False
    for ch in sql:
        if ch == "'":
            in_q = not in_q
            out.append(ch)
        elif ch == "?" and not in_q:
            out.append("%s")
        elif ch == "%" and (escape_percent or not in_q):
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def _serial_set(cur) -> set:
    global _serial_tables
    if _serial_tables is None:
        with _serial_lock:
            if _serial_tables is None:
                rows = cur.execute(
                    "SELECT DISTINCT table_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND column_name='id' "
                    "AND column_default LIKE 'nextval%'").fetchall()
                _serial_tables = {r["table_name"] for r in rows}
    return _serial_tables


def translate(sql: str, cur=None, has_params: bool = False) -> str:
    """Rewrite one SQLite-dialect statement into PostgreSQL dialect."""
    s = sql.strip()
    if _PRAGMA_RE.match(s):
        raise ValueError(
            "PRAGMA is SQLite-only — db.py handles dialect branches before this point")
    s = ddl(s)
    s = _IS_NOT_NUM_RE.sub(r"IS DISTINCT FROM \1", s)

    had_ignore = bool(_OR_IGNORE_RE.search(s))
    if had_ignore:
        s = _OR_IGNORE_RE.sub("INSERT INTO", s, count=1)
        # statements already carrying ON CONFLICT stay untouched (single clause)
        if "on conflict" not in s.lower():
            s = s + " ON CONFLICT DO NOTHING"

    s = _qmarks(s, escape_percent=has_params)

    # lastrowid support: serial-id INSERTs gain RETURNING id
    m = _INSERT_TBL_RE.match(s)
    if m and cur is not None:
        tbl = m.group(1).lower()
        if tbl in {t.lower() for t in _serial_set(cur)} \
                and "returning" not in s.lower():
            s = s + " RETURNING id"
    return s


# ---------------------------------------------------------------------------
# Cursor / Connection wrappers
# ---------------------------------------------------------------------------
class Cursor:
    def __init__(self, cur):
        self._cur = cur
        self._lastrowid = None

    def execute(self, sql, params=()):
        translated = translate(sql, self._cur, has_params=bool(params))
        if params:
            self._cur.execute(translated, tuple(params))
        else:
            self._cur.execute(translated)
        if "returning id" in translated.lower():
            row = self._cur.fetchone()
            self._lastrowid = row["id"] if row else None
        return self

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def fetchone(self):
        r = self._cur.fetchone()
        return Row(r) if r is not None else None

    def fetchall(self):
        return [Row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield Row(r)


class Conn:
    """Borrowed pooled connection with sqlite3-style semantics.

    `with conn() as c:` commits on success / rolls back on error and returns
    the session to the pool. close() discards the session instead.
    """

    def __init__(self):
        self._c = _acquire()

    # -- internal -----------------------------------------------------------
    def _exec(self, sql, params):
        cur = Cursor(self._c.cursor())
        return cur.execute(sql, params)

    def _dead(self, e) -> bool:
        return isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError))

    # -- API ----------------------------------------------------------------
    def execute(self, sql, params=()):
        try:
            return self._exec(sql, params)
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            # dead/broken session: discard, reconnect, retry exactly once
            try:
                self._c.close()
            except Exception:
                pass
            self._c = _acquire()
            return self._exec(sql, params)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        try:
            self._c.rollback()
            self._c.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self._c.commit()
            else:
                self._c.rollback()
        finally:
            _release(self._c)
        return False


def conn() -> Conn:
    return Conn()


@contextmanager
def tx(immediate: bool = False):
    """Transaction context identical to the SQLite one (BEGIN is implicit)."""
    with conn() as c:
        yield c


# ---------------------------------------------------------------------------
# Health / schema introspection / dump-restore
# ---------------------------------------------------------------------------
def healthy() -> tuple[bool, str]:
    try:
        with conn() as c:
            r = c.execute("SELECT 1").fetchone()
        return (r is not None and r[0] == 1), "ok" if r else "no response"
    except Exception as e:
        return False, type(e).__name__


def table_columns(table: str) -> set[str]:
    with conn() as c:
        rows = c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=?", (table,)).fetchall()
    return {r[0] for r in rows}


def run_schema(c: Conn, schema_sql: str) -> None:
    """Execute a multi-statement DDL script (psycopg has no executescript).

    Full-line `--` comments are stripped first: comment text may contain
    semicolons, which would corrupt a naive `;` split.
    """
    clean = "\n".join(line for line in schema_sql.splitlines()
                      if not line.strip().startswith("--"))
    for stmt in clean.split(";"):
        stmt = stmt.strip()
        if stmt:
            c.execute(stmt)
    reset_serial_cache()


def list_tables() -> set[str]:
    with conn() as c:
        rows = c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' "
            "AND table_name NOT LIKE '\\_%'").fetchall()
    return {r[0] for r in rows}


def dump_all() -> dict:
    """Full consistent dump: {table: [ {col: val, ...}, ... ]} for backups."""
    data: dict = {}
    with conn() as c:
        tables = sorted(list_tables())
        for t in tables:
            rows = c.execute(f"SELECT * FROM {t}").fetchall()
            data[t] = [dict(r) for r in rows]
    return data


def restore_json(data: dict) -> str:
    """Full restore from a dump: wipe + insert + resync id sequences."""
    tables = sorted(t for t in data.keys() if isinstance(data.get(t), list))
    with conn() as c:
        for t in tables:
            c.execute(f"DELETE FROM {t}")
            for row in data[t]:
                cols = list(row.keys())
                if not cols:
                    continue
                # JSON has no ints-vs-bools distinction for old dumps: coerce
                vals = [(int(v) if isinstance(v, bool) else v) for k in cols
                        for v in (row[k],)]
                marks = ",".join(["?"] * len(cols))
                collist = ",".join(cols)
                c.execute(f"INSERT INTO {t}({collist}) VALUES({marks})", vals)
        # resync every serial sequence after explicit-id inserts
        serials = c.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema='public' AND column_name='id' "
            "AND column_default LIKE 'nextval%'").fetchall()
        table_set = set(tables)
        for r in serials:
            t = r[0]
            if t in table_set:
                c.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)")
    return f"restored {len(tables)} tables"
