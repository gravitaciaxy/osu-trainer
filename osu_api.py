# -*- coding: utf-8 -*-
"""
Официальный API osu! v2 - только для личного тренера: закреплённые скоры профиля и старые результаты
на картах. Вход по client credentials: ключи игрок создаёт сам (osu.ppy.sh -> настройки аккаунта ->
OAuth -> новое приложение) и вставляет в тренер; хранятся только в локальном config.json.

Правила API: не больше 60 запросов в минуту - запросы идут через net.get с ограничителем osu.ppy.sh
(30 в минуту), токен переиспользуется, пока не истечёт.
"""
import datetime
import time
import urllib.parse

import config
import net

API = "https://osu.ppy.sh/api/v2/"
HEADERS = {"x-api-version": "20220705"}         # новый формат статистики: great / ok / meh / miss
_token = {"value": None, "until": 0.0}


def configured():
    c = config.load()
    return bool(str(c.get("osu_client_id") or "").strip() and str(c.get("osu_client_secret") or "").strip())


def token():
    if _token["value"] and time.time() < _token["until"] - 60:
        return _token["value"]
    c = config.load()
    try:
        cid = int(str(c.get("osu_client_id")).strip())
    except (TypeError, ValueError):
        raise RuntimeError("Client ID приложения osu! должен быть числом")
    r = net.post_json("https://osu.ppy.sh/oauth/token", {
        "client_id": cid, "client_secret": str(c.get("osu_client_secret")).strip(),
        "grant_type": "client_credentials", "scope": "public"})
    if r.status_code != 200:
        raise RuntimeError("osu! не принял ключи приложения (код %s) — проверь Client ID и Client Secret" % r.status_code)
    d = r.json()
    _token.update(value=d["access_token"], until=time.time() + int(d.get("expires_in", 3600)))
    return _token["value"]


def get(path, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    r = net.get(API + path + q, headers=dict(HEADERS, Authorization="Bearer " + token()), timeout=30)
    if r is None:
        raise RuntimeError("osu! не отвечает — попробуй позже")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("osu! ответил не JSON")


def user(name):
    name = str(name or "").strip()
    if not name:
        raise RuntimeError("Укажи ник в osu!")
    if name.isdigit():
        return get("users/%s/osu" % name)
    return get("users/%s/osu" % urllib.parse.quote(name), key="username")


def pinned(uid):
    return get("users/%d/scores/pinned" % uid, mode="osu", limit=100) or []


def best_on_map(bid, uid):
    """Лучший результат игрока на карте или None."""
    r = net.get(API + "beatmaps/%d/scores/users/%d?mode=osu" % (bid, uid),
                headers=dict(HEADERS, Authorization="Bearer " + token()), timeout=30)
    if r is None:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return d.get("score") if isinstance(d, dict) else None


def score_brief(s):
    """Результат из API в том же виде, что у тренера: точность, промахи, дата, моды."""
    st = s.get("statistics") or {}
    ended = s.get("ended_at") or s.get("created_at") or ""
    try:
        ts = datetime.datetime.fromisoformat(ended.replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = 0
    return dict(acc=round(float(s.get("accuracy") or 0), 4), misses=int(st.get("miss", st.get("count_miss", 0)) or 0),
                ts=ts, rank=s.get("rank"), mods=[m.get("acronym") for m in s.get("mods") or [] if isinstance(m, dict)],
                pp=s.get("pp"), source="osu!")
