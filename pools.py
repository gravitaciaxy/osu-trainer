# -*- coding: utf-8 -*-
"""
Турнирные мапулы: osu!wiki + Liquipedia.

Каждая запись: турнир, издание, год, раунд, слот (NM1, HD2, DT3, TB1...), id карты,
рейтинговый диапазон участников (open / 3-digit / 4-digit / 5-digit / 6-digit ...) и уровень
турнира по Liquipedia (S/A/B/C/D), если известен.
"""
import concurrent.futures as cf
import gzip
import itertools
import json
import os
import re
import sys
import threading
import time

import collector
import config
import liquipedia
import net
from i18n import _

CACHE = config.CACHE_DIR
WIKI_DIR = os.path.join(CACHE, "wiki")
POOLS_JSON = os.path.join(CACHE, "pools.json")
BEATMAPS_JSON = os.path.join(CACHE, "beatmaps.json")
RAW = "https://raw.githubusercontent.com/ppy/osu-wiki/master/"

MODS = [
    (r"no\s*mod|nomod|\bnm\b", "NM"),
    (r"hidden|\bhd\b", "HD"),
    (r"hard\s*rock|\bhr\b", "HR"),
    (r"double\s*time|nightcore|\bdt\b|\bnc\b", "DT"),
    (r"free\s*mod|\bfm\b", "FM"),
    (r"tie\s*breaker|\btb\b", "TB"),
    (r"flash\s*light|\bfl\b", "FL"),
    (r"\beasy\b|\bez\b", "EZ"),
]
MOD_RE = [(re.compile(p, re.I), code) for p, code in MODS]
LINK_RE = re.compile(r"https?://osu\.ppy\.sh/beatmapsets/(\d+)#(osu|taiko|fruits|mania)/(\d+)")
ITEM_RE = re.compile(r"^\s*(\d+)\.\s")
HEAD_RE = re.compile(r"^(#{2,5})\s+(.*)")
BULLET_RE = re.compile(r"^-\s+(.+?)\s*$")


def _log(*a):
    print(*a, flush=True)


# ------------------------------------------------------ рейтинговый диапазон --

NUM_RE = re.compile(r"(#\s?)?(\d{1,3}(?:[,.  ]\d{3})+|\d+)(\s?[kK]\b)?")
DIGIT_RE = re.compile(r"(?i)\b([1-7])\s*[- ]?\s*digits?\b")
OPEN_RE = re.compile(r"(?i)\bopen[- ]rank|\bno rank (?:limit|restriction|range|cap|requirement)|"
                     r"\bany rank\b|\ball ranks\b|\bwithout (?:a |any )?rank (?:limit|restriction)|"
                     r"regardless of (?:their )?rank|\bunrestricted\b|open to (?:all|every) (?:players|osu)")
CONTEXT_RE = re.compile(
    r"(?i)rank(?:ed|s|ing)?\s*(?:range|limit|cap|restriction|requirement|between|from|of|within|must|"
    r"better|higher|above|below|lower|worse|under|over|at least|no|#)|between\s+(?:the\s+)?ranks?|"
    r"ranked\s+(?:between|from|#|\d)|players?\s+(?:ranked|from|between)|"
    r"#\s?\d[\d,]*\s+or\s+(?:higher|better|above|lower|worse|below)")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")


def _num(m):
    has_hash, raw, k = bool(m.group(1)), m.group(2), bool(m.group(3))
    grouped = bool(re.search(r"[,.  ]", raw))
    n = int(re.sub(r"\D", "", raw))
    if k:
        n *= 1000
    if not (has_hash or grouped or k):
        if 1990 <= n <= 2035 or n < 100:
            return None
    return n


def digits_of(rank_min):
    if rank_min is None:
        return None
    if rank_min <= 1:
        return 0
    return len(str(int(rank_min)))


