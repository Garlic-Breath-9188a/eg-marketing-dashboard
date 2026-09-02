#!/usr/bin/env bash
# Cloud Agent install phase: idempotent dependency refresh after checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

# Create the venv once; reuse it on subsequent installs.
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

# Materialize a secrets.toml so the app can boot. Real values come from the
# Cloud Agent Secrets panel (injected as env vars); absent keys fall back to
# safe development defaults.
python .cursor/gen_secrets.py

echo "install.sh complete: venv ready, dependencies installed, secrets.toml generated."
