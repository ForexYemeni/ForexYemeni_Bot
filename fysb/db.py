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
import json
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
_wal_ready_paths: set[str] = set()
_wal_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_start_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")


def conn():
    """Engine-appropriate connection (PG: pooled autocommit session; SQLite: fast WAL)."""
    if config.USE_PG:
        return pglayer.conn()
    db_path = str(config.DB_FILE)
    c = sqlite3.connect(db_path, timeout=30)
    c.row_factory = sqlite3.Row
    if db_path not in _wal_ready_paths:
        c.execute("PRAGMA journal_mode=WAL")
        with _wal_lock:
            _wal_ready_paths.add(db_path)
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
  notifications INTEGER DEFAULT 1, timeframe TEXT DEFAULT '',
  strategy TEXT DEFAULT '', market TEXT DEFAULT '',
  min_score INTEGER DEFAULT 0, language TEXT DEFAULT 'ar'
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
CREATE TABLE IF NOT EXISTS chart_channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
  title TEXT DEFAULT '', markets TEXT DEFAULT 'ALL',
  enabled INTEGER NOT NULL DEFAULT 1,
  send_on_signal INTEGER NOT NULL DEFAULT 1,
  send_on_tracking INTEGER NOT NULL DEFAULT 1,
  send_on_schedule INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS channel_partners(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER UNIQUE NOT NULL,
  title TEXT DEFAULT '',
  campaign_code TEXT UNIQUE NOT NULL,
  commission_pct REAL NOT NULL DEFAULT 25.0,
  post_mode TEXT NOT NULL DEFAULT 'teaser',
  sample_daily_limit INTEGER NOT NULL DEFAULT 2,
  perm_auto_signals INTEGER NOT NULL DEFAULT 1,
  perm_auto_analysis INTEGER NOT NULL DEFAULT 1,
  perm_manual_post INTEGER NOT NULL DEFAULT 1,
  perm_choose_markets INTEGER NOT NULL DEFAULT 1,
  perm_custom_branding INTEGER NOT NULL DEFAULT 1,
  perm_vip_gatekeeper INTEGER NOT NULL DEFAULT 1,
  max_channels INTEGER NOT NULL DEFAULT 2,
  manual_analysis_limit INTEGER NOT NULL DEFAULT 6,
  manual_signal_limit INTEGER NOT NULL DEFAULT 6,
  manual_usage_date TEXT DEFAULT '',
  manual_analysis_used INTEGER NOT NULL DEFAULT 0,
  manual_signal_used INTEGER NOT NULL DEFAULT 0,
  brand_label TEXT DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS partner_commissions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partner_id INTEGER NOT NULL,
  buyer_user_id INTEGER NOT NULL,
  plan_code TEXT NOT NULL,
  payment_id INTEGER DEFAULT 0,
  gross_amount REAL NOT NULL DEFAULT 0,
  currency TEXT DEFAULT 'USD',
  commission_pct REAL NOT NULL DEFAULT 25.0,
  commission_amount REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'EARNED',
  created_at TEXT NOT NULL,
  paid_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vip_channel_members(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  chat_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  invite_link TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  granted_at TEXT NOT NULL,
  removed_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS free_trial_reminders(
  user_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  PRIMARY KEY(user_id, stage)
);
CREATE TABLE IF NOT EXISTS user_risk_profiles(
  user_id INTEGER PRIMARY KEY,
  balance REAL NOT NULL DEFAULT 1000.0,
  risk_pct REAL NOT NULL DEFAULT 1.0,
  leverage INTEGER NOT NULL DEFAULT 100,
  max_daily_dd_pct REAL NOT NULL DEFAULT 3.0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  signal_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  action TEXT NOT NULL,
  lots REAL NOT NULL DEFAULT 0.01,
  risk_usd REAL NOT NULL DEFAULT 0.0,
  realized_pips REAL NOT NULL DEFAULT 0.0,
  realized_usd REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL DEFAULT 'OPEN',
  created_at TEXT NOT NULL,
  closed_at TEXT DEFAULT '',
  UNIQUE(user_id, signal_id)
);
CREATE TABLE IF NOT EXISTS academy_lessons(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  track TEXT NOT NULL DEFAULT 'FREE',
  sort_order INTEGER NOT NULL DEFAULT 1,
  summary TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  quiz_json TEXT NOT NULL DEFAULT '[]',
  media_type TEXT NOT NULL DEFAULT 'TEXT',
  media_file_id TEXT NOT NULL DEFAULT '',
  media_caption TEXT NOT NULL DEFAULT '',
  edited_by_admin INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS academy_enrollments(
  user_id INTEGER PRIMARY KEY,
  plan_type TEXT NOT NULL DEFAULT 'PAID',
  starts_at TEXT NOT NULL,
  ends_at TEXT DEFAULT '',
  source TEXT NOT NULL DEFAULT 'MANUAL',
  granted_by INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS academy_progress(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  lesson_code TEXT NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  quiz_score INTEGER NOT NULL DEFAULT 0,
  quiz_total INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT DEFAULT '',
  UNIQUE(user_id, lesson_code)
);
CREATE TABLE IF NOT EXISTS gift_codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  target_plan TEXT NOT NULL,
  duration_days INTEGER NOT NULL DEFAULT 30,
  max_uses INTEGER NOT NULL DEFAULT 1,
  uses_count INTEGER NOT NULL DEFAULT 0,
  note TEXT DEFAULT '',
  created_by INTEGER DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  expires_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gift_redemptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  target_plan TEXT NOT NULL,
  duration_days INTEGER NOT NULL DEFAULT 30,
  redeemed_at TEXT NOT NULL,
  UNIQUE(code_id, user_id)
);
CREATE TABLE IF NOT EXISTS affiliate_links(
  invitee_id INTEGER PRIMARY KEY,
  referrer_id INTEGER NOT NULL,
  converted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS affiliate_commissions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer_id INTEGER NOT NULL,
  referred_user_id INTEGER NOT NULL,
  payment_id INTEGER DEFAULT 0,
  amount_usd REAL NOT NULL DEFAULT 0.0,
  rate_pct REAL NOT NULL DEFAULT 15.0,
  commission_usd REAL NOT NULL DEFAULT 0.0,
  tier_name TEXT NOT NULL DEFAULT 'BRONZE',
  status TEXT NOT NULL DEFAULT 'CREDITED',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS affiliate_payouts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  amount_usd REAL NOT NULL DEFAULT 0.0,
  wallet_address TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  admin_note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  processed_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS user_exchange_keys(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  exchange TEXT NOT NULL,
  label TEXT DEFAULT '',
  testnet INTEGER NOT NULL DEFAULT 1,
  auto_execute INTEGER NOT NULL DEFAULT 0,
  risk_pct REAL NOT NULL DEFAULT 1.0,
  max_trade_usd REAL NOT NULL DEFAULT 100.0,
  creds_enc TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  last_check_at TEXT DEFAULT '',
  last_error TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, exchange)
);
CREATE TABLE IF NOT EXISTS exchange_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL DEFAULT 0.0,
  entry_price REAL NOT NULL DEFAULT 0.0,
  stop_price REAL NOT NULL DEFAULT 0.0,
  tp1_price REAL NOT NULL DEFAULT 0.0,
  testnet INTEGER NOT NULL DEFAULT 1,
  order_id TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'SUBMITTED',
  raw_response TEXT DEFAULT '',
  signal_id INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  discount_pct INTEGER NOT NULL DEFAULT 10,
  max_uses INTEGER NOT NULL DEFAULT 100,
  used_count INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_redemptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  payment_id INTEGER NOT NULL DEFAULT 0,
  plan_code TEXT NOT NULL DEFAULT '',
  discount_pct INTEGER NOT NULL DEFAULT 0,
  saved_amount REAL NOT NULL DEFAULT 0,
  redeemed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bridge_connections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  account_mode TEXT NOT NULL DEFAULT 'DEMO',
  label TEXT NOT NULL DEFAULT '',
  bridge_key TEXT NOT NULL DEFAULT '',
  creds_enc TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  admin_frozen INTEGER NOT NULL DEFAULT 0,
  risk_pct REAL NOT NULL DEFAULT 1.0,
  lot_mode TEXT NOT NULL DEFAULT 'auto',
  fixed_lot REAL NOT NULL DEFAULT 0.01,
  max_daily_dd_pct REAL NOT NULL DEFAULT 3.0,
  max_open_trades INTEGER NOT NULL DEFAULT 3,
  partial_tp_be INTEGER NOT NULL DEFAULT 1,
  markets TEXT NOT NULL DEFAULT 'ALL',
  symbol_suffix TEXT NOT NULL DEFAULT '',
  last_balance REAL NOT NULL DEFAULT 0.0,
  last_equity REAL NOT NULL DEFAULT 0.0,
  last_heartbeat TEXT NOT NULL DEFAULT '',
  last_status TEXT NOT NULL DEFAULT 'READY',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, platform, account_mode)
);
CREATE TABLE IF NOT EXISTS bridge_user_rules(
  user_id INTEGER PRIMARY KEY,
  override_mode TEXT NOT NULL DEFAULT 'AUTO',
  note TEXT NOT NULL DEFAULT '',
  updated_by INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bridge_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  connection_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  account_mode TEXT NOT NULL DEFAULT 'DEMO',
  signal_id INTEGER NOT NULL DEFAULT 0,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  action_type TEXT NOT NULL DEFAULT 'OPEN',
  lots REAL NOT NULL DEFAULT 0.01,
  entry_price REAL NOT NULL DEFAULT 0.0,
  stop_price REAL NOT NULL DEFAULT 0.0,
  tp1_price REAL NOT NULL DEFAULT 0.0,
  tp2_price REAL NOT NULL DEFAULT 0.0,
  tp3_price REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL DEFAULT 'QUEUED',
  external_order_id TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  pnl_usd REAL NOT NULL DEFAULT 0.0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
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
        "promo_code": "TEXT DEFAULT ''",
        "discount_pct": "INTEGER DEFAULT 0",
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
        "partial_tp_be": "INTEGER DEFAULT 1",
    },
    "demo_positions": {
        "signal_id": "INTEGER DEFAULT 0",
        "partials_done": "INTEGER DEFAULT 0",
        "initial_lots": "REAL DEFAULT 0",
    },
    "broadcast_channels": {
        "owner_partner_id": "INTEGER DEFAULT 0",
        "post_mode": "TEXT DEFAULT ''",
        "is_vip_gate": "INTEGER DEFAULT 0",
        "vip_min_plan": "TEXT DEFAULT ''",
        "daily_full_date": "TEXT DEFAULT ''",
        "daily_full_count": "INTEGER DEFAULT 0",
    },
    "channel_partners": {
        "manual_analysis_limit": "INTEGER NOT NULL DEFAULT 6",
        "manual_signal_limit": "INTEGER NOT NULL DEFAULT 6",
        "manual_usage_date": "TEXT DEFAULT ''",
        "manual_analysis_used": "INTEGER NOT NULL DEFAULT 0",
        "manual_signal_used": "INTEGER NOT NULL DEFAULT 0",
    },
    "academy_lessons": {
        "media_type": "TEXT NOT NULL DEFAULT 'TEXT'",
        "media_file_id": "TEXT NOT NULL DEFAULT ''",
        "media_caption": "TEXT NOT NULL DEFAULT ''",
        "edited_by_admin": "INTEGER NOT NULL DEFAULT 0",
    },
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_partners_user ON channel_partners(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_partcomm_partner ON partner_commissions(partner_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_vipmem_status ON vip_channel_members(status, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_vipmem_chan ON vip_channel_members(channel_id, user_id)",
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
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_txid_uniq ON payments(LOWER(txid)) WHERE txid IS NOT NULL AND txid != ''",
    "CREATE INDEX IF NOT EXISTS idx_acad_prog_user ON academy_progress(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_gift_redemp_user ON gift_redemptions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_aff_comm_ref ON affiliate_commissions(referrer_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_aff_links_ref ON affiliate_links(referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_aff_payout_status ON affiliate_payouts(status, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_exch_keys_user ON user_exchange_keys(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_exch_orders_user ON exchange_orders(user_id, created_at)",
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
    "min_signal_score": "72",
    # Risk engine (policy enforced server-side)
    "risk_per_trade_pct": "1.0",
    "max_daily_risk_pct": "3.0",
    "max_daily_loss_pct": "5.0",
    "max_open_per_symbol": "3",
    "max_open_signals": "12",
    "max_correlated_signals": "3",
    "min_rr": "2.0",
    "volatility_filter": "1",
    "atr_pct_max": "6.0",
    # FREE quota (server-enforced, anti-bypass)
    "free_daily_signals": "1",
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
    # UI navigation mode (v6.14.1): "0" = proven FRESH reply_text message on
    # every button tap (guarantees every page opens visibly right below the
    # tap); "1" = opt-in in-place edit_text (toggleable live in Admin Settings).
    "ui_edit_in_place": "0",
    # Admin personal signal-receive filter (v6.17.0): "" = receive ALL enabled
    # markets; comma-separated symbols (e.g. "XAUUSD") = Admin receives personal
    # signal/tracker alerts ONLY for those symbols while users & channels keep
    # receiving every enabled market allowed by their plan/channel filters.
    "admin_signal_markets": "",
    # v6.24.0 Multi-timeframe auto scanner (comma-separated enabled TFs, e.g. M15,H1)
    "engine_scan_timeframes": "M15,H1",
    # v6.25.0 Live Economic Calendar & News Guard (free keyless feed)
    "news_guard_enabled": "1",
    "news_guard_minutes": "30",
    "news_auto_broadcast": "0",
    "news_broadcast_users": "1",
    "news_broadcast_partners": "1",
    "news_broadcast_channels": "1",
    "news_lead_minutes": "30",
    # v6.26.0 Local zero-cost institutional charts (Pillow) + v7.0.2 scheduler
    "charts_enabled": "1",
    "chart_schedule_minutes": "0",
    "chart_schedule_channels": "0",
    # v6.27.0 Independent Trading Academy + Tiered Affiliates
    "academy_enabled": "1",
    "academy_price_usd": "49",
    "academy_duration_days": "365",
    "affiliate_min_payout_usd": "20",
    # v6.28.0 Free Crypto Exchange API Integration (Binance/Bybit/OKX)
    "crypto_api_enabled": "1",
    # v7.2.0 / v7.2.2 Zero-Config Bot-Hosting Auto-HTTPS Tunnel + Ngrok-443
    "webapp_auto_tunnel": "1",
    "ngrok_authtoken": "",
    "webapp_custom_url": "",
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

ROYAL_MARKETS = (
    "XAUUSD,XAGUSD,XPTUSD,"
    "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,"
    "EURJPY,GBPJPY,EURGBP,"
    "BTCUSD,ETHUSD,SOLUSD,XRPUSD,"
    "US30,NAS100,SPX500"
)

SEED_PLANS = [
    ("FREE", "الباقة المجانية — FREE", 0.0, "USD", 7, 80, 0, 1,
     "XAUUSD", "M15,H1", "CLASSIC,TREND", 1),
    ("PRO", "الباقة الاحترافية — PRO", 19.0, "USD", 30, 72, 1, 1,
     "XAUUSD,EURUSD,GBPUSD,USDJPY", "M15,M30,H1,H4",
     "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU", 4),
    ("VIP", "باقة كبار الشخصيات — VIP", 39.0, "USD", 30, 65, 2, 1,
     "XAUUSD,XAGUSD,EURUSD,GBPUSD,USDJPY,US30,NAS100,BTCUSD",
     "M5,M15,M30,H1,H4,D1",
     "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU,SMC,ICT,SK,WYCKOFF", 10),
    ("ROYAL", "الباقة الملكية — ROYAL ELITE", 79.0, "USD", 30, 60, 5, 1,
     ROYAL_MARKETS, "M1,M5,M15,M30,H1,H4,D1",
     "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU,SMC,ICT,SK,WYCKOFF,CRT,AMD,SILVER_BULLET,TURTLE_SOUP", 25),
]

# Daily analyses quota per plan (admin-adjustable; 0 = analytics disabled for the plan)
DEFAULT_ANALYSES_QUOTA = {"FREE": 1, "PRO": 4, "VIP": 10, "ROYAL": 25}

# Strategy codes implemented + covered by the automated test-suite.
IMPLEMENTED_STRATEGIES = ["ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP",
                          "SILVER_BULLET", "FIBONACCI", "TREND", "ICHIMOKU",
                          "CLASSIC", "SK", "WYCKOFF"]

# v6.22.0 — 4-TIER ROYAL PLAN ARCHITECTURE (FREE trial + PRO + VIP + ROYAL ELITE).
# One-time versioned migration stamps all 4 plans, enables all plan markets and
# all 13 implemented strategies, and sets FREE's configurable trial duration (7d).
# Subsequent boots NEVER overwrite Admin's own customizations.
PLANS_4TIER_V6220 = {
    "FREE": {
        "title": "الباقة المجانية — FREE", "price": 0.0, "duration_days": 7, "min_score": 80,
        "max_signals_per_day": 1, "max_analyses_per_day": 1,
        "markets": "XAUUSD", "timeframes": "M15,H1",
        "strategies": "CLASSIC,TREND", "enabled": 1,
    },
    "PRO": {
        "title": "الباقة الاحترافية — PRO", "price": 19.0, "duration_days": 30, "min_score": 72,
        "max_signals_per_day": 4, "max_analyses_per_day": 4,
        "markets": "XAUUSD,EURUSD,GBPUSD,USDJPY", "timeframes": "M15,M30,H1,H4",
        "strategies": "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU", "enabled": 1,
    },
    "VIP": {
        "title": "باقة كبار الشخصيات — VIP", "price": 39.0, "duration_days": 30, "min_score": 65,
        "max_signals_per_day": 10, "max_analyses_per_day": 10,
        "markets": "XAUUSD,XAGUSD,EURUSD,GBPUSD,USDJPY,US30,NAS100,BTCUSD",
        "timeframes": "M5,M15,M30,H1,H4,D1",
        "strategies": "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU,SMC,ICT,SK,WYCKOFF", "enabled": 1,
    },
    "ROYAL": {
        "title": "الباقة الملكية — ROYAL ELITE", "price": 79.0, "duration_days": 30, "min_score": 60,
        "max_signals_per_day": 25, "max_analyses_per_day": 25,
        "markets": ROYAL_MARKETS, "timeframes": "M1,M5,M15,M30,H1,H4,D1",
        "strategies": "CLASSIC,TREND,PRICE_ACTION,FIBONACCI,ICHIMOKU,SMC,ICT,SK,WYCKOFF,CRT,AMD,SILVER_BULLET,TURTLE_SOUP",
        "enabled": 1,
    },
}
# Legacy alias kept for compatibility with older imports
LUXURY_PLANS_V6100 = PLANS_4TIER_V6220
PLANS_LUXURY_FLAG = "plans_luxury_v6100_applied"
PLANS_4TIER_FLAG = "plans_4tier_v6220_applied"

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


def _clear_all_caches() -> None:
    """Reset all process-local TTL caches (called on init_db / DB switches)."""
    global _settings_bulk_at
    with _settings_lock:
        _settings_cache.clear()
        _settings_bulk_at = 0.0
    with _wal_lock:
        _wal_ready_paths.clear()
    try:
        from fysb.services import subscriptions as _subs
        _subs.invalidate_plans_cache()
    except Exception:
        pass
    try:
        from fysb.services import strategy_stats as _ss
        _ss.invalidate_leaderboard_cache()
    except Exception:
        pass


def init_db(snapshot_before_structural: bool = True) -> list[str]:
    """Initialize schema and run additive migrations. Returns log lines.

    Order matters: on an EXISTING database, columns are added (with a
    pre-migration snapshot) BEFORE/PLUS schema creation; on a FRESH database
    the schema is created first and migrations become no-ops.
    """
    log_lines: list[str] = []
    with _migration_lock:
        _clear_all_caches()
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
                try:
                    c.execute(idx)
                except Exception as idx_err:
                    log_lines.append(f"index warning: {idx_err}")
            _rename_legacy_elite_plan(c)
            for k, v in DEFAULT_SETTINGS.items():
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            # v6.14.1 one-time hotfix: databases upgraded through v6.13.0/v6.14.0
            # got ui_edit_in_place='1' which hid pages on scrolled chats. Reset
            # once to '0' (proven fresh-page delivery); admin can still opt in via Settings.
            if get_setting("ui_render_reset_v6141", "") != "1":
                c.execute(
                    "INSERT INTO settings(key,value) VALUES('ui_edit_in_place','0') "
                    "ON CONFLICT(key) DO UPDATE SET value='0'"
                )
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('ui_render_reset_v6141','1')")
                with _settings_lock:
                    _settings_cache["ui_edit_in_place"] = ("0", time.time())
                    _settings_cache["ui_render_reset_v6141"] = ("1", time.time())
            # v6.18.0 one-time multi-market risk upgrade: databases still at the
            # old single-market default max_open_signals='3' are smoothly raised
            # to '12' paired with max_open_per_symbol='2' (custom admin values
            # are preserved untouched).
            if get_setting("risk_multimarket_v6180", "") != "1":
                r_cur = c.execute("SELECT value FROM settings WHERE key='max_open_signals'").fetchone()
                cur_max = (r_cur["value"] if r_cur else "").strip()
                if cur_max in ("", "3", "3.0"):
                    c.execute(
                        "INSERT INTO settings(key,value) VALUES('max_open_signals','12') "
                        "ON CONFLICT(key) DO UPDATE SET value='12'"
                    )
                    with _settings_lock:
                        _settings_cache["max_open_signals"] = ("12", time.time())
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('max_open_per_symbol','3')")
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('risk_multimarket_v6180','1')")
                with _settings_lock:
                    _settings_cache["risk_multimarket_v6180"] = ("1", time.time())
            # Real-Entry protocol epoch (v6.6.4): signals CREATED before this
            # instant were born under the old pipeline that never recorded the
            # birth quote — the tracker may prove their at-birth fill from the
            # first tracked candle (legacy salvage). Signals created after it
            # carry an exact birth status; the heuristic never touches them.
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('real_entry_protocol_since',?)",
                      (config.now(),))
            # v6.12.0: Seed markets/timeframes/strategies WITHOUT wiping admin
            # enabled toggles across restarts!
            # Fresh rows get the GOLD-FIRST defaults (XAUUSD enabled=1, others 0;
            # metal/forex/crypto verified=1); existing rows keep their admin state.
            for sym, title, provider, cat, pip, digits, contract, spread in SEED_MARKETS:
                init_verified = 1 if cat in SEED_VERIFIED_CATEGORIES else 0
                init_enabled = 1 if sym == GOLD_DEFAULT_SYMBOL else 0
                c.execute(
                    """INSERT INTO market_catalog(symbol,title,provider_symbol,enabled,verified,created_at,updated_at,
                       pip_size,digits,contract_size,category,spread_pips)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO NOTHING""",
                    (sym, title, provider, init_enabled, init_verified, config.now(), config.now(),
                     pip, digits, contract, cat, spread))
            # Fill instrument specs & verified coverage for pre-existing catalog rows (never clobbers enabled).
            for sym, _t, _p, cat, pip, digits, contract, spread in SEED_MARKETS:
                c.execute("""UPDATE market_catalog SET pip_size=?,digits=?,contract_size=?,category=?,spread_pips=?
                             WHERE symbol=? AND (pip_size IS NULL OR pip_size=0)""",
                          (pip, digits, contract, cat, spread, sym))
                if cat in SEED_VERIFIED_CATEGORIES:
                    c.execute("UPDATE market_catalog SET verified=1, updated_at=? WHERE symbol=? AND verified=0",
                              (config.now(), sym))
            # Self-heal only when literally ZERO markets are enabled (never overrides active choices).
            if (c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1").fetchone()["n"] or 0) == 0:
                c.execute("UPDATE market_catalog SET enabled=1, verified=1, updated_at=? WHERE symbol=?",
                          (config.now(), GOLD_DEFAULT_SYMBOL))

            for code, interval in SEED_TIMEFRAMES:
                c.execute("INSERT OR IGNORE INTO timeframe_catalog(code,provider_interval,enabled,verified,created_at,updated_at) VALUES(?,?,1,1,?,?)",
                          (code, interval, config.now(), config.now()))
            if (c.execute("SELECT COUNT(*) n FROM timeframe_catalog WHERE enabled=1").fetchone()["n"] or 0) == 0:
                for code, _interval in SEED_TIMEFRAMES:
                    c.execute("UPDATE timeframe_catalog SET enabled=1, verified=1, updated_at=? WHERE code=?",
                              (config.now(), code))

            # v6.12.0 — ONLY real implemented & automated-tested strategies exist
            # in the catalog (all 25 empty/unimplemented placeholders purged).
            for code, title, desc in [
                ("ICT", "ICT", "Liquidity sweeps, FVG, order blocks, displacement, kill zones."),
                ("SMC", "Smart Money Concepts", "BOS/CHOCH, HH/HL/LH/LL, supply & demand."),
                ("PRICE_ACTION", "Price Action", "Engulfing, pin bar, breakout/retest, S/R."),
                ("CRT", "Candle Range Theory", "مدى الشمعة المرجعية، سحب السيولة، إغلاق داخل المدى، دخول CRT 50%."),
                ("AMD", "Power of Three", "تراكم · تلاعب · توزيع على نطاق الافتتاح (Judas Swing)."),
                ("TURTLE_SOUP", "Turtle Soup", "فشل كسر قمة/قاع 20 شمعة وانعكاس مضاد (اصطياد السيولة)."),
                ("SILVER_BULLET", "Silver Bullet", "نوافذ ICT الزمنية (لندن/نيويورك) + استمرار FVG."),
                ("FIBONACCI", "Fibonacci Golden Pocket", "مستوى ارتداد 61.8–78.6% من الموجة الدافعية مع بنية سليمة (منفذة واختبارة آلياً)."),
                ("TREND", "Trend Following", "ترتيب المتوسطات 9/21/50 + ارتداد إلى قيمة EMA21 واستئناف الاتجاه (منفذة واختبارة آلياً)."),
                ("ICHIMOKU", "Ichimoku Kinko Hyo", "نظام السحابة الياباني: كسر الكومو + تقاطع تينكان/كيجون + سحابة مستقبلية مؤكدة (منفذة واختبارة آلياً)."),
                ("CLASSIC", "CLASSIC", "التحليل الكلاسيكي: اتجاه EMA50 + مستوى أفقي حقيقي من القمم والقيعان + تأكيد شمعة ابتلاع/دبوس (منفذة واختبارة آلياً)."),
                ("SK", "SK", "محرك سكالبينج سريع: مستوى مفتاحي 12 شمعة يُكسر بإغلاق مؤكد + زخم RSI + اتجاه EMA9/21 (منفذة واختبارة آلياً)."),
                ("WYCKOFF", "Wyckoff", "منهج وايكوف: نطاق تداول حقيقي + Spring/Upthrust يخترق الحد ويغلق داخله + تأكيد الحجم عند توفره؛ الوقف خلف ذيل الحدث والأهداف قياس المدى (منفذة واختبارة آلياً)."),
            ]:
                c.execute(
                    "INSERT OR IGNORE INTO strategy_catalog(code,title,description,enabled,verified,created_at,updated_at,min_confidence) VALUES(?,?,?,1,1,?,?,60)",
                    (code, title, desc, config.now(), config.now()))
            # Mark implemented strategies as verified (graduating previously-unverified
            # strategies once without overwriting an admin's enabled=0 decision on
            # already-verified strategies!).
            for code in IMPLEMENTED_STRATEGIES:
                c.execute("UPDATE strategy_catalog SET verified=1, enabled=1, updated_at=? WHERE code=? AND verified=0",
                          (config.now(), code))
            # Purge any legacy unimplemented placeholder rows from strategy_catalog.
            placeholders = ",".join("?" for _ in IMPLEMENTED_STRATEGIES)
            purged = c.execute(
                f"DELETE FROM strategy_catalog WHERE code NOT IN ({placeholders})",
                tuple(IMPLEMENTED_STRATEGIES),
            ).rowcount or 0
            if purged:
                log_lines.append(f"purged {purged} unimplemented placeholder strategies")
            # Clear any user preference pointing at a deleted placeholder strategy.
            c.execute(
                f"UPDATE users SET strategy='' WHERE strategy IS NOT NULL AND strategy<>'' AND UPPER(strategy) NOT IN ({placeholders})",
                tuple(IMPLEMENTED_STRATEGIES),
            )
            # v6.11.0 — WYCKOFF graduation description refresh.
            c.execute("UPDATE strategy_catalog SET description=?, updated_at=? WHERE code='WYCKOFF'",
                      ("منهج وايكوف: نطاق تداول حقيقي + Spring/Upthrust يخترق الحد ويغلق داخله + تأكيد الحجم عند توفره؛ الوقف خلف ذيل الحدث والأهداف قياس المدى (منفذة واختبارة آلياً).",
                       config.now()))
            # Auto-encrypt any legacy plaintext broker_adapters.config_json rows (ENC1).
            from fysb.services import secrets as secrets_service
            for brow in c.execute("SELECT id, config_json FROM broker_adapters").fetchall():
                raw_cfg = brow["config_json"] or ""
                if raw_cfg and not secrets_service.is_encrypted(raw_cfg):
                    try:
                        parsed = json.loads(raw_cfg)
                        if isinstance(parsed, dict):
                            c.execute(
                                "UPDATE broker_adapters SET config_json=?, updated_at=? WHERE id=?",
                                (secrets_service.encrypt_json(parsed), config.now(), brow["id"]),
                            )
                    except Exception:
                        pass
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
            for sp in SEED_PLANS:
                c.execute(
                    "INSERT OR IGNORE INTO plans(code,title,price,currency,duration_days,min_score,max_accounts,enabled,markets,timeframes,strategies,max_signals_per_day) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO NOTHING",
                    sp,
                )
            # v6.22.0 — one-time 4-TIER ROYAL PLAN & CATALOG ACTIVATION.
            # Stamps FREE (7-day configurable trial) + PRO + VIP + ROYAL ELITE,
            # activates all plan markets/timeframes/strategies once, and syncs
            # free_daily_signals=1. Subsequent boots NEVER overwrite admin edits.
            if get_setting(PLANS_4TIER_FLAG, "") != "1":
                for code, fields in PLANS_4TIER_V6220.items():
                    sets = ", ".join(f"{k}=?" for k in fields)
                    c.execute(f"UPDATE plans SET {sets} WHERE code=?",
                              (*fields.values(), code))
                # Activate all markets included in the 4-tier plan catalog
                for m_sym in [x.strip().upper() for x in ROYAL_MARKETS.split(",") if x.strip()]:
                    c.execute(
                        "UPDATE market_catalog SET enabled=1, verified=1, updated_at=? WHERE symbol=?",
                        (config.now(), m_sym),
                    )
                # Ensure all 13 implemented strategies & M1..D1 timeframes are enabled
                for s_code in IMPLEMENTED_STRATEGIES:
                    c.execute(
                        "UPDATE strategy_catalog SET enabled=1, verified=1, updated_at=? WHERE code=?",
                        (config.now(), s_code),
                    )
                for tf_code in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
                    c.execute(
                        "UPDATE timeframe_catalog SET enabled=1, verified=1, updated_at=? WHERE code=?",
                        (config.now(), tf_code),
                    )
                c.execute(
                    "INSERT INTO settings(key,value) VALUES('free_daily_signals','1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?, '1')", (PLANS_LUXURY_FLAG,))
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?, '1')", (PLANS_4TIER_FLAG,))
                with _settings_lock:
                    _settings_cache["free_daily_signals"] = ("1", time.time())
                    _settings_cache[PLANS_LUXURY_FLAG] = ("1", time.time())
                    _settings_cache[PLANS_4TIER_FLAG] = ("1", time.time())
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
        # Reset bulk-preload timestamp after commit so the first read warms all
        # committed DEFAULT_SETTINGS cleanly in 1 query.
        with _settings_lock:
            _settings_cache.clear()
            globals()["_settings_bulk_at"] = 0.0
    return log_lines


# --- Settings fast-path cache (v6.20.1 bulk preload, 0ms hot reads) ----------
# get_setting() runs on EVERY button press (maintenance gate, ui_edit_in_place,
# kill-switch, quotas, trading_mode...). Over WAN PostgreSQL each uncached call
# costs 150-400ms; a single bulk SELECT preloads ALL ~45 settings for 30s and
# set_setting()/set_settings_bulk() update write-through immediately.
_SETTINGS_TTL = 30.0
_settings_cache: dict[str, tuple[str, float]] = {}
_settings_bulk_at: float = 0.0
_settings_lock = threading.Lock()


def _preload_settings() -> None:
    global _settings_bulk_at
    now_ts = time.time()
    try:
        with conn() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        if rows:
            with _settings_lock:
                for r in rows:
                    _settings_cache[r["key"]] = (r["value"], now_ts)
                _settings_bulk_at = now_ts
    except Exception:
        pass


def get_setting(key: str, default: str = "") -> str:
    now_ts = time.time()
    ent = _settings_cache.get(key)
    if ent is not None and (now_ts - ent[1]) < _SETTINGS_TTL:
        return ent[0]
    if (now_ts - _settings_bulk_at) >= _SETTINGS_TTL:
        _preload_settings()
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
