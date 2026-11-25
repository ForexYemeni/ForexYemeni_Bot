"""FOREXYEMENI — محرك التحديث الذاتي (v6.9.0).

يجلب أحدث إصدار منشور على GitHub Releases (الملف المرفق: ForexYemeni1.zip)،
يثبّته فوق شجرة المشروع الحالية بأمان، ثم يعيد تشغيل العملية لتحميل الكود الجديد.

الضمانات:
  * معياري بالكامل (مكتبة بايثون القياسية فقط — بلا تبعيات جديدة).
  * الريبو عام ⇒ لا يلزم أي توكن؛ إن أصبح خاصاً يكفي GITHUB_TOKEN في .env.
  * لا يلمس أبداً: .env، قواعد البيانات (*.db)، السجلات، النسخ الاحتياطية،
    spot_calibration.json — بيئة التشغيل المحلية محفوظة لا تُمس.
  * يرفض تثبيت أي حزمة ناقصة (بلا main.py أو fysb) — لا يكسر النسخة العاملة أبداً.
  * أي فشل (شبكة/حزمة/استخراج) ⇒ يكمل الإقلاع بالنسخة الحالية بلا صمت مرعب —
    سطر BOOT واضح في السجل يشرح ما حدث.
  * حماية Zip-Slip: أي مسار خارج جذر المشروع ⇒ رفض فوري.

الاستخدام:
  * عند الإقلاع: main.py يستدعي boot_check() قبل تحميل أي كود من fysb
    (يمكن تعطيله بمتغير البيئة AUTO_UPDATE=0).
  * من داخل تلغرام: /update (الإدارة فقط) عبر handlers/common.py.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent

# --- ما لا تلمسه عملية التحديث أبداً (بيئة التشغيل المحلية) ------------------
PRESERVE_EXACT = {".env", "fysb.lock", ".DS_Store", "spot_calibration.json", ".last_boot_update.json"}
PRESERVE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".log", ".pyc")
PRESERVE_DIRS = {"logs", "backups", "__pycache__", ".git", ".pytest_cache", "node_modules", ".bin", ".local", ".cache"}

TIMEOUT_JSON = 25      # ثوانٍ — فحص الإصدار
TIMEOUT_ZIP = 180      # ثوانٍ — تنزيل الحزمة
MAX_ZIP_BYTES = 30 * 1024 * 1024   # حارس حجم معقول — الحزم الحقيقية أقل من 2MB


def _config():
    """إعدادات الريبو من fysb.config (استيراد كسول لتفادي الدوران عند الإقلاع)."""
    from fysb import config
    return (
        getattr(config, "REPO_OWNER", "ForexYemeni"),
        getattr(config, "REPO_NAME", "ForexYemeni_Bot"),
        getattr(config, "UPDATE_ASSET", "ForexYemeni1.zip"),
    )


def _ssl_context():
    try:                                    # certifi متوفرة عبر تبعيات PTB
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _token() -> str:
    """توكن اختياري — يلزم فقط إذا أصبح المستودع خاصاً أو لرفع حد الطلبات."""
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()


def _headers() -> dict:
    h = {
        "User-Agent": "ForexYemeni-Bot-Updater",
        "Accept": "application/vnd.github+json",
    }
    tok = _token()
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def _get_json(url: str, timeout: int = TIMEOUT_JSON):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: pathlib.Path, timeout: int = TIMEOUT_ZIP) -> pathlib.Path:
    req = urllib.request.Request(url, headers={**_headers(), "Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp, \
            open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 256)
    return dest


def _get_text(url: str, timeout: int = TIMEOUT_JSON) -> str:
    req = urllib.request.Request(url, headers={**_headers(), "Accept": "text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read(65536).decode("utf-8", errors="replace")


def sha256_file(path) -> str:
    """Compute lowercase SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _parse_sha256_text(text: str, asset_name: str = "") -> str:
    """Extract a 64-char SHA-256 hex digest from checksum file or release body."""
    if not text:
        return ""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    # Prefer a line mentioning the exact asset name (e.g. SHA256SUMS.txt)
    if asset_name:
        for ln in lines:
            if asset_name in ln:
                m = re.search(r"\b([0-9a-fA-F]{64})\b", ln)
                if m:
                    return m.group(1).lower()
    # Explicit sha256:<hex> label (e.g. GitHub asset digest or release notes)
    m = re.search(r"sha256\s*[:=]\s*([0-9a-fA-F]{64})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # Standalone checksum file ("<64hex>" or "<64hex>  filename")
    if len(lines) == 1:
        m = re.match(r"^([0-9a-fA-F]{64})(?:\s+.*)?$", lines[0])
        if m:
            return m.group(1).lower()
    return ""


def verify_sha256(zip_path, expected_hex: str) -> bool:
    """Constant-time comparison of file SHA-256 against expected 64-hex digest."""
    import hmac
    exp = (expected_hex or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", exp):
        return False
    return hmac.compare_digest(sha256_file(zip_path), exp)


# --- مقارنة الإصدارات --------------------------------------------------------

def parse_version(text) -> tuple:
    """'v6.9.0' أو '6.9.0' → (6, 9, 0). أي نص غير رقمي ⇒ (0,)."""
    digits = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in str(text or ""))
    chunks = [c for c in digits.split() if c]
    if not chunks:
        return (0,)
    nums = []
    for part in chunks[0].split(".")[:4]:
        try:
            nums.append(int(part))
        except ValueError:
            break
    return tuple(nums) if nums else (0,)


def is_newer(remote_tag, local_version) -> bool:
    return parse_version(remote_tag) > parse_version(local_version)


def display_tag(tag) -> str:
    """'v6.10.0' → '6.10.0' — تُسبَق دائماً بـ v واحدة في الرسائل (لا vv)."""
    t = str(tag or "").strip()
    return t[1:] if t[:1].lower() == "v" else t


_TAG_RE = re.compile(r"^[vV]?[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9._-]+)?$")


def normalize_tag(tag: str) -> str:
    """'6.14.1' أو 'v6.14.1' → 'v6.14.1'. يعيد '' لأي نص غير صالح."""
    t = str(tag or "").strip()
    if not t or not _TAG_RE.match(t):
        return ""
    return "v" + t[1:] if t[:1].lower() == "v" else "v" + t


def _parse_release_payload(data: dict, asset_name: str):
    """Extract {tag, url, name, sha256?, sha256_url?, title?, published_at?}."""
    if not isinstance(data, dict):
        return None
    tag = (data.get("tag_name") or "").strip()
    asset_url = ""
    sha256_hex = ""
    sha256_url = ""
    for asset in data.get("assets") or []:
        aname = asset.get("name") or ""
        if aname == asset_name:
            asset_url = asset.get("browser_download_url") or ""
            sha256_hex = _parse_sha256_text(asset.get("digest") or "", asset_name)
        elif aname in (f"{asset_name}.sha256", f"{asset_name}.sha256sum", "SHA256SUMS.txt", "checksums.txt"):
            sha256_url = asset.get("browser_download_url") or ""
    if not sha256_hex and data.get("body"):
        sha256_hex = _parse_sha256_text(data.get("body") or "", asset_name)
    if not (tag and asset_url):
        return None
    out = {
        "tag": tag,
        "url": asset_url,
        "name": asset_name,
        "title": (data.get("name") or tag).strip(),
        "published_at": (data.get("published_at") or "").strip(),
    }
    if sha256_hex:
        out["sha256"] = sha256_hex
    if sha256_url:
        out["sha256_url"] = sha256_url
    return out


# --- GitHub Releases ---------------------------------------------------------

def tags_from_feed(owner, repo, limit: int = 10) -> list[str]:
    """قائمة التاغات من تغذية releases.atom — بلا حدود طلبات API."""
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    req = urllib.request.Request(url, headers={"User-Agent": _headers()["User-Agent"]})
    with urllib.request.urlopen(req, timeout=TIMEOUT_JSON, context=_ssl_context()) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    seen = []
    for m in re.finditer(r"releases/tag/([^\"<>]+)\"", text):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.append(t)
            if len(seen) >= limit:
                break
    return seen


def latest_tag_from_feed(owner, repo):
    """آخر تاغ من تغذية releases.atom — بلا حدود طلبات API (مسار احتياطي)."""
    tags = tags_from_feed(owner, repo, limit=1)
    return tags[0] if tags else ""


def asset_exists(url) -> bool:
    """تحقق HEAD رخيص من وجود ملف الحزمة على الرابط المباشر.
    أي خطأ غير 404 يُقبل (التنزيل الفعلي هو الحَكَم)."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": _headers()["User-Agent"]})
        with urllib.request.urlopen(req, timeout=TIMEOUT_JSON, context=_ssl_context()) as resp:
            return resp.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code != 404
    except Exception:
        return True


def latest_release(owner=None, repo=None, asset_name=None):
    """أحدث Release منشور: {"tag": ..., "url": ...} أو None عند عدم وجوده.

    المسار الأساسي: API الرسمي. عند حد الطلبات (403/429) أو أي فشل HTTP ⇒
    مسار احتياطي من تغذية Atom + رابط التنزيل المباشر (كلاهما بلا حد طلبات).
    """
    if owner is None or repo is None or asset_name is None:
        c_owner, c_repo, c_asset = _config()
        owner = owner or c_owner
        repo = repo or c_repo
        asset_name = asset_name or c_asset
    try:
        data = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
        parsed = _parse_release_payload(data, asset_name)
        if parsed:
            return parsed
    except Exception:
        pass                      # أي فشل API ⇒ المسار الاحتياطي أدناه
    # المسار الاحتياطي (بلا حد طلبات): تغذية Atom + رابط التنزيل المباشر
    try:
        tag = latest_tag_from_feed(owner, repo)
        if tag:
            url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{asset_name}"
            if asset_exists(url):
                return {"tag": tag, "url": url, "name": asset_name}
    except Exception:
        pass
    return None


def release_by_tag(tag: str, owner=None, repo=None, asset_name=None):
    """جلب إصدار محدد برقم الإصدار (مثل v6.14.1 أو 6.14.1) للترقية أو الرجوع."""
    norm = normalize_tag(tag)
    if not norm:
        return None
    if owner is None or repo is None or asset_name is None:
        c_owner, c_repo, c_asset = _config()
        owner = owner or c_owner
        repo = repo or c_repo
        asset_name = asset_name or c_asset
    try:
        data = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{norm}")
        parsed = _parse_release_payload(data, asset_name)
        if parsed:
            return parsed
    except Exception:
        pass
    # مسار احتياطي مباشر عند حد الطلبات (بدون API)
    try:
        url = f"https://github.com/{owner}/{repo}/releases/download/{norm}/{asset_name}"
        if asset_exists(url):
            out = {"tag": norm, "url": url, "name": asset_name, "title": norm}
            sha_url = url + ".sha256"
            if asset_exists(sha_url):
                out["sha256_url"] = sha_url
            return out
    except Exception:
        pass
    return None


def list_releases(limit: int = 8, owner=None, repo=None, asset_name=None) -> list[dict]:
    """قائمة بأحدث الإصدارات المتاحة للتثبيت أو التراجع من GitHub Releases."""
    if owner is None or repo is None or asset_name is None:
        c_owner, c_repo, c_asset = _config()
        owner = owner or c_owner
        repo = repo or c_repo
        asset_name = asset_name or c_asset
    limit = max(1, min(int(limit or 8), 20))
    out: list[dict] = []
    try:
        items = _get_json(f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={limit}")
        if isinstance(items, list):
            for item in items:
                if item.get("draft"):
                    continue
                parsed = _parse_release_payload(item, asset_name)
                if parsed:
                    out.append(parsed)
            if out:
                return out
    except Exception:
        pass
    try:
        for tag in tags_from_feed(owner, repo, limit=limit):
            url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{asset_name}"
            out.append({"tag": tag, "url": url, "name": asset_name, "title": tag, "published_at": ""})
    except Exception:
        pass
    return out


# --- استخراج آمن --------------------------------------------------------------

def _zip_root_prefix(names) -> str:
    """إن كانت كل الملفات داخل مجلد جذر واحد، تُعاد بادئته للتسطيح."""
    if not names or not all("/" in n for n in names):
        return ""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1:
        return tops.pop() + "/"
    return ""


def _stripped_names(names) -> set:
    prefix = _zip_root_prefix(names)
    if not prefix:
        return set(names)
    return {n[len(prefix):] for n in names if n.startswith(prefix)}


def validate_package(zip_path) -> bool:
    """تأكد أن الحزمة قابلة للإقلاع: main.py + fysb/config.py بعد التسطيح."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
        stripped = _stripped_names(names)
        return "main.py" in stripped and "fysb/config.py" in stripped
    except Exception:
        return False


def safe_extract(zip_path, dest_root) -> int:
    """استخراج محكم فوق شجرة المشروع: تسطيح، حماية Zip-Slip، تخطي المحلي المحفوظ.

    يعيد عدد الملفات المثبتة. لا يمس .env ولا قواعد البيانات ولا السجلات.
    """
    dest_root = pathlib.Path(dest_root).resolve()
    installed = 0
    with zipfile.ZipFile(zip_path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        prefix = _zip_root_prefix(names)
        for info in z.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            parts = pathlib.PurePosixPath(rel).parts
            if not parts:
                continue
            if any(p in PRESERVE_DIRS for p in parts):
                continue
            if parts[-1] in PRESERVE_EXACT or parts[-1].endswith(PRESERVE_SUFFIXES):
                continue
            target = dest_root.joinpath(*parts).resolve()
            if dest_root != target and dest_root not in target.parents:
                raise ValueError("مسار غير آمن داخل الحزمة: " + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 256)
            installed += 1
    _clear_pycache(dest_root)
    return installed


def _clear_pycache(root) -> None:
    """حذف بايت-كود قديم حتى لا يعبث أي .pyc بسلوك الكود الجديد."""
    for p in pathlib.Path(root).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


ROLLBACK_PREFIX = "update_rollback_v"
ROLLBACK_KEEP = 5
_SNAPSHOT_SKIP_DIRS = PRESERVE_DIRS | {
    ".venv", "venv", ".mypy_cache", ".ruff_cache",
    ".bin", ".local", ".cache", ".pip", ".npm", "dist", "build",
}
_SNAPSHOT_MAX_FILE_BYTES = 5 * 1024 * 1024   # never compress 30-40MB binaries like ngrok/cloudflared


def create_rollback_snapshot(dest_root, version_label: str = "") -> pathlib.Path | None:
    """أخذ نسخة احتياطية فورية لكود النسخة الحالية قبل التحديث للتراجع الفوري.

    لا تتضمن أبداً .env أو قواعد البيانات أو السجلات أو الملفات التنفيذية الكبيرة (.bin/ngrok).
    تُحفظ داخل backups/update_rollback_v<ver>_<ts>.zip مع الإبقاء على أحدث 5 نسخ.
    """
    root = pathlib.Path(dest_root).resolve()
    if not ((root / "main.py").is_file() and (root / "fysb" / "config.py").is_file()):
        return None
    bdir = root / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    safe_ver = re.sub(r"[^0-9A-Za-z._-]", "", display_tag(version_label) or "unknown") or "unknown"
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    snap = bdir / f"{ROLLBACK_PREFIX}{safe_ver}_{ts}.zip"
    try:
        with zipfile.ZipFile(snap, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(root)
                parts = rel.parts
                if any(part in _SNAPSHOT_SKIP_DIRS for part in parts):
                    continue
                if parts[-1] in PRESERVE_EXACT or parts[-1].endswith(PRESERVE_SUFFIXES):
                    continue
                try:
                    if p.stat().st_size > _SNAPSHOT_MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                z.write(p, f"ForexYemeni_Bot/{rel.as_posix()}")
        if not validate_package(snap):
            snap.unlink(missing_ok=True)
            return None
        # prune older rollback snapshots beyond ROLLBACK_KEEP
        snaps = list_rollback_snapshots(root)
        for old in snaps[ROLLBACK_KEEP:]:
            old.unlink(missing_ok=True)
        return snap
    except Exception:
        snap.unlink(missing_ok=True)
        return None


def list_rollback_snapshots(dest_root=None) -> list[pathlib.Path]:
    """قائمة نسخ التراجع المحلية مرتبة من الأحدث للأقدم."""
    root = pathlib.Path(dest_root).resolve() if dest_root else ROOT
    bdir = root / "backups"
    if not bdir.is_dir():
        return []
    snaps = [p for p in bdir.glob(f"{ROLLBACK_PREFIX}*.zip") if p.is_file()]
    snaps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return snaps


def snapshot_version(path: pathlib.Path) -> str:
    """استخراج رقم الإصدار من اسم ملف نسخة التراجع."""
    m = re.match(rf"^{re.escape(ROLLBACK_PREFIX)}([^_]+)_", path.name)
    return m.group(1) if m else "?"


def rollback_update(dest_root=None, snapshot_path=None) -> dict:
    """تراجع فوري لآخر نسخة محفوظة محلياً (يعمل حتى بلا إنترنت أو GitHub)."""
    result = {"status": "error", "local": None, "restored_version": None,
              "backup_name": "", "files": 0, "detail": ""}
    dest_root = pathlib.Path(dest_root).resolve() if dest_root else ROOT
    try:
        from fysb import config
        result["local"] = config.VERSION
    except Exception:
        pass
    try:
        snap = pathlib.Path(snapshot_path) if snapshot_path else None
        if snap is None:
            snaps = list_rollback_snapshots(dest_root)
            snap = snaps[0] if snaps else None
        if snap is None or not snap.is_file():
            result["detail"] = "لا توجد نسخة تراجع محلية محفوظة بعد — استخدم /update v<الإصدار> للرجوع من GitHub"
            return result
        if not validate_package(snap):
            result["detail"] = "ملف نسخة التراجع غير سليم — أُلغي التراجع لحماية النظام"
            return result
        result["files"] = safe_extract(snap, dest_root)
        result["restored_version"] = snapshot_version(snap)
        result["backup_name"] = snap.name
        result["status"] = "rolled_back"
        return result
    except Exception as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result


# --- الدورة الكاملة ----------------------------------------------------------

def self_update(dest_root=None, target_tag: str = "", force: bool = False) -> dict:
    """فحص ← تنزيل ← نسخة أمان ← تثبيت. يعيد تقريراً بلا أي استثناءات.

    * target_tag فارغ: يجلب أحدث إصدار فقط إذا كان أحدث من المحلي (ما لم تكن force=True).
    * target_tag محدد (مثل 'v6.14.1' أو '6.14.1'): يثبّت ذلك الإصدار بالضبط
      (ترقية أو رجوع لإصدار سابق أو إعادة تثبيت) مع حفظ نسخة تراجع محلية تلقائياً.
    """
    result = {"status": "error", "local": None, "remote": None, "detail": "",
              "files": 0, "direction": "upgrade", "rollback_backup": ""}
    dest_root = pathlib.Path(dest_root) if dest_root else ROOT
    try:
        from fysb import config
        result["local"] = config.VERSION
    except Exception:
        pass
    try:
        if target_tag:
            norm = normalize_tag(target_tag)
            if not norm:
                result["detail"] = f"صيغة رقم الإصدار غير صالحة: {target_tag} (مثال صحيح: v6.14.1)"
                return result
            rel = release_by_tag(norm)
            if not rel:
                result["detail"] = f"الإصدار {norm} غير موجود أو لا يحتوي على حزمة {(_config()[2])}"
                return result
            result["remote"] = rel["tag"]
            lv = parse_version(result["local"] or "0")
            rv = parse_version(rel["tag"])
            if rv > lv:
                result["direction"] = "upgrade"
            elif rv < lv:
                result["direction"] = "downgrade"
            else:
                result["direction"] = "reinstall"
        else:
            rel = latest_release()
            if not rel:
                result["detail"] = "لا يوجد إصدار منشور على GitHub Releases"
                return result
            result["remote"] = rel["tag"]
            if not force and not is_newer(rel["tag"], result["local"] or "0"):
                result["status"] = "no_update"
                return result
        tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="fysb_upd_"))
        tmp_zip = tmp_dir / "update.zip"
        _download(rel["url"], tmp_zip)
        try:
            if tmp_zip.stat().st_size > MAX_ZIP_BYTES:
                result["detail"] = "حجم الحزمة أكبر من الحد المسموح — أُلغي التثبيت"
                return result
            # Integrity verification: check SHA-256 whenever published by the
            # release (asset digest, release body, or companion .sha256 asset).
            expected_sha = (rel.get("sha256") or "").strip().lower()
            if not expected_sha and rel.get("sha256_url"):
                try:
                    expected_sha = _parse_sha256_text(
                        _get_text(rel["sha256_url"], timeout=TIMEOUT_JSON),
                        rel.get("name") or "",
                    )
                except Exception:
                    expected_sha = ""
            require_sha = (os.getenv("UPDATE_REQUIRE_SHA256", "0").strip() == "1")
            if expected_sha:
                if not verify_sha256(tmp_zip, expected_sha):
                    result["detail"] = "فشل التحقق من بصمة SHA-256 للحزمة — أُلغي التثبيت لحماية النظام"
                    return result
                result["sha256_verified"] = True
            elif require_sha:
                result["detail"] = "بصمة SHA-256 مطلوبة (UPDATE_REQUIRE_SHA256=1) ولم تتوفر في الإصدار — أُلغي التثبيت"
                return result
            if not validate_package(tmp_zip):
                result["detail"] = "الحزمة ناقصة (main.py أو fysb مفقود) — أُلغي التثبيت"
                return result
            # Save automatic pre-update rollback snapshot of current live code
            snap = create_rollback_snapshot(dest_root, result["local"] or "")
            if snap is not None:
                result["rollback_backup"] = snap.name
            result["files"] = safe_extract(tmp_zip, dest_root)
            # v7.0.1: ensure newly added requirements (e.g. Pillow) are installed
            # on existing hosts updating via /update without manual pip commands.
            try:
                import importlib.util as _ilu
                if _ilu.find_spec("PIL") is None:
                    import subprocess as _sp
                    _sp.run(
                        [sys.executable, "-m", "pip", "install", "--quiet", "Pillow>=10.4.0"],
                        stdout=_sp.DEVNULL,
                        stderr=_sp.DEVNULL,
                        timeout=60,
                        check=False,
                    )
            except Exception:
                pass
        finally:
            try:
                tmp_zip.unlink()
                tmp_dir.rmdir()
            except Exception:
                pass
        result["status"] = "updated"
        return result
    except Exception as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result


def _prepare_for_restart() -> None:
    """Flush DB state, stop tunnel child processes, and release fysb.lock cleanly before os.execve().

    Critical on Linux: os.execve() keeps the exact same PID and /proc start
    tick. Releasing fysb.lock + stopping child tunnel (ngrok/cloudflared) +
    flushing SQLite WAL / closing PG pool prevents lock self-deadlock and
    orphan tunnel processes holding Ngrok static domains.
    """
    # 0. Terminate active HTTPS tunnel child process cleanly (ngrok / cloudflared / ssh)
    try:
        if "fysb.webapp.tunnel" in sys.modules:
            from fysb.webapp import tunnel as _tun
            proc = _tun._STATE.get("proc")
            if proc is not None and getattr(proc, "returncode", None) is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
    except Exception:
        pass
    # 1. Flush SQLite WAL or close PostgreSQL connection pool (best-effort)
    try:
        if "fysb.db" in sys.modules:
            from fysb import config as _cfg, db as _db
            if getattr(_cfg, "USE_PG", False):
                from fysb import pglayer
                pglayer.close_pool()
            else:
                with _db.conn() as c:
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    # 2. Release single-instance lock file if held by our PID
    try:
        if "fysb.handlers.background" in sys.modules:
            from fysb.handlers import background as _bg
            _bg.release_lock_file()
        else:
            lf = ROOT / "fysb.lock"
            if lf.exists():
                pid_txt = lf.read_text().strip().split(":")[0]
                if not pid_txt.isdigit() or int(pid_txt) == os.getpid():
                    lf.unlink(missing_ok=True)
    except Exception:
        pass
    # 3. Flush stdio buffers
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def restart() -> None:
    """إعادة تشغيل العملية نفسها لتحميل الكود الجديد (استبدال فوري آمن للعملية)."""
    _prepare_for_restart()
    env = os.environ.copy()
    python = sys.executable or "python3"
    os.execve(python, [python, str(ROOT / "main.py")], env)


_BOOT_LOOP_WINDOW_SEC = 600


def _boot_stamp_path(root=None) -> pathlib.Path:
    return pathlib.Path(root or ROOT) / ".last_boot_update.json"


def _boot_loop_blocked(remote_tag: str, root=None) -> bool:
    """Circuit breaker: never re-apply the exact same remote tag in a tight boot loop."""
    if not remote_tag:
        return False
    sp = _boot_stamp_path(root)
    try:
        if not sp.is_file():
            return False
        data = json.loads(sp.read_text(encoding="utf-8"))
        if str(data.get("tag") or "") == str(remote_tag):
            age = time.time() - float(data.get("ts") or 0)
            if 0 <= age < _BOOT_LOOP_WINDOW_SEC:
                return True
    except Exception:
        pass
    return False


def _record_boot_stamp(remote_tag: str, root=None) -> None:
    try:
        _boot_stamp_path(root).write_text(
            json.dumps({"tag": str(remote_tag), "ts": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass


def boot_check() -> dict:
    """نقطة الإقلاع — تستدعى من main.py قبل تحميل أي كود من fysb.

    يوجد تحديث أحدث ⇒ تثبيته ثم إعادة تشغيل فورية (الدالة لا تعود).
    بلا تحديث أو عند أي فشل ⇒ سطر BOOT واضح ثم متابعة الإقلاع الطبيعي.
    حماية قاطع الدائرة (v7.4.6): يمنع حلقة التحديث اللانهائية لو تطابق التاغ خلال 10 دقائق.
    """
    t0 = time.monotonic()
    # Quick pre-check against boot-loop stamp before downloading anything
    try:
        rel_peek = latest_release()
        if rel_peek and _boot_loop_blocked(rel_peek.get("tag") or ""):
            dt = int((time.monotonic() - t0) * 1000)
            print(
                f"BOOT: UPDATE — تخطي حلقة التحديث المتكررة ({rel_peek.get('tag')}) "
                f"| {dt}ms — متابعة الإقلاع بالنسخة المثبتة"
            )
            return {"status": "loop_guarded", "remote": rel_peek.get("tag") or ""}
    except Exception:
        pass
    res = self_update()
    dt = int((time.monotonic() - t0) * 1000)
    if res["status"] == "updated":
        _record_boot_stamp(res.get("remote") or "")
        print(f"BOOT: UPDATE ✓ v{res['local']} → {res['remote']} | {res['files']} ملف | {dt}ms — إعادة تشغيل للكود الجديد...")
        sys.stdout.flush()
        restart()                            # لا يعود أبداً
    elif res["status"] == "no_update":
        print(f"BOOT: UPDATE — الأحدث {res['remote']} = المحلي v{res['local']} | {dt}ms — لا تغيير")
    else:
        print(f"BOOT: UPDATE ✗ {res['detail']} — متابعة الإقلاع بالإصدار الحالي v{res['local']}")
    return res
