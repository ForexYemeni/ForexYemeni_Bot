# دليل التثبيت والتشغيل — FOREXYEMENI

## الطريقة الوحيدة المطلوبة (Linux / Termux / استضافة) — أمر واحد فقط

```bash
unzip FOREXYEMENI.zip && cd FOREXYEMENI && bash INSTALL_AND_RUN.sh
```

إذا رفض النظام التنفيذ مرة واحدة فقط نفّذ:

```bash
chmod +x INSTALL_AND_RUN.sh && ./INSTALL_AND_RUN.sh
```

السكربت ينفذ تلقائياً وبالترتيب:
1. كشف البيئة (Linux عادي / Termux عبر `TERMUX_VERSION`).
2. التحقق من إصدار Python (المطلوب ≥ 3.10، الموصى به 3.11+).
3. إنشاء بيئة افتراضية `venv` داخل مجلد المشروع (لا يفسد بيئة النظام، لا يحتاج root).
4. تثبيت المتطلبات من `requirements.txt` مع ترقية pip فقط داخل الـ venv.
5. التحقق من وجود `.env` — وإن لم يوجد ينسخ `.env.example` ويطلب تعبئة القيم محلياً (بدون طباعة أي قيمة).
6. فحص المتغيرات المطلوبة (`BOT_TOKEN`, `ADMIN_ID`) بفحص وجود فقط.
7. فحص سلامة قاعدة البيانات (`PRAGMA integrity_check`) وتشغيل Migrations الآمنة (إضافية فقط).
8. فحص ما قبل التشغيل (preflight كامل عبر `python -m fysb.preflight`).
9. التأكد من عدم وجود نسخة أخرى تعمل (قفل `fysb.lock`).
10. تشغيل البوت: `python main.py`.

إيقاف البوت: `Ctrl+C` (يُغلق بأمان ويحرر القفل ويعمل WAL checkpoint).

## متطلبات مسبقة

- Python ≥ 3.10 (على Termux: `pkg install python -y` — لا حاجة لتحديث Python إن كان 3.10+).
- اتصال إنترنت (TwelveData + Telegram).
- ملف `.env` يحتوي `BOT_TOKEN` و`ADMIN_ID` (الأصل مرفق بالأرشيف).
- اختياري للإشارات الآلية: `TWELVEDATA_API_KEY` موجود في `.env` الأصلي.

## التثبيت اليدوي (إن رغبت بفهم الخطوات)

```bash
python -m venv venv
source venv/bin/activate          # Termux: نفس الأمر
pip install -r requirements.txt
cp .env.example .env              # فقط في بيئة جديدة — لا تستبدل ملفاً موجوداً
python -m fysb.preflight          # فحص كامل قبل التشغيل
python main.py                    # التشغيل الفعلي
```

## بيئات الاستضافة

أي بيئة تدعم Python 3.10+ مع بروكسي/شبكة مفتوحة نحو `api.telegram.org` و `api.twelvedata.com`:
- شغّل نفس السكربت عبر `bash INSTALL_AND_RUN.sh` أو استخدم systemd/Termux:boot بالأمر `python main.py` داخل الـ venv.
- لا يفترض السكربت صلاحيات root ولا يثبت حزم نظام.

## استكشاف الأخطاء

| الرسالة | السبب | الحل |
|---|---|---|
| `BOT_TOKEN missing from .env` | الملف غير مكتمل | عبّئ `.env` محلياً |
| `يوجد نسخة أخرى من البوت تعمل` | قفل `fysb.lock` نشط | أوقف النسخة القديمة (أو احذف الملف إن كانت العملية ميتة) |
| `database integrity failed` | تلف ملف قاعدة البيانات | لا تحذف شيئاً — استخدم نسخة من مجلد `backups/` ثم استعادة من لوحة الإدارة |
| لا تصل إشارات آلية | Kill Switch مفعّل / سوق غير موثق / استراتيجية غير مفعلة | اتبع «خطوات ما بعد التشغيل» في README |

## ما لا يفعله السكربت عمداً

- لا يحذف ملفات المستخدم ولا قاعدة البيانات ولا يستبدل `.env` الموجود.
- لا ينفذ Migration مدمراً (كل التغييرات إضافة أعمدة/جداول فقط).
- لا يفعّل LIVE ولا ينفذ أي صفقة حقيقية.
- لا يعرض أي سر ولا يرسل بيانات المشروع لأي جهة خارجية.
