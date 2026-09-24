# -*- coding: utf-8 -*-
"""
Обратная связь: проверка сообщения, хранение на сервере (cache/feedback.jsonl)
и уведомление владельцу в Telegram.

Токен и чат берутся из переменных окружения TELEGRAM_TOKEN и TELEGRAM_CHAT_ID
(на сервере - файл /etc/osu-trainer.env, его пишет deploy/telegram-setup.sh).
Локальная программа своих токенов не хранит: она пересылает сообщение на сайт.
"""
import collections
import hashlib
import json
import os
import secrets
import threading
import time

import config
import net

FILE = os.path.join(config.CACHE_DIR, "feedback.jsonl")
SALT_FILE = os.path.join(config.CACHE_DIR, "feedback.salt")
KINDS = {"idea": "💡 Идея", "bug": "🐞 Ошибка", "maps": "🎯 Карты не подошли", "other": "💬 Другое"}
CONTEXT_KEYS = ("mode", "lang", "version", "share", "params")
PER_IP_PER_HOUR = 5
PER_DAY = 300

_lock = threading.Lock()
_ip_hits = collections.defaultdict(collections.deque)
_day_hits = collections.deque()


def clean(body):
    """Приводит сообщение к безопасному виду. ValueError - если текста нет."""
    text = str(body.get("text") or "").strip()[:3000]
    if len(text) < 3:
        raise ValueError("empty")
    kind = body.get("kind") if body.get("kind") in KINDS else "other"
    ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
    context = {k: str(ctx[k])[:400] for k in CONTEXT_KEYS if ctx.get(k)}
    return dict(kind=kind, text=text, contact=str(body.get("contact") or "").strip()[:200], context=context)


def allow(ip):
    """Лимит: не больше PER_IP_PER_HOUR сообщений в час с адреса и PER_DAY в сутки всего."""
    now = time.time()
    with _lock:
        hits = _ip_hits[ip]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        while _day_hits and now - _day_hits[0] > 86400:
            _day_hits.popleft()
        if len(hits) >= PER_IP_PER_HOUR or len(_day_hits) >= PER_DAY:
            return False
        hits.append(now)
        _day_hits.append(now)
        return True


def _ip_hash(ip):
    """Настоящие адреса не храним: только хэш с солью, чтобы отличать повторы и спам."""
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    if not os.path.exists(SALT_FILE):
        with open(SALT_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(16))
    with open(SALT_FILE, encoding="utf-8") as f:
        salt = f.read().strip()
    return hashlib.sha256((salt + ip).encode("utf-8")).hexdigest()[:12]


def save(entry, ip):
    record = dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S"), ip=_ip_hash(ip))
    with _lock:
        with open(FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def telegram_text(entry):
    ctx = entry["context"]
    lines = ["%s · osu!drill" % KINDS[entry["kind"]], "", entry["text"], ""]
    lines.append("Контакт: %s" % (entry["contact"] or "—"))
    lines.append(" · ".join(x for x in (ctx.get("mode"), ctx.get("lang"),
                                        "v" + ctx["version"] if ctx.get("version") else "") if x))
    if ctx.get("share"):
        lines.append("Подборка: %s/s/%s" % (config.SITE_URL, ctx["share"]))
    if ctx.get("params"):
        lines.append("Параметры: %s" % ctx["params"])
    return "\n".join(lines)[:4000]


def notify(entry):
    """Отправка в Telegram в фоне; если бот не настроен - сообщение просто остаётся в файле."""
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return

    def send():
        try:
            r = net.post_json("https://api.telegram.org/bot%s/sendMessage" % token,
                              {"chat_id": chat, "text": telegram_text(entry),
                               "disable_web_page_preview": True})
            if r.status_code != 200:
                print("[feedback] Telegram ответил %s" % r.status_code, flush=True)
        except OSError as e:
            print("[feedback] Telegram недоступен: %s" % e.__class__.__name__, flush=True)

    threading.Thread(target=send, daemon=True).start()


def forward(entry):
    """Локальная программа: пересылает сообщение на сайт. Возвращает HTTP-код ответа сайта."""
    try:
        r = net.post_json(config.SITE_URL.rstrip("/") + "/api/feedback", entry,
                          headers={"X-Requested-With": "osu-trainer"})
        return r.status_code
    except OSError:
        return 502
