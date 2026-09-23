#!/usr/bin/env sh
cd "$(dirname "$0")"
[ -d node_modules/realm ] || ./setup.sh
exec python3 ui.py "$@"
