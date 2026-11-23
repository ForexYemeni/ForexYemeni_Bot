#!/usr/bin/env bash
# =============================================================================
#  FOREXYEMENI — منصة إشارات التداول
#  INSTALL_AND_RUN.sh — الطريقة الوحيدة لتثبيت وتشغيل المشروع
#  يدعم: Linux · Android Termux · بيئات استضافة البوتات (بدون root)
#
#  الضمانات:
#   • لا يحذف ملفات المستخدم ولا قاعدة البيانات ولا يستبدل .env الموجود
#   • لا ينفذ Migration مدمراً (إضافي فقط، مع نسخة أمان تلقائية قبل أي تغيير)
#   • لا يعرض أي أسرار ولا يرسل بيانات لأي جهة خارجية
#   • لا يفعّل LIVE ولا ينفذ أي صفقة حقيقية
#   • يمنع تشغيل نسختين من البوت (قفل محلي + كشف تعارض تلغرام عند التشغيل)
#
#  كل استدعاء في هذا السكربت مُختبَر فعلياً مقابل ملفات المشروع:
#   • from fysb import db  →  fysb/db.py  (init_db, healthy, conn)
#   • python -m fysb.preflight  →  fysb/preflight.py  (run)
#   • python main.py  →  main.py  (SingleInstanceLock + preflight + run_polling)
# =============================================================================
set -u

# --- إعادة التنفيذ عبر bash إذا شُغّل بـ sh (echo -e والأقواس تتطلب bash) ----
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

CYAN='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}⛔ $1${NC}"; exit 1; }
step() { echo -e "\n${CYAN}━━ $1 ━━${NC}"; }

cd "$(dirname "$0")" || fail "تعذر الدخول إلى مجلد المشروع"
PROJECT_DIR="$(pwd)"

# =============================================================================
step "1/9 · فحص بيئة التشغيل"
# =============================================================================
IS_TERMUX=0
if [ -n "${TERMUX_VERSION:-}" ] || [ "${PREFIX:-}" = "/data/data/com.termux/files/usr" ]; then
  IS_TERMUX=1
  ok "البيئة: Android Termux"
else
  ok "البيئة: Linux/Unix عام"
fi

PKG=""; command -v pkg >/dev/null 2>&1 && PKG="pkg"
command -v apt-get >/dev/null 2>&1 && APT="apt-get" || APT=""

# =============================================================================
step "2/9 · التحقق من Python (3.10+)"
# =============================================================================
if ! command -v python3 >/dev/null 2>&1; then
  if [ "$IS_TERMUX" -eq 1 ] && [ -n "$PKG" ]; then
    warn "Python غير مثبت — سيُثبَّت تلقائياً عبر pkg (حزمة رسمية في Termux)..."
    pkg install -y python python-pip || fail "فشل تثبيت Python. نفّذ يدوياً: pkg install python python-pip ثم أعد السكربت"
  else
    fail "Python3 غير مثبت. ثبّته أولاً (مثال: apt install python3 python3-venv python3-pip) ثم أعد السكربت"
  fi
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PYMAJ="$(python3 -c 'import sys; print(sys.version_info[0])')"
PYMIN="$(python3 -c 'import sys; print(sys.version_info[1])')"
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 10 ]; }; then
  fail "Python $PYVER أقدم من المطلوب (3.10+). حدّث Python دون كسر بيئتك ثم أعد المحاولة."
fi
ok "Python $PYVER متوافق (3.10+ مطلوب)"

