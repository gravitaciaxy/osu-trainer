#!/usr/bin/env sh
# osu!trainer - установка одной командой (macOS / Linux):
#
#   curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh
#
# Нужны python3 (3.9+) и node (18+). Повторный запуск обновляет программу, данные сохраняются.
set -e
REPO="gravitaciaxy/osu-trainer"
case "$(uname -s)" in
  Darwin) DIR="$HOME/Library/Application Support/osu-trainer" ;;
  *) DIR="${XDG_DATA_HOME:-$HOME/.local/share}/osu-trainer" ;;
esac
say() { printf "\033[35m[osu!trainer]\033[0m %s\n" "$1"; }
command -v python3 >/dev/null 2>&1 || { say "Нужен Python 3.9+ / Python 3.9+ is required: https://www.python.org/downloads/"; exit 1; }
command -v node >/dev/null 2>&1 || { say "Нужен Node.js 18+ / Node.js 18+ is required: https://nodejs.org/"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { say "Python слишком старый / Python is too old (need 3.9+)"; exit 1; }
node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)' || { say "Node.js слишком старый / Node.js is too old (need 18+)"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "Скачиваю программу / downloading..."
curl -fsSL -o "$TMP/app.zip" "https://github.com/$REPO/releases/latest/download/osu-trainer.zip" 2>/dev/null ||
  curl -fsSL -o "$TMP/app.zip" "https://github.com/$REPO/archive/refs/heads/main.zip"
python3 -m zipfile -e "$TMP/app.zip" "$TMP/app"
SRC="$(find "$TMP/app" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
mkdir -p "$DIR"
cp -R "$SRC"/. "$DIR"/
chmod +x "$DIR"/*.sh
cd "$DIR"
say "Ставлю модуль для работы с базой osu! / installing dependencies..."
npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error
say "Готово / Done. Запуск / run: \"$DIR/start.sh\""
exec "$DIR/start.sh"
