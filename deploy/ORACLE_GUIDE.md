# الدليل الملكي — تشغيل البوت 24/7 مجاناً على Oracle Cloud

الوقت المطلوب: 20 دقيقة — مرة واحدة في العمر. النتيجة: بوت يعمل 24 ساعة / 7 أيام بدون توقف،
مع إعادة تشغيل تلقائية عند أي انهيار، ونسخ احتياطي يومي، وقاعدة بيانات دائمة — **بصفر تكلفة**.

> لماذا Oracle؟ الحصة «Always Free» **دائمة بلا سقف زمني** (ليست تجربة 30 يوماً)، وتشمل سيرفر لينكس
> حقيقياً وليس حاوية تنام — فالبوت يعمل بوضعه الحالي دون تعديل سطر واحد. البطاقة مطلوبة للتحقق من
> الهوية فقط — **لا خصم على الموارد المجانية**.

---

## الخطوة 1 — إنشاء الحساب (10 دقائق)

1. افتح: `cloud.oracle.com` ← **Start for free**.
2. أدخل بياناتك واختر **Home Region** الأقرب إليك: `me-dubai-1` (دبي) أو `me-jeddah-1` (جدة) — **لا يمكن تغييره لاحقاً**.
3. أدخل البطاقة (Debit مقبولة) للتحقق — قد يُخصم مبلغ رمزي مؤقتاً ثم يُلغى تلقائياً.
4. بعد تفعيل الحساب: نستخدم فقط الموارد الموسومة **Always Free** — لا تشغّل أي خدمة مدفوعة.

## الخطوة 2 — إنشاء السيرفر (5 دقائق)

1. من قائمة البurger أعلى اليسار: **Compute → Instances → Create Instance**.
2. Name: `forexyemeni`
3. Image: **Ubuntu 22.04** (أو 24.04).
4. Shape (حجم السيرفر):
   - الأولوية: **VM.Standard.A1.Flex** — اضبط 2 OCPU و 12 GB RAM (كامل الحصة الدائمة).
   - إن ظهرت رسالة `Out of capacity`: جرّب بعد ساعات، أو اختر Availability Domain آخر، أو استخدم **VM.Standard.E2.1.Micro** (AMD) — يكفي للبوت أيضاً.
5. SSH Keys: اختر **Generate a key pair** ← نزّل **المفتاحين** واحفظهما — **المفتاح الخاص هو مفتاح دخولك للسيرفر، لا تفقده ولا تشاركه مع أحد**.
6. اضغط **Create** وانتظر ~دقيقة حتى تتحول الحالة إلى RUNNING.

## الخطوة 3 — الاتصال بالسيرفر

انسخ الـ **Public IP address** من صفحة السيرفر، ثم من الطرفية (Termux على الهاتف أو Terminal على الكمبيوتر):

```bash
chmod 600 مسار_الملف/key          # مرة واحدة — المفتاح الخاص الذي نزلته
ssh -i مسار_الملف/key ubuntu@الـIP_العام
```

أول اتصال اكتب `yes` ثم Enter. الآن أنت داخل سيرفرك.

## الخطوة 4 — تحميل الكود من GitHub (المستودع الخاص)

أنشئ توكن **قراءة فقط** للسيرفر:
GitHub ← Settings ← Developer settings ← **Fine-grained tokens** ← Generate new token:
- Repository access: **Only select repositories** ← `ForexYemeni_Bot`
- Permissions ← Repository permissions ← Contents: **Read-only**
- Generate ← انسخ التوكن.

> توكن القراءة لا يكفي لتعديل أي شيء — إن أُسرِب يومناً فهو غير خطر، ويبقى على سيرفرك فقط.

على السيرفر:

```bash
sudo apt update && sudo apt install -y git
git clone https://ForexYemeni:التوكن@github.com/ForexYemeni/ForexYemeni_Bot.git
cd ForexYemeni_Bot
```

## الخطوة 5 — الإعدادات (.env) — مرة واحدة

```bash
cp .env.example .env
nano .env
```

- `BOT_TOKEN` = توكن البوت من @BotFather (لا يُرسل لأي شخص ولا يوضع في GitHub).
- `ADMIN_ID` = رقمك التعريفي (اعرفه من @userinfobot مثلاً).
- `TWELVEDATA_API_KEY` = اختياري (للفوركس؛ الذهب والكريبتو يعملان بدونه).

الحفظ: `Ctrl+O` ثم Enter ثم `Ctrl+X`.

## الخطوة 6 — التشغيل بأمر واحد

```bash
bash deploy/install.sh
sudo systemctl start forexyemeni
```

السكربت يتولى تلقائياً: الحزم، بيئة بايثون معزولة، الاعتماديات، **خدمة systemd بإعادة تشغيل
تلقائية عند أي انهيار واقلاع مع السيرفر**، و**نسخة احتياطية يومية 3 فجراً** مجدولة.

## الخطوة 7 — التحقق النهائي

```bash
sudo systemctl status forexyemeni      ← يجب: active (running) بالأخضر
journalctl -u forexyemeni -f           ← السجلات المباشرة (Ctrl+C للخروج)
```

ثم في تلغرام: أرسل **/start** ← تفتح اللوحة الملكية. البوت الآن يعمل 24/7 حتى لو أطفأت جهازك.

## التحديثات لاحقاً (عند أي إصدار جديد)

```bash
cd ~/ForexYemeni_Bot
git pull
sudo systemctl restart forexyemeni
```

## النسخ الاحتياطي والاسترجاع

- **تلقائي يومياً 3 فجراً** في مجلد `backups/` — النسخ يبدأ باسم `fysb_db_...`.
- **يدوي فوري**: أرسل `/backup` للبوت ← تصلك نسخة القاعدة في تلغرام مباشرة.
- **الاسترجاع**: من لوحة الإدارة (قسم النسخ الاحتياطي/الاستعادة) أو استبدل ملف القاعدة ثم `sudo systemctl restart forexyemeni`.

## استكشاف الأخطاء

| المشكلة | الحل |
|---|---|
| `Out of capacity` عند إنشاء السيرفر | جرّب بعد ساعات أو AD آخر أو استخدم VM.Standard.E2.1.Micro |
| البوت لا يرد في تلغرام | `sudo systemctl status forexyemeni` ثم `journalctl -u forexyemeni -n 50` |
| `401 Unauthorized` في السجلات | التوكن خاطئ — عدّل `.env` ثم `sudo systemctl restart forexyemeni` |
| `git clone` فشل | تأكد من التوكن (قراءة فقط) ومن كتابة اسم المستخدم صحيحاً |
| انقطاع إنترنت قصير | لا شيء — الخدمة تعيد التشغيل تلقائياً (Restart=always) |
| انقطاع كهرباء السيرفر | الحصة الدائمة لا تنتهي — فعّل السيرفر من لوحة Oracle إن توقف |

## أسئلة شائعة

- **هل أحتاج فتح منافذ (Ports)؟** لا — البوت يعمل بنمط polling صادر فقط، لا منافذ داخلة ولا قواعد شبكة.
- **هل المكوث الدائم مجاني فعلاً؟** نعم — Always Free بلا سقف زمني طالما الموارد ضمن الحصة.
- **هل أضبط توقيت السيرفر؟** اختياري: `sudo timedatectl set-timezone Asia/Aden` — يجعل النسخ الاحتياطي 3 فجراً بتوقيتك.
- **هل يمكن ترحيل قاعدة بياناتي القديمة؟** نعم — ارفع ملف `database/forexyemeni.db` من جهازك إلى السيرفر بنفس المسار قبل التشغيل، وسيتكفل الـ migrations بالباقي.
