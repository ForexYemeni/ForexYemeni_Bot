"""Backup & restore service — engine-aware (SQLite file snapshots / PostgreSQL JSON dumps).

Full project archive is a real ZIP (code + docs + .env + fresh DB snapshot)
sent to the admin on request — the admin owns the environment file.
"""
import json
import os
import shutil
import sqlite3
import tarfile
import time
import zipfile
from pathlib import Path

from fysb import config, db
from fysb.config import log


def quick_db_snapshot(tag: str = "manual") -> Path | None:
    """Consistent snapshot of the live DB — BEST-EFFORT (v6.7.2).

    SQLite: the sqlite3 backup API (WAL-safe page copy).
    PostgreSQL: full JSON dump of every table (restore-ready).

    Returns None when the snapshot cannot be taken (hosted DB slow/busy,
    statement budget exceeded). A failed backup is a logged warning — it
    never raises into the caller and above all never blocks the boot: the
    live Supabase crash of 2026-11-23 (statement_timeout on the boot-time
    pre-migration dump aborting init_db, rolling back its migrations and
    re-crashing on every restart) is exactly what this contract makes
    impossible.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    try:
        if config.USE_PG:
            dest = config.BACKUP_DIR / f"fysb_db_{tag}_{ts}.json"
            data = db.pglayer.dump_all()
            if not any(isinstance(v, list) for v in data.values()):
                raise RuntimeError("dump returned no tables")
            dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        else:
            dest = config.BACKUP_DIR / f"fysb_db_{tag}_{ts}.db"
            src = db.conn()
            try:
                target = sqlite3.connect(str(dest))
                with target:
                    src.backup(target)
                target.close()
            finally:
                src.close()
    except Exception as e:
        log.warning("DB snapshot '%s' skipped (%s) — backup is best-effort, never boot-blocking",
                    tag, type(e).__name__)
        return None
    _prune_old(keep=10)
    return dest


def _prune_old(keep: int = 10) -> None:
    files = sorted(
        list(config.BACKUP_DIR.glob("fysb_db_*.db"))
        + list(config.BACKUP_DIR.glob("fysb_db_*.json")),
        key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def verify_archive(path: Path) -> tuple[bool, str]:
    """Check an archive: DB -> integrity_check, json -> parse, zip/tar -> listing."""
    try:
        if path.suffix == ".db":
            c = sqlite3.connect(str(path))
            try:
                row = c.execute("PRAGMA integrity_check(10)").fetchone()
                ok = str(row[0]).lower() == "ok"
                return ok, str(row[0])
            finally:
                c.close()
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return (len(data) > 0), f"{len(data)} tables"
        if path.suffix == ".zip":
            with zipfile.ZipFile(str(path), "r") as z:
                names = z.namelist()
                bad = z.testzip()
            return (len(names) > 0 and bad is None), f"{len(names)} files"
        if path.suffix in (".gz",) or path.name.endswith(".tar.gz"):
            with tarfile.open(str(path), "r:gz") as t:
                members = t.getnames()
            return (len(members) > 0), f"{len(members)} files"
        return False, "unsupported archive type"
    except Exception as e:
        return False, type(e).__name__


def settings_export() -> Path:
    """Export settings + catalog definitions (NO secrets, NO .env content)."""
    data = {"exported_at": db.now(), "version": config.VERSION}
    with db.conn() as c:
        data["settings"] = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
        data["market_catalog"] = [dict(r) for r in c.execute("SELECT * FROM market_catalog")]
        data["timeframe_catalog"] = [dict(r) for r in c.execute("SELECT * FROM timeframe_catalog")]
        data["strategy_catalog"] = [dict(r) for r in c.execute("SELECT * FROM strategy_catalog")]
        data["plans"] = [dict(r) for r in c.execute("SELECT * FROM plans")]
    dest = config.BACKUP_DIR / f"fysb_settings_{time.strftime('%Y%m%d_%H%M%S')}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def full_project_archive(include_env: bool = False) -> Path:
    """Real ZIP of the whole project: code + docs + consistent DB snapshot.

    Security (v6.12.0): `.env` (which holds BOT_TOKEN, DATABASE_URL, and
    BROKER_SECRET_KEY) is EXCLUDED by default so secrets are never leaked into
    unencrypted ZIP archives sent over Telegram. Pass `include_env=True` only
    for strictly local/offline archival.
    """
    snap = quick_db_snapshot("archived")  # may be None — archive still builds
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"FYSB_PROJECT_BACKUP_{ts}.zip"
    skip_dirs = {
        "venv", ".venv", "__pycache__", "node_modules", ".git", "logs", "backups",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".arena",
    }
    base = config.BASE_DIR
    with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if path.is_file() and path.name.endswith((".pyc", ".pyo", ".db-shm", ".db-wal")):
                continue
            if snap is not None and path == snap:
                continue
            if path.name == "fysb.lock":
                continue
            # Never bundle .env or secret env overrides in the main walk (.env.example stays)
            if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
                continue
            try:
                z.write(str(path), arcname=str(rel))
            except (OSError, PermissionError):
                continue
        # Fresh consistent DB snapshot inside the archive (when available)
        if snap is not None:
            z.write(str(snap), arcname=f"database/forexyemeni_backup{snap.suffix}")
        else:
            z.writestr(
                "database/SNAPSHOT_UNAVAILABLE.txt",
                "النسخة اللحظية لقاعدة البيانات لم تكن متاحة لحظة إنشاء الأرشيف "
                "(قاعدة البيانات مشغولة / تجاوز ميزانية الزمن). الأرشيف يحتوي كامل "
                "الكود؛ خُذ نسخة قاعدة بيانات منفصلة عبر /backup أو زر «نسخة قاعدة بيانات الآن».")
        env_file = base / ".env"
        if include_env and env_file.is_file():
            z.write(str(env_file), arcname=".env")
        elif not include_env:
            z.writestr(
                "ENV_EXCLUDED_FOR_SECURITY.txt",
                "تم استثناء ملف .env تلقائياً لحماية توكن البوت وكلمات المرور ومفتاح التشفير "
                "BROKER_SECRET_KEY. استخدم .env.example كقالب عند النقل لسيرفر جديد.\n")
    if snap is not None:
        snap.unlink(missing_ok=True)
    return dest


def list_backups() -> list[dict]:
    out = []
    for p in sorted(config.BACKUP_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and (p.suffix in (".db", ".json", ".gz", ".zip") or p.name.endswith(".tar.gz")):
            out.append({
                "name": p.name,
                "size_kb": round(p.stat().st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                "path": p,
            })
    return out[:15]


def restore_from(db_file: Path) -> tuple[bool, str]:
    """Restore a DB snapshot. Takes a fresh safety snapshot first.

    SQLite: verified .db file swap. PostgreSQL: JSON dump replay (wipe +
    insert + sequence resync) inside one transaction.
    """
    ok, detail = verify_archive(db_file)
    if not ok:
        return False, f"archive failed verification: {detail}"
    if config.USE_PG:
        if db_file.suffix != ".json":
            return False, "PostgreSQL engine restores JSON dumps only (.json)"
        try:
            # Safety snapshot is best-effort: if it fails we still proceed —
            # restore_json is one atomic transaction (wipe + insert commit
            # together or not at all), so data survives a failed restore.
            quick_db_snapshot("pre-restore")
            data = json.loads(db_file.read_text(encoding="utf-8"))
            res = db.pglayer.restore_json(data)
            ok2, res2 = db.healthy()
            log.info("DB restored (PostgreSQL) | result=%s", res2)
            return ok2, f"{res} | health: {res2}"
        except Exception as e:
            return False, type(e).__name__
    quick_db_snapshot("pre-restore")
    tmp_target = db.conn()
    tmp_target.close()
    try:
        os.replace(str(db_file), str(config.DB_FILE))
        # Rebuild WAL cleanly
        c = db.conn()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        ok2, res = db.healthy()
        log.info("DB restored | result=%s", res)
        return ok2, res
    except Exception as e:
        return False, type(e).__name__
