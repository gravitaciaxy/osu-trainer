# -*- coding: utf-8 -*-
"""
Турнирные мапулы с Liquipedia (liquipedia.net/osu), данные под лицензией CC-BY-SA 3.0.

Правила API Liquipedia соблюдаются: только api.php, не чаще 1 запроса в 2 секунды,
gzip, собственный User-Agent, результаты кэшируются на диске.
"""
import gzip
import http.client
import json
import os
import re
import ssl
import time
import urllib.parse

import config
from i18n import EN, _

EN.update({"Liquipedia не отвечает (код %s)": "Liquipedia is not responding (code %s)"})

API = "https://liquipedia.net/osu/api.php"
CACHE = os.path.join(config.CACHE_DIR, "liquipedia")
PAGES_JSON = os.path.join(CACHE, "pages.json")
DELAY = 2.5

MOD_ALIASES = {"NM": "NM", "HD": "HD", "HR": "HR", "DT": "DT", "NC": "DT", "FM": "FM",
               "TB": "TB", "EZ": "EZ", "FL": "FL"}
SLOT_RE = re.compile(r"(?<![A-Za-z])(NM|HD|HR|DT|NC|FM|TB|EZ|FL)\s*(\d{0,2})(?![A-Za-z])")
LINK_RE = re.compile(r"osu\.ppy\.sh/(?:beatmaps/(\d+)|b/(\d+)|beatmapsets/(\d+)#(osu|taiko|fruits|mania)/(\d+))")
TABS_RE = re.compile(r"\{\{\s*Tabs dynamic\s*$", re.I)
TAB_RE = re.compile(r"\{\{\s*Tabs dynamic/tab\s*\|\s*(\d+)\s*\}\}", re.I)
TABS_END_RE = re.compile(r"\{\{\s*Tabs dynamic/end\s*\}\}", re.I)
NAME_RE = re.compile(r"^\|\s*name(\d+)\s*=\s*(.+?)\s*$")
HEAD_RE = re.compile(r"^(={2,5})\s*(.+?)\s*\1\s*$")
TIERS = {"1": "S", "2": "A", "3": "B", "4": "C", "5": "D"}


