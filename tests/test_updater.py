"""Self-update engine tests (v6.9.0) — version math, safe extraction, packaging
validation, the full self_update() cycle (all network mocked), boot_check()
behaviour, and the /update admin command in all four outcomes.

NOTHING here touches the real project tree or the real network: every test
extracts into a temporary directory and every GitHub call is patched.
"""
import asyncio
import io
import pathlib
import py_compile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.helpers import MockMessage
import updater
from fysb import config
from fysb.handlers.common import update_command

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADMIN_ID = 9001          # mirrors tests/helpers.py .env


def run(coro):
    return asyncio.run(coro)


def build_package(path: pathlib.Path, root_dir: str = "pkg"):
    """A realistic release zip: main.py + fysb + noise + protected runtime files."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{root_dir}/main.py", "# main\nprint('BOOT: FOREXYEMENI')\n")
        z.writestr(f"{root_dir}/fysb/__init__.py", "__version__ = '6.10.0'\n")
        z.writestr(f"{root_dir}/fysb/config.py", "VERSION = '6.10.0'\n")
        z.writestr(f"{root_dir}/requirements.txt", "python-telegram-bot==22.8\n")
        # protected runtime files must NEVER be installed over the live tree
        z.writestr(f"{root_dir}/.env", "BOT_TOKEN=SHOULD_NOT_LAND\n")
        z.writestr(f"{root_dir}/logs/fysb.log", "old log line\n")
        z.writestr(f"{root_dir}/backups/snapshot.db", "old data\n")
        z.writestr(f"{root_dir}/database/forexyemeni.db", "old data\n")
        z.writestr(f"{root_dir}/spot_calibration.json", "{}\n")
        z.writestr(f"{root_dir}/fysb/__pycache__/config.cpython-312.pyc", "stale bytecode\n")


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = type("U", (), {"id": uid})()
        self.effective_message = MockMessage()


async def _fake_sleep(_seconds):
    return None


# --- version math -------------------------------------------------------------

class TestVersionMath(unittest.TestCase):
    def test_parse_version_basic(self):
        self.assertEqual(updater.parse_version("v6.9.0"), (6, 9, 0))
        self.assertEqual(updater.parse_version("6.8.3"), (6, 8, 3))
        self.assertEqual(updater.parse_version("6.8"), (6, 8))
        self.assertEqual(updater.parse_version(""), (0,))
        self.assertEqual(updater.parse_version("abc"), (0,))
        self.assertEqual(updater.parse_version(None), (0,))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("v6.10.0", "6.9.0"))
        self.assertTrue(updater.is_newer("v7", "6.9.9"))
        self.assertFalse(updater.is_newer("v6.9.0", "6.9.0"))
        self.assertFalse(updater.is_newer("v6.8.3", "6.9.0"))
        self.assertFalse(updater.is_newer("", "6.9.0"))

    def test_config_repo_constants(self):
        self.assertEqual(config.REPO_OWNER, "ForexYemeni")
        self.assertEqual(config.REPO_NAME, "ForexYemeni_Bot")
        self.assertEqual(config.UPDATE_ASSET, "ForexYemeni1.zip")


# --- safe extraction -----------------------------------------------------------

class TestSafeExtract(unittest.TestCase):
    def test_flatten_and_preserve(self):
        pkg = pathlib.Path("/tmp/_upd_test_pkg.zip")
        dest = pathlib.Path("/tmp/_upd_test_dest")
        build_package(pkg)
        try:
            installed = updater.safe_extract(pkg, dest)
            self.assertGreaterEqual(installed, 4)
            self.assertTrue((dest / "main.py").is_file())          # flattened
            self.assertTrue((dest / "fysb/config.py").is_file())   # flattened
            self.assertFalse((dest / "pkg").exists())              # root dir collapsed
            self.assertFalse((dest / ".env").exists())             # protected
            self.assertFalse((dest / "logs").exists())             # protected tree
            self.assertFalse((dest / "backups").exists())          # protected tree
            self.assertFalse((dest / "database/forexyemeni.db").exists())
            self.assertFalse((dest / "spot_calibration.json").exists())
            self.assertFalse(list(dest.rglob("__pycache__")))      # bytecode cleared
        finally:
            pkg.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_zip_slip_blocked(self):
        evil = pathlib.Path("/tmp/_upd_test_evil.zip")
        dest = pathlib.Path("/tmp/_upd_test_evil_dest")
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr(zipfile.ZipInfo("../evil.py"), "malicious")
            z.writestr(zipfile.ZipInfo("sub/../../escape.py"), "malicious")
        try:
            with self.assertRaises(ValueError):
                updater.safe_extract(evil, dest)
            self.assertFalse((dest.parent / "evil.py").exists())
            self.assertFalse((dest.parent / "escape.py").exists())
        finally:
            evil.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


# --- package validation ---------------------------------------------------------

class TestValidatePackage(unittest.TestCase):
    def test_valid_package(self):
        pkg = pathlib.Path("/tmp/_upd_valid.zip")
        build_package(pkg)
        try:
            self.assertTrue(updater.validate_package(pkg))
        finally:
            pkg.unlink(missing_ok=True)

    def test_missing_fysb(self):
        pkg = pathlib.Path("/tmp/_upd_nofysb.zip")
        with zipfile.ZipFile(pkg, "w") as z:
            z.writestr("main.py", "# main only\n")
        try:
            self.assertFalse(updater.validate_package(pkg))
        finally:
            pkg.unlink(missing_ok=True)

    def test_garbage_file(self):
        pkg = pathlib.Path("/tmp/_upd_garbage.zip")
        pkg.write_text("this is not a zip file")
        try:
            self.assertFalse(updater.validate_package(pkg))
        finally:
            pkg.unlink(missing_ok=True)


# --- self_update full cycle (network mocked) ------------------------------------

class TestSelfUpdate(unittest.TestCase):
    def test_no_update_when_equal(self):
        with patch.object(updater, "latest_release",
                          return_value={"tag": f"v{config.VERSION}", "url": "http://x"}):
            res = updater.self_update(dest_root=pathlib.Path("/tmp/_never"))
        self.assertEqual(res["status"], "no_update")
        self.assertEqual(res["remote"], f"v{config.VERSION}")
        self.assertEqual(res["local"], config.VERSION)

    def test_no_update_when_older(self):
        with patch.object(updater, "latest_release",
                          return_value={"tag": "v6.8.3", "url": "http://x"}):
            res = updater.self_update(dest_root=pathlib.Path("/tmp/_never"))
        self.assertEqual(res["status"], "no_update")

    def test_updated_installs_new_files(self):
        pkg = pathlib.Path("/tmp/_upd_cycle.zip")
        build_package(pkg)
        dest = pathlib.Path("/tmp/_upd_cycle_dest")

        def fake_download(url, dpath, timeout=updater.TIMEOUT_ZIP):
            pathlib.Path(dpath).write_bytes(pkg.read_bytes())
            return pathlib.Path(dpath)

        try:
            with patch.object(updater, "latest_release",
                              return_value={"tag": "v6.10.0", "url": "http://x"}), \
                 patch.object(updater, "_download", side_effect=fake_download):
                res = updater.self_update(dest_root=dest)
            self.assertEqual(res["status"], "updated")
            self.assertEqual(res["remote"], "v6.10.0")
            self.assertEqual(res["local"], config.VERSION)
            self.assertGreaterEqual(res["files"], 4)
            self.assertTrue((dest / "main.py").is_file())
            self.assertTrue((dest / "fysb/config.py").is_file())
            self.assertFalse((dest / ".env").exists())
        finally:
            pkg.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_no_release_published(self):
        with patch.object(updater, "latest_release", return_value=None):
            res = updater.self_update(dest_root=pathlib.Path("/tmp/_never"))
        self.assertEqual(res["status"], "error")
        self.assertIn("لا يوجد إصدار", res["detail"])

    def test_network_failure_is_reported_not_raised(self):
        with patch.object(updater, "latest_release",
                          side_effect=RuntimeError("network down")):
            res = updater.self_update(dest_root=pathlib.Path("/tmp/_never"))
        self.assertEqual(res["status"], "error")
        self.assertIn("RuntimeError", res["detail"])
        self.assertEqual(res["local"], config.VERSION)

    def test_incomplete_package_refused(self):
        pkg = pathlib.Path("/tmp/_upd_incomplete.zip")
        with zipfile.ZipFile(pkg, "w") as z:
            z.writestr("pkg/readme.txt", "broken release payload\n")

        def fake_download(url, dpath, timeout=updater.TIMEOUT_ZIP):
            pathlib.Path(dpath).write_bytes(pkg.read_bytes())
            return pathlib.Path(dpath)

        try:
            with patch.object(updater, "latest_release",
                              return_value={"tag": "v6.10.0", "url": "http://x"}), \
                 patch.object(updater, "_download", side_effect=fake_download):
                res = updater.self_update(dest_root=pathlib.Path("/tmp/_upd_inc_dest"))
            self.assertEqual(res["status"], "error")
            self.assertIn("ناقصة", res["detail"])
            self.assertFalse(pathlib.Path("/tmp/_upd_inc_dest").exists())
        finally:
            pkg.unlink(missing_ok=True)


# --- boot_check ------------------------------------------------------------------

class TestBootCheck(unittest.TestCase):
    def test_no_update_continues_boot(self):
        with patch.object(updater, "self_update",
                          return_value={"status": "no_update", "local": "6.9.0",
                                        "remote": "v6.9.0", "detail": "", "files": 0}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                res = updater.boot_check()
        self.assertEqual(res["status"], "no_update")
        self.assertIn("BOOT: UPDATE", buf.getvalue())

    def test_error_continues_boot_with_current_build(self):
        with patch.object(updater, "self_update",
                          return_value={"status": "error", "local": "6.9.0",
                                        "remote": None, "detail": "timeout", "files": 0}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                res = updater.boot_check()
        self.assertEqual(res["status"], "error")
        self.assertIn("متابعة الإقلاع", buf.getvalue())
        self.assertIn("v6.9.0", buf.getvalue())

    def test_updated_triggers_restart(self):
        restarted = []
        with patch.object(updater, "self_update",
                          return_value={"status": "updated", "local": "6.9.0",
                                        "remote": "v6.10.0", "detail": "", "files": 42}), \
             patch.object(updater, "restart", side_effect=lambda: restarted.append(True)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                updater.boot_check()
        self.assertTrue(restarted, "boot_check must restart after installing an update")
        self.assertIn("v6.9.0 → v6.10.0", buf.getvalue())


# --- /update command ---------------------------------------------------------------

class TestUpdateCommand(unittest.TestCase):
    def test_non_admin_denied(self):
        upd = FakeUpdate(4242)
        with patch("asyncio.sleep", _fake_sleep):
            run(update_command(upd, None))
        self.assertIn("للإدارة المخولة فقط", upd.effective_message.replies[-1]["text"])
        self.assertEqual(upd.effective_message.edits, [])

    def test_admin_updated_replies_and_restarts(self):
        upd = FakeUpdate(ADMIN_ID)
        restarted = []
        # update_command does `import updater` inside the function body, so patch
        # the REAL module attributes (same module object the handler resolves).
        with patch.object(updater, "self_update",
                          return_value={"status": "updated", "local": "6.9.0",
                                        "remote": "v6.10.0", "detail": "", "files": 42}), \
             patch.object(updater, "restart", side_effect=lambda: restarted.append(True)), \
             patch("asyncio.sleep", _fake_sleep):
            run(update_command(upd, None))
        self.assertIn("تم تنزيل وتثبيت الإصدار v6.10.0", upd.effective_message.edits[-1]["text"])
        self.assertIn("/ping", upd.effective_message.edits[-1]["text"])
        self.assertTrue(restarted, "update_command must restart after a successful update")

    def test_admin_no_update_replies_calm(self):
        upd = FakeUpdate(ADMIN_ID)
        restarted = []
        with patch.object(updater, "self_update",
                          return_value={"status": "no_update", "local": "6.9.0",
                                        "remote": "v6.9.0", "detail": "", "files": 0}), \
             patch.object(updater, "restart", side_effect=lambda: restarted.append(True)):
            run(update_command(upd, None))
        self.assertIn("أحدث إصدار بالفعل", upd.effective_message.edits[-1]["text"])
        self.assertFalse(restarted, "no_update must NOT restart the process")

    def test_admin_error_replies_with_reason(self):
        upd = FakeUpdate(ADMIN_ID)
        restarted = []
        with patch.object(updater, "self_update",
                          return_value={"status": "error", "local": "6.9.0",
                                        "remote": None, "detail": "network down", "files": 0}), \
             patch.object(updater, "restart", side_effect=lambda: restarted.append(True)):
            run(update_command(upd, None))
        text = upd.effective_message.edits[-1]["text"]
        self.assertIn("تعذّر التحديث", text)
        self.assertIn("network down", text)
        self.assertFalse(restarted)


# --- atom-feed fallback (no API rate limits) ------------------------------------

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-US">
  <id>tag:github.com,2008:https://github.com/ForexYemeni/ForexYemeni_Bot/releases</id>
  <link rel="self" type="application/atom+xml" href="https://github.com/ForexYemeni/ForexYemeni_Bot/releases.atom"/>
  <link rel="alternate" type="text/html" href="https://github.com/ForexYemeni/ForexYemeni_Bot/releases"/>
  <entry>
    <id>tag:github.com,2008:Repository/999111/v6.8.4</id>
    <updated>2026-11-23T12:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/ForexYemeni/ForexYemeni_Bot/releases/tag/v6.8.4"/>
    <title>v6.8.4 — النسخة الملكية المستقرة</title>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/999111/v6.8.2</id>
    <updated>2026-11-22T09:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/ForexYemeni/ForexYemeni_Bot/releases/tag/v6.8.2"/>
    <title>v6.8.2</title>
  </entry>
</feed>"""


