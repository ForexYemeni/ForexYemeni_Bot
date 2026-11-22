"""Backup & restore service — SQLite-safe, WAL-aware, no secrets in reports.

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


def quick_db_snapshot(tag: str = "manual") -> Path:
    """Consistent snapshot of the live DB using the sqlite3 backup API.

    Works correctly with WAL: the backup connection copies committed pages.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"fysb_db_{tag}_{ts}.db"
    src = db.conn()
    try:
        target = sqlite3.connect(str(dest))
        with target:
            src.backup(target)
        target.close()
    finally:
        src.close()
    _prune_old(keep=10)
    return dest


def _prune_old(keep: int = 10) -> None:
    files = sorted(config.BACKUP_DIR.glob("fysb_db_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def verify_archive(path: Path) -> tuple[bool, str]:
    """Check an archive: DB -> integrity_check, zip/tar -> readable listing."""
    try:
        if path.suffix == ".db":
            c = sqlite3.connect(str(path))
            try:
                row = c.execute("PRAGMA integrity_check(10)").fetchone()
                ok = str(row[0]).lower() == "ok"
                return ok, str(row[0])
            finally:
                c.close()
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


def full_project_archive() -> Path:
    """Real ZIP of the whole project: code + docs + database snapshot + .env.

    The admin explicitly requested the .env inside the archive (their own bot,
    their own token, delivered to their own chat) so the archive can restore a
    working copy anywhere. Excludes generated junk (venv/__pycache__/logs/backups).
    """
    snap = quick_db_snapshot("archived")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"FYSB_PROJECT_BACKUP_{ts}.zip"
    skip_dirs = {"venv", ".venv", "__pycache__", "node_modules", ".git", "logs", "backups"}
    base = config.BASE_DIR
    with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if path.is_file() and path.name.endswith((".pyc", ".pyo", ".db-shm", ".db-wal")):
                continue
            if path == snap:
                continue
            if path.name == "fysb.lock":
                continue
            if path == (base / ".env"):
                continue  # added explicitly below (avoid duplicate zip entry)
            try:
                z.write(str(path), arcname=str(rel))
            except (OSError, PermissionError):
                continue
        # Fresh consistent DB snapshot inside the archive
        z.write(str(snap), arcname="database/forexyemeni_backup.db")
        # .env explicitly (never silently omitted) — skipped in the loop above
        env_file = base / ".env"
        if env_file.is_file():
            z.write(str(env_file), arcname=".env")
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

    The swap happens after integrity verification; active connections are
    short-lived per operation, so the next operation picks the restored file.
    """
    ok, detail = verify_archive(db_file)
    if not ok:
        return False, f"archive failed verification: {detail}"
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