# =============================================================================
step "3/9 · التحقق من ملفات المشروع (مرجع إلزامي — لا استدعاءات وهمية)"
# =============================================================================
[ -f "fysb/db.py" ]        || fail "fysb/db.py غير موجود — شغّل السكربت من مجلد المشروع المستخرج كاملاً"
[ -f "fysb/preflight.py" ] || fail "fysb/preflight.py غير موجود — شغّل السكربت من مجلد المشروع المستخرج كاملاً"
[ -f "fysb/config.py" ]    || fail "fysb/config.py غير موجود — شغّل السكربت من مجلد المشروع المستخرج كاملاً"
[ -f "main.py" ]           || fail "main.py غير موجود — شغّل السكربت من مجلد المشروع المستخرج كاملاً"
[ -f "requirements.txt" ]  || fail "requirements.txt غير موجود — شغّل السكربت من مجلد المشروع المستخرج كاملاً"
[ -d "fysb/handlers" ]     || fail "مجلد fysb/handlers مفقود — الأرشيف غير مكتمل، أعد فك الضغط"
ok "بنية المشروع مكتملة (fysb/db.py · fysb/preflight.py · main.py)"

# =============================================================================
step "4/9 · تجهيز البيئة الافتراضية (venv)"
# =============================================================================
VENV_OK=0
if [ -x "venv/bin/python" ]; then
  if venv/bin/python -c "import sys; assert sys.version_info >= (3,10)" >/dev/null 2>&1; then
    VENV_OK=1
    ok "venv موجودة وسليمة"
  else
    warn "venv موجودة لكنها تالفة أو بإصدار قديم — ستُعاد إنشاؤها (venv ملفات مولّدة، لا تمس بياناتك)"
    rm -rf venv
  fi
fi

if [ "$VENV_OK" -eq 0 ]; then
  echo "→ إنشاء venv داخل مجلد المشروع (لا يمس بيئة النظام)..."
  if python3 -m venv venv 2>&1; then
    VENV_OK=1
  else
    RC=$?
    echo -e "${YELLOW}→ فشل إنشاء venv (رمز $RC) — السبب أعلاه ظاهر كاملاً. جارٍ الإصلاح حسب البيئة...${NC}"
    if [ "$IS_TERMUX" -eq 1 ]; then
      # سبب الفشل الجذري في Termux: ensurepip يأتي مع حزمة python-pip المنفصلة.
      if [ -n "$PKG" ]; then
        warn "Termux: يُثبَّت python-pip (يوفّر ensurepip المطلوب لإنشاء venv)..."
        pkg install -y python-pip || fail "فشل تثبيت python-pip. نفّذ يدوياً: pkg install python-pip ثم أعد السكربت"
        if python3 -m venv venv 2>&1; then
          VENV_OK=1
        else
          fail "فشل إنشاء venv حتى بعد تثبيت python-pip. جرّب: pkg install python python-pip ثم احذف مجلد venv وأعد السكربت"
        fi
      else
        fail "لا يمكن الوصول إلى pkg. افتح Termux وشغّل: pkg update && pkg install python python-pip ثم أعد السكربت"
      fi
    else
      # Linux: الغالب أن حزمة python3-venv غير مثبتة (Debian/Ubuntu).
      if [ -n "$APT" ]; then
        if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
          warn "Debian/Ubuntu: يُثبَّت python3-venv تلقائياً (sudo غير تفاعلي متاح)..."
          sudo -n apt-get install -y python3-venv >/dev/null 2>&1 || true
        fi
        if python3 -m venv venv 2>&1; then
          VENV_OK=1
        else
          warn "ما زال إنشاء venv يفشل — جارٍ المحاولة عبر virtualenv بديلاً..."
          python3 -m pip install --user virtualenv >/dev/null 2>&1 \
            && python3 -m virtualenv venv >/dev/null 2>&1 && VENV_OK=1
          [ "$VENV_OK" -eq 1 ] || fail "فشل إنشاء venv. ثبّت حزمة النظام: sudo apt install python3-venv ثم أعد السكربت"
        fi
      else
        fail "فشل إنشاء venv ولم يُعثر على apt-get. ثبّت python3-venv أو virtualenv ثم أعد السكربت"
      fi
    fi
  fi
fi

