# -*- coding: utf-8 -*-
"""Сетевой слой без сторонних библиотек: несколько зеркал, ограничение частоты запросов, кэш."""
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

HEAD = {"User-Agent": "Mozilla/5.0 (osu-trainer; personal beatmap collection helper)"}
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

    def wait(self):
        while True:
            with self.lock:
                now = time.time()
                while self.hits and now - self.hits[0] > 60:
                    self.hits.popleft()
                recent = sum(1 for t in self.hits if now - t < self.burst_window)
                if len(self.hits) < self.per_min and recent < self.burst_n:
                    self.hits.append(now)
                    return
                if recent >= self.burst_n:
                    delay = self.burst_window - (now - self.hits[-recent])
                else:
                    delay = 60 - (now - self.hits[0])
            time.sleep(max(delay, 0.05))


BUCKETS = {
    "osu.direct": Bucket(90, 5, 2.2),
    "catboy.best": Bucket(50, 3, 2.5),
    "osu.ppy.sh": Bucket(80, 4, 2.0),
}
DISABLED = {}
_lock = threading.Lock()


def disable(host, why):
    with _lock:
        if host not in DISABLED:
            DISABLED[host] = (why, time.time())
            print("  [зеркало %s отключено: %s]" % (host, why), flush=True)


def _disabled(host):
    item = DISABLED.get(host)
    if not item:
        return False
    if time.time() - item[1] > 1800:        # через полчаса пробуем снова
        DISABLED.pop(host, None)
        return False
    return True


def get(url, params=None, stream=False, timeout=30, tries=3):
    host = urllib.parse.urlparse(url).netloc
    if _disabled(host):
        return None
    bucket = BUCKETS.get(host)
    for attempt in range(tries):
        if bucket:
            bucket.wait()
        try:
            r = fetch(url, params=params, timeout=timeout, stream=stream)
        except (OSError, ValueError):
            time.sleep(1.0 + attempt)
            continue
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After", 5))
            except (TypeError, ValueError):
                wait = 5
            time.sleep(min(wait, 30))
            continue
        if r.status_code == 403:
            disable(host, "доступ запрещён (403)")
            return None
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
                disable(host, "временный бан за частые запросы")
                return None
        return r
    return None


# ------------------------------------------------------------- провайдеры ---

def search(query, stars, status, offset, limit=50):
    """Список бимапсетов в формате osu!api v2 (или пустой список)."""
    if not _disabled("catboy.best") and stars[0] is not None:
        q = ("stars>%.2f stars<%.2f %s" % (stars[0] - 0.25, stars[1] + 0.25, query)).strip()
        r = get("https://catboy.best/api/v2/search",
                params={"query": q, "limit": limit, "offset": offset, "mode": 0, "status": status})
        if r is not None:
            try:
                data = r.json()
                if isinstance(data, list):
                    return data
            except ValueError:
                pass
    r = get("https://osu.direct/api/v2/search",
            params={"q": query, "amount": limit, "offset": offset, "mode": 0, "status": status})
    if r is not None:
        try:
            data = r.json()
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    return []


def osu_file(beatmap_id, cache_dir):
    """Скачивает .osu (с кэшем на диске)."""
    path = os.path.join(cache_dir, "osu", "%s.osu" % beatmap_id)
    if os.path.exists(path) and os.path.getsize(path) > 200:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    for url in ("https://osu.ppy.sh/osu/%s" % beatmap_id,
                "https://osu.direct/api/osu/%s" % beatmap_id,
                "https://catboy.best/osu/%s" % beatmap_id):
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
    for url in ("https://osu.direct/api/d/%s" % set_id,
                "https://catboy.best/d/%s" % set_id):
        r = get(url, stream=True, timeout=240, tries=2)
        if r is None:
            continue
        tmp = path + ".part"
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            continue
        if os.path.getsize(tmp) < 10000:
            os.remove(tmp)
            continue
        with open(tmp, "rb") as f:
            if f.read(2) != b"PK":
                os.remove(tmp)
                continue
        os.replace(tmp, path)
        return path
    return None


DOWNLOAD_LINKS = [
    ("osu!", "https://osu.ppy.sh/beatmapsets/%s"),
    ("osu.direct", "https://osu.direct/api/d/%s"),
    ("catboy", "https://catboy.best/d/%s"),
]
