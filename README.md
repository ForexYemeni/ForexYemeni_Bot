# FOREXYEMENI — منصة إشارات التداول الملكية (بوت تيليجرام)

> **FOREXYEMENI Royal Trading Signals Platform** — بوت تيليجرام فاخر لإشارات وتحليلات الفوركس والذهب،
> بيانات سوق حقيقية، حساب تجريبي كامل، نظام إحالات بتحويل موثق، ولوحة إدارة شاملة.
> مبني بالكامل على Python + python-telegram-bot + SQLite.

![Tests](https://github.com/ForexYemeni/ForexYemeni_Bot/actions/workflows/tests.yml/badge.svg)

> الشارة تعرض حال الاختبارات مباشرة من GitHub Actions عند كل تعديل.

---

## الحالة الرقمية

| المؤشر | القيمة |
|---|---|
| الاختبارات الآلية | 189/189 ناجحة (CI يشغّلها عند كل تعديل) |
| قاعدة البيانات | 29 جدولاً — SQLite WAL مع فهرسة ومعاملات ذرية |
| الاستراتيجيات المنفذة | 7 (ICT / SMC / Price Action / CRT / AMD / Turtle Soup / Silver Bullet) |
| الاعتماديات | python-telegram-bot 22.8 + python-dotenv فقط |
| اللغة | Python 100% — يعمل على Linux / Termux / أي سيرفر |

## أبرز القدرات

- **بيانات سوق حقيقية**: سلسلة مصادر متتالية مع failover تلقائي ومعايرة السعر الفوري — أسماء المصادر لا تظهر أبداً.
- **تحليلات احترافية**: سوق + إطار زمني + استراتيجية ← تحليل حقيقي على سعر السوق الفعلي.
- **إشارات بدورة حياة كاملة**: TP1/TP2/TP3/SL/EXPIRED يُتتبع من بيانات سعر حقيقية، مع تنبيه تأمين نصف الهدف الأول.
- **تنفيذ تجريبي تلقائي**: فتح الصفقة فوراً بحجم المستخدم ومخاطرته، وإغلاق 50% عند TP1 ونقل الوقف لنقطة الدخول.
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
nano .env          # أدخل BOT_TOKEN و ADMIN_ID

# 3) تشغيل بأمر واحد (venv + اعتماديات + فحوص + تشغيل)
bash INSTALL_AND_RUN.sh
```

## متغيرات البيئة (.env)

| المتغير | إلزامي | الوصف |
|---|---|---|
| `BOT_TOKEN` | نعم | توكن البوت من @BotFather |
| `ADMIN_ID` | نعم | الرقم التعريفي للمدير في تلغرام |
| `DB_PATH` | لا | مسار قاعدة البيانات (الافتراضي database/forexyemeni.db) |
| `LOG_LEVEL` | لا | مستوى السجلات (INFO) |
| `TWELVEDATA_API_KEY` | لا | مزود بيانات الفوركس (الذهب والكريبتو يعملان بدونه) |

## النشر الدائم 24/7 مجاناً

الدليل الكامل خطوة بخطوة — من فتح الحساب حتى أول إشارة: **[deploy/ORACLE_GUIDE.md](deploy/ORACLE_GUIDE.md)**
سيرفر Oracle Cloud دائم من الحصة الدائمة المجانية (Always Free)، خدمة systemd بإعادة تشغيل تلقائية عند أي انهيار، ونسخ احتياطي يومي مجدول.

## الرفع إلى GitHub

الدليل الكامل: **[deploy/GITHUB_STEPS.md](deploy/GITHUB_STEPS.md)** — أو مباشرة:

```bash
bash UPLOAD_TO_GITHUB.sh
```

## الاختبارات

```bash
python -m pytest -q        # 189/189
```

## بنية المشروع

```
main.py                نقطة الدخول (preflight + تشغيل البوت)
fysb/
  config.py            الإعدادات والأسرار من .env (لا تُطبع أبداً)
  theme.py             نظام التصميم الفاخر (قوالب/أيقونات/تنسيق)
  db.py                SQLite: WAL + migrations آمنة + فهارس + شفاء ذاتي
  instruments.py       مواصفات الرموز: Pip/Point/العقد/التنسيق
  engines/             محرك السوق، البيانات، الإشارات، التحليل، المخاطر، التتبع، الاستراتيجيات
  services/            الاشتراكات، المدفوعات، الإحالات، البث، الدعم، النسخ، التقارير، المستخدمون
  handlers/            الموجه المركزي، صفحات المستخدم، لوحات الإدارة، المدخلات، الخلفية
  ui/                  keyboards (لغة الألوان)، cards، htmlguard (درع الوسوم)
tests/                 189 اختبار انحدار وتكامل
deploy/                سكربتات النشر: install.sh + systemd + نسخ احتياطي + الأدلة
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
python-telegram-bot 22.8 + SQLite: real multi-source market data with automatic failover, a full signal
lifecycle tracker (TP/SL) with demo auto-execution, a royal referral system with conversion tracking
(campaign targets like 50 invites -> 5 paid subscribers), channel publishing with per-market posts and an
HTML tag shield, a color-language UI system, manual payments, support tickets, broadcasts, backups, and a
full admin panel. 189 automated tests pass on every push via GitHub Actions CI.

- Quick start: `cp .env.example .env` -> fill `BOT_TOKEN` / `ADMIN_ID` -> `bash INSTALL_AND_RUN.sh`
- Free 24/7 hosting guide: [deploy/ORACLE_GUIDE.md](deploy/ORACLE_GUIDE.md)
- License: proprietary — all rights reserved.