def _plain(text):
    """Убирает разметку, которая раздувает предложения: ссылки, флаги, шаблоны."""
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"::\{[^}]*\}::", "", text)
    text = re.sub(r"\{\{(?:flag|Flag)\|[^}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    return text


def rank_info(title, text):
    """Определяет рейтинговое ограничение турнира по названию и тексту страницы."""
    m = DIGIT_RE.search(title) or re.match(r"^([3-7])(?:WC|DWC)\b", title, re.I)
    if m:
        d = int(m.group(1))
        return dict(digits=d, rank_min=10 ** (d - 1), rank_max=10 ** d - 1)
    fallback = None
    for sent in SENT_SPLIT.split(_plain(text[:30000])):
        sent = sent[:1200]
        low = sent.lower()
        if "rank" in low or "digit" in low:
            md = DIGIT_RE.search(sent)
            if md and re.search(r"(?i)rank|range|players|aimed|tournament|cup", sent):
                d = int(md.group(1))
                return dict(digits=d, rank_min=10 ** (d - 1), rank_max=10 ** d - 1)
            if CONTEXT_RE.search(sent):
                nums = [n for n in (_num(x) for x in NUM_RE.finditer(sent)) if n and 10 <= n <= 5000000]
                lo = hi = None
                if len(nums) >= 2:
                    lo, hi = min(nums), max(nums)
                elif nums:
                    n = nums[0]
                    capped = re.search(r"(?i)better|higher|above|\btop\b|or better|within", low)
                    floor = re.search(r"(?i)worse|lower than|or worse|and below", low)
                    if capped and not floor:
                        lo, hi = 1, n
                    elif re.search(r"(?i)worse|lower|or below|and below|beyond|minimum", low):
                        lo, hi = n, None
                if lo is not None:
                    return dict(digits=digits_of(lo), rank_min=lo, rank_max=hi)
        if OPEN_RE.search(sent):
            fallback = fallback or dict(digits=0, rank_min=1, rank_max=None)
    return fallback or dict(digits=None, rank_min=None, rank_max=None)


def digit_label(d):
    if d is None:
        return "?"
    if d == 0:
        return "open"
    return "%d-digit" % d


# ------------------------------------------------------------- osu!wiki -----

def wiki_page_list(force=False):
    path = os.path.join(CACHE, "wiki_tournaments.json")
    if os.path.exists(path) and not force:
        return json.load(open(path, encoding="utf-8"))
    r = net.get("https://api.github.com/repos/ppy/osu-wiki/git/trees/master?recursive=1", timeout=90)
    if r is None:
        raise RuntimeError("Не удалось получить список страниц osu!wiki")
    pages = [t["path"] for t in r.json()["tree"]
             if t["path"].startswith("wiki/Tournaments/") and t["path"].endswith("/en.md")]
    os.makedirs(CACHE, exist_ok=True)
    json.dump(pages, open(path, "w", encoding="utf-8"))
    return pages


def wiki_fetch(pages, force=False, threads=8, log=_log):
    os.makedirs(WIKI_DIR, exist_ok=True)

    def one(p):
        local = os.path.join(WIKI_DIR, p.replace("wiki/Tournaments/", "").replace("/", "__"))
        if os.path.exists(local) and os.path.getsize(local) > 100 and not force:
            return local
        r = net.get(RAW + p, timeout=40)
        if r is None:
            return local if os.path.exists(local) else None
        with open(local, "wb") as f:
            f.write(r.content)
        return local

    out, done = [], 0
    with cf.ThreadPoolExecutor(max_workers=threads) as ex:
        for res in ex.map(one, pages):
            done += 1
            if done % 100 == 0:
                log(_("  osu!wiki: %d/%d страниц", done, len(pages)))
            if res:
                out.append(res)
    return out


def mod_code(text):
    t = re.sub(r"[*_`\[\]]", "", text).strip()
    if len(t) > 40:
        return None
    for rx, code in MOD_RE:
        if rx.search(t):
            return code
    return None


def wiki_parse(path):
    name = os.path.basename(path)
    name = name[:-3] if name.endswith(".md") else name
    parts = name.replace("__en", "").split("__")
    tournament = parts[0]
    edition = parts[1] if len(parts) > 1 else ""
    key = "/".join(parts)
    year = 0
    m = re.search(r"(20\d\d)", edition or tournament)
    if m:
        year = int(m.group(1))
    text = open(path, encoding="utf-8", errors="ignore").read()
    intro = text.split("## Mappool")[0]
    rank = rank_info(tournament + " " + edition, intro)

    entries, in_pool, rnd, mod, idx = [], False, "", None, 0
    for line in text.splitlines():
        head = HEAD_RE.match(line)
        if head:
            title = re.sub(r"[*_`]", "", head.group(2)).strip()
            if len(head.group(1)) == 2:
                in_pool = bool(re.search(r"mappool", title, re.I))
                rnd, mod = "", None
            elif in_pool:
                rnd, mod, idx = title, None, 0
            continue
        if not in_pool:
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            code = mod_code(bullet.group(1))
            if code:
                mod, idx = code, 0
            continue
        link = LINK_RE.search(line)
        if not link or link.group(2) != "osu":
            continue
        item = ITEM_RE.match(line)
        idx = int(item.group(1)) if item else idx + 1
        if not mod:
            continue
        entries.append(dict(tournament=tournament, edition=edition, year=year, round=rnd,
                            slot="%s%d" % (mod, idx), mod=mod, index=idx,
                            sid=int(link.group(1)), bid=int(link.group(3)),
                            source="osu!wiki", tier=None, wiki_key=key,
                            page="https://osu.ppy.sh/wiki/Tournaments/" + key,
                            **rank))
    return entries


# ---------------------------------------------------------------- сборка ----

def build(force=False, log=_log, sources=("osu!wiki", "Liquipedia")):
    entries = []
    if "osu!wiki" in sources:
        pages = wiki_page_list(force)
        log(_("osu!wiki: страниц турниров — %d", len(pages)))
        for path in wiki_fetch(pages, force=force, log=log):
            try:
                entries.extend(wiki_parse(path))
            except Exception as e:
                log(_("  ошибка разбора %s: %s", os.path.basename(path), e))
        log(_("  osu!wiki: записей %d", len(entries)))

    lp_meta = {}
    if "Liquipedia" in sources:
        log(_("Liquipedia: загрузка (не чаще 1 запроса в 2 с, по правилам API)..."))
        lp_pages = liquipedia.fetch_pages(force=force, log=log)
        n0 = len(entries)
        for title, text in lp_pages.items():
            try:
                lp_entries, meta = liquipedia.parse_page(title, text)
            except Exception as e:
                log(_("  ошибка разбора %s: %s", title, e))
                continue
            head = text.split("==Mappool")[0]
            rank = rank_info(title + " " + meta.get("name", ""), head)
            for e in lp_entries:
                e.update(rank)
            entries.extend(lp_entries)
            if meta.get("osu_wiki"):
                key = meta["osu_wiki"].split("Tournaments/", 1)[-1].strip("/")
                lp_meta[key] = dict(tier=meta.get("tier"), **rank)
        log(_("  Liquipedia: записей %d", len(entries) - n0))

    # данные Liquipedia (уровень, рейтинг) переносим на те же турниры из osu!wiki
    for e in entries:
        if e["source"] == "osu!wiki" and e.get("wiki_key") in lp_meta:
            m = lp_meta[e["wiki_key"]]
            e["tier"] = e.get("tier") or m.get("tier")
            if e.get("digits") is None and m.get("digits") is not None:
                e.update(digits=m["digits"], rank_min=m["rank_min"], rank_max=m["rank_max"])

    # слияние дублей: та же карта в том же слоте в тот же год - один и тот же турнир
    merged, index = [], {}
    for e in entries:
        key = (e["bid"], e["slot"], e["year"])
        if key in index:
            cur = merged[index[key]]
            cur["sources"] = sorted(set(cur["sources"]) | {e["source"]})
            for f in ("tier", "digits", "rank_min", "rank_max", "sid"):
                if cur.get(f) is None and e.get(f) is not None:
                    cur[f] = e[f]
            continue
        e = dict(e)
        e["sources"] = [e["source"]]
        index[key] = len(merged)
        merged.append(e)

    tmp = POOLS_JSON + ".tmp"
    json.dump(merged, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, POOLS_JSON)
    eds = set((e["tournament"], e["edition"]) for e in merged)
    log(_("Итого: карт в пулах — %d, турнирных изданий — %d", len(merged), len(eds)))
    return merged


_POOL_CACHE = {"mtime": None, "data": None}


SEED_DIR = os.path.join(config.TOOL_DIR, "data")


def _seed(target, name):
    """Первый запуск: берём готовую базу из комплекта (data/*.json.gz), чтобы не собирать её заново."""
    src = os.path.join(SEED_DIR, name + ".gz")
    if os.path.exists(target) or not os.path.exists(src):
        return os.path.exists(target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with gzip.open(src, "rb") as f, open(target + ".tmp", "wb") as out:
        out.write(f.read())
    os.replace(target + ".tmp", target)
    mtime = os.path.getmtime(src)
    os.utime(target, (mtime, mtime))
    return True


def available():
    """Есть ли база мапулов (при первом запуске распаковывает готовую из data/)."""
    return _seed(POOLS_JSON, "pools.json")


def load():
    if not _seed(POOLS_JSON, "pools.json"):
        return build()
    mtime = os.path.getmtime(POOLS_JSON)
    if _POOL_CACHE["mtime"] != mtime:
        _POOL_CACHE["data"] = json.load(open(POOLS_JSON, encoding="utf-8"))
        _POOL_CACHE["mtime"] = mtime
    return _POOL_CACHE["data"]


# ------------------------------------------------ данные карт по id (SR) ----

def _bm_cache():
    _seed(BEATMAPS_JSON, "beatmaps.json")
    if os.path.exists(BEATMAPS_JSON):
        try:
            return json.load(open(BEATMAPS_JSON, encoding="utf-8"))
        except ValueError:
            return {}
    return {}


_BM_LOCK = threading.Lock()


def _save_bm(cache):
    with _BM_LOCK:
        merged = {}
        if os.path.exists(BEATMAPS_JSON):
            try:
                with open(BEATMAPS_JSON, encoding="utf-8") as f:
                    merged = json.load(f)
            except ValueError:
                merged = {}
        merged.update({k: v for k, v in cache.items() if v is not None or k not in merged})
        tmp = BEATMAPS_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False)
        os.replace(tmp, BEATMAPS_JSON)


def resolve_beatmap(bid, cache):
    key = str(bid)
    if key in cache and (cache[key] is None or "genre" in cache[key]):
        return cache[key]
    r = net.get("https://osu.direct/api/v2/b/%d/set" % bid, timeout=30)
    info = None
    if r is not None:
        try:
            s = r.json()
            for b in s.get("beatmaps", []):
                if b.get("id") == bid:
                    info = dict(sr=round(b.get("difficulty_rating", 0), 2), md5=b.get("checksum"),
                                mode=b.get("mode_int"), version=b.get("version", ""),
                                length=b.get("total_length", 0), bpm=b.get("bpm") or s.get("bpm") or 0,
                                sid=s.get("id"), title=s.get("title", ""), artist=s.get("artist", ""),
                                status=s.get("status", ""), playcount=b.get("playcount", 0),
                                genre=s.get("genre_id") or 0, language=s.get("language_id") or 0,
                                tags=(s.get("tags") or "")[:400], source=s.get("source") or "")
                    break
        except ValueError:
            pass
        cache[key] = info
    return info


def _fresh(entries):
    """Записи пула со свежими данными карт с osu.direct, по 100 карт за запрос: (запись, [sid, режим,
    звёзды, bpm, длина, статус, игр, md5] или None, если карты на osu! больше нет). Звёзды в кэше
    устаревают при пересчётах рейтинга osu!, md5, BPM и длина - при обновлении карты.
    Если зеркало не ответило, бросает OSError."""
    for k in range(0, len(entries), 100):
        chunk = entries[k:k + 100]
        rows = collector.fresh_meta([e["bid"] for e in chunk])
        if rows is None:
            raise OSError("osu.direct не ответил")
        for e in chunk:
            yield e, rows[e["bid"]]


def _edition(e):
    """Турнир и издание: по ним чередуются турниры и считается per_tournament."""
    return e["tournament"], e["edition"].split("/")[0]


def _match_digits(e, digits):
    if not digits:
        return True
    d = e.get("digits")
    if d is None:
        return "unknown" in digits
    if d == 0:
        return "open" in digits
    return str(d) in digits or (d >= 6 and "6" in digits)


def text_match(info, words):
    """Есть ли хотя бы одно из слов в тегах, названии, исполнителе или источнике."""
    if not words:
        return True
    hay = " ".join(str(info.get(k) or "") for k in ("tags", "title", "artist", "source")).lower()
    return any(w in hay for w in words)


def query(slots=None, mods=None, stars=(0, 12), years=None, tournaments=None, digits=None,
          tiers=None, sources=None, need=40, max_lookups=600, threads=3, progress=None,
          per_tournament=3, genres=None, words=None, bpm=None, length=None, log=_log):
    """Карты турнирных пулов под слоты/звёзды/рейтинг (свежие турниры первыми, по кругу).
    Звёзды, md5, BPM и длина - свежие с osu.direct; название, жанр и теги - из кэша или запросом
    на карту, только для подошедших по звёздам."""
    pool = load()
    slots = set(s.upper() for s in slots) if slots else None
    mods = set(m.upper() for m in mods) if mods else None
    tours = [t.lower() for t in tournaments] if tournaments else None
    digits = set(str(d).lower() for d in digits) if digits else None
    tiers = set(t.upper() for t in tiers) if tiers else None
    sources = set(sources) if sources else None

    cand = []
    for e in pool:
        if slots and e["slot"] not in slots:
            continue
        if mods and e["mod"] not in mods:
            continue
        if years and not (years[0] <= e["year"] <= years[1]):
            continue
        if tours and not any(t in e["tournament"].lower() for t in tours):
            continue
        if not _match_digits(e, digits):
            continue
        if tiers and (e.get("tier") or "?") not in tiers:
            continue
        if sources and not (set(e.get("sources", [e.get("source")])) & sources):
            continue
        cand.append(e)
    cand.sort(key=lambda e: (-e["year"], e["tournament"], e["edition"], e["round"], e["slot"]))

    groups, order, seen_bid = {}, [], set()
    for e in cand:
        if e["bid"] in seen_bid:
            continue
        seen_bid.add(e["bid"])
        key = _edition(e)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    ordered, i = [], 0
    while True:
        added = False
        for key in order:
            if i < len(groups[key]):
                ordered.append(groups[key][i])
                added = True
        if not added:
            break
        i += 1

    cache = _bm_cache()
    out, looked, used = [], 0, {}

    def fits(row):
        if not row or row[1] != 0 or not row[7]:
            return False                        # карты на osu! больше нет или она не osu!standard
        return (stars[0] <= row[2] <= stars[1] and (not bpm or bpm[0] <= row[3] <= bpm[1])
                and (not length or length[0] <= row[4] <= length[1]))

    def full(e):
        return bool(per_tournament) and used.get(_edition(e), 0) >= per_tournament

    limit = min(len(ordered), max_lookups)
    checked = _fresh(ordered[:limit])
    # по 25 карт - четыре порции на запрос к зеркалу: если оно не ответит, полученные карты не пропадут
    for _start in range(0, limit, 25):
        try:
            chunk = list(itertools.islice(checked, 25))
        except OSError:
            if not out:
                raise RuntimeError(_("Зеркало osu.direct не отвечает - попробуй позже"))
            log(_("  зеркало osu.direct не отвечает - остальные карты пулов пропущены"))
            break
        looked += len(chunk)
        todo = [(e, row) for e, row in chunk if fits(row) and not full(e)]
        with cf.ThreadPoolExecutor(max_workers=threads) as ex:
            infos = list(ex.map(lambda e: resolve_beatmap(e["bid"], cache), [e for e, _row in todo]))
        for (e, row), info in zip(todo, infos):
            if not info:
                continue
            _sid, _mode, sr, map_bpm, map_length, _status, playcount, md5 = row
            # свежие данные заменяют и записанные в кэш при первом запросе карты
            info.update(sr=sr, md5=md5, bpm=map_bpm, length=map_length, playcount=playcount)
            if genres and info.get("genre") not in genres:
                continue
            if not text_match(info, words):
                continue
            if full(e):
                continue
            key = _edition(e)
            used[key] = used.get(key, 0) + 1
            item = dict(e)
            item.update(info)
            out.append(item)
        if todo:
            _save_bm(cache)
        if progress:
            progress(looked, limit, len(out))
        if len(out) >= need:
            break
    return out[:need], len(ordered)


def stats():
    pool = load()
    slots, digits, tiers, sources, tours = {}, {}, {}, {}, {}
    for e in pool:
        slots[e["slot"]] = slots.get(e["slot"], 0) + 1
        dl = digit_label(e.get("digits"))
        digits[dl] = digits.get(dl, 0) + 1
        t = e.get("tier") or "?"
        tiers[t] = tiers.get(t, 0) + 1
        for s in e.get("sources", [e.get("source")]):
            sources[s] = sources.get(s, 0) + 1
        tours.setdefault(e["tournament"], set()).add(e["edition"])
    return dict(entries=len(pool), slots=slots, digits=digits, tiers=tiers, sources=sources,
                editions=sum(len(v) for v in tours.values()),
                tournaments=sorted((t, len(v)) for t, v in tours.items()))


def warm(limit=None, log=_log):
    """Заранее запрашивает данные всех карт пулов (название, жанр, теги), чтобы подбор не спрашивал
    их по одной. Звёзды подбор всё равно берёт свежие. Около 90 карт в минуту."""
    cache = _bm_cache()
    todo = []
    seen = set()
    for e in sorted(load(), key=lambda e: -e["year"]):
        k = str(e["bid"])
        if k in seen:
            continue
        seen.add(k)
        if k in cache and (cache[k] is None or "genre" in cache[k]):
            continue
        todo.append(e["bid"])
    if limit:
        todo = todo[:limit]
    log("к запросу: %d карт (уже известно: %d)" % (len(todo), len(seen) - len(todo)))
    started = time.time()
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda b: resolve_beatmap(b, cache), chunk))
        _save_bm(cache)
        done = min(i + 30, len(todo))
        rate = done / max(time.time() - started, 1) * 60
        log("  %d/%d (%.0f/мин)" % (done, len(todo), rate))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build(force="--force" in sys.argv)
    elif cmd == "warm":
        warm(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif cmd == "stats":
        st = stats()
        print("записей:", st["entries"], "| изданий турниров:", st["editions"])
        print("источники:", st["sources"])
        print("рейтинг:", st["digits"])
        print("уровень:", st["tiers"])
        print("слоты:", ", ".join("%s=%d" % kv for kv in sorted(st["slots"].items(),
                                                                key=lambda x: -x[1])[:30]))
