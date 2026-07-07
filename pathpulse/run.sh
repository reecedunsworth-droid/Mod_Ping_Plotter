#!/usr/bin/env bash
#
# PathPulse launcher: bootstraps a virtualenv, installs dependencies, and
# starts the local web UI. Pass any `python -m pathpulse` flags through, e.g.
#
#     ./run.sh 8.8.8.8 1.1.1.1          # start monitoring two targets
#     sudo ./run.sh --port 9000 8.8.8.8 # full raw-ICMP detail on port 9000
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv (.venv)"
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if [ "$(id -u)" -ne 0 ]; then
  cat <<'EOF'

  NOTE: not running as root.
  PathPulse will use *unprivileged* ICMP (best-effort per-hop detail).
  For full per-hop raw-ICMP traceroute, re-run with:

      sudo ./run.sh "$@"

EOF
fi

echo "==> Starting PathPulse"
exec python -m pathpulse "$@"
