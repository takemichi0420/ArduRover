#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"
OUT_DIR="${SCRIPT_DIR}/png"

if ! command -v npx >/dev/null 2>&1; then
  echo "npx not found. Install Node.js/npm first." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

render_one() {
  local name="$1"
  echo "render: ${name}.mmd -> ${name}.png"
  npx -y @mermaid-js/mermaid-cli \
    -i "${SRC_DIR}/${name}.mmd" \
    -o "${OUT_DIR}/${name}.png" \
    -w 1920 \
    -H 1080 \
    -t neutral \
    -b white
}

for name in \
  01_architecture \
  02_workflow \
  03_troubleshooting \
  04_network_policy \
  05_systemd_boot \
  06_latency_path
do
  render_one "${name}"
done

echo "done: ${OUT_DIR}"
