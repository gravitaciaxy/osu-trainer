#!/usr/bin/env sh
# osu!drill - one-line installer for macOS / Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh
#
# Needs python3 (3.9+) and node (18+). Running it again updates the app; your data is kept.
set -e
REPO="gravitaciaxy/osu-trainer"
case "$(uname -s)" in
  Darwin) DIR="$HOME/Library/Application Support/osu-trainer" ;;
  *) DIR="${XDG_DATA_HOME:-$HOME/.local/share}/osu-trainer" ;;
esac
say() { printf "\033[35m[osu!drill]\033[0m %s\n" "$1"; }
command -v python3 >/dev/null 2>&1 || { say "Python 3.9+ is required: https://www.python.org/downloads/"; exit 1; }
command -v node >/dev/null 2>&1 || { say "Node.js 18+ is required: https://nodejs.org/"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { say "Python is too old (need 3.9+)"; exit 1; }
node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)' || { say "Node.js is too old (need 18+)"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "Downloading the app..."
curl -fsSL -o "$TMP/app.zip" "https://github.com/$REPO/releases/latest/download/osu-trainer.zip" 2>/dev/null ||
  curl -fsSL -o "$TMP/app.zip" "https://github.com/$REPO/archive/refs/heads/main.zip"
python3 -m zipfile -e "$TMP/app.zip" "$TMP/app"
SRC="$(find "$TMP/app" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
mkdir -p "$DIR"
cp -R "$SRC"/. "$DIR"/
chmod +x "$DIR"/*.sh
cd "$DIR"
say "Installing the osu! database module (realm)..."
npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error
say "Done. Run: \"$DIR/start.sh\""
exec "$DIR/start.sh"
