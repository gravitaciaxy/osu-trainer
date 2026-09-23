#!/usr/bin/env sh
# osu!trainer: установка зависимостей (macOS / Linux). Проще: install.sh одной командой (см. README).
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Нужен Python 3.9+ / Python 3.9+ required"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Нужен Node.js 18+ / Node.js 18+ required: https://nodejs.org/"; exit 1; }
npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error
echo "Готово / Done. Run ./start.sh"