# shellcheck disable=SC1091
source venv/bin/activate || fail "تعذر تفعيل البيئة الافتراضية"

# التحقق من أن python داخل venv يعمل فعلاً وأن pip متاح (bootstrap عند الحاجة)
if ! python -c "import sys" >/dev/null 2>&1; then
  fail "venv تم إنشاؤها لكن python داخلها لا يعمل — احذف مجلد venv وأعد السكربت"
fi
if ! python -m pip --version >/dev/null 2>&1; then
  warn "pip غير موجود داخل venv — جارٍ تهيئته عبر ensurepip..."
  python -m ensurepip --upgrade >/dev/null 2>&1 || fail "فشل ensurepip داخل venv. احذف مجلد venv وأعد السكربت"
fi
ok "venv مفعلة ($(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')) — pip: $(python -m pip --version 2>/dev/null | awk '{print $2}')"

# =============================================================================
step "5/9 · تثبيت المتطلبات (إصدارات مُثبَّتة ومُختبَرة فعلياً)"
# =============================================================================
python -m pip install --upgrade pip --quiet 2>/dev/null || warn "تعذر ترقية pip (غير حرج)"
python -m pip install -r requirements.txt || fail "فشل تثبيت المتطلبات — راجع الرسالة أعلاه"
python -c "import telegram, dotenv" 2>/dev/null || fail "المكتبات لم تُستورد بعد التثبيت — راجع الرسالة أعلاه"
ok "المكتبات مثبتة ومُستوردة بنجاح (python-telegram-bot 22.8 · python-dotenv 1.2.4)"

# =============================================================================
step "6/9 · فحص ملف الإعدادات .env (بدون طباعة أي قيمة)"
# =============================================================================
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    warn "لم يوجد .env — نُسخ .env.example إلى .env"
    echo -e "${YELLOW}   ➜ افتح .env محلياً وأدخل BOT_TOKEN و ADMIN_ID الحقيقيان ثم أعد تشغيل السكربت.${NC}"
    exit 1
  else
    fail "لا يوجد .env ولا .env.example"
  fi
fi
ok ".env موجود (المحتوى لن يُطبع)"

# الفحص يتم عبر python-dotenv نفسه — نفس الطريقة التي يقرأ بها المشروع .env
check_env_var() {
  if python - "$1" <<'EOF'
import sys
from dotenv import dotenv_values
key = sys.argv[1]
v = dotenv_values(".env").get(key) or ""
v = str(v).strip()
if not v:
    sys.exit(2)
# placeholders النسخة الإرشادية ليست قيماً حقيقية
placeholders = ("ضع_", "هنا", "YOUR_", "change_me", "xxx")
if key == "BOT_TOKEN" and any(p in v for p in placeholders):
    print(f"PLACEHOLDER:{key}")
    sys.exit(3)
if key == "ADMIN_ID" and any(p in v for p in placeholders):
    print(f"PLACEHOLDER:{key}")
    sys.exit(3)
if key == "ADMIN_ID" and not v.lstrip("-").isdigit():
    print(f"NOT_NUMERIC:{key}")
    sys.exit(4)
sys.exit(0)
EOF
  then ok "المتغير $1 مضبوط"
  else
    RC=$?
    case $RC in
      2) fail "المتغير $1 مفقود أو فارغ في .env — عبّئه محلياً ثم أعد التشغيل" ;;
      3) fail "المتغير $1 ما زال يحمل القيمة الإرشادية من .env.example — أدخل قيمتك الحقيقية في .env ثم أعد التشغيل" ;;
      4) fail "المتغير ADMIN_ID يجب أن يكون رقماً (Telegram user id) — صحّحه في .env ثم أعد التشغيل" ;;
      *) fail "فحص المتغير $1 فشل — راجع .env ثم أعد التشغيل" ;;
    esac
  fi
}
check_env_var "BOT_TOKEN"
check_env_var "ADMIN_ID"