class Client:
    """Клиент MediaWiki API: одно постоянное соединение, gzip, пауза между запросами."""

    def __init__(self, log=print):
        self.log = log
        self.last = 0.0
        self.conn = None
        self.headers = {"User-Agent": config.liquipedia_user_agent(), "Accept-Encoding": "gzip"}

    def _connection(self):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection("liquipedia.net", timeout=90,
                                                    context=ssl.create_default_context())
        return self.conn

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def query(self, **params):
        wait = self.last + DELAY - time.time()
        if wait > 0:
            time.sleep(wait)
        params.setdefault("format", "json")
        path = "/osu/api.php?" + urllib.parse.urlencode(params)
        status = "?"
        for attempt in range(3):
            try:
                c = self._connection()
                c.request("GET", path, headers=self.headers)
                r = c.getresponse()
                body = r.read()
                status = r.status
                self.last = time.time()
                if (r.getheader("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                if r.status == 200:
                    return json.loads(body.decode("utf-8"))
                if r.status == 429:
                    time.sleep(30)
                    continue
            except (OSError, http.client.HTTPException, ValueError):
                self.last = time.time()
                self.close()
            time.sleep(DELAY * (attempt + 2))
        raise RuntimeError(_("Liquipedia не отвечает (код %s)", status))


def fetch_pages(force=False, log=print, max_age_days=7):
    """Скачивает викитекст всех турниров osu!standard (пачками по 50 страниц)."""
    if os.path.exists(PAGES_JSON) and not force:
        age = (time.time() - os.path.getmtime(PAGES_JSON)) / 86400
        if age < max_age_days:
            return json.load(open(PAGES_JSON, encoding="utf-8"))
    os.makedirs(CACHE, exist_ok=True)
    cl = Client(log)
    titles, cont = [], {}
    while True:
        d = cl.query(action="query", list="categorymembers", cmtitle="Category:Osu!standard Tournaments",
                     cmlimit=500, cmtype="page", **cont)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            break
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}
    log(_("  Liquipedia: турниров osu!standard: %d", len(titles)))
    pages = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        d = cl.query(action="query", prop="revisions", rvprop="content", rvslots="main",
                     titles="|".join(chunk))
        for p in d["query"]["pages"].values():
            if "revisions" in p:
                pages[p["title"]] = p["revisions"][0]["slots"]["main"]["*"]
        log(_("  Liquipedia: %d/%d страниц", min(i + 50, len(titles)), len(titles)))
    cl.close()
    tmp = PAGES_JSON + ".tmp"
    json.dump(pages, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, PAGES_JSON)
    return pages


def _infobox(text):
    m = re.search(r"\{\{Infobox league(.*?)\n\}\}", text, re.S)
    info = {}
    if m:
        for k, v in re.findall(r"^\|([a-z_0-9]+)=(.*)$", m.group(1), re.M):
            info[k] = v.strip()
    return info


def _clean(s):
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    return re.sub(r"['\[\]{}|=]", "", s).strip()


def _is_mod_name(name):
    n = name.lower()
    return bool(re.search(r"no ?mod|hidden|hard ?rock|double ?time|free ?mod|tie ?breaker|nightcore|"
                          r"easy|flashlight|^nm|^hd|^hr|^dt|^fm|^tb", n))


def parse_page(title, text):
    """Мапулы со страницы Liquipedia -> список записей (формат как у pools.py)."""
    info = _infobox(text)
    parts = title.split("/")
    tournament = parts[0]
    edition = "/".join(parts[1:]) if len(parts) > 1 else ""
    year = 0
    for src in (info.get("sdate", ""), info.get("edate", ""), title):
        m = re.search(r"(20\d\d)", src)
        if m:
            year = int(m.group(1))
            break
    default_round = "Qualifier" if re.search(r"qualif", title, re.I) else ""
    meta = dict(tier=TIERS.get(info.get("liquipediatier", "").split("|")[0].strip()),
                osu_wiki=info.get("osu", ""), name=info.get("name", "") or title)

    lines = text.splitlines()
    start, end = None, len(lines)
    for i, line in enumerate(lines):
        h = HEAD_RE.match(line)
        if h and len(h.group(1)) == 2:
            if start is None and re.search(r"map\s*pool", h.group(2), re.I):
                start = i + 1
            elif start is not None:
                end = i
                break
    if start is None:
        start = 0  # подстраницы квалификаций часто без заголовка - берём строки с метками слотов

    entries = []
    stack = []            # [[names{}, current_index]]
    heading_round = ""
    collecting_names = None
    seen = set()
    for line in lines[start:end]:
        s = line.strip()
        h = HEAD_RE.match(s)
        if h:
            title_h = _clean(h.group(2))
            if not _is_mod_name(title_h) and not re.search(r"(?i)map\s*pools?$", title_h):
                heading_round = title_h
            continue
        if TABS_RE.search(s):
            collecting_names = {}
            stack.append([collecting_names, 0])
            continue
        if collecting_names is not None:
            nm = NAME_RE.match(s)
            if nm:
                collecting_names[int(nm.group(1))] = _clean(nm.group(2))
                continue
            if s.startswith("}}") or s.startswith("|This") or s.startswith("|"):
                if s.startswith("}}"):
                    collecting_names = None
                continue
        t = TAB_RE.search(s)
        if t and stack:
            stack[-1][1] = int(t.group(1))
            continue
        if TABS_END_RE.search(s):
            if stack:
                stack.pop()
            continue

        link = LINK_RE.search(s)
        if not link:
            continue
        if link.group(4) and link.group(4) != "osu":
            continue
        before = s[:link.start()]
        slot = SLOT_RE.search(before) or SLOT_RE.search(s[link.end():link.end() + 12])
        if not slot:
            continue
        mod = MOD_ALIASES[slot.group(1)]
        idx = int(slot.group(2)) if slot.group(2) else 1
        bid = int(link.group(1) or link.group(2) or link.group(5))
        sid = int(link.group(3)) if link.group(3) else None

        rnd = ""
        for names, cur in stack:
            name = names.get(cur, "")
            if name and not _is_mod_name(name) and not re.search(r"(?i)map\s*pools?$", name):
                rnd = name
        rnd = rnd or heading_round or default_round
        key = (rnd, mod, idx, bid)
        if key in seen:
            continue
        seen.add(key)
        entries.append(dict(tournament=tournament, edition=edition, year=year, round=rnd,
                            slot="%s%d" % (mod, idx), mod=mod, index=idx, sid=sid, bid=bid,
                            source="Liquipedia", tier=meta["tier"],
                            page="https://liquipedia.net/osu/" + title.replace(" ", "_")))
    return entries, meta