class _FakeResp:
    def __init__(self, payload="", status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestFeedFallback(unittest.TestCase):
    def test_parse_feed_first_tag(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(SAMPLE_ATOM)):
            tag = updater.latest_tag_from_feed("ForexYemeni", "ForexYemeni_Bot")
        self.assertEqual(tag, "v6.8.4")     # newest entry wins

    def test_fallback_to_feed_when_api_rate_limited(self):
        import urllib.error as ue
        err = ue.HTTPError("http://api", 403, "rate limit exceeded", {}, None)
        with patch.object(updater, "_get_json", side_effect=err), \
             patch.object(updater, "latest_tag_from_feed", return_value="v6.10.0"), \
             patch.object(updater, "asset_exists", return_value=True):
            rel = updater.latest_release()
        self.assertEqual(rel["tag"], "v6.10.0")
        self.assertEqual(rel["url"],
                         "https://github.com/ForexYemeni/ForexYemeni_Bot/releases/download/v6.10.0/ForexYemeni1.zip")

    def test_none_when_no_releases_anywhere(self):
        import urllib.error as ue
        err = ue.HTTPError("http://api", 404, "not found", {}, None)
        with patch.object(updater, "_get_json", side_effect=err), \
             patch.object(updater, "latest_tag_from_feed", return_value=""):
            self.assertIsNone(updater.latest_release())

    def test_none_when_feed_tag_has_no_asset(self):
        import urllib.error as ue
        err = ue.HTTPError("http://api", 403, "rate limit exceeded", {}, None)
        with patch.object(updater, "_get_json", side_effect=err), \
             patch.object(updater, "latest_tag_from_feed", return_value="v6.10.0"), \
             patch.object(updater, "asset_exists", return_value=False):
            self.assertIsNone(updater.latest_release())

    def test_asset_exists_head(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(status=200)):
            self.assertTrue(updater.asset_exists("https://github.com/x/y"))
        with patch("urllib.request.urlopen",
                   side_effect=updater.urllib.error.HTTPError("u", 404, "nf", {}, None)):
            self.assertFalse(updater.asset_exists("https://github.com/x/y"))


# --- syntax sanity for the touched entry points -------------------------------------

class TestSyntax(unittest.TestCase):
    def test_touched_files_compile(self):
        for rel in ("main.py", "updater.py", "fysb/handlers/common.py", "fysb/config.py"):
            py_compile.compile(str(ROOT / rel), doraise=True)


if __name__ == "__main__":
    unittest.main()
