#!/usr/bin/env bash
# FOREXYEMENI — نسخة احتياطية مجدولة (يستدعيها cron يومياً 3 فجراً)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"
PY="${REPO_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY=python3
"${PY}" -c "from fysb.services.backup import quick_db_snapshot; p = quick_db_snapshot('cron'); print('backup:', p)"
