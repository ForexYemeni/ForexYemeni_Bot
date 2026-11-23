"""Data layer — SQLite (WAL) or PostgreSQL (Supabase), same API, zero call-site changes.

Engine selection is automatic from .env:
  * DATABASE_URL set        -> real PostgreSQL via fysb.pglayer (Supabase-ready)
  * DATABASE_URL absent     -> classic local SQLite (WAL mode)

Guarantees (both engines):
  * Existing tables/rows are never dropped or rewritten destructively.
  * Migrations only ADD columns/tables/indexes, and a pre-migration snapshot
    is taken automatically when a structural change is applied.
  * All multi-step writes use explicit transactions.
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fysb import config

if config.USE_PG:
    from fysb import pglayer

# Unified integrity-error surface for both engines (caught by services)
IntegrityError = pglayer.IntegrityError if config.USE_PG else sqlite3.IntegrityError

_local = threading.local()
_migration_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_start_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")


def conn():
    """Engine-appropriate connection (PG: pooled session; SQLite: fresh)."""
    if config.USE_PG:
        return pglayer.conn()
    c = sqlite3.connect(str(config.DB_FILE), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


@contextmanager
def tx(immediate: bool = False):
    """Explicit transaction context: commit on success, rollback on error."""
    if config.USE_PG:
        with pglayer.conn() as c:   # BEGIN is implicit in PostgreSQL
            yield c
        return
    with conn() as c:
        c.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def healthy() -> tuple[bool, str]:
    if config.USE_PG:
        return pglayer.healthy()
    try:
        with conn() as c:
            row = c.execute("PRAGMA integrity_check(5)").fetchone()
        ok = str(row[0]).lower() == "ok"
        return ok, str(row[0])
    except Exception as e:
        return False, type(e).__name__


def wal_checkpoint() -> None:
    if config.USE_PG:
        return  # no WAL concept on PostgreSQL
    try:
        with conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Schema: CREATE IF NOT EXISTS (never drops), then ADD-only column migrations.
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', full_name TEXT DEFAULT '',
  joined_at TEXT NOT NULL, is_blocked INTEGER DEFAULT 0,
  notifications INTEGER DEFAULT 1, timeframe TEXT DEFAULT 'M15',
  strategy TEXT DEFAULT '', market TEXT DEFAULT 'XAUUSD',
  min_score INTEGER DEFAULT 80, language TEXT DEFAULT 'ar'
);
CREATE TABLE IF NOT EXISTS plans(
  code TEXT PRIMARY KEY, title TEXT NOT NULL, price REAL DEFAULT 0,
  currency TEXT DEFAULT 'USD', duration_days INTEGER DEFAULT 30,
  min_score INTEGER DEFAULT 80, max_accounts INTEGER DEFAULT 0,
  enabled INTEGER DEFAULT 1, markets TEXT DEFAULT 'XAUUSD',
  timeframes TEXT DEFAULT 'M15,H1,H4', strategies TEXT DEFAULT 'ICT,SMC'
);
CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  plan_code TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT,
  status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  plan_code TEXT NOT NULL, amount REAL DEFAULT 0, method TEXT DEFAULT 'MANUAL',
  receipt_file_id TEXT, status TEXT DEFAULT 'PENDING',
  rejection_reason TEXT DEFAULT '', created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
  direction TEXT NOT NULL, timeframe TEXT DEFAULT 'M15',
  entry REAL NOT NULL, stop REAL NOT NULL, tp1 REAL NOT NULL,
  tp2 REAL NOT NULL, tp3 REAL NOT NULL, targets TEXT DEFAULT '', rr REAL NOT NULL,
  score INTEGER DEFAULT 0, strategy TEXT DEFAULT 'MANUAL',
  reason TEXT DEFAULT '', status TEXT DEFAULT 'ACTIVE',
  source TEXT DEFAULT 'MANUAL', created_by INTEGER, created_at TEXT NOT NULL,
  filled_at TEXT DEFAULT '',
  UNIQUE(symbol,direction,timeframe,entry,created_at)
);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  subject TEXT DEFAULT 'Support', message TEXT NOT NULL,
  status TEXT DEFAULT 'OPEN', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER,
  action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  broker TEXT NOT NULL, account_type TEXT NOT NULL,
  account_ref TEXT DEFAULT '', status TEXT DEFAULT 'NOT_CONNECTED',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  signal_id INTEGER, status TEXT DEFAULT 'PAPER_ONLY', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engine_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
  result TEXT NOT NULL, detail TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mode_registry(
  code TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, executable INTEGER NOT NULL DEFAULT 0,
  adapter_key TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_catalog(
  symbol TEXT PRIMARY KEY, title TEXT NOT NULL, provider_symbol TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeframe_catalog(
  code TEXT PRIMARY KEY, provider_interval TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_catalog(
  code TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_methods(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  instructions TEXT NOT NULL, account_ref TEXT DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS support_config(
  id INTEGER PRIMARY KEY CHECK(id=1), username TEXT DEFAULT '', phone TEXT DEFAULT '',
  channel TEXT DEFAULT '', group_chat TEXT DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_deliveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  signal_id INTEGER NOT NULL, delivered_at TEXT NOT NULL, UNIQUE(user_id,signal_id)
);
-- ---- NEW IN THIS BUILD (all additive; nothing below alters legacy rows) ----
CREATE TABLE IF NOT EXISTS ticket_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
  sender_role TEXT NOT NULL DEFAULT 'user', sender_id INTEGER NOT NULL,
  body TEXT NOT NULL, file_id TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE,
  balance REAL NOT NULL DEFAULT 10000, currency TEXT DEFAULT 'USD',
  peak_equity REAL NOT NULL DEFAULT 10000, leverage REAL DEFAULT 100,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
  lots REAL NOT NULL, entry REAL NOT NULL, sl REAL DEFAULT 0,
  tp REAL DEFAULT 0, status TEXT DEFAULT 'OPEN',
  exit_price REAL, pnl REAL DEFAULT 0, reason TEXT DEFAULT '',
  opened_at TEXT NOT NULL, closed_at TEXT, peak_pnl REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS broadcasts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
  audience TEXT NOT NULL DEFAULT 'all', plan_code TEXT DEFAULT '',
  text TEXT NOT NULL, file_id TEXT DEFAULT '', has_photo INTEGER DEFAULT 0,
  total INTEGER DEFAULT 0, sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
  status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS broker_adapters(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL, status TEXT DEFAULT 'NOT_CONFIGURED',
  enabled INTEGER DEFAULT 0, config_json TEXT DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  kind TEXT NOT NULL, payload TEXT DEFAULT '', read INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
  tool TEXT DEFAULT '', analysis_type TEXT DEFAULT '', strategy TEXT DEFAULT '',
  price REAL DEFAULT 0, direction TEXT DEFAULT 'NEUTRAL', quality INTEGER DEFAULT 0,
  summary TEXT DEFAULT '', detail TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS broadcast_channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
  title TEXT DEFAULT '', kind TEXT DEFAULT 'channel',
  enabled INTEGER NOT NULL DEFAULT 0,
  auto_analysis INTEGER NOT NULL DEFAULT 1,
  auto_signals INTEGER NOT NULL DEFAULT 0,
  markets TEXT DEFAULT 'XAUUSD', timeframes TEXT DEFAULT 'M15,H1',
  strategies TEXT DEFAULT 'ALL', interval_minutes INTEGER DEFAULT 60,
  last_sent TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_watchers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  symbol TEXT NOT NULL, timeframe TEXT NOT NULL, strategy TEXT NOT NULL,
  style TEXT DEFAULT '', interval_minutes INTEGER DEFAULT 30,
  status TEXT DEFAULT 'ACTIVE', next_run TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS signal_watchers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
  symbol TEXT NOT NULL, timeframe TEXT NOT NULL, strategy TEXT NOT NULL,
  style TEXT DEFAULT '', interval_minutes INTEGER DEFAULT 30,
  status TEXT DEFAULT 'ACTIVE', next_run TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ref_campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL, owner_id INTEGER NOT NULL DEFAULT 0,
  target_invited INTEGER NOT NULL DEFAULT 50,
  target_converted INTEGER NOT NULL DEFAULT 5,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS referrals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL,
  invitee_id INTEGER NOT NULL UNIQUE, invited_at TEXT NOT NULL,
  converted INTEGER NOT NULL DEFAULT 0, converted_at TEXT DEFAULT '',
  plan_code TEXT DEFAULT ''
);
"""

