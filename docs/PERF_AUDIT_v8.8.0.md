# 👑 ForexYemeni — تقرير تشخيص الأداء الشامل (Performance Audit)

**الإصدار المفحوص:** v8.8.0 · **الفرع:** `arena/66eb559d-forexyemeni-bot`
**تاريخ الفحص:** 2026-11-24 · **الطريقة:** قياس فعلي (benchmark + profiling + FD/ast analysis) — لا تخمين.

قاعدة بيانات محاكاة واقعية للإنتاج: 8,000 إشارة · 3,000 مستخدم · 199,164 تسليمة · 30,000 سجل محرك · حجم 32 MB.

---

## ملخص ترتيب الأقسام من الأبطأ إلى الأسرع

| # | القسم | القياس الفعلي | السبب الجذري |
|---|---|---|---|
| 1 | `db.healthy()` | **616 ms** @32MB | `PRAGMA integrity_check(5)` يفحص كل صفحات DB |
| 2 | `/api/admin/overview` | **315–449 ms** · 112 اتصال DB · 61 KB | 112× `db.conn()` + digest غير مُخزَّن + مسح كامل |
| 3 | `/api/admin/signals` | **305–328 ms** · 121 اتصال · 106 KB | `seq_of()` ×60 + تصنيف 8,000 صف لعرض 60 |
| 4 | `emit_to_users` | **40.3 s لـ1000 مستخدم** | حلقة تسلسلية: DB + RTT تلغرام لكل مستخدم |
| 5 | `digest.build_digest_data` | **67–73 ms** | `list_quarantined_strategies` = 13 مسح كامل + 30 اتصال |
| 6 | `tracker.check_open_signals` | 60s+ مع 50 إشارة | `_delivery_refs` ×2 بدون فهرس (10.65ms) + جلب شبكي تسلسلي |
| 7 | `market_engine_loop` | **240 s نوم صافٍ/دورة** | `MARKET_SYMBOL_DELAY_SECONDS=6` × 40 فحصاً |
| 8 | `render_signal_chart` | **87 ms/شارت** | 7 تحميلات TTF + مسار PIL بدل stdlib (22ms) |
| 9 | `_safe_send/_safe_edit` | 10.1 s لـ200 رسالة | `asyncio.sleep(0.05)` إجباري + تسلسل |
| 10 | thread pool | **6 خيوط فقط** (2 vCPU) | لا `set_default_executor` في `main.py` |

---

## 1️⃣ القيد العام: مجمع الخيوط = 6 فقط

`afn.run()` و `asyncio.to_thread` كلها تصطف على 6 خيوط (min(32, 2+4)).
- 12 مهمة متزامنة (1s) → **2.00s** بدل 1.00s (تضاعف).
- 8 طلبات `/api/admin/overview` (0.3s منفردة) → **3.76s** · 470ms لكل طلب.
- **التأثير:** كل طلب واجهة يتنافس مع مهام تلغرام/المحرك على 6 خيوط.

## 2️⃣ فوضى اتصالات SQLite

- **523** موضع `db.conn()` — كل واحد يفتح اتصالاً جديداً + 3 PRAGMAs = **0.87 ms** (الاتصال وحده 0.03ms → **29×**).
- `with db.conn()` لا يُغلق المؤشر أبداً (`sqlite3.Connection.__exit__` يكتفي بـ commit).
  - 500 استدعاء → **107 FD مفتوحة** · 3,000 → 124 · `ulimit -n = 1024`.
- `/api/admin/overview` = **112 اتصالاً لكل طلب** · `/api/admin/signals` = 121.
- **على PostgreSQL/Supabase:** 112 رحلة WAN × ~150ms = **~17 ثانية** لفتح لوحة الإدارة.

## 3️⃣ لوحة الإدارة

### `_query_admin_signals` = 224 ms
- `SELECT * FROM signals ORDER BY id DESC` → **SCAN كامل** (44ms @8k صف، ينمو خطياً).
- `seq_of()` × 60 = `SELECT COUNT(*) ... WHERE id<=?` → **118 ms** من أصل 224.
- **8,000** استدعاء لـ `_classify_signal_outcome` و `signal_creator_label` لعرض 60 صفاً فقط.

### `digest.build_digest_data(7)` = 67–73 ms
- `list_quarantined_strategies()` = **13× `strategy_quarantine_status()`** كل واحدة مسح كامل على جدول الإشارات = **68 ms**.
- **30** فتح اتصال لتقرير واحد.

## 4️⃣ توصيل الإشارات `emit_to_users`

لكل مستخدم داخل الحلقة تسلسلياً:
`try_consume_signal` (BEGIN IMMEDIATE + قفل كتابة) → `personal_block` (قراءات DB) → `await bot_send` (RTT) → `record_delivery_refs` (UPDATE).

| المستلمون | تسلسلي (الواقع) | متوازٍ محدود(8) | التسريع |
|---|---|---|---|
| 100 | 4.0 s | 0.5 s | 7.6× |
| 500 | 20.2 s | 2.5 s | 7.9× |
| 1,000 | 40.3 s | 5.1 s | 8.0× |

