# -*- coding: utf-8 -*-
"""
Сетевой слой без сторонних библиотек.

Правила вежливости, чтобы нас нигде не забанили:
- честный User-Agent с адресом проекта, браузером не притворяемся;
- лимиты ниже опубликованных: osu.direct - 120 запросов в минуту и 10 за 2 с, osu! - не больше
  60 в минуту; .osu берём с зеркала, а с osu.ppy.sh - только если на зеркале карты нет;
- на 429 ждём Retry-After всеми потоками сразу; на 403 и бан отключаем сервис и не стучимся
  в него часами (catboy.best нас уже заблокировал, поэтому программа к нему не обращается);
- одинаковые поисковые запросы берутся из памяти.
"""
import collections
import gzip
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

HEAD = {"User-Agent": config.user_agent()}
_SSL = ssl.create_default_context()


class Response:
    """Минимальная замена requests.Response."""

    def __init__(self, status, headers, raw=None, body=None):
        self.status_code = status
        self.headers = headers
        self._raw = raw
        self._body = body

    @property
    def content(self):
        if self._body is None:
            data = self._raw.read() if self._raw else b""
            if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
                data = gzip.decompress(data)
            self._body = data
            self.close()
        return self._body

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    def iter_content(self, size=1 << 16):
        while True:
            chunk = self._raw.read(size)
            if not chunk:
                break
            yield chunk
        self.close()

    def close(self):
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:
                pass
            self._raw = None


