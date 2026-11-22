#!/usr/bin/env bash
# =====================================================================
# FOREXYEMENI — تجهيز سيرفر Oracle Cloud (أو أي لينكس) بضغطة واحدة
# التشغيل من داخل مجلد المستودع:  bash deploy/install.sh
# يتولى: الحزم، البيئة الافتراضية، الاعتماديات، خدمة systemd الدائمة،
# والنسخ الاحتياطي اليومي المجدول.
# =====================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SVC_USER="$(id -un)"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO=sudo

echo "== FOREXYEMENI — تجهيز السيرفر الملكي =="
echo "المجلد: ${REPO_DIR}"

command -v python3 >/dev/null 2>&1 || {
  echo "خطأ: python3 غير مثبت — ثبّته أولاً: ${SUDO} apt install -y python3"; exit 1
}
PY_MAJOR=$(python3 -c 'import sys;print(sys.version_info[0])')
PY_MINOR=$(python3 -c 'import sys;print(sys.version_info[1])')
if [ "${PY_MAJOR}" -lt 3 ] || { [ "${PY_MAJOR}" -eq 3 ] && [ "${PY_MINOR}" -lt 10 ]; }; then
  echo "خطأ: بايثون 3.10+ مطلوب (الحالي: ${PY_MAJOR}.${PY_MINOR})"; exit 1
fi

echo "-- 1/6 الحزم الأساسية (git / venv / pip) --"
${SUDO} apt-get update -y -qq
${SUDO} apt-get install -y -qq python3-venv python3-pip git >/dev/null

echo "-- 2/6 البيئة الافتراضية والاعتماديات --"
[ -d "${REPO_DIR}/.venv" ] || python3 -m venv "${REPO_DIR}/.venv"
"${REPO_DIR}/.venv/bin/pip" install --upgrade pip -q
"${REPO_DIR}/.venv/bin/pip" install -r "${REPO_DIR}/requirements.txt" -q

echo "-- 3/6 مجلدات التشغيل و.env --"
mkdir -p "${REPO_DIR}/logs" "${REPO_DIR}/backups" "${REPO_DIR}/database"
[ -f "${REPO_DIR}/.env" ] || cp "${REPO_DIR}/.env.example" "${REPO_DIR}/.env"

echo "-- 4/6 خدمة systemd (تشغيل دائم + إعادة تشغيل تلقائية + إقلاع مع السيرفر) --"
sed -e "s#User=ubuntu#User=${SVC_USER}#g" \
    -e "s#/home/ubuntu/forexyemeni-bot#${REPO_DIR}#g" \
    "${REPO_DIR}/deploy/forexyemeni.service" \
    | ${SUDO} tee /etc/systemd/system/forexyemeni.service >/dev/null
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable forexyemeni >/dev/null

echo "-- 5/6 نسخ احتياطي يومي 3 فجراً (توقيت السيرفر) --"
CRON_LINE="0 3 * * * ${REPO_DIR}/deploy/cron-backup.sh >> ${REPO_DIR}/logs/cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v "cron-backup.sh" ; echo "${CRON_LINE}" ) | crontab -

echo "-- 6/6 جاهز --"
cat <<EOF

====================================================
 اكتمل التجهيز. الخطوات المتبقية (مرة واحدة فقط):
 1) nano ${REPO_DIR}/.env      ← أدخل BOT_TOKEN و ADMIN_ID ثم احفظ
 2) sudo systemctl start forexyemeni
 3) sudo systemctl status forexyemeni   ← يجب أن يكون active (running)
 4) افتح تلغرام وأرسل /start للبوت

 أوامر مهمة:
  السجلات المباشرة  : journalctl -u forexyemeni -f
  آخر 50 سطراً      : journalctl -u forexyemeni -n 50
  إعادة التشغيل     : sudo systemctl restart forexyemeni
  بعد أي git pull   : sudo systemctl restart forexyemeni
====================================================
EOF
