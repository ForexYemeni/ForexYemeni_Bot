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
PRESERVE_EXACT = {".env", "fysb.lock", ".DS_Store", "spot_calibration.json"}
PRESERVE_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".log", ".pyc")
PRESERVE_DIRS = {"logs", "backups", "__pycache__", ".git", ".pytest_cache", "node_modules"}

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


# --- GitHub Releases ---------------------------------------------------------

def latest_tag_from_feed(owner, repo):
    """آخر تاغ من تغذية releases.atom — بلا حدود طلبات API (مسار احتياطي)."""
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    req = urllib.request.Request(url, headers={"User-Agent": _headers()["User-Agent"]})
    with urllib.request.urlopen(req, timeout=TIMEOUT_JSON, context=_ssl_context()) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    m = re.search(r"releases/tag/([^\"<>]+)\"", text)
    return m.group(1) if m else ""


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
        tag = (data.get("tag_name") or "").strip()
        asset_url = ""
        for asset in data.get("assets") or []:
            if asset.get("name") == asset_name:
                asset_url = asset.get("browser_download_url") or ""
                break
        if tag and asset_url:
            return {"tag": tag, "url": asset_url, "name": asset_name}
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


# --- استخراج آمن --------------------------------------------------------------

def _zip_root_prefix(names) -> str:
    """إن كانت كل الملفات داخل مجلد جذر واحد، تُعاد بادئته للتسطيح."""
    tops = set()
    for n in names:
        if "/" in n:
            tops.add(n.split("/", 1)[0])
    if len(tops) == 1 and any("/" in n for n in names):
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


# --- الدورة الكاملة ----------------------------------------------------------

def self_update(dest_root=None) -> dict:
    """فحص ← تنزيل ← تثبيت. يعيد تقريراً بلا أي استثناءات."""
    result = {"status": "error", "local": None, "remote": None, "detail": "", "files": 0}
    dest_root = pathlib.Path(dest_root) if dest_root else ROOT
    try:
        from fysb import config
        result["local"] = config.VERSION
    except Exception:
        pass
    try:
        rel = latest_release()
        if not rel:
            result["detail"] = "لا يوجد إصدار منشور على GitHub Releases"
            return result
        result["remote"] = rel["tag"]
        if not is_newer(rel["tag"], result["local"] or "0"):
            result["status"] = "no_update"
            return result
        tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="fysb_upd_"))
        tmp_zip = tmp_dir / "update.zip"
        _download(rel["url"], tmp_zip)
        try:
            if tmp_zip.stat().st_size > MAX_ZIP_BYTES:
                result["detail"] = "حجم الحزمة أكبر من الحد المسموح — أُلغي التثبيت"
                return result
            if not validate_package(tmp_zip):
                result["detail"] = "الحزمة ناقصة (main.py أو fysb مفقود) — أُلغي التثبيت"
                return result
            result["files"] = safe_extract(tmp_zip, dest_root)
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


def restart() -> None:
    """إعادة تشغيل العملية نفسها لتحميل الكود الجديد (استبدال فوري للعملية)."""
    env = os.environ.copy()
    python = sys.executable or "python3"
    os.execve(python, [python, str(ROOT / "main.py")], env)


def boot_check() -> dict:
    """نقطة الإقلاع — تستدعى من main.py قبل تحميل أي كود من fysb.

    يوجد تحديث أحدث ⇒ تثبيته ثم إعادة تشغيل فورية (الدالة لا تعود).
    بلا تحديث أو عند أي فشل ⇒ سطر BOOT واضح ثم متابعة الإقلاع الطبيعي.
    """
    t0 = time.monotonic()
    res = self_update()
    dt = int((time.monotonic() - t0) * 1000)
    if res["status"] == "updated":
        print(f"BOOT: UPDATE ✓ v{res['local']} → {res['remote']} | {res['files']} ملف | {dt}ms — إعادة تشغيل للكود الجديد...")
        sys.stdout.flush()
        restart()                            # لا يعود أبداً
    elif res["status"] == "no_update":
        print(f"BOOT: UPDATE — الأحدث {res['remote']} = المحلي v{res['local']} | {dt}ms — لا تغيير")
    else:
        print(f"BOOT: UPDATE ✗ {res['detail']} — متابعة الإقلاع بالإصدار الحالي v{res['local']}")
    return res