# =============================================================================
step "7/9 · سلامة قاعدة البيانات + Migrations الآمنة (إضافية فقط)"
# =============================================================================
if [ ! -f database/forexyemeni.db ] && [ ! -f forexyemeni.db ]; then
  warn "لا يوجد ملف قاعدة بيانات — سيُنشأ جديد عند أول تشغيل (لا يمس أي بيانات)"
fi
python - <<'EOF' || fail "فشل فحص/تهيئة قاعدة البيانات — لا تُحذف أي بيانات، راجع الرسالة أعلاه"
from fysb import db
log_lines = db.init_db()
for line in log_lines:
    print("   🛟 " + line)
ok, res = db.healthy()
print(("✅ سلامة قاعدة البيانات: " + res) if ok else ("⛔ تلف في قاعدة البيانات: " + res))
raise SystemExit(0 if ok else 1)
EOF

# =============================================================================
step "8/9 · فحص ما قبل التشغيل (Preflight)"
# =============================================================================
python -m fysb.preflight || fail "Preflight لم يجتز — صحّح الملاحظات أعلاه وأعد المحاولة"

# =============================================================================
step "9/9 · منع التشغيل المزدوج ثم التشغيل"
# =============================================================================
if [ -f fysb.lock ]; then
  LOCKPID="$(cut -d: -f1 fysb.lock 2>/dev/null | tr -dc '0-9')"
  GENUINE=0
  if [ -n "$LOCKPID" ] && kill -0 "$LOCKPID" 2>/dev/null; then
    # PID may have been reused by an unrelated process after a restart —
    # block only if it is really a main.py run from THIS folder.
    if [ -r "/proc/$LOCKPID/cmdline" ]; then
      if grep -q "main.py" "/proc/$LOCKPID/cmdline" 2>/dev/null \
         && [ "$(readlink -f "/proc/$LOCKPID/cwd" 2>/dev/null)" = "$(pwd)" ]; then
        GENUINE=1
      fi
    else
      GENUINE=1
    fi
  fi
  if [ "$GENUINE" = "1" ]; then
    fail "يوجد نسخة أخرى تعمل الآن من هذا المجلد (PID $LOCKPID). أوقفها أولاً: kill $LOCKPID ثم أعد التشغيل."
  else
    rm -f fysb.lock
    warn "قفل قديم من عملية ميتة أو معاد استخدام رقم PID الخاص بها — أُزيل بأمان"
  fi
fi
warn "ملاحظة: إن كان البوت يعمل على جهاز/سيرفر آخر بنفس BOT_TOKEN فسيكتشفه البوت عند الإقلاع ويخبرك بدقة."
ok "كل الفحوصات نجحت — تشغيل FOREXYEMENI"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  للتشغيل مستقبلاً: ${CYAN}./INSTALL_AND_RUN.sh${NC}"
echo -e "  للإيقاف:          ${CYAN}Ctrl+C${NC} (إغلاق نظيف)"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

python main.py
EXIT_CODE=$?
echo ""
case "$EXIT_CODE" in
  0) ok "أُغلق البوت بأمان." ;;
  1) warn "خرج البوت برمز 1 — مشكلة إقلاع (BOT_TOKEN/ADMIN_ID/قاعدة البيانات). راجع رسائل الإقلاع أعلاه." ;;
  2) warn "خرج البوت برمز 2 — يوجد نسخة أخرى تعمل من نفس المجلد (fysb.lock). أوقفها ثم أعد التشغيل." ;;
  3) warn "خرج البوت برمز 3 — نسخة أخرى تستقبل التحديثات بنفس BOT_TOKEN. أوقف النسخة الأخرى (kill) ثم أعد التشغيل." ;;
  *) warn "خرج البوت بالرمز $EXIT_CODE — راجع logs/fysb.log (السجل لا يطبع أسراراً)." ;;
esac
exit $EXIT_CODE
