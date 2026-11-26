# FOREXYEMENI — منصة إشارات التداول الملكية (بوت تيليجرام)

> **FOREXYEMENI Royal Trading Signals Platform** — بوت تيليجرام فاخر لإشارات وتحليلات الفوركس والذهب،
> بيانات سوق حقيقية، حساب تجريبي كامل، نظام إحالات بتحويل موثق، ولوحة إدارة شاملة.
> مبني بالكامل على Python + python-telegram-bot + SQLite.

![Tests](https://github.com/ForexYemeni/ForexYemeni_Bot/actions/workflows/tests.yml/badge.svg)

> الشارة تعرض حال الاختبارات مباشرة من GitHub Actions عند كل تعديل.

---

## 🖼 تحديث v8.11.1 — مركز التشخيص الملكي بالأزرار وتعريب الأسباب

تطوير مباشر لتحديث الإنقاذ v8.11.0 بناءً على تجربة الإدارة: لوحة التشخيص صارت
مركزاً بأزرار ملكية (بطاقة مختصرة بـ«المانع الرئيسي الآن» + قرار كل سوق + سجل
المحرك بإرقام صفحات + إدارة السقف بأزرار فعلية)، وكل أسباب المحرك تُعرض
بالعربية الملكية في كل المواضع بلا إنجليزية خام.
لا تغيير لقواعد التداول أو البيانات أو الصلاحيات. للتثبيت: `/update v8.11.1` ثم `/ping`.
[تفاصيل v8.11.1](docs/RELEASE_v8.11.1.md) · [الإصلاحات الست v8.11.0](docs/RELEASE_v8.11.0.md) · [التشريح المؤسسي](docs/SIGNAL_PIPELINE_AUDIT_v8.10.1.md).

## 👑 تحديث v8.10.0 — صحة الفريمات والتتبع وحماية الإصدار

- تصحيح المحركات الـ13 وعقود الشموع والوقف والأهداف، مع وصف فعلي لكل قاعدة.
- فريم واحد متسق للسياق والرسم، وتجميع HTF ثابت، وALL مقيد بالباقة والكتالوج.
- تتبع متسق لحماية التعادل، وخصوصية الرادار، واستبعاد العدّائين المفتوحين من النتائج المغلقة.
- نموذج محاسبة جزئي معلن للإشارات الآلية الجديدة، دون إعادة كتابة التاريخ القديم.
- منع النسخ للأفكار المعلقة/غير المسلمة، وحجز محاولة الوسيط قبل الإرسال؛ UNKNOWN يحتاج reconciliation لا إعادة طلب.
- الإغلاق الآمن عند غياب خبر/سعر موثق أو توفر بيانات بديلة PROXY فقط، بدل صناعة إشارات.

من المحادثة الخاصة بحساب الإدارة، بعد نسخة بيانات مستقلة:

```text
/update v8.10.0
```

ثم `/ping` للتحقق من الإصدار بعد إعادة التشغيل. النشر على GitHub ليس تثبيتًا على استضافتك.
تبقى بياناتك وملفاتك المحلية وآلية تنبيهات التحديث والتراجع محفوظة؛ ترحيل accounting_model إضافي فقط.
**قد يقل عدد الإشارات والنسخ بسبب حمايات الرفض الجديدة. لا اعتماد Live ولا ضمان ربح.**

[ملاحظات الإصدار وحدوده والتراجع](docs/RELEASE_v8.10.0.md) · [قواعد الاستراتيجيات](docs/STRATEGIES.md) · [آلية تنبيهات التحديث](docs/UPDATE_NOTICES.md).

## الحالة الرقمية

| المؤشر | القيمة |
|---|---|
| الاختبارات الآلية | 1049 ناجحة محليًا على SQLite، 5 متخطاة و8 تحذيرات؛ لا اختبار PostgreSQL حي في هذه الجلسة |
| قاعدة البيانات | PostgreSQL حقيقية (Supabase) عبر DATABASE_URL — أو SQLite WAL محلياً — 28 جدولاً بفهرسة ومعاملات ذرية |
| الاستراتيجيات المنفذة | 13 — التفاصيل في docs/STRATEGIES.md |
| الاعتماديات | python-telegram-bot 22.8 + python-dotenv + psycopg 3 |
| اللغة | Python 100% — يعمل على Linux / Termux / أي سيرفر |

## أبرز القدرات

- **بيانات سوق حقيقية**: سلسلة مصادر متتالية مع failover تلقائي ومعايرة السعر الفوري — تسجل هوية المزود وتصنف البيانات البديلة؛ ليست كل شمعة بديلة spot مباشرًا.
- **قاعدة بيانات حقيقية 100%**: PostgreSQL (Supabase أو أي سيرفر PG) بمتغير DATABASE_URL واحد — نفس الكود يعمل على SQLite محلياً بلا أي تغيير، مع ترجمة SQL شفافة و pool اتصالات و lastrowid ونسخ احتياطي/استعادة كاملة.
- **تحليلات احترافية**: سوق + إطار زمني + استراتيجية ← تحليل حقيقي على سعر السوق الفعلي.
- **إشارات بدورة حياة كاملة**: TP1/TP2/TP3/SL/EXPIRED يُتتبع من بيانات سعر حقيقية، مع تنبيه تأمين نصف الهدف الأول.
- **تنفيذ تجريبي تلقائي**: فتح الصفقة فوراً بحجم المستخدم ومخاطرته، والتأمين الجزئي وفق إعدادات محاكاته، لا بالضرورة نموذج الإشارة نفسه.
- **نظام الإحالات الملكي**: حملات دعوات برروابط حقيقية، أهداف قابلة للضبط (مثال: 50 دعوة ← 5 مشتركين فعلاً)، تتبع التحويل آلياً داخل مسار اعتماد الاشتراك نفسه، ولوحة إدارية بأشرطة تقدم وشارات.
- **نشر القنوات**: ربط قناة بأكثر من سوق — كل سوق يُنشر برسالة مستقلة سليمة HTML مع درع وسوم وفحص صلاحيات مسبق وتشخيص دقيق للخطأ الفعلي.
- **لغة ألوان ملكية**: نقاط لونية ثابتة الدلالة (زمرد/ذهب/ياقوت/ياقوت أزرق/أرجواني/كهرمان) عبر كل لوحات المستخدم والإدارة.
- **حساب تجريبي كامل**: رصيد، مخاطرة %، لوت، سبريد، انزلاق، Equity/Drawdown — يديره المستخدم بالكامل.
- **مدفوعات يدوية**: محافظ يمنية (جوالي، ون كاش، فلو ساك، MTN Cash) + عملات رقمية + حوالات، بوسائل دفع موجهة بالأزرار وإيصالات واعتماد إداري.
- **لوحة إدارة شاملة**: مستخدمون، اشتراكات، باقات، مدفوعات، إشارات، محرك السوق، استراتيجيات، مخاطر، قنوات، إحالات، تذاكر، بث جماعي، نسخ احتياطي، تقارير.

> **منصة تحليلية وليست تنفيذاً آلياً حقيقياً**: لا يوجد أي تكامل وسيط، ووضع LIVE مغلق عمداً افتراضياً.
> كل ما يُعرض في الواجهة مأخوذ من بيانات فعلية أو حالة حقيقية — لا أرقام وهمية.

## التشغيل السريع محلياً

```bash
# 1) استنساخ
git clone https://github.com/ForexYemeni/ForexYemeni_Bot.git
cd ForexYemeni_Bot

# 2) إعداد الإعدادات
cp .env.example .env
nano .env          # أدخل BOT_TOKEN و ADMIN_ID (و DATABASE_URL لـ PostgreSQL — اختياري)

# 3) تشغيل بأمر واحد (venv + اعتماديات + فحوص + تشغيل)
bash INSTALL_AND_RUN.sh
```

## متغيرات البيئة (.env)

| المتغير | إلزامي | الوصف |
|---|---|---|
| `BOT_TOKEN` | نعم | توكن البوت من @BotFather |
| `ADMIN_ID` | نعم | الرقم التعريفي للمدير في تلغرام |
| `DATABASE_URL` | لا | رابط PostgreSQL حقيقية (Supabase) — عند تركه فارغاً يعمل البوت على SQLite محلياً |
| `DB_PATH` | لا | مسار قاعدة SQLite (الافتراضي database/forexyemeni.db) — يُهمل عند وجود DATABASE_URL |
| `LOG_LEVEL` | لا | مستوى السجلات (INFO) |
| `TWELVEDATA_API_KEY` | لا | مزود بيانات الفوركس (الذهب والكريبتو يعملان بدونه) |

## النشر الدائم 24/7 مجاناً

**الخيار الأول — bot-hosting.net (الأسهل، ربط GitHub مباشر):**
الدليل الكامل خطوة بخطوة: **[deploy/BOT_HOSTING_GUIDE.md](deploy/BOT_HOSTING_GUIDE.md)**
— سيرفر مجاني دائم بدون وضع نوم، سحب المستودع الخاص تلقائياً، تثبيت الاعتماديات آلياً،
وإعادة تشغيل تلقائية عند أي انهيار (الصيانة الوحيدة: زر تجديد كل 4 أيام).

**الخيار الثاني — Oracle Cloud Always Free (الأقوى، بلا تجديد يدوي):**
الدليل الكامل: **[deploy/ORACLE_GUIDE.md](deploy/ORACLE_GUIDE.md)** — systemd بإعادة تشغيل تلقائية ونسخ احتياطي يومي مجدول.

في كلا الخيارين: بياناتك الحقيقية تعيش على Supabase PostgreSQL عبر `DATABASE_URL` —
إعادة إنشاء السيرفر في أي لحظة لا تفقد شيئاً من المستخدمين أو الاشتراكات أو الإشارات.

## الرفع إلى GitHub

الدليل الكامل: **[deploy/GITHUB_STEPS.md](deploy/GITHUB_STEPS.md)** — أو مباشرة:

```bash
bash UPLOAD_TO_GITHUB.sh
```

## الاختبارات

```bash
python -m pytest -q        # 189/189 على SQLite (CI)
# + 4 اختبارات حية على PostgreSQL عند تعيين FY_TEST_DATABASE_URL
```

## بنية المشروع

```
main.py                نقطة الدخول (preflight + تشغيل البوت)
fysb/
  config.py            الإعدادات والأسرار من .env (لا تُطبع أبداً)
  theme.py             نظام التصميم الفاخر (قوالب/أيقونات/تنسيق)
  db.py                طبقة البيانات: PostgreSQL (pglayer) أو SQLite WAL — نفس API
  pglayer.py           محرك PostgreSQL: ترجمة SQL شفافة + pool + نسخ/استعادة JSON
  instruments.py       مواصفات الرموز: Pip/Point/العقد/التنسيق
  engines/             محرك السوق، البيانات، الإشارات، التحليل، المخاطر، التتبع، الاستراتيجيات
  services/            الاشتراكات، المدفوعات، الإحالات، البث، الدعم، النسخ، التقارير، المستخدمون
  handlers/            الموجه المركزي، صفحات المستخدم، لوحات الإدارة، المدخلات، الخلفية
  ui/                  keyboards (لغة الألوان)، cards، htmlguard (درع الوسوم)
tests/                 189 اختبار SQLite + 4 اختبارات حية PostgreSQL
deploy/                سكربتات النشر: bot-hosting + install.sh + systemd + الأدلة
```

## الأمان

- الأسرار تعيش في `.env` فقط ولا تُطبع ولا تُسجل أبداً (مفحوص آلياً في الاختبارات).
- `.gitignore` يمنع رفع `.env` وقاعدة البيانات والسجلات والنسخ الاحتياطية نهائياً.
- التحقق الإداري يتم على الخادم في الموجه وفي كل صفحة إدارية.
- كل نصوص المستخدم تمر بدالة تنقية HTML قائمة بيضاء.

## الترخيص

جميع الحقوق محفوظة — انظر [LICENSE](LICENSE).

---

## English (summary)

**FOREXYEMENI** is a luxurious Telegram trading-signals platform (forex & gold) built in pure Python on
python-telegram-bot 22.8: real multi-source market data with automatic failover, a full signal
lifecycle tracker (TP/SL) with demo auto-execution, a royal referral system with conversion tracking
(campaign targets like 50 invites -> 5 paid subscribers), channel publishing with per-market posts and an
HTML tag shield, a color-language UI system, manual payments, support tickets, broadcasts, backups, and a
full admin panel. The data layer runs on real PostgreSQL (Supabase) via DATABASE_URL — transparent
SQLite->PostgreSQL SQL translation, connection pooling, lastrowid/RETURNING support and JSON
backup/restore — or falls back to local SQLite WAL with zero call-site changes. 189 automated tests
pass on every push via GitHub Actions CI (+4 live PostgreSQL checks when FY_TEST_DATABASE_URL is set).

- Quick start: `cp .env.example .env` -> fill `BOT_TOKEN` / `ADMIN_ID` -> `bash INSTALL_AND_RUN.sh`
- Free 24/7 hosting guide: [deploy/ORACLE_GUIDE.md](deploy/ORACLE_GUIDE.md)
- License: proprietary — all rights reserved.
