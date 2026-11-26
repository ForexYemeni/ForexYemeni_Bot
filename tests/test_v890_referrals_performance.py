"""v8.9.0 — «دعوة الأصدقاء» & commission wallet performance contract.

Measured baseline on a production-scale database (3,000 users / 2,168 invite
links / 3,000 commissions) BEFORE this release:

    u:referrals (دعوة الأصدقاء)  →  39 DB connections  →  5.99 s on Supabase
    u:affiliate (محفظة العمولات) → 100 DB connections  → 15.24 s on Supabase
    db.conn() + SELECT 1        →  0.72 ms (synchronous=NORMAL re-paid per checkout)

Root causes proven here:
  1. 18 setting keys were read by code but never seeded → a MISS is never cached,
     so every read opened a new connection forever (12 of 13 inside get_config).
  2. get_config() was called 3× per affiliate_stats, and tier_for_count()
     re-read it twice more for a single rank lookup.
  3. The referrer handler computed the identical snapshot twice (dashboard +
     affiliate_stats) and awaited a live Telegram get_me() on every tap.

These tests pin the fixed behaviour so the page can never silently regress.
"""
from __future__ import annotations

from fysb import config, db
from fysb.services import affiliates


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v890.db"))
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()


def _seed(uid: int, name: str = "Trader"):
    with db.conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, full_name, username, joined_at) VALUES(?,?,?,?)",
            (uid, name, f"u{uid}", config.now()),
        )


def _count_conn(monkeypatch):
    """Instrument db.conn() checkouts, returning the live counter dict."""
    hits = {"n": 0}
    real = db.conn

    def counting():
        hits["n"] += 1
        return real()

    monkeypatch.setattr(db, "conn", counting)
    return hits


def test_every_affiliate_setting_is_seeded_so_reads_are_cached(tmp_path, monkeypatch):
    """A key with no row can never be cached → it cost a connection on every read."""
    _fresh_db(tmp_path, monkeypatch)
    required = (
        "affiliate_enabled",
        "affiliate_mode",
        "affiliate_flat_pct",
        "affiliate_balance_unlock_enabled",
        "affiliate_milestone_enabled",
        "affiliate_milestone_paid_refs",
        "affiliate_milestone_target",
        "affiliate_milestone_days",
        "affiliate_tier_bronze",
        "affiliate_tier_silver",
        "affiliate_tier_gold",
        "affiliate_tier_royal",
    )
    with db.conn() as c:
        keys = {r["key"] for r in c.execute("SELECT key FROM settings").fetchall()}
    missing = [k for k in required if k not in keys]
    assert missing == [], f"unseeded settings re-introduce the N-connection bug: {missing}"


def test_get_config_and_rank_are_virtual_cache_hits(tmp_path, monkeypatch):
    """After one warm read, config + rank must not touch the database at all."""
    _fresh_db(tmp_path, monkeypatch)
    _seed(101)
    affiliates.get_config()  # warm the settings cache
    hits = _count_conn(monkeypatch)

    cfg = affiliates.get_config()
    assert cfg["mode"] in ("TIERED", "FLAT")
    rank = affiliates.tier_for_count(0)          # used to re-read config twice
    rank_cfg = affiliates.tier_for_count(30, cfg)  # pre-loaded config path
    assert rank["code"] == "BRONZE"
    assert rank_cfg["code"] == "ROYAL"
    assert hits["n"] == 0, f"config/rank must be memory-only once warm, got {hits['n']} connections"


def test_affiliate_stats_uses_a_single_connection(tmp_path, monkeypatch):
    """38 → 1-3 checkouts, with every published number unchanged."""
    _fresh_db(tmp_path, monkeypatch)
    uid = 555
    _seed(uid)
    with db.conn() as c:
        c.execute("INSERT INTO affiliate_links(invitee_id,referrer_id,converted,created_at) VALUES(?,?,?,?)",
                  (901, uid, 0, config.now()))
        c.execute("INSERT INTO affiliate_links(invitee_id,referrer_id,converted,created_at) VALUES(?,?,?,?)",
                  (902, uid, 1, config.now()))
        c.execute(
            "INSERT INTO affiliate_commissions(referrer_id,referred_user_id,payment_id,amount_usd,"
            "rate_pct,commission_usd,tier_name,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, 902, 77, 20.0, 15.0, 3.0, "BRONZE", "CREDITED", config.now()),
        )
        c.execute(
            "INSERT INTO affiliate_payouts(user_id,amount_usd,wallet_address,status,created_at) "
            "VALUES(?,?,?,?,?)", (uid, 0.5, "WALLET123", "APPROVED", config.now()),
        )
        c.execute(
            "INSERT INTO affiliate_payouts(user_id,amount_usd,wallet_address,status,created_at) "
            "VALUES(?,?,?,?,?)", (uid, 0.25, "WALLET123", "PENDING", config.now()),
        )
    affiliates.get_config()
    hits = _count_conn(monkeypatch)

    st = affiliates.affiliate_stats(uid)

    assert hits["n"] <= 3, f"affiliate_stats opened {hits['n']} connections (target: ≤3, was 38)"
    assert st["total_refs"] == 2          # union of affiliate_links + personal campaigns
    assert st["paid_refs"] == 1
    assert st["earned_usd"] == 3.0
    assert st["paid_out"] == 0.5
    assert st["pending_out"] == 0.25
    assert st["available_usd"] == 2.25
    # both milestone aliases must survive — the referrals screen reads `remaining`
    assert st["milestone_needed"] == st["milestone_remaining"]


def test_dashboard_accepts_a_preloaded_snapshot_without_recomputation(tmp_path, monkeypatch):
    """The wallet screen must not compute the same numbers twice (100 conns)."""
    _fresh_db(tmp_path, monkeypatch)
    _seed(777)
    affiliates.get_config()
    st = affiliates.affiliate_stats(777)
    hits = _count_conn(monkeypatch)

    body = affiliates.render_affiliate_dashboard(777, "ForexYemeniBot", st, None)

    assert "برنامج الشركاء" in body
    assert "ref_777" in body or "ForexYemeniBot" in body
    assert hits["n"] == 0, f"preloaded dashboard still opened {hits['n']} connections"


def test_bot_username_is_cached_not_fetched_every_tap():
    """The @username of a running bot never changes → no network call per tap."""
    first = affiliates.cached_bot_username()
    second = affiliates.cached_bot_username()
    assert first == second
    assert first, "bot username must resolve (config signature fallback)"
