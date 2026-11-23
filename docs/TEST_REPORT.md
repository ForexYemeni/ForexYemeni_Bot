# FOREXYEMENI — تقرير الاختبارات الفعلية

تاريخ التنفيذ: 2026-11-23 13:31 UTC
النتيجة الإجمالية: **322/322 ناجح** — 0 فاشل، 0 مخطى

| Suite | Tests | Failures | Errors | Skipped | Seconds | الحالة |
|---|---|---|---|---|---|---|
| test_instruments | 11 | 0 | 0 | 0 | 0.07 | PASS |
| test_analysis | 12 | 0 | 0 | 0 | 0.08 | PASS |
| test_strategies | 16 | 0 | 0 | 0 | 0.12 | PASS |
| test_quota | 7 | 0 | 0 | 0 | 0.21 | PASS |
| test_payments | 12 | 0 | 0 | 0 | 0.57 | PASS |
| test_signal_pipeline | 6 | 0 | 0 | 0 | 0.29 | PASS |
| test_ui_callbacks | 18 | 0 | 0 | 0 | 1.2 | PASS |
| test_services | 12 | 0 | 0 | 0 | 0.46 | PASS |
| test_royal_upgrade | 21 | 0 | 0 | 0 | 1.12 | PASS |
| test_royal_elite_v7 | 49 | 0 | 0 | 0 | 1.83 | PASS |
| test_truth_layer | 19 | 0 | 0 | 0 | 0.24 | PASS |
| test_picker_selfheal | 6 | 0 | 0 | 0 | 0.95 | PASS |
| test_referrals | 7 | 0 | 0 | 0 | 0.14 | PASS |
| test_channel_publish | 14 | 0 | 0 | 0 | 0.88 | PASS |
| test_admin_delete_features | 11 | 0 | 0 | 0 | 3.02 | PASS |
| test_signal_lifecycle_fix | 24 | 0 | 0 | 0 | 1.02 | PASS |
| test_live_signal_cards | 16 | 0 | 0 | 0 | 0.35 | PASS |
| test_real_entry_protocol | 24 | 0 | 0 | 0 | 0.34 | PASS |
| test_provider_and_prefs | 12 | 0 | 0 | 0 | 0.14 | PASS |
| test_pg_backend | 4 | 0 | 0 | 0 | 0.09 | PASS |
| test_pg_resilience | 9 | 0 | 0 | 0 | 0.37 | PASS |
| test_single_instance_lock | 12 | 0 | 0 | 0 | 2.76 | PASS |

## تفاصيل الفشل (إن وجد)

لا توجد فشل — جميع الاختبارات المنفذة ناجحة.

## ملاحظات المنهجية

- كل حزمة اختبار تنفذ في **عملية فرعية معزولة** بقاعدة بيانات مؤقتة جديدة — لا تلوث بين الحزم.
- إشارات المحرك اختُبرت ببيانات اصطناعية مؤهلة + بيانات مزود حقيقية عند توفر الشبكة.
- أزرار الواجهة أُعيد إرسالها كـ callbacks حقيقية عبر الموجّه (dispatcher) مع كائنات Telegram واقعية (رسائل بمعرفات، تعديل في المكان، حذف).
- لا يوجد أي اختبار PASS غير منفذ فعلياً؛ كل سطر في الجدول أعلاه نتج عن تشغيل حقيقي في هذه الجلسة.