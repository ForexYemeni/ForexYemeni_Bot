# FOREXYEMENI — تقرير الاختبارات الفعلية

تاريخ التنفيذ: 2026-11-23 13:01 UTC
النتيجة الإجمالية: **205/205 ناجح** — 0 فاشل

| Suite | Tests | Failures | Errors | Seconds | الحالة |
|---|---|---|---|---|---|
| test_instruments | 11 | 0 | 0 | 0.0 | PASS |
| test_analysis | 12 | 0 | 0 | 0.0 | PASS |
| test_strategies | 16 | 0 | 0 | 0.02 | PASS |
| test_quota | 7 | 0 | 0 | 0.1 | PASS |
| test_payments | 12 | 0 | 0 | 0.31 | PASS |
| test_signal_pipeline | 6 | 0 | 0 | 0.05 | PASS |
| test_ui_callbacks | 13 | 0 | 0 | 1.02 | PASS |
| test_services | 12 | 0 | 0 | 0.5 | PASS |
| test_royal_upgrade | 21 | 0 | 0 | 0.2 | PASS |
| test_royal_elite_v7 | 49 | 0 | 0 | 0.85 | PASS |
| test_truth_layer | 19 | 0 | 0 | 0.02 | PASS |
| test_picker_selfheal | 6 | 0 | 0 | 0.05 | PASS |
| test_referrals | 7 | 0 | 0 | 0.03 | PASS |
| test_channel_publish | 14 | 0 | 0 | 0.02 | PASS |

## تفاصيل الفشل (إن وجد)

لا توجد فشل — جميع الاختبارات المنفذة ناجحة.

## ملاحظات المنهجية

- الاختبارات تعمل على قاعدة بيانات مؤقتة معزولة (لا تمس قاعدة الإنتاج).
- إشارات المحرك اختُبرت ببيانات اصطناعية مؤهلة + بيانات مزود حقيقية عند توفر الشبكة.
- أزرار الواجهة أُعيد إرسالها كـ callbacks حقيقية عبر الموجّه (dispatcher) مع كائنات Telegram وهمية.
- لا يوجد أي اختبار PASS غير منفذ فعلياً؛ كل سطر في الجدول أعلاه نتج عن تشغيل حقيقي في هذه الجلسة.