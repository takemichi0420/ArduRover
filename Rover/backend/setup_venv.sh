#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "python3.11 が見つかりません。先にインストールしてください:" >&2
  echo "  sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv" >&2
  exit 1
fi

PY311_VERSION="$(python3.11 - <<'PY'
import platform
print(platform.python_version())
PY
)"

if python3.11 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info.releaselevel == "final" else 1)
PY
then
  :
else
  echo "[WARN] python3.11 is not a final release: ${PY311_VERSION}" >&2
  echo "[WARN] You can continue, but final 3.11.x is recommended for production." >&2
fi

python3.11 -m venv rvenv
source rvenv/bin/activate
python -m pip install --upgrade pip
if ! pip install -r requirements.txt; then
  echo "[ERROR] pip install failed." >&2
  echo "[HINT] Check internet/DNS or use an internal PyPI mirror." >&2
  exit 1
fi
python --version

echo "[OK] backend venv is ready (rvenv)"
