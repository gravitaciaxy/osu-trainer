#!/bin/sh
# Подключение уведомлений обратной связи osu!trainer к Telegram. Запускать на сервере от root,
# в интерактивном терминале (токен вводится с клавиатуры и не попадает ни в историю, ни в чат).
# Команда одинаково работает из cmd, PowerShell и bash - без вложенных кавычек и $, которые
# cmd не понимает, а PowerShell 5.1 теряет при передаче аргументов в ssh:
#
#   ssh -t vps "curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/deploy/telegram-setup.sh -o osu-tg.sh && sh osu-tg.sh; rm -f osu-tg.sh"
#
# (скрипт сначала скачивается в файл: при "curl | sh" read читал бы не с клавиатуры, а из самого скрипта)
#
# Что делает: проверяет токен бота, находит твой чат (после того как ты напишешь боту),
# сохраняет токен и chat id в /etc/osu-trainer.env (только root), перезапускает сайт
# и отправляет тестовое сообщение.
set -eu

ENV_FILE=/etc/osu-trainer.env
say() { printf "\033[35m[osu!trainer]\033[0m %s\n" "$1"; }

[ "$(id -u)" = 0 ] || { say "Нужен root"; exit 1; }
command -v curl >/dev/null 2>&1 || { say "Нужен curl"; exit 1; }
command -v python3 >/dev/null 2>&1 || { say "Нужен python3"; exit 1; }

printf "Токен бота от @BotFather (ввод не отображается): "
stty -echo 2>/dev/null || true
read -r TOKEN
stty echo 2>/dev/null || true
echo
[ -n "$TOKEN" ] || { say "Токен пустой"; exit 1; }

api() { curl -fsS --max-time 20 "https://api.telegram.org/bot$TOKEN/$1"; }

BOT=$(api getMe 2>/dev/null | python3 -c 'import sys, json; print(json.load(sys.stdin)["result"]["username"])') \
    || { say "Telegram не принял токен - проверь, что скопировал его целиком"; exit 1; }
say "Бот: @$BOT"
say "Напиши боту @$BOT в Telegram любое сообщение (например /start), потом нажми Enter здесь."
read -r _

CHAT=$(api getUpdates | python3 -c '
import sys, json
updates = json.load(sys.stdin).get("result", [])
chats = [u["message"]["chat"] for u in updates
         if "message" in u and u["message"]["chat"].get("type") == "private"]
if chats:
    c = chats[-1]
    print("%s %s" % (c["id"], c.get("username") or c.get("first_name") or "?"))
')
[ -n "$CHAT" ] || { say "Сообщение боту не найдено - напиши ему и запусти скрипт ещё раз"; exit 1; }
CHAT_ID=${CHAT%% *}
say "Чат: ${CHAT#* } ($CHAT_ID)"

umask 077
printf 'TELEGRAM_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TOKEN" "$CHAT_ID" > "$ENV_FILE"
chmod 600 "$ENV_FILE"
say "Сохранено в $ENV_FILE"

systemctl restart osu-trainer
curl -fsS --max-time 20 -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
    --data-urlencode "chat_id=$CHAT_ID" \
    --data-urlencode "text=✅ osu!trainer: сообщения из формы обратной связи будут приходить сюда" >/dev/null \
    && say "Готово: тестовое сообщение отправлено в Telegram" \
    || say "Настройки сохранены, но тестовое сообщение не отправилось"
