"""Master test runner — executes every suite and writes docs/TEST_REPORT.md.

Run: python tests/run_all.py
Every PASS in the report reflects an actually executed assertion.

v6.8.2: each suite runs in its OWN subprocess. helpers.py builds a fresh
isolated temp database per process, so suites can no longer pollute each
other's DB state (cross-suite pollution produced phantom failures that the
old single-process runner misreported).
"""
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import helpers  # noqa: F401  (sets the isolated test env FIRST)

SUITES = [
    "test_instruments", "test_analysis", "test_strategies", "test_quota",
    "test_payments", "test_signal_pipeline", "test_ui_callbacks",
    "test_services", "test_royal_upgrade", "test_royal_elite_v7",
    "test_truth_layer", "test_picker_selfheal",
    "test_referrals", "test_channel_publish",
    # v6.8.2: the runner must cover EVERY suite on disk — the living-panel
    # incident hid precisely because suites were missing from this list.
    "test_admin_delete_features", "test_signal_lifecycle_fix",
    "test_live_signal_cards", "test_real_entry_protocol",
    "test_provider_and_prefs",
    "test_pg_backend", "test_pg_resilience",
]

# pytest-native suites (fixtures API): executed via `python -m pytest`.
PYTEST_SUITES = ["test_single_instance_lock"]


def _failure_ids(stream: str) -> list[str]:
    """Extract FAIL/ERROR test ids + their last traceback line."""
    out = []
    for b in re.split(r"\n(?=(?:FAIL|ERROR): )", stream):
        if b.startswith("FAIL: ") or b.startswith("ERROR: "):
            lines = b.strip().splitlines()
            head = lines[0]
            tail = lines[-1] if len(lines) > 1 else ""
            out.append(f"{head} — {tail}")
    return out


def _parse_suite_output(stream: str) -> dict:
    """Parse `python -m unittest ... -v` output into counters."""
    tests = failures = errors = skipped = 0
    ok = False
    for ln in stream.splitlines():
        m = ln.strip()
        if m.startswith("Ran "):
            try:
                tests = int(m.split()[1])
            except (IndexError, ValueError):
                pass
        elif m.startswith("FAILED ("):
            failures = int((re.search(r"failures=(\d+)", m) or [0, 0])[1] if re.search(r"failures=(\d+)", m) else 0)
            me = re.search(r"errors=(\d+)", m)
            errors = int(me.group(1)) if me else 0
            ms = re.search(r"skipped=(\d+)", m)
            skipped = int(ms.group(1)) if ms else 0
        elif m == "OK" or m.startswith("OK ("):
            ok = True
    if ok:
        failures = failures or 0
        errors = 0
    return {"tests": tests, "failures": failures, "errors": errors, "skipped": skipped}


def _parse_pytest_output(stream: str) -> dict:
    """Parse `python -m pytest -q` output into the same counters."""
    m = re.search(r"(\d+) passed", stream)
    passed_n = int(m.group(1)) if m else 0
    f = re.search(r"(\d+) failed", stream)
    e = re.search(r"(\d+) error", stream)
    s = re.search(r"(\d+) skipped", stream)
    ran = re.search(r"(\d+) items? ran", stream)
    return {
        "tests": passed_n + (int(f.group(1)) if f else 0) + (int(e.group(1)) if e else 0) + (int(s.group(1)) if s else 0),
        "failures": int(f.group(1)) if f else 0,
        "errors": int(e.group(1)) if e else 0,
        "skipped": int(s.group(1)) if s else 0,
    }


def main() -> int:
    results = {}
    for name in SUITES:
        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", f"tests.{name}", "-v"],
                capture_output=True, text=True, cwd=str(HERE.parent),
                timeout=300)
            stream = proc.stdout + proc.stderr
            code = proc.returncode
        except subprocess.TimeoutExpired as e:
            stream = (e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace")
            stream += "\nTIMEOUT after 300s"
            code = -1
        elapsed = time.time() - start
        counters = _parse_suite_output(stream)
        if counters["tests"] == 0:
            # output parse failed — fall back to exit code honesty
            counters["errors"] = counters["errors"] or 1
        results[name] = {
            **counters,
            "seconds": round(elapsed, 2),
            "exit": code,
            "details": _failure_ids(stream),
        }

    for name in PYTEST_SUITES:
        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", f"tests/{name}.py", "-q", "--no-header"],
                capture_output=True, text=True, cwd=str(HERE.parent),
                timeout=300)
            stream = proc.stdout + proc.stderr
            code = proc.returncode
        except subprocess.TimeoutExpired as e:
            stream = (e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace")
            stream += "\nTIMEOUT after 300s"
            code = -1
        elapsed = time.time() - start
        counters = _parse_pytest_output(stream)
        if counters["tests"] == 0:
            counters["errors"] = counters["errors"] or 1
        results[name] = {
            **counters,
            "seconds": round(elapsed, 2),
            "exit": code,
            "details": _failure_ids(stream),
        }

    total = sum(r["tests"] for r in results.values())
    failed = sum(r["failures"] + r["errors"] for r in results.values())
    skipped = sum(r["skipped"] for r in results.values())
    passed = total - failed - skipped
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# FOREXYEMENI — تقرير الاختبارات الفعلية",
        "",
        f"تاريخ التنفيذ: {stamp}",
        f"النتيجة الإجمالية: **{passed}/{total} ناجح** — {failed} فاشل، {skipped} مخطى",
        "",
        "| Suite | Tests | Failures | Errors | Skipped | Seconds | الحالة |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        status = "PASS" if (r["failures"] + r["errors"]) == 0 else "FAIL"
        lines.append(f"| {name} | {r['tests']} | {r['failures']} | {r['errors']} | {r['skipped']} | {r['seconds']} | {status} |")
    lines.append("\n## تفاصيل الفشل (إن وجد)\n")
    any_fail = False
    for name, r in results.items():
        if r["details"]:
            any_fail = True
            lines.append(f"### {name}")
            lines.append("```")
            lines.extend(r["details"])
            lines.append("```")
    if not any_fail:
        lines.append("لا توجد فشل — جميع الاختبارات المنفذة ناجحة.")
    lines.append("\n## ملاحظات المنهجية\n")
    lines.append("- كل حزمة اختبار تنفذ في **عملية فرعية معزولة** بقاعدة بيانات مؤقتة جديدة — لا تلوث بين الحزم.")
    lines.append("- إشارات المحرك اختُبرت ببيانات اصطناعية مؤهلة + بيانات مزود حقيقية عند توفر الشبكة.")
    lines.append("- أزرار الواجهة أُعيد إرسالها كـ callbacks حقيقية عبر الموجّه (dispatcher) مع كائنات Telegram واقعية (رسائل بمعرفات، تعديل في المكان، حذف).")
    lines.append("- لا يوجد أي اختبار PASS غير منفذ فعلياً؛ كل سطر في الجدول أعلاه نتج عن تشغيل حقيقي في هذه الجلسة.")
    report = "\n".join(lines)
    out = HERE.parent / "docs" / "TEST_REPORT.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
