# -*- coding: utf-8 -*-
"""
Официальный API osu! v2 - только для личного тренера: закреплённые скоры профиля, старые результаты
на картах и полный список результатов игрока. Вход по client credentials: ключи игрок создаёт сам
(osu.ppy.sh -> настройки аккаунта -> OAuth -> новое приложение) и вставляет в тренер; хранятся только
в локальном config.json.

Правила API: не больше 60 запросов в минуту - запросы идут через net.get с отдельным ограничителем
«osu-api» (50 в минуту), токен переиспользуется, пока не истечёт.
"""
import datetime
import time
import urllib.parse

import config
import net

API = "https://osu.ppy.sh/api/v2/"
HEADERS = {"x-api-version": "20220705"}         # новый формат статистики: great / ok / meh / miss
BUCKET = "osu-api"
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


def _auth():
    return dict(HEADERS, Authorization="Bearer " + token())


def get(path, **params):
    q = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
    r = net.get(API + path + q, headers=_auth(), timeout=30, bucket=BUCKET)
    if r is None:
        raise RuntimeError("osu! не отвечает — попробуй позже")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("osu! ответил не JSON")


def post(path, payload):
    """POST с тем же ограничителем, что у get. None - osu! не ответил или отказал."""
    bucket = net.BUCKETS[BUCKET]
    for attempt in range(3):
        bucket.wait()
        try:
            r = net.post_json(API + path, payload, headers=_auth(), timeout=30)
        except OSError:
            time.sleep(1.0 + attempt)
            continue
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After") or 5)
            except (TypeError, ValueError):
                wait = 5
            bucket.pause(min(max(wait, 5), 120))
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def user(name):
    name = str(name or "").strip()
    if not name:
        raise RuntimeError("Укажи ник в osu!")
    if name.isdigit():
        return get("users/%s/osu" % name)
    return get("users/%s/osu" % urllib.parse.quote(name), key="username")


def pinned(uid):
    return get("users/%d/scores/pinned" % uid, mode="osu", limit=100) or []


def most_played(uid):
    """Все карты, которые игрок запускал хоть раз, со счётчиком попыток (по 100 за запрос)."""
    out, offset = [], 0
    while True:
        page = get("users/%d/beatmapsets/most_played" % uid, limit=100, offset=offset) or []
        out += page
        if len(page) < 100:
            return out
        offset += 100


def map_scores(bid, uid):
    """Все сохранённые результаты игрока на карте: [] - результатов нет, None - osu! не ответил."""
    r = net.get(API + "beatmaps/%d/scores/users/%d/all?ruleset=osu" % (bid, uid), headers=_auth(), timeout=30,
                bucket=BUCKET, missing_ok=True)
    if r is None:
        return None
    if r.status_code == 404:
        return []
    try:
        d = r.json()
    except ValueError:
        return None
    return (d.get("scores") or []) if isinstance(d, dict) else None


def best_on_map(bid, uid):
    """Лучший результат игрока на карте или None."""
    r = net.get(API + "beatmaps/%d/scores/users/%d?mode=osu" % (bid, uid), headers=_auth(), timeout=30, bucket=BUCKET)
    if r is None:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return d.get("score") if isinstance(d, dict) else None


def beatmaps(ids):
    """Сведения о картах по 50 за запрос (md5, max combo, звёзды): {bid: карта}."""
    ids, out = list(ids), {}
    for i in range(0, len(ids), 50):
        d = get("beatmaps", **{"ids[]": ids[i:i + 50]})
        for b in (d or {}).get("beatmaps") or []:
            out[b["id"]] = b
    return out


def star_rating(bid, mods):
    """Звёзды карты с модами (DT, HR, DA с настройками...) по расчёту osu!; None - не ответил."""
    d = post("beatmaps/%d/attributes" % bid, {"mods": mods, "ruleset": "osu"})
    return ((d or {}).get("attributes") or {}).get("star_rating")


def _ts(s):
    ended = s.get("ended_at") or s.get("created_at") or ""
    try:
        return datetime.datetime.fromisoformat(ended.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def score_brief(s):
    """Результат из API в том же виде, что у тренера: точность, промахи, дата, моды."""
    st = s.get("statistics") or {}
    return dict(acc=round(float(s.get("accuracy") or 0), 4), misses=int(st.get("miss", st.get("count_miss", 0)) or 0),
                ts=_ts(s), rank=s.get("rank"), mods=[m.get("acronym") for m in s.get("mods") or [] if isinstance(m, dict)],
                pp=s.get("pp"), source="osu!")


def compact(s):
    """Результат для кэша профиля: только то, по чему выбираются лучшие скоры. Моды - без CL,
    но с настройками (скорость DT, AR у DA): от них зависят звёзды."""
    st = s.get("statistics") or {}
    mods = [dict(acronym=m["acronym"], **({"settings": m["settings"]} if m.get("settings") else {}))
            for m in s.get("mods") or [] if isinstance(m, dict) and m.get("acronym") and m["acronym"] != "CL"]
    return dict(id=s.get("id"), acc=round(float(s.get("accuracy") or 0), 4), miss=int(st.get("miss", 0) or 0),
                n100=int(st.get("ok", 0) or 0), n50=int(st.get("meh", 0) or 0), combo=int(s.get("max_combo") or 0),
                fc=bool(s.get("is_perfect_combo") or s.get("legacy_perfect")), rank=s.get("rank"), pp=s.get("pp"),
                mods=mods, ts=_ts(s), replay=bool(s.get("has_replay")), passed=s.get("passed") is not False)
