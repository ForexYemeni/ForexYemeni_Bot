"""Preflight module — full pre-start validation WITHOUT connecting to Telegram.

Used by INSTALL_AND_RUN.sh (step 8). Exit codes: 0 = ready, 1 = blocked.
Never prints secrets.
"""
import sys

from fysb import config, db
from fysb.config import log


def run() -> int:
    problems = config.validate_boot()
    if problems:
        for p in problems:
            print(f"⛔ BOOT: {p}")
        return 1
    db.init_db()
    ok, integrity = db.healthy()
    if not ok:
        print(f"⛔ BOOT: database integrity failed ({integrity})")
        return 1
    with db.conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        plans = c.execute("SELECT COUNT(*) n FROM plans").fetchone()["n"]
        markets = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        strategies = c.execute("SELECT COUNT(*) n FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        open_signals = c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')").fetchone()["n"]
    print("✅ BOOT: environment validated (BOT_TOKEN present, ADMIN_ID present)")
    engine = "PostgreSQL (Supabase)" if config.USE_PG else f"SQLite ({config.DB_FILE.name})"
    print(f"✅ BOOT: database ok ({integrity}) | engine={engine} — users={users}, plans={plans}")
    print(f"✅ BOOT: ready markets (enabled+verified)={markets} | ready strategies={strategies}")
    print(f"✅ BOOT: open signals={open_signals} | engine={'ON' if db.get_setting('engine_enabled')=='1' else 'OFF'} | kill_switch={'ON' if db.kill_switch_on() else 'OFF'}")
    print(f"ℹ️  BOOT: market data key={'configured' if config.has_market_api() else 'MISSING (signals engine disabled)'}")
    print(f"🔒 BOOT: live trading={'ENABLED ⚠️' if db.get_setting('live_enabled')=='1' else 'locked (safe default)'}")
    if markets == 0 or strategies == 0:
        print("ℹ️  BOOT: اتبع «خطوات ما بعد التشغيل» في README لتفعيل سوق/إطار/استراتيجية من لوحة الإدارة.")
    print("🚀 Preflight passed — you can run: python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(run())
