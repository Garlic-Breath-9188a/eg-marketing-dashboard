#!/usr/bin/env bash
# Cloud Agent install phase: idempotent dependency refresh after checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

# Create the venv once; reuse it on subsequent installs. The base image may not
# ship the matching python venv module (Debian/Ubuntu splits it into a separate
# package), so install it on demand if venv creation fails.
if [ ! -x ".venv/bin/python" ]; then
  if ! "$PYTHON_BIN" -m venv .venv 2>/dev/null; then
    echo "venv module missing — installing it via apt..."
    rm -rf .venv
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${PYTHON_BIN}-venv" || sudo apt-get install -y -qq python3-venv
    "$PYTHON_BIN" -m venv .venv
  fi
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
