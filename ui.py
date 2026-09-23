# -*- coding: utf-8 -*-
"""
Веб-интерфейс osu!trainer.

Локальный режим (по умолчанию): http://127.0.0.1:8730 - подбор, скачивание, коллекции в игре,
приём подборок с сайта по кнопке «Добавить в игру».

Серверный режим (--server): публичный сайт за nginx - только подбор и ссылки на карты;
в игру коллекцию кладёт локальный osu!trainer пользователя.
"""
import argparse
import collections
import http.server
import json
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import i18n  # noqa: E402
import net  # noqa: E402
import pools  # noqa: E402
import skills  # noqa: E402
import trainer  # noqa: E402

MODE = "local"
PORT = 8730
PUBLIC_HOST = None          # в серверном режиме: домен сайта
SHARES = os.path.join(config.CACHE_DIR, "shares")
SHARE_TTL = 60 * 86400

JOBS = {}
JOB_LOCK = threading.Lock()
RUN_SLOTS = threading.BoundedSemaphore(2)       # одновременно выполняемых подборов на сервере
IP_STARTS = collections.defaultdict(collections.deque)
MAX_JOBS_PER_HOUR = 30
JOB_DEADLINE = 15 * 60

MD5_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, ip=""):
        self.id = secrets.token_urlsafe(9)
        self.kind = kind
        self.ip = ip
        self.lines = []
        self.state = "queued"
        self.result = None
        self.error = None
        self.cancel = False
        self.created = time.time()
        self.finished = None
        self.deadline = None

    def log(self, *a):
        if self.cancel or (self.deadline and time.time() > self.deadline):
            raise Cancelled()
        text = " ".join(str(x) for x in a)
        self.lines.append(text)
        if MODE == "local":
            print("[%s] %s" % (self.kind, text), flush=True)


def new_job(kind, target, ip="", limited=False):
    job = Job(kind, ip)
    with JOB_LOCK:
        JOBS[job.id] = job

    def work():
        acquired = False
        try:
            if limited:
                while not RUN_SLOTS.acquire(timeout=1):
                    if job.cancel:
                        raise Cancelled()
                acquired = True
            job.state = "running"
            job.deadline = time.time() + JOB_DEADLINE if MODE == "server" else None
            job.result = target(job)
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
            job.lines.append(i18n._("Отменено."))
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.lines.append(i18n._("Ошибка: %s", e))
            if MODE == "local":
                traceback.print_exc()
        finally:
            job.finished = time.time()
            if acquired:
                RUN_SLOTS.release()

    threading.Thread(target=work, daemon=True).start()
    return job


def cleanup_loop():
    while True:
        time.sleep(600)
        now = time.time()
        with JOB_LOCK:
            for jid in [j.id for j in JOBS.values() if j.finished and now - j.finished > 3600]:
                JOBS.pop(jid, None)
        if os.path.isdir(SHARES):
            for name in os.listdir(SHARES):
                path = os.path.join(SHARES, name)
                try:
                    if now - os.path.getmtime(path) > SHARE_TTL:
                        os.remove(path)
                except OSError:
                    pass


# ---------------------------------------------------------- подборки ------

PUBLIC_FIELDS = ("bid", "sid", "md5", "sr", "score", "artist", "title", "diff", "why", "slot", "page", "genre")


def public_map(c):
    out = {k: c.get(k) for k in PUBLIC_FIELDS}
    out["links"] = [[label, url % c["sid"]] for label, url in net.DOWNLOAD_LINKS] if c.get("sid") else []
    return out


