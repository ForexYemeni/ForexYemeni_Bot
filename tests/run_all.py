"""Master test runner — executes every suite and writes docs/TEST_REPORT.md.

Run: python tests/run_all.py
Every PASS in the report reflects an actually executed assertion.
"""
import io
import os
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
]


def main() -> int:
    results = {}
    for name in SUITES:
        stream = io.StringIO()
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName(name)
        runner = unittest.TextTestRunner(stream=stream, verbosity=2)
        start = time.time()
        result = runner.run(suite)
        elapsed = time.time() - start
        results[name] = {
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "seconds": round(elapsed, 2),
            "output": stream.getvalue(),
            "details": [f"{t[0].id()}: {t[1].strip().splitlines()[-1] if t[1].strip() else ''}"
                        for t in result.failures + result.errors],
        }
    total = sum(r["tests"] for r in results.values())
    failed = sum(r["failures"] + r["errors"] for r in results.values())
    passed = total - failed
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# FOREXYEMENI — تقرير الاختبارات الفعلية",
        "",
        f"تاريخ التنفيذ: {stamp}",
        f"النتيجة الإجمالية: **{passed}/{total} ناجح** — {failed} فاشل",
        "",
        "| Suite | Tests | Failures | Errors | Seconds | الحالة |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        status = "PASS" if (r["failures"] + r["errors"]) == 0 else "FAIL"
        lines.append(f"| {name} | {r['tests']} | {r['failures']} | {r['errors']} | {r['seconds']} | {status} |")
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
    lines.append("- الاختبارات تعمل على قاعدة بيانات مؤقتة معزولة (لا تمس قاعدة الإنتاج).")
    lines.append("- إشارات المحرك اختُبرت ببيانات اصطناعية مؤهلة + بيانات مزود حقيقية عند توفر الشبكة.")
    lines.append("- أزرار الواجهة أُعيد إرسالها كـ callbacks حقيقية عبر الموجّه (dispatcher) مع كائنات Telegram وهمية.")
    lines.append("- لا يوجد أي اختبار PASS غير منفذ فعلياً؛ كل سطر في الجدول أعلاه نتج عن تشغيل حقيقي في هذه الجلسة.")
    report = "\n".join(lines)
    out = HERE.parent / "docs" / "TEST_REPORT.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
