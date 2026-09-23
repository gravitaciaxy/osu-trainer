#!/usr/bin/env sh
# Выкладка сайта osu!trainer на сервер: код -> /opt/osu-trainer, перезапуск юнита, проверка.
# Запуск из папки проекта:  sh deploy/deploy.sh [ssh-хост]   (по умолчанию: vps)
set -e
HOST="${1:-vps}"
APP=/opt/osu-trainer
cd "$(dirname "$0")/.."

FILES="analyze.py config.py feedback.py i18n.py liquipedia.py net.py pools.py skills.py trainer.py ui.py index.html data"
echo "-> копирую код на $HOST:$APP"
tar czf - $FILES | ssh "$HOST" "set -e
  id osutrainer >/dev/null 2>&1 || useradd --system --home $APP --shell /usr/sbin/nologin osutrainer
  mkdir -p $APP/cache && tar xzf - -C $APP && chown -R osutrainer:osutrainer $APP"

echo "-> systemd-юнит"
scp deploy/osu-trainer.service "$HOST:/etc/systemd/system/osu-trainer.service"
ssh "$HOST" "systemctl daemon-reload && systemctl enable --now osu-trainer && systemctl restart osu-trainer"

echo "-> проверка"
sleep 2
ssh "$HOST" "curl -fsS http://127.0.0.1:3002/api/ping && echo && systemctl --no-pager --lines=5 status osu-trainer | head -n 5"
echo "Готово. nginx-конфиг (deploy/nginx-osu.conf) ставится один раз вручную - см. deploy/README.md"
