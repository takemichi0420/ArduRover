#!/usr/bin/env bash
set -euo pipefail

find_python_bin() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PY_BIN="$(find_python_bin)"; then
  echo "Python 3.11 以上が見つかりません。先にインストールしてください:" >&2
  echo "  Debian trixie:   sudo apt-get update && sudo apt-get install -y python3 python3-venv" >&2
  echo "  Debian bookworm: sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv" >&2
  exit 1
fi

PY_VERSION="$("$PY_BIN" - <<'PY'
import platform
print(platform.python_version())
PY
)"

if "$PY_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info.releaselevel == "final" else 1)
PY
then
  :
else
  echo "[WARN] ${PY_BIN} is not a final release: ${PY_VERSION}" >&2
  echo "[WARN] You can continue, but a final Python release is recommended for production." >&2
fi

if ! "$PY_BIN" -m venv rvenv; then
  echo "[ERROR] Failed to create venv with ${PY_BIN}." >&2
  echo "[HINT] Install the venv package for your distro, for example:" >&2
  echo "  Debian trixie:   sudo apt-get install -y python3-venv" >&2
  echo "  Debian bookworm: sudo apt-get install -y python3.11-venv" >&2
  exit 1
fi
source rvenv/bin/activate
python -m pip install --upgrade pip
if ! pip install -r requirements.txt; then
  echo "[ERROR] pip install failed." >&2
  echo "[HINT] Check internet/DNS or use an internal PyPI mirror." >&2
  exit 1
fi
python --version

echo "[OK] backend venv is ready (rvenv)"
