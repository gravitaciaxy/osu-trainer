#!/bin/sh
# Установка и обновление сайта osu!trainer на сервере (Ubuntu / Debian). Запускать от root:
#
#   curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/deploy/server-install.sh | sh
#
# Что делает:
#   - код из GitHub -> /opt/osu-trainer (cache с базой и подборками при обновлении сохраняется)
#   - системный пользователь osutrainer без входа в систему
#   - systemd-юнит osu-trainer на 127.0.0.1:3002 (только Python, без Node.js)
#   - nginx: создаёт sites-available/osu, если его ещё нет; чужие конфиги не трогает,
#     перед перезагрузкой проверяет nginx -t и при ошибке откатывает свой файл
# Настройки через переменные: DOMAIN (osu.gravitacia.art), PORT (3002), APP (/opt/osu-trainer),
# REF (коммит или ветка; по умолчанию - последний коммит main)
set -eu

REPO="gravitaciaxy/osu-trainer"
DOMAIN="${DOMAIN:-osu.gravitacia.art}"
PORT="${PORT:-3002}"
APP="${APP:-/opt/osu-trainer}"

say() { printf "\033[35m[osu!trainer]\033[0m %s\n" "$1"; }

[ "$(id -u)" = 0 ] || { say "Нужен root: запусти через sudo"; exit 1; }
command -v python3 >/dev/null 2>&1 || { say "Нужен python3: apt install -y python3"; exit 1; }
command -v curl >/dev/null 2>&1 || { say "Нужен curl: apt install -y curl"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { say "Нужен Python 3.9+"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# архив конкретного коммита: архив ветки main GitHub несколько минут отдаёт из кэша
REF="${REF:-}"
if [ -z "$REF" ]; then
    REF="$(curl -fsSL -H 'Accept: application/vnd.github.sha' "https://api.github.com/repos/$REPO/commits/main" 2>/dev/null || true)"
fi
[ -n "$REF" ] || REF="refs/heads/main"
say "Скачиваю код с GitHub ($REF)..."
curl -fsSL -o "$TMP/app.zip" "https://github.com/$REPO/archive/$REF.zip"
python3 -m zipfile -e "$TMP/app.zip" "$TMP/src"
SRC="$(find "$TMP/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

id osutrainer >/dev/null 2>&1 || useradd --system --home-dir "$APP" --no-create-home --shell /usr/sbin/nologin osutrainer
mkdir -p "$APP/cache"
for f in analyze.py config.py feedback.py i18n.py liquipedia.py net.py pools.py skills.py trainer.py ui.py index.html; do
    cp "$SRC/$f" "$APP/$f"
done
rm -rf "$APP/data"
cp -R "$SRC/data" "$APP/data"
chown -R osutrainer:osutrainer "$APP"
say "Код в $APP"

say "systemd-юнит osu-trainer..."
sed -e "s#https://osu.gravitacia.art#https://$DOMAIN#" \
    -e "s#--port 3002#--port $PORT#" \
    -e "s#/opt/osu-trainer#$APP#g" \
    "$SRC/deploy/osu-trainer.service" > /etc/systemd/system/osu-trainer.service
systemctl daemon-reload
systemctl enable osu-trainer >/dev/null 2>&1
systemctl restart osu-trainer

ok=""
for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
done
if [ -z "$ok" ]; then
    say "Сервис не отвечает. Журнал:"
    journalctl -u osu-trainer -n 30 --no-pager || true
    exit 1
fi
say "Сервис работает на 127.0.0.1:$PORT"

if command -v nginx >/dev/null 2>&1; then
    SITE=/etc/nginx/sites-available/osu
    if [ -e "$SITE" ]; then
        say "nginx: $SITE уже есть - не трогаю"
    else
        sed -e "s#osu.gravitacia.art#$DOMAIN#g" -e "s#127.0.0.1:3002#127.0.0.1:$PORT#g" \
            "$SRC/deploy/nginx-osu.conf" > "$SITE"
        if [ ! -f /etc/nginx/snippets/security-headers.conf ]; then
            sed -i '/security-headers.conf/d' "$SITE"
        fi
        ln -sf "$SITE" /etc/nginx/sites-enabled/osu
        if nginx -t >/dev/null 2>&1; then
            systemctl reload nginx
            say "nginx: сайт $DOMAIN подключён"
        else
            rm -f /etc/nginx/sites-enabled/osu "$SITE"
            say "nginx -t не прошёл - свой конфиг убрал, остальные сайты не тронуты:"
            nginx -t || true
            exit 1
        fi
    fi
    if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
        say "Готово: https://$DOMAIN"
    else
        say "Готово: http://$DOMAIN"
        say "Осталось включить HTTPS: certbot --nginx -d $DOMAIN"
    fi
else
    say "nginx не найден - сайт доступен только на 127.0.0.1:$PORT, настрой прокси сам"
fi