def fetch(url, params=None, headers=None, timeout=30, stream=False, gzip_ok=True):
    """Один HTTP GET. Возвращает Response (в том числе для кодов 4xx/5xx) или бросает OSError."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = dict(HEAD)
    if headers:
        h.update(headers)
    if gzip_ok and not stream:
        h.setdefault("Accept-Encoding", "gzip")
    req = urllib.request.Request(url, headers=h)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_SSL)
        return Response(raw.status, raw.headers, raw=raw)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return Response(e.code, e.headers or {}, body=body)


def post_json(url, payload, headers=None, timeout=20):
    """HTTP POST с JSON. Возвращает Response (в том числе для 4xx/5xx) или бросает OSError."""
    h = dict(HEAD)
    h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_SSL)
        return Response(raw.status, raw.headers, raw=raw)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return Response(e.code, e.headers or {}, body=body)


class Bucket:
    """Ограничитель: N запросов в минуту + защита от всплесков."""

    def __init__(self, per_min, burst_n, burst_window):
        self.per_min = per_min
        self.burst_n = burst_n
        self.burst_window = burst_window
        self.hits = collections.deque()
        self.lock = threading.Lock()
        self.paused_until = 0.0

    def pause(self, seconds):
        """Сервис ответил 429: ждут все потоки, а не только тот, кому отказали."""
        with self.lock:
            self.paused_until = max(self.paused_until, time.time() + seconds)

    def wait(self):
        while True:
            with self.lock:
                now = time.time()
                while self.hits and now - self.hits[0] > 60:
                    self.hits.popleft()
                recent = sum(1 for t in self.hits if now - t < self.burst_window)
                if now < self.paused_until:
                    delay = self.paused_until - now
                elif len(self.hits) < self.per_min and recent < self.burst_n:
                    self.hits.append(now)
                    return
                elif recent >= self.burst_n:
                    delay = self.burst_window - (now - self.hits[-recent])
                else:
                    delay = 60 - (now - self.hits[0])
            time.sleep(max(delay, 0.05))


BUCKETS = {
    "osu.direct": Bucket(100, 8, 2.0),          # у зеркала 120 в минуту и 10 за 2 с
    "osu.ppy.sh": Bucket(30, 2, 2.0),           # запасной источник .osu; osu! просит не больше 60 в минуту
    "osu-api": Bucket(50, 3, 2.0),              # официальный API osu! (osu_api): тоже не больше 60 в минуту
    "osucollector.com": Bucket(40, 1, 1.4),     # сайт энтузиаста: по одному запросу, не чаще раза в 1.4 с
}
DISABLED = {}                                   # хост -> (причина, до какого времени не обращаться)
STRIKES = collections.Counter()                 # сколько раз подряд хост нам отказал
OFF_HOURS = (0.25, 1, 4, 24)
_lock = threading.Lock()


def disable(host, why, hours=None):
    """Отключает хост: сначала на 15 минут, при повторных отказах на час, 4 часа и сутки.
    Стучаться в сервис, который нас уже заблокировал, - верный путь к постоянному бану."""
    with _lock:
        if _disabled(host):
            return
        STRIKES[host] += 1
        hours = hours or OFF_HOURS[min(STRIKES[host], len(OFF_HOURS)) - 1]
        DISABLED[host] = (why, time.time() + hours * 3600)
    print("  [%s отключён на %g ч: %s]" % (host, hours, why), flush=True)


def _disabled(host):
    item = DISABLED.get(host)
    return bool(item) and time.time() < item[1]


def get(url, params=None, stream=False, timeout=30, tries=3, headers=None, bucket=None, missing_ok=False):
    """bucket - имя ограничителя, если у адресов одного хоста разные правила (API osu! и .osu с сайта).
    missing_ok - на 404 вернуть ответ, а не None: «нет такого» отличается от «сервис не ответил»."""
    host = urllib.parse.urlparse(url).netloc
    if _disabled(host):
        return None
    bucket = BUCKETS.get(bucket or host)
    for attempt in range(tries):
        if bucket:
            bucket.wait()
        try:
            r = fetch(url, params=params, headers=headers, timeout=timeout, stream=stream)
        except (OSError, ValueError):
            time.sleep(1.0 + attempt)
            continue
        if r.status_code == 429:
            r.close()
            try:
                wait = float(r.headers.get("Retry-After") or 5)
            except (TypeError, ValueError):
                wait = 5
            wait = min(max(wait, 5), 120)
            if bucket:
                bucket.pause(wait)
            else:
                time.sleep(wait)
            continue
        if r.status_code == 403:
            r.close()
            disable(host, "доступ запрещён (403)")
            return None
        if r.status_code == 404 and missing_ok:
            return r
        if 400 <= r.status_code < 500:
            r.close()
            return None                 # «нет такой карты» повтором не исправить - не тратим запросы
        if r.status_code != 200:
            r.close()
            time.sleep(0.5 + attempt)
            continue
        if not stream:
            try:
                head = r.content[:200].lower()
            except (OSError, ValueError):
                continue
            if b"banned from our services" in head:
                disable(host, "бан за частые запросы", hours=24)
                return None
        STRIKES.pop(host, None)
        return r
    return None


# ------------------------------------------------------------- провайдеры ---

SEARCH_TTL = 600
_search_cache = {}


def search(query, stars, status, offset, limit=50):
    """Бимапсеты с osu.direct в формате osu!api v2 с фильтром по звёздам (или пустой список).
    Одинаковый запрос 10 минут отдаётся из памяти: подбор часто перезапускают, поменяв пару
    фильтров, и зеркалу незачем отвечать на то же самое ещё раз."""
    key = (query, stars[0], stars[1], status, offset, limit)
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < SEARCH_TTL:
        return hit[1]
    q = query
    if stars[0] is not None:
        q = ("[beatmaps.difficulty_rating %.2f TO %.2f] %s"
             % (max(stars[0] - 0.02, 0), stars[1] + 0.02, query)).strip()
    r = get("https://osu.direct/api/v2/search",
            params={"q": q, "amount": limit, "offset": offset, "mode": 0, "status": status})
    try:
        data = r.json() if r is not None else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        return []
    with _lock:
        if len(_search_cache) > 1000:
            _search_cache.clear()
        _search_cache[key] = (time.time(), data)
    return data


def direct_search(filters=(), sort=None, offset=0, status=None, amount=100):
    """Наборы osu!standard с osu.direct по условиям Meilisearch («beatmaps.bpm 170 TO 200»,
    «(id = 1 OR id = 2)»), sort - «поле:desc». Список наборов или None, если зеркало не ответило."""
    params = {"q": "[%s]" % " AND ".join(filters) if filters else "", "amount": amount,
              "offset": offset, "mode": 0}
    if sort:
        params["sort"] = sort
    if status:
        params["status"] = status
    r = get("https://osu.direct/api/v2/search", params=params, timeout=60)
    try:
        data = r.json() if r is not None else None
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def sets_by_id(ids):
    """Наборы по номерам, по 100 за запрос: {номер: набор} или None, если зеркало не ответило."""
    out = {}
    for k in range(0, len(ids), 100):
        sets = direct_search(["(%s)" % " OR ".join("id = %d" % i for i in ids[k:k + 100])])
        if sets is None:
            return None
        out.update((s["id"], s) for s in sets)
    return out


def osu_file(beatmap_id, cache_dir):
    """Скачивает .osu (с кэшем на диске)."""
    path = os.path.join(cache_dir, "osu", "%s.osu" % beatmap_id)
    if os.path.exists(path) and os.path.getsize(path) > 200:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    # сначала зеркало (osu.direct прямо разрешает автоматическое скачивание), с сайта osu! -
    # только если на зеркале карты нет, и медленно
    for url in ("https://osu.direct/api/osu/%s" % beatmap_id,
                "https://osu.ppy.sh/osu/%s" % beatmap_id):
        r = get(url, timeout=25)
        if r is None:
            continue
        data = r.content
        if len(data) > 200 and data[:20].lstrip().startswith(b"osu"):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return data.decode("utf-8", "ignore")
    return None


def osz(set_id, out_dir):
    """Скачивает .osz набора, возвращает путь к файлу."""
    path = os.path.join(out_dir, "%s.osz" % set_id)
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        return path
    r = get("https://osu.direct/api/d/%s" % set_id, stream=True, timeout=240, tries=2)
    if r is None:
        return None
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        return None
    with open(tmp, "rb") as f:
        ok = os.path.getsize(tmp) >= 10000 and f.read(2) == b"PK"
    if not ok:
        os.remove(tmp)
        return None
    os.replace(tmp, path)
    return path


DOWNLOAD_LINKS = [
    ("osu!", "https://osu.ppy.sh/beatmapsets/%s"),
    ("osu.direct", "https://osu.direct/api/d/%s"),
    ("catboy", "https://catboy.best/d/%s"),
]
