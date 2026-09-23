#!/usr/bin/env sh
# osu!trainer: install dependencies (macOS / Linux). Easier: the one-line install.sh from the README.
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Python 3.9+ is required"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Node.js 18+ is required: https://nodejs.org/"; exit 1; }
npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error
echo "Done. Run ./start.sh"