`BEGIN IMMEDIATE` لكل مستخدم + `busy_timeout=15000` = أي تنافس يصبح توقفاً 15 ثانية.

## 5️⃣ رادار التتبع `tracker_loop` (كل 60 ثانية)

- `SELECT * FROM signals WHERE status IN (...)` = 16.8 ms لـ3,982 إشارة، **بلا حد أعلى**.
- **جلب شبكي تسلسلي لكل إشارة** (200 شمعة 5min) — 50 إشارة = 30–75 ثانية.
- `seq_of()` = 1.7 ms لكل إشارة (مسح كامل).
- `_delivery_refs()` **مرتين** لكل إشارة، والاستعلام على `signal_deliveries.signal_id` **بلا فهرس**:
  - **10.65 ms → 0.04 ms بفهرس** = **266× أسرع** (مُقاس).
- التعديلات تُرسل تسلسلياً مع `sleep(0.05)` إجباري.

## 6️⃣ محرك السوق

- `MARKET_SYMBOL_DELAY_SECONDS=6` **بين كل رمز×إطار**: 20 سوقاً × 2 إطار × 6s = **240 ثانية نوم صافٍ** لكل دورة.
- تسلسلي تماماً: `await asyncio.to_thread(generate_signal, …)` واحداً بعد الآخر.
- `generate_signal` = 2–4 طلبات HTTP + 4–5 اتصالات DB لكل رمز/إطار.
- **بلا single-flight:** 10 استدعاءات متزامنة لنفس الزوج → **40 ضربة لمزود البيانات** بدل 1 (قطيع راعد) → حظر المزود → سلاسل failover بطيئة.
- `engine_runs` بلا تقليم تلقائي: **11,520 صفاً/يوم = 4.2 مليون/سنة**. `_engine_runs_clean` موجود لكن **يُستدعى بزر إدارة فقط**.

## 7️⃣ خادم الميني‑آب

- **بلا gzip:** 83 KB (مستخدم) و90 KB (إدارة) تُخدَم خاماً → **23/24 KB بضغط** (توفير 73–74%).
- **`Connection: close`** على كل رد → مصافحة TCP/TLS جديدة لكل طلب API.
- `Cache-Control: no-store` حتى على هيكل التطبيق الثابت.

## 8️⃣ الشارتات

- `render_signal_chart` = **87 ms** · المسار البديل `_render_board_stdlib` = **22 ms** (4× أسرع).
- **7 تحميلات خط TTF لكل شارت** — بلا cache، و20 موضع `_load_font`.
- `build_signal_chart_bundle` يجلب شموعاً من الشبكة **قبل كل رسم**، ثم يُرسل N صورة تسلسلياً.

## 9️⃣ متفرقات مؤثرة

- `_safe_send` / `_safe_edit`: `asyncio.sleep(0.05)` **حتى عند النجاح** → 10.1s لكل 200 رسالة.
- `poll_active_monitors`: `has_access(uid)` + `db.conn()` لكل مراقب (حتى 60) = 180+ اتصالاً/دورة.
- لا `PRAGMA optimize` ولا `ANALYZE` → مخطط استعلامات SQLite بلا إحصاءات.
- بلا تقليم لـ `signal_deliveries` (199k صف) و`signals` (ينمو بلا حد).

---

## خطة الإصلاح المقترحة (بالأثر)

**المستوى 1 — أثر فوري ×8–266:**
1. `set_default_executor(32)` + عزل مجمع للواجهة.
2. فهرس `signal_deliveries(signal_id)` + `signals(created_at)`: 266× على الاستعلامات الساخنة.
3. `db.conn()`: PRAGMAs مرة واحدة لكل مسار + إغلاق حقيقي + pool للقراءة.
4. `db.healthy()`: استبدال `integrity_check` بـ `quick_check` محدود ومُخزَّن مؤقتاً.
5. توازٍ محدود في `emit_to_users` (8–12) → 40s ← 5s.
6. gzip + keep-alive في خادم الميني‑آب → 73% أقل بيانات.

**المستوى 2 — هيكلي:**
7. `seq_of` بدفعة واحدة (نافذة ranks) بدل 60 استعلاماً.
8. `_query_admin_signals`: تصفية في SQL + تصنيف الصفوف المعروضة فقط.
9. `list_quarantined_strategies`: استعلام تجميعي واحد (`GROUP BY strategy`) بدل 13 مسحاً.
10. single-flight لـ `fetch_candles` + إزالة `sleep(6)` لصالح توازٍ محدود.
11. cache للخطوط + مسار الرسم الأسرع.
12. تقليم تلقائي لـ `engine_runs`/`signal_deliveries` + `PRAGMA optimize` دوري.

**المستوى 3 — سلوكي:**
13. `_safe_send` بلا نوم عند النجاح (خنق تكيفي فقط عند 429).
14. `has_access` بدفعة واحدة في `poll_active_monitors`.
15. توثيق `DATABASE_URL` = أسرع خيار للإنتاج (PG pooled) بدل SQLite المحلية على استضافة مشتركة.
