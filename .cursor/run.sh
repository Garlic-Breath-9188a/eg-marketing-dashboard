#!/usr/bin/env bash
# Cloud Agent terminal: run the Streamlit dashboard.
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

# Refresh secrets.toml from the current environment on every boot so newly
# added Cloud Agent secrets take effect without reinstalling.
python .cursor/gen_secrets.py

exec streamlit run app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true