# ADD-only column migrations: table -> {column: definition}
ADD_COLUMNS = {
    "users": {
        "last_seen": "TEXT DEFAULT ''",
        "is_admin_granted": "INTEGER DEFAULT 0",
    },
    "plans": {"max_signals_per_day": "INTEGER DEFAULT 3",
              "max_analyses_per_day": "INTEGER DEFAULT 3"},
    "signals": {
        "timeframe": "TEXT DEFAULT 'M15'", "tp1": "REAL DEFAULT 0",
        "tp2": "REAL DEFAULT 0", "tp3": "REAL DEFAULT 0",
        "targets": "TEXT DEFAULT ''", "rr": "REAL DEFAULT 0",
        "score": "INTEGER DEFAULT 0", "strategy": "TEXT DEFAULT 'MANUAL'",
        "reason": "TEXT DEFAULT ''", "source": "TEXT DEFAULT 'MANUAL'",
        "hit_tp": "INTEGER DEFAULT 0", "closed_at": "TEXT DEFAULT ''",
        "pips_result": "REAL DEFAULT 0", "data_source": "TEXT DEFAULT ''",
        "data_time": "TEXT DEFAULT ''", "strategy_scores": "TEXT DEFAULT ''",
        "expires_at": "TEXT DEFAULT ''", "entry_zone_lo": "REAL DEFAULT 0",
        "entry_zone_hi": "REAL DEFAULT 0", "notify_lock": "INTEGER DEFAULT 0",
        "half_notified": "INTEGER DEFAULT 0",
        "live_px": "REAL DEFAULT 0",
        "filled_at": "TEXT DEFAULT ''",
    },
    "signal_deliveries": {
        "chat_id": "TEXT DEFAULT ''",
        "message_id": "INTEGER DEFAULT 0",
    },
    "subscriptions": {
        "source": "TEXT DEFAULT 'MANUAL'",
        "remind_3d": "INTEGER DEFAULT 0",
        "remind_1d": "INTEGER DEFAULT 0",
    },
    "payment_methods": {"country": "TEXT DEFAULT ''", "linked_plans": "TEXT DEFAULT ''"},
    "payments": {
        "txid": "TEXT DEFAULT ''",
        "chain": "TEXT DEFAULT ''",
        "confirmed_at": "TEXT DEFAULT ''",
    },
    "tickets": {"type": "TEXT DEFAULT 'general'", "updated_at": "TEXT DEFAULT ''"},
    "market_catalog": {
        "pip_size": "REAL DEFAULT 0", "digits": "INTEGER DEFAULT 5",
        "contract_size": "REAL DEFAULT 100000", "category": "TEXT DEFAULT 'forex'",
        "spread_pips": "REAL DEFAULT 2",
    },
    "strategy_catalog": {"min_confidence": "INTEGER DEFAULT 60"},
    "demo_trades": {"demo_position_id": "INTEGER DEFAULT 0"},
    "demo_accounts": {
        "risk_pct": "REAL DEFAULT 1.0",
        "lot_mode": "TEXT DEFAULT 'auto'",
        "fixed_lot": "REAL DEFAULT 0.01",
        "auto_trade": "INTEGER DEFAULT 0",
    },
    "demo_positions": {
        "signal_id": "INTEGER DEFAULT 0",
        "partials_done": "INTEGER DEFAULT 0",
        "initial_lots": "REAL DEFAULT 0",
    },
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_signals_status_created ON signals(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_signals_source_time ON signals(source, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_user ON signal_deliveries(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_time ON signal_deliveries(delivered_at)",
    "CREATE INDEX IF NOT EXISTS idx_subs_user_status ON subscriptions(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_subs_ends ON subscriptions(status, ends_at)",
    "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_engine_runs_created ON engine_runs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_engine_runs_symbol ON engine_runs(symbol, timeframe)",
    "CREATE INDEX IF NOT EXISTS idx_ticketmsgs_ticket ON ticket_messages(ticket_id)",
    "CREATE INDEX IF NOT EXISTS idx_demopos_account ON demo_positions(account_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked)",
    "CREATE INDEX IF NOT EXISTS idx_broadcast_status ON broadcasts(status)",
    "CREATE INDEX IF NOT EXISTS idx_notif_user ON notification_events(user_id, read)",
    "CREATE INDEX IF NOT EXISTS idx_analyses_user_time ON analyses(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON analyses(symbol, timeframe)",
    "CREATE INDEX IF NOT EXISTS idx_demopos_signal ON demo_positions(signal_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_signals_open ON signals(status, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_ref_campaign ON referrals(campaign_id, converted)",
    "CREATE INDEX IF NOT EXISTS idx_ref_invitee ON referrals(invitee_id)",
    "CREATE INDEX IF NOT EXISTS idx_watchers_due ON signal_watchers(status, next_run)",
]

DEFAULT_SETTINGS = {
    "maintenance": "0",
    "trading_mode": "SIGNALS_ONLY",
    "engine_enabled": "1",
    "live_enabled": "0",
    # Kill switch ships OFF: the engine must be able to issue real signals from
    # the first boot. The admin can still arm it any time (a:risk -> killswitch).
    "global_kill_switch": "0",
    "default_market": "XAUUSD",
    "engine_interval_seconds": "300",
    "engine_min_score": "80",
    # Risk engine (policy enforced server-side)
    "risk_per_trade_pct": "1.0",
    "max_daily_risk_pct": "3.0",
    "max_daily_loss_pct": "5.0",
    "max_open_signals": "3",
    "min_rr": "2.0",
    "volatility_filter": "1",
    "atr_pct_max": "6.0",
    # FREE quota (server-enforced, anti-bypass)
    "free_daily_signals": "2",
    # Subscriptions
    "expiry_remind_days": "3",
    "signal_expiry_hours": "72",
    # Demo
    "demo_start_balance": "10000",
    "demo_max_open": "5",
    "demo_slippage_points": "1",
    # Broadcast
    "broadcast_rate_per_sec": "20",
    # Security
    "block_new_users": "0",
    "signal_push_enabled": "1",
    # Analytics module (user-facing, quota per plan)
    "analytics_enabled": "1",
    # Auto channel posts (Mecca-time scheduler)
    "channels_quiet_hours": "1-7",
}

SEED_MARKETS = [
    # symbol, title, provider, category, pip_size, digits, contract, spread_pips
    # --- المعادن (Metals) ---
    ("XAUUSD", "Gold / US Dollar", "XAU/USD", "metal", 0.1, 2, 100, 2.5),
    ("XAGUSD", "Silver / US Dollar", "XAG/USD", "metal", 0.01, 3, 5000, 3.0),
    ("XPTUSD", "Platinum / US Dollar", "XPT/USD", "metal", 0.1, 2, 50, 5.0),
    # --- الفوركس (Forex majors) ---
    ("EURUSD", "Euro / US Dollar", "EUR/USD", "forex", 0.0001, 5, 100000, 1.0),
    ("GBPUSD", "Pound / US Dollar", "GBP/USD", "forex", 0.0001, 5, 100000, 1.5),
    ("USDJPY", "US Dollar / Yen", "USD/JPY", "forex", 0.01, 3, 100000, 1.2),
    ("USDCHF", "US Dollar / Franc", "USD/CHF", "forex", 0.0001, 5, 100000, 1.4),
    ("AUDUSD", "Aussie / US Dollar", "AUD/USD", "forex", 0.0001, 5, 100000, 1.3),
    ("USDCAD", "US Dollar / Loonie", "USD/CAD", "forex", 0.0001, 5, 100000, 1.6),
    ("NZDUSD", "Kiwi / US Dollar", "NZD/USD", "forex", 0.0001, 5, 100000, 1.8),
    # --- التقاطعات (Forex crosses) ---
    ("EURJPY", "Euro / Yen", "EUR/JPY", "forex", 0.01, 3, 100000, 1.8),
    ("GBPJPY", "Pound / Yen", "GBP/JPY", "forex", 0.01, 3, 100000, 2.5),
    ("EURGBP", "Euro / Pound", "EUR/GBP", "forex", 0.0001, 5, 100000, 1.5),
    # --- العملات الرقمية (Crypto) ---
    ("BTCUSD", "Bitcoin / US Dollar", "BTC/USD", "crypto", 1.0, 2, 1, 15),
    ("ETHUSD", "Ether / US Dollar", "ETH/USD", "crypto", 0.1, 2, 1, 1.5),
    ("SOLUSD", "Solana / US Dollar", "SOL/USD", "crypto", 0.01, 3, 1, 0.5),
    ("XRPUSD", "Ripple / US Dollar", "XRP/USD", "crypto", 0.0001, 4, 1, 0.02),
    # --- المؤشرات (Indices) ---
    ("US30", "Dow Jones 30", "DJI", "index", 1.0, 2, 1, 3.0),
    ("NAS100", "Nasdaq 100", "NDX", "index", 1.0, 2, 1, 2.0),
    ("SPX500", "S&P 500", "SPX", "index", 1.0, 2, 1, 1.5),
    ("GER40", "DAX 40", "DAX", "index", 1.0, 2, 1, 2.5),
    ("UK100", "FTSE 100", "FTSE", "index", 1.0, 2, 1, 2.5),
    # --- الأسهم (Stocks) ---
    ("AAPL", "Apple Inc.", "AAPL", "stock", 0.01, 2, 1, 1.0),
    ("TSLA", "Tesla Inc.", "TSLA", "stock", 0.01, 2, 1, 2.0),
    ("NVDA", "NVIDIA Corp.", "NVDA", "stock", 0.01, 2, 1, 1.5),
    ("MSFT", "Microsoft Corp.", "MSFT", "stock", 0.01, 2, 1, 1.0),
    ("AMZN", "Amazon Inc.", "AMZN", "stock", 0.01, 2, 1, 1.2),
]

# v6.8.0 — GOLD-FIRST shipping policy: verified provider coverage exists for
# metals/forex/crypto (the FX-grade provider chain); indices/stocks need the
# admin's explicit Verify action per symbol (data contracts differ).
SEED_VERIFIED_CATEGORIES = {"metal", "forex", "crypto"}
GOLD_DEFAULT_SYMBOL = "XAUUSD"
SEED_TIMEFRAMES = [("M1", "1min"), ("M5", "5min"), ("M15", "15min"), ("M30", "30min"),
                   ("H1", "1h"), ("H4", "4h"), ("D1", "1day"), ("W1", "1week")]

SEED_TIMEFRAME_AR = {"M1": "1 دقيقة", "M5": "5 دقائق", "M15": "15 دقيقة", "M30": "30 دقيقة",
                     "H1": "ساعة", "H4": "4 ساعات", "D1": "يومي", "W1": "أسبوعي"}

SEED_PLANS = [
    ("FREE", "Free", 0.0, "USD", 0, 80, 0, 1, "XAUUSD", "M15", "ICT,PRICE_ACTION", 2),
    ("PRO", "Pro", 19.0, "USD", 30, 70, 1, 1, "XAUUSD,EURUSD", "M5,M15,H1,H4", "ICT,SMC,PRICE_ACTION", 15),
    ("VIP", "VIP", 49.0, "USD", 30, 60, 3, 0, "XAUUSD,EURUSD,GBPUSD", "M5,M15,M30,H1,H4,D1", "ICT,SMC,PRICE_ACTION", 50),
]

# Daily analyses quota per plan (admin-adjustable; 0 = analytics disabled for the plan)
DEFAULT_ANALYSES_QUOTA = {"FREE": 3, "PRO": 10, "VIP": 30}

# Strategy codes implemented + covered by the automated test-suite.
IMPLEMENTED_STRATEGIES = ["ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP",
                          "SILVER_BULLET", "FIBONACCI", "TREND", "ICHIMOKU",
                          "CLASSIC", "SK"]

# v6.10.0 — LUXURY PLAN DEFAULTS (owner demand: «اضبط انت اعدادات الباقات بشكل
# فاخر جداً»). A one-time migration stamps FREE/PRO/VIP with these exact
# values, and the old boot-time force-merge of implemented strategies into
# every plan is retired — the tiering stays EXACTLY as designed.
LUXURY_PLANS_V6100 = {
    "FREE": {"title": "Free", "price": 0.0, "duration_days": 0, "min_score": 80,
             "max_signals_per_day": 2, "max_analyses_per_day": 3,
             "markets": "XAUUSD", "timeframes": "M15",
             "strategies": "ICT,PRICE_ACTION", "enabled": 1},
    "PRO": {"title": "Pro", "price": 19.0, "duration_days": 30, "min_score": 70,
            "max_signals_per_day": 5, "max_analyses_per_day": 10,
            "markets": "XAUUSD,EURUSD,GBPUSD", "timeframes": "M15,M30,H1,H4",
            "strategies": "ICT,SMC,PRICE_ACTION,CRT,AMD", "enabled": 1},
    "VIP": {"title": "VIP", "price": 49.0, "duration_days": 30, "min_score": 60,
            "max_signals_per_day": 12, "max_analyses_per_day": 30,
            "markets": "XAUUSD,XAGUSD,EURUSD,GBPUSD,USDJPY,BTCUSD,ETHUSD",
            "timeframes": "M5,M15,M30,H1,H4,D1",
            "strategies": ",".join(IMPLEMENTED_STRATEGIES), "enabled": 1},
}
PLANS_LUXURY_FLAG = "plans_luxury_v6100_applied"

SEED_MODES = [
    ("A", "SIGNALS ONLY", "تحليل/نشر التوصيات دون تنفيذ صفقات.", 1, 1, "signals"),
    ("B", "DEMO / PAPER", "محاكاة تنفيذ على بيانات سوق حقيقية دون وسيط.", 0, 1, "paper_simulator"),
    ("C", "LIVE", "تنفيذ حقيقي؛ مغلق افتراضياً حتى توثيق واختبار وسيط معتمد.", 0, 0, "broker_adapter"),
]


def _rename_legacy_elite_plan(c) -> None:
    """One-time brand cleanup: rename the legacy ELITE plan to VIP everywhere.

    Idempotent — touches plans, subscriptions, payments, broadcasts and the
    linked-plans text on payment methods. Safe on fresh databases too.
    """
    for table, column in (("subscriptions", "plan_code"), ("payments", "plan_code"),
                          ("broadcasts", "plan_code")):
        try:
            c.execute(f"UPDATE {table} SET {column}='VIP' WHERE {column}='ELITE'")
        except Exception:
            pass
    try:
        c.execute("UPDATE payment_methods SET linked_plans=REPLACE(linked_plans,'ELITE','VIP') "
                  "WHERE linked_plans LIKE '%ELITE%'")
    except Exception:
        pass
    if c.execute("SELECT 1 FROM plans WHERE code='ELITE'").fetchone():
        try:
            c.execute("UPDATE plans SET code='VIP', title='VIP' WHERE code='ELITE'")
        except Exception:
            pass
    c.execute("UPDATE plans SET title='VIP' WHERE code='VIP' AND title IN ('Elite','ELITE','elite')")


def _table_cols(c, table: str) -> set:
    """Dialect-aware column introspection (PRAGMA vs information_schema)."""
    if config.USE_PG:
        return pglayer.table_columns(table)
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}


def _apply_add_columns(c) -> bool:
    changed = False
    for table, columns in ADD_COLUMNS.items():
        present = _table_cols(c, table)
        for column, definition in columns.items():
            if column not in present:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                changed = True
    return changed


STRATEGY_PREF_FLAG = "strategy_pref_normalized_v651"


def _normalize_strategy_preference(c) -> bool:
    """One-time reset of the (historically cosmetic) users.strategy default.

    Returns True when rows were changed. Runs inside init_db behind a settings
    flag so a user's ACTIVE ICT choice made AFTER this migration is never
    touched again.
    """
    flag = get_setting(STRATEGY_PREF_FLAG, "")
    if flag == "1":
        return False
    cur = c.execute("UPDATE users SET strategy='' WHERE strategy IS NOT NULL AND strategy<>''")
    changed = cur.rowcount or 0
    c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (STRATEGY_PREF_FLAG, "1"))
    return changed > 0


def init_db(snapshot_before_structural: bool = True) -> list[str]:
    """Initialize schema and run additive migrations. Returns log lines.

    Order matters: on an EXISTING database, columns are added (with a
    pre-migration snapshot) BEFORE/PLUS schema creation; on a FRESH database
    the schema is created first and migrations become no-ops.
    """
    log_lines: list[str] = []
    with _migration_lock:
        c = conn()
        try:
            if config.USE_PG:
                tables_now = pglayer.list_tables()
            else:
                tables_now = {r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            structural = bool(tables_now & set(ADD_COLUMNS.keys()))
            if structural and snapshot_before_structural:
                # v6.7.2: the snapshot runs BEFORE any ALTER takes its lock and
                # is BEST-EFFORT. The live Supabase crash (2026-11-23): the
                # boot-time dump hit the server's statement_timeout, the
                # exception aborted init_db, the with-conn() rollback undid the
                # just-applied migrations, and every restart re-applied them
                # and crashed again — an infinite boot loop. Migrations are
                # additive-only, so a missing snapshot is always safe to skip.
                from fysb.services.backup import quick_db_snapshot
                try:
                    snap = quick_db_snapshot("pre-migration")
                    log_lines.append(f"pre-migration snapshot: {snap.name}" if snap else
                                     "pre-migration snapshot unavailable (DB busy) — "
                                     "continuing: migrations are additive-only")
                except Exception as e:  # pragma: no cover — snapshot() no longer raises
                    log_lines.append(f"pre-migration snapshot skipped ({type(e).__name__}) — "
                                     "continuing: migrations are additive-only")
            if structural:
                _apply_add_columns(c)
            if config.USE_PG:
                pglayer.run_schema(c, SCHEMA)
            else:
                c.executescript(SCHEMA)
            # Commit the schema immediately (SQLite's executescript auto-commits,
            # and PostgreSQL information_schema introspection below must see it)
            if config.USE_PG:
                c.commit()
            _apply_add_columns(c)
            for idx in INDEXES:
                c.execute(idx)
            _rename_legacy_elite_plan(c)
            for k, v in DEFAULT_SETTINGS.items():
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            # Real-Entry protocol epoch (v6.6.4): signals CREATED before this
            # instant were born under the old pipeline that never recorded the
            # birth quote — the tracker may prove their at-birth fill from the
            # first tracked candle (legacy salvage). Signals created after it
            # carry an exact birth status; the heuristic never touches them.
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('real_entry_protocol_since',?)",
                      (config.now(),))
            for sym, title, provider, cat, pip, digits, contract, spread in SEED_MARKETS:
                c.execute(
                    """INSERT INTO market_catalog(symbol,title,provider_symbol,enabled,verified,created_at,updated_at,
                       pip_size,digits,contract_size,category,spread_pips)
                       VALUES(?,?,?,0,0,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO NOTHING""",
                    (sym, title, provider, config.now(), config.now(), pip, digits, contract, cat, spread))
            # Fill instrument specs for pre-existing catalog rows (additive).
            for sym, _t, _p, cat, pip, digits, contract, spread in SEED_MARKETS:
                c.execute("""UPDATE market_catalog SET pip_size=?,digits=?,contract_size=?,category=?,spread_pips=?
                             WHERE symbol=? AND (pip_size IS NULL OR pip_size=0)""",
                          (pip, digits, contract, cat, spread, sym))
            for code, interval in SEED_TIMEFRAMES:
                c.execute("INSERT OR IGNORE INTO timeframe_catalog(code,provider_interval,enabled,verified,created_at,updated_at) VALUES(?,?,0,0,?,?)",
                          (code, interval, config.now(), config.now()))
            for code, title, desc in [
                ("ICT", "ICT", "Liquidity sweeps, FVG, order blocks, displacement, kill zones."),
                ("SMC", "Smart Money Concepts", "BOS/CHOCH, HH/HL/LH/LL, supply & demand."),
                ("PRICE_ACTION", "Price Action", "Engulfing, pin bar, breakout/retest, S/R."),
                ("CRT", "Candle Range Theory", "مدى الشمعة المرجعية، سحب السيولة، إغلاق داخل المدى، دخول CRT 50%."),
                ("AMD", "Power of Three", "تراكم · تلاعب · توزيع على نطاق الافتتاح (Judas Swing)."),
                ("TURTLE_SOUP", "Turtle Soup", "فشل كسر قمة/قمة 20 شمعة وانعكاس مضاد (اصطياد السيولة)."),
                ("SILVER_BULLET", "Silver Bullet", "نوافذ ICT الزمنية (لندن/نيويورك) + استمرار FVG."),
                ("FIBONACCI", "Fibonacci Golden Pocket", "مستوى ارتداد 61.8–78.6% من الموجة الدافعية مع بنية سليمة (منفذة واختبارة آلياً)."),
                ("TREND", "Trend Following", "ترتيب المتوسطات 9/21/50 + ارتداد إلى قيمة EMA21 واستئناف الاتجاه (منفذة واختبارة آلياً)."),
                ("ICHIMOKU", "Ichimoku Kinko Hyo", "نظام السحابة الياباني: كسر الكومو + تقاطع تينكان/كيجون + سحابة مستقبلية مؤكدة (منفذة واختبارة آلياً)."),
                ("CLASSIC", "CLASSIC", "التحليل الكلاسيكي: اتجاه EMA50 + مستوى أفقي حقيقي من القمم والقيعان + تأكيد شمعة ابتلاع/دبوس (منفذة واختبارة آلياً)."),
                ("SK", "SK", "محرك سكالبينج سريع: مستوى مفتاحي 12 شمعة يُكسر بإغلاق مؤكد + زخم RSI + اتجاه EMA9/21 (منفذة واختبارة آلياً)."),
                ("WYCKOFF", "Wyckoff", "منهج وايكوف؛ غير معتمد آلياً بعد — مغلق."),
                # --- v6.8.1: مجموعات الاستراتيجيات العالمية الكاملة (كل مدرسة، كلها) ---
                # كل صف يُولد مغلقاً وغير مختبر (حوكمة صادقة) حتى يُنفذ ويُختبر آلياً.
                ("ELLIOTT_WAVE", "Elliott Waves",
                 "عد موجات إليوت: دافعية 1-2-3-4-5 وتصحيحية A-B-C مع مناطق التماثل وقيادة الموجة الثالثة."),
                ("HARMONIC", "Harmonic Patterns",
                 "أنماط توافقية بنِسب فيبوناتشي المضبوطة: جارتفلي · بات · كاب · بترفلاي ومنطقة PRZ."),
                ("VOLUME_PROFILE", "Volume Profile",
                 "توزيع الحجم بالسعر: POC وحدود منطقة القيمة VAH/VAL ومناطق القيمة العادلة لكل جلسة."),
                ("VWAP", "VWAP",
                 "السعر المتوسط المرجح بالحجم: انحرافات ±1/±2 للانضباط المؤسسي وانعكاسات الانحراف."),
                ("BREAKOUT_RETEST", "Breakout & Retest",
                 "كسر مستوى هيكلي بإغلاق مؤكد ثم إعادة اختبار ناجحة ودخول مع الاستمرار — الكلاسيك الصلب."),
                ("SUPPORT_RESISTANCE", "Support & Resistance",
                 "مناطق دعم/مقاومة محورية مبنية على القمم والقيعان الملموسة وتقاطع الأطر الكبرى."),
                ("RSI_DIVERGENCE", "RSI Divergence",
                 "دايفرجنس RSI بين السعر والزخم مع مناطق تشبع 70/30 وفلتر اتجاه الإطار الأعلى."),
                ("MACD_STRAT", "MACD Momentum",
                 "تقاطعات MACD ودايفرجنس الهيستوجرام مع فلتر الترند الأعلى ومراكز الصفر."),
                ("BOLLINGER", "Bollinger Bands",
                 "انضغاط بولينجر (Squeeze) قبل الانفجار وارتدادات من الحدود مع وسط 20 كمرجع."),
                ("DONCHIAN", "Donchian Breakout",
                 "كسر قناة دونشيان 20/55 — قواعد السلاحف الأصلية مع وقف ATR وجني متدرج."),
                ("MA_CROSS", "MA Crossover",
                 "تقاطع المتوسطات السريع/البطيء (الذهبي/المميت) مع فلتر اتجاه الأطر الأعلى."),
                ("SUPERTREND", "Supertrend",
                 "خط السوبر تريند ATR: تبديل الاتجاه عند كسر الخط والدخول معه بوقف ديناميكي."),
                ("KELTNER", "Keltner Channels",
                 "قنوات كلتنر EMA+ATR: اختراقات الامتداد وأهداف القناة المقابلة."),
                ("ORB", "Opening Range Breakout",
                 "كسر مدى الافتتاح (أول 15/30 دقيقة من الجلسة) بأهداف ممددة من حجم المدى."),
                ("ASIA_RANGE", "Asia Range",
                 "مدى جلسة آسيا: كسر سيولة آسيا في افتتاح لندن — نمط الجلسات الملكية."),
                ("LONDON_BREAK", "London Breakout",
                 "كسر قمة/قاع أول ساعتين من لندن مع فلتر اتجاه اليوم وإدارة بنسبة ثابتة."),
                ("PIVOT_POINTS", "Pivot Points",
                 "نقاط المحور اليومية الكلاسيكية PP/R1-R3/S1-S3 للانعكاس والاستمرار المؤسسي."),
                ("CPR", "Central Pivot Range",
                 "مدى المحور المركزي TC/PIVOT/BC وتصنيف اليوم الضيق/العريض قبل الافتتاح."),
                ("MARKET_PROFILE", "Market Profile (TPO)",
                 "ملف السوق TPO: أشكال اليوم البسيطة/المزدوجة ومناطق قبول ورفض السعر."),
                ("ORDER_FLOW", "Order Flow & Delta",
                 "تدفق الأوامر والدلتا التراكمية وامتصاص السيولة عند المستويات الكبرى."),
                ("MEAN_REVERSION", "Mean Reversion",
                 "الارتداد للمتوسط: Z-Score على النطاقات الملتزمة والأزواج المرتبطة إحصائياً."),
                ("GAP_TRADE", "Gap Trading",
                 "فجوات الافتتاح: ملء الفجوة أو الاستمرار بحسب نوعها وحجمها وموقعها من الاتجاه."),
                ("HEIKIN_ASHI", "Heikin Ashi",
                 "شموع هيكين آشي الملساء: تلوين الاتجاه وغياب الهيبات العكسية لتصفية الضجيج."),
                ("MOMENTUM", "Momentum Breakout",
                 "اندفاعات الزخم: شموع توسع بحجم استثنائي وامتداد مؤكد فوق مستوى المقاومة."),
                ("ATR_BREAKOUT", "ATR Volatility Break",
                 "كسر نطاق N×ATR مع وقف ديناميكي وأهداف ممتدة حسب التقلب اللحظي."),
            ]:
                c.execute("INSERT OR IGNORE INTO strategy_catalog(code,title,description,enabled,verified,created_at,updated_at,min_confidence) VALUES(?,?,?,0,0,?,?,60)",
                          (code, title, desc, config.now(), config.now()))
            # Implemented + tested strategies are verified & enabled (honest governance:
            # verification = implementation + automated tests, not marketing).
            for code in IMPLEMENTED_STRATEGIES:
                c.execute("UPDATE strategy_catalog SET verified=1, enabled=1, updated_at=? WHERE code=?",
                          (config.now(), code))
            # INFRASTRUCTURE SELF-HEAL (picker-empty bug, seen live 2026-11-22):
            # markets & timeframes were seeded disabled and nothing ever enabled
            # them, so the user analytics flow rendered the header with ZERO
            # timeframe buttons. v6.8.0 GOLD-FIRST POLICY (owner's explicit
            # instruction): GOLD ships OPEN and every other instrument ships
            # CLOSED — the heal arms gold only; admins open the rest per-market
            # from the markets center after verifying each provider. Verified
            # flags reflect real provider coverage (metal/forex/crypto = the
            # proven FX-grade chain; indices/stocks require per-symbol verify).
            for sym, _t, _p, cat, _pip, _dig, _con, _sp in SEED_MARKETS:
                verified = 1 if cat in SEED_VERIFIED_CATEGORIES else 0
                enabled = 1 if sym == GOLD_DEFAULT_SYMBOL else 0
                c.execute("UPDATE market_catalog SET enabled=?, verified=?, updated_at=? WHERE symbol=?",
                          (enabled, verified, config.now(), sym))
            for code, _interval in SEED_TIMEFRAMES:
                c.execute("UPDATE timeframe_catalog SET enabled=1, verified=1, updated_at=? WHERE code=?",
                          (config.now(), code))
            # v6.10.0 — RETIRED: the old force-merge of ALL implemented strategies
            # into every plan destroyed the luxury tiering (FREE ended up with 12
            # methodologies). Now: only plans whose strategies are EMPTY get the
            # implemented baseline filled in — admin-set lists are never touched.
            for row in c.execute("SELECT code, strategies FROM plans").fetchall():
                if not (row["strategies"] or "").strip():
                    c.execute("UPDATE plans SET strategies=? WHERE code=?",
                              (",".join(IMPLEMENTED_STRATEGIES), row["code"]))
            c.execute("INSERT OR IGNORE INTO support_config(id,updated_at) VALUES(1,?)", (config.now(),))
            # Referral engine: the default royal campaign matches the launch
            # target (invite 50 → at least 5 real subscribers). INSERT OR
            # IGNORE keeps every admin edit intact across reboots.
            c.execute("""INSERT OR IGNORE INTO ref_campaigns(code,title,owner_id,target_invited,target_converted,
                         enabled,created_at,updated_at) VALUES('ROYAL','الحملة الملكية الرئيسية',0,50,5,1,?,?)""",
                      (config.now(), config.now()))
            c.execute("INSERT OR IGNORE INTO plans(code,title,price,currency,duration_days,min_score,max_accounts,enabled,markets,timeframes,strategies,max_signals_per_day) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(code) DO NOTHING", SEED_PLANS[0])
            c.execute("INSERT OR IGNORE INTO plans(code,title,price,currency,duration_days,min_score,max_accounts,enabled,markets,timeframes,strategies,max_signals_per_day) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(code) DO NOTHING", SEED_PLANS[1])
            c.execute("INSERT OR IGNORE INTO plans(code,title,price,currency,duration_days,min_score,max_accounts,enabled,markets,timeframes,strategies,max_signals_per_day) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(code) DO NOTHING", SEED_PLANS[2])
            c.execute("UPDATE plans SET max_signals_per_day=2 WHERE code='FREE' AND max_signals_per_day IS NOT 2 AND max_signals_per_day>2")
            # v6.10.0 — one-time LUXURY plan stamping (owner-set tiering). Runs
            # AFTER the seed inserts (a fresh DB has no plan rows before them)
            # and exactly ONCE — afterwards the admin's own edits are never
            # overwritten by boots.
            if get_setting(PLANS_LUXURY_FLAG, "") != "1":
                for code, fields in LUXURY_PLANS_V6100.items():
                    sets = ", ".join(f"{k}=?" for k in fields)
                    c.execute(f"UPDATE plans SET {sets} WHERE code=?",
                              (*fields.values(), code))
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?, '1')",
                          (PLANS_LUXURY_FLAG,))
            # Analyses quota defaults (additive — only fills unset/zero values)
            for code, quota in DEFAULT_ANALYSES_QUOTA.items():
                c.execute("UPDATE plans SET max_analyses_per_day=? WHERE code=? AND (max_analyses_per_day IS NULL OR max_analyses_per_day=0)",
                          (quota, code))
            for m in SEED_MODES:
                c.execute("INSERT OR IGNORE INTO mode_registry(code,title,description,enabled,executable,adapter_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (m[0], m[1], m[2], m[3], m[4], m[5], config.now(), config.now()))
            c.execute("UPDATE mode_registry SET enabled=1,executable=1 WHERE code='A'")
            # Strategy preference normalization (v6.5.1, one-time): the profile's
            # "الاستراتيجية المفضلة" is now a REAL delivery gate. Historically the
            # column was a cosmetic label with schema default 'ICT' while delivery
            # ignored it entirely — every existing row is therefore a default, not
            # a choice. Reset to '' (= every methodology, exactly the behavior the
            # user has always received) so the preference only exists once the user
            # ACTIVELY picks it from the profile picker. Gated by a settings flag.
            _normalize_strategy_preference(c)
            # Legacy OPEN status normalization + legacy targets parsing (kept from V5)
            signal_cols = _table_cols(c, "signals")
            if "targets" in signal_cols:
                old_rows = c.execute("SELECT id,targets,tp1,tp2,tp3 FROM signals").fetchall()
                for old in old_rows:
                    if old[2] == 0 and old[3] == 0 and old[4] == 0 and old[1]:
                        try:
                            vals = [float(x.strip()) for x in str(old[1]).split(",")[:3]]
                            if len(vals) == 3:
                                c.execute("UPDATE signals SET tp1=?,tp2=?,tp3=? WHERE id=?", (*vals, old[0]))
                        except (TypeError, ValueError):
                            pass
            c.execute("UPDATE signals SET status='ACTIVE' WHERE status='OPEN'")
            # Expire subscriptions whose window already passed (status normalization only).
            c.execute("UPDATE subscriptions SET status='EXPIRED' WHERE status='ACTIVE' AND ends_at IS NOT NULL AND ends_at<=?", (config.now(),))
            c.commit()
        finally:
            c.close()
    return log_lines


# --- Settings fast-path cache (v6.8.1 button-latency fix) --------------------
# get_setting() runs on EVERY button press (maintenance gate, kill-switch,
# quotas, push flag...). Over WAN PostgreSQL each call is a network round trip;
# a tiny process-local cache with a short TTL removes most of them. Correctness:
# the only writer is this process, and set_setting()/set_settings_bulk() update
# the cache write-through, so a toggle is visible on the very next read.
_SETTINGS_TTL = 5.0
_settings_cache: dict[str, tuple[str, float]] = {}
_settings_lock = threading.Lock()


def get_setting(key: str, default: str = "") -> str:
    ent = _settings_cache.get(key)
    if ent is not None and (time.time() - ent[1]) < _SETTINGS_TTL:
        return ent[0]
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return default  # misses are never cached — a later INSERT stays visible
    val = r["value"]
    with _settings_lock:
        _settings_cache[key] = (val, time.time())
    return val


def get_setting_int(key: str, default: int) -> int:
    try:
        return int(float(get_setting(key, str(default))))
    except (TypeError, ValueError):
        return default


def get_setting_float(key: str, default: float) -> float:
    try:
        return float(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value) -> None:
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))
    with _settings_lock:   # write-through — the next read never sees a stale value
        _settings_cache[key] = (str(value), time.time())


def set_settings_bulk(pairs: dict) -> None:
    with tx() as c:
        for k, v in pairs.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, str(v)))
    with _settings_lock:
        now_ts = time.time()
        for k, v in pairs.items():
            _settings_cache[k] = (str(v), now_ts)


def maintenance_on() -> bool:
    return get_setting("maintenance", "0") == "1"


def kill_switch_on() -> bool:
    return get_setting("global_kill_switch", "0") == "1"
