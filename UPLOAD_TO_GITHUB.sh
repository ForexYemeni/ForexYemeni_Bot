#!/usr/bin/env bash
# =====================================================================
# FOREXYEMENI — الرفع الملكي إلى GitHub (بضغطة واحدة)
# التشغيل من داخل مجلد المستودع:  bash UPLOAD_TO_GITHUB.sh
# التوكن يُستخدم هنا فقط للدفع ثم يُحذف من الإعدادات المحلية —
# لا تشاركه مع أي شخص إطلاقاً.
# =====================================================================
set -euo pipefail

echo "===================================================="
echo "  FOREXYEMENI — الرفع الملكي إلى GitHub"
echo "===================================================="

command -v git >/dev/null 2>&1 || {
  echo "خطأ: git غير مثبت. مثّله أولاً:"
  echo "  Ubuntu/سيرفر : sudo apt install -y git"
  echo "  Termux       : pkg install git"
  exit 1
}

read -rp "اسم مستخدم GitHub (افتراضي: ForexYemeni): " GH_USER
GH_USER="${GH_USER:-ForexYemeni}"
[ -n "$GH_USER" ] || { echo "خطأ: اسم المستخدم فارغ"; exit 1; }

read -rsp "Personal Access Token (لن يظهر على الشاشة): " GH_TOKEN; echo
[ -n "$GH_TOKEN" ] || {
  echo "خطأ: التوكن فارغ — أنشئه من:"
  echo "  GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)"
  echo "  → Generate new token (classic) → فعّل صلاحية repo فقط"
  exit 1
}

[ -d .git ] || git init -b main
git add -A
git -c user.name="$GH_USER" -c user.email="$GH_USER@users.noreply.github.com" \
    commit -m "FOREXYEMENI v6.4.0 — first royal upload" 2>/dev/null \
    || echo "(لا تغييرات جديدة للالتزام)"

git remote remove origin 2>/dev/null || true
git remote add origin "https://${GH_USER}:${GH_TOKEN}@github.com/${GH_USER}/ForexYemeni_Bot.git"
git push -u origin main

# حذف التوكن من إعدادات المستودع المحلية — لا يبقى مخزناً في أي ملف
git remote set-url origin "https://github.com/${GH_USER}/ForexYemeni_Bot.git"

echo "===================================================="
echo "  تم الرفع بنجاح"
echo "  المستودع: https://github.com/${GH_USER}/ForexYemeni_Bot"
echo "  تم حذف التوكن من الإعدادات المحلية — احتفظ به عندك فقط."
echo "  افتح تبويب Actions في المستودع لتشاهد الاختبارات تُنفذ الآن."
echo "===================================================="