def save_share(name, picked, params):
    os.makedirs(SHARES, exist_ok=True)
    sid = secrets.token_urlsafe(8)
    data = dict(id=sid, name=name, created=int(time.time()), version=config.VERSION,
                params={k: params.get(k) for k in ("skill", "tournament", "stars", "digits", "genres")},
                maps=[public_map(c) for c in picked])
    with open(os.path.join(SHARES, sid + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return sid


def load_share(sid):
    if not ID_RE.match(sid or ""):
        return None
    path = os.path.join(SHARES, sid + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_maps(maps):
    """Проверка карт, пришедших с сайта: только числа и md5, строки обрезаны."""
    out = []
    for m in maps or []:
        try:
            md5 = str(m.get("md5", "")).lower()
            if not MD5_RE.match(md5):
                continue
            out.append(dict(bid=int(m.get("bid") or 0), sid=int(m.get("sid") or 0), md5=md5,
                            sr=float(m.get("sr") or 0), artist=str(m.get("artist") or "")[:120],
                            title=str(m.get("title") or "")[:160], diff=str(m.get("diff") or "")[:120],
                            why=str(m.get("why") or "")[:200], slot=str(m.get("slot") or "")[:8]))
        except (TypeError, ValueError, AttributeError):
            continue
    return out[:300]


def allowed_site(site):
    site = (site or "").rstrip("/")
    sites = [s.rstrip("/") for s in config.load().get("sites", [])] + [config.SITE_URL.rstrip("/")]
    return site if site in sites else None


def fetch_remote_share(site, sid):
    site = allowed_site(site)
    if not site:
        raise RuntimeError(i18n._("Сайт %s не в списке разрешённых", site))
    if not ID_RE.match(sid or ""):
        raise RuntimeError(i18n._("Подборка не найдена или устарела"))
    r = net.fetch(site + "/api/share/" + sid, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(i18n._("Подборка не найдена или устарела"))
    data = r.json()
    name = re.sub(r"[\r\n\t]", " ", str(data.get("name") or "osu!trainer"))[:100]
    return name, clean_maps(data.get("maps"))


# ------------------------------------------------------------- задачи -----

def run_selection(params, ip=""):
    server = MODE == "server"
    a = trainer.coerce_params(params, server=server)

    def target(job):
        i18n.set_lang(a.lang)
        result = trainer.run(a, log=job.log)
        maps = [public_map(c) for c in result["picked"]]
        for m, c in zip(maps, result["picked"]):
            m["owned"] = c.get("owned")
        out = dict(name=result["name"], picked=maps, downloaded=result.get("downloaded", 0),
                   collection=result.get("collection"))
        if server:
            out["share"] = save_share(result["name"], result["picked"], params)
        return out

    return new_job("run", target, ip=ip, limited=server)


def run_import(site, sid, lang, name_override=None):
    a = trainer.coerce_params({"lang": lang})

    def target(job):
        i18n.set_lang(lang)
        job.log(i18n._("Загружаю подборку с %s...", site))
        name, maps = fetch_remote_share(site, sid)
        name = (name_override or name)[:100]
        job.log(i18n._("Подборка «%s», карт: %d", name, len(maps)))
        result = trainer.apply(maps, name, a, log=job.log)
        return dict(name=name, picked=[dict(m, owned=m.get("owned")) for m in result["picked"]],
                    downloaded=result["downloaded"], collection=result["collection"])

    return new_job("import", target)


def refresh_pools(params):
    def target(job):
        i18n.set_lang(params.get("lang", "ru"))
        pools.build(force=True, log=job.log)
        return {"ok": True}
    return new_job("pools", target)


def environment():
    st = config.status()
    st["node"] = bool(config.find_node())
    st["realm_module"] = os.path.isdir(os.path.join(config.TOOL_DIR, "node_modules", "realm"))
    return st


def install_commands():
    raw = "https://raw.githubusercontent.com/%s/main/" % config.REPO
    return dict(windows="irm %sinstall.ps1 | iex" % raw, unix="curl -fsSL %sinstall.sh | sh" % raw,
                repo="https://github.com/%s" % config.REPO)


def init_data(lang):
    i18n.set_lang(lang)
    st = None
    try:
        st = pools.stats() if pools.available() else None
    except Exception:
        st = None
    pool = None
    if st:
        pool = dict(entries=st["entries"], editions=st["editions"], digits=st["digits"],
                    tiers=st["tiers"], sources=st["sources"],
                    slots=sorted(st["slots"].items(), key=lambda kv: (kv[0][:2], int(kv[0][2:] or 0))),
                    tournaments=[t for t, _n in st["tournaments"]],
                    updated=time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(pools.POOLS_JSON))))
    data = dict(
        mode=MODE, version=config.VERSION, site=config.SITE_URL, install=install_commands(),
        skills=[dict(key=k, title=v["title"], title_en=v["title_en"], desc=v["desc"], desc_en=v["desc_en"])
                for k, v in skills.SKILLS.items()],
        genres=[dict(id=k, ru=v[0], en=v[1]) for k, v in trainer.GENRES.items()],
        pool=pool, collections=[], collections_error=None, env=None,
    )
    if MODE == "local":
        env = environment()
        data["env"] = env
        if env["osu_data"] and env["node"] and env["realm_module"]:
            try:
                data["collections"] = trainer.collections()
            except Exception as e:
                data["collections_error"] = str(e)
    return data


# ---------------------------------------------------------------- HTTP ----

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "osu-trainer"

    def log_message(self, *a):
        pass

    def client_ip(self):
        ip = self.client_address[0]
        if MODE == "server" and ip in ("127.0.0.1", "::1"):
            ip = self.headers.get("X-Real-IP") or ip
        return ip

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self):
        # защита от DNS-rebinding: принимаем только обращения по своему имени
        host = (self.headers.get("Host") or "").lower()
        ok = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT}
        if PUBLIC_HOST:
            ok |= {PUBLIC_HOST, PUBLIC_HOST + ":443"}
        return host in ok

    def _cors(self):
        """CORS для /api/ping: сайт проверяет, запущен ли локальный osu!trainer."""
        origin = self.headers.get("Origin") or ""
        if MODE == "local" and allowed_site(origin):
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Private-Network": "true",
                    "Access-Control-Allow-Methods": "GET", "Access-Control-Allow-Headers": "Content-Type"}
        return {}

    def do_OPTIONS(self):
        if urllib.parse.urlparse(self.path).path == "/api/ping" and self._cors():
            self.send_response(204)
            for k, v in self._cors().items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(405, {"error": "method not allowed"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/ping":
            return self._send(200, {"app": "osu-trainer", "version": config.VERSION, "mode": MODE},
                              extra=self._cors())
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            with open(os.path.join(config.TOOL_DIR, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if url.path.startswith("/s/"):
            sid = url.path[3:]
            if ID_RE.match(sid):
                self.send_response(302)
                self.send_header("Location", "/?share=" + sid)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if url.path == "/api/init":
            return self._send(200, init_data(q.get("lang", config.load().get("language", "ru"))))
        if url.path == "/api/job":
            job = JOBS.get(q.get("id", ""))
            if not job:
                return self._send(404, {"error": "no such job"})
            start = max(0, int(q.get("from", 0) or 0))
            return self._send(200, dict(kind=job.kind, lines=job.lines[start:], total=len(job.lines),
                                        state=job.state, error=job.error,
                                        result=job.result if job.state == "done" else None))
        if url.path.startswith("/api/share/"):
            data = load_share(url.path.rsplit("/", 1)[-1])
            if not data:
                return self._send(404, {"error": "not found"})
            return self._send(200, data)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        # защита от запросов со сторонних сайтов: браузер не даст им выставить этот заголовок
        if self.headers.get("X-Requested-With") != "osu-trainer":
            return self._send(403, {"error": "missing header"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 65536:
            return self._send(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "bad json"})
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/run":
            if MODE == "server":
                ip = self.client_ip()
                now = time.time()
                starts = IP_STARTS[ip]
                while starts and now - starts[0] > 3600:
                    starts.popleft()
                busy = any(j.ip == ip and j.state in ("queued", "running") for j in list(JOBS.values()))
                if busy or len(starts) >= MAX_JOBS_PER_HOUR:
                    return self._send(429, {"error": "too many requests"})
                starts.append(now)
                return self._send(200, {"id": run_selection(body, ip).id})
            return self._send(200, {"id": run_selection(body).id})
        if path == "/api/cancel":
            job = JOBS.get(str(body.get("id", "")))
            if job:
                job.cancel = True
            return self._send(200, {"ok": True})

        if MODE != "local":
            return self._send(404, {"error": "not available on the website"})

        if path == "/api/pools/refresh":
            return self._send(200, {"id": refresh_pools(body).id})
        if path == "/api/config":
            config.save(body)
            return self._send(200, {"env": environment()})
        if path == "/api/collection/delete":
            try:
                i18n.set_lang(body.get("lang", "ru"))
                trainer.backup_realm()
                trainer.realm_cmd("remove", str(body["name"]))
                return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if path == "/api/import/preview":
            try:
                i18n.set_lang(body.get("lang", "ru"))
                name, maps = fetch_remote_share(body.get("site"), body.get("id"))
                return self._send(200, {"name": name, "count": len(maps), "maps": maps[:200]})
            except Exception as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/import":
            site = allowed_site(body.get("site"))
            if not site:
                return self._send(400, {"error": "site not allowed"})
            job = run_import(site, str(body.get("id", "")), body.get("lang", "ru"),
                             str(body.get("name") or "")[:100] or None)
            return self._send(200, {"id": job.id})
        return self._send(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def main():
    global MODE, PORT, PUBLIC_HOST
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="osu!trainer UI")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    p.add_argument("--server", action="store_true", help="режим публичного сайта (за nginx)")
    p.add_argument("--public-url", default=None, help="адрес сайта, напр. https://osu.gravitacia.art")
    args = p.parse_args()

    if args.server:
        MODE = "server"
        url = args.public_url or config.SITE_URL
        PUBLIC_HOST = urllib.parse.urlparse(url).netloc.lower()
        port = args.port or 3002
    else:
        port = args.port or int(config.load().get("port") or 8730)

    srv = None
    for candidate in ([port] if MODE == "server" else range(port, port + 10)):
        try:
            srv = Server(("127.0.0.1", candidate), Handler)
            PORT = candidate
            break
        except OSError:
            continue
    if not srv:
        raise SystemExit("Не удалось занять порт %d" % port)

    threading.Thread(target=cleanup_loop, daemon=True).start()
    url = "http://127.0.0.1:%d/" % PORT
    print("osu!trainer %s [%s] -> %s  (Ctrl+C - выход)" % (config.VERSION, MODE, url), flush=True)
    if MODE == "local" and not args.no_browser and os.environ.get("OSU_TRAINER_NO_OPEN") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
