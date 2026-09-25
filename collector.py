# -*- coding: utf-8 -*-
"""
Коллекции игроков с osu!Collector (osucollector.com) как «человеческая» разметка карт.

Игроки сами собирают подборки «tech», «streams», «aim» и т.п. Карта, которую положили во много
подборок «tech», скорее всего tech: это прибавка к оценке анализатора и отдельный источник
кандидатов. Карты, которые часто лежат в одних подборках с картами-образцами, - кандидаты
для «похожих».

Базу собирает `python collector.py build` (долго, около полутора часов):
  1. поиск подборок по словам навыков и разметка по названию (RULES);
  2. состав каждой подборки - один запрос к osu!Collector, не чаще раза в 1.4 с;
  3. звёзды, BPM, длина и статус карт, встречающихся хотя бы в двух подборках, - с зеркала
     osu.direct по 100 карт за запрос. Названия не храним: они есть в .osu, который всё равно
     скачивается для разбора.
Все ответы кэшируются в cache/collector, повторная сборка докачивает только изменившееся.
`--cached` не ищет подборки заново, `--refresh-meta` заново спрашивает данные всех карт: звёзды
меняются при пересчётах рейтинга osu! (`build --cached --refresh-meta` - около 15 минут). При подборе
данные карт из базы всё равно сверяются с osu.direct (refreshed).
Готовая база лежит в data/collector.json.gz; `python release.py` кладёт туда свежую.
"""
import collections
import concurrent.futures as cf
import gzip
import itertools
import json
import math
import os
import re
import sys
import threading
import time

import config
import net

API = "https://osucollector.com/api/collections/"
CACHE = config.CACHE_DIR
RAW_DIR = os.path.join(CACHE, "collector")              # found.json, c<id>.json, meta.json
META_JSON = os.path.join(RAW_DIR, "meta.json")
INDEX_JSON = os.path.join(CACHE, "collector.json")
SEED = os.path.join(config.TOOL_DIR, "data", "collector.json.gz")

STATUS = {"ranked": 1, "approved": 2, "qualified": 3, "loved": 4}
STATUS_NAME = {v: k for k, v in STATUS.items()}

# что ищем на сайте; найденные подборки потом размечаются по названию правилами RULES
SEARCH = ["stream", "streams", "deathstream", "jump", "jumps", "aim", "jumpstream",
          "speed", "burst", "tapping", "alt", "stamina", "marathon", "tech", "technical", "sv",
          "finger control", "fingercontrol", "rhythm", "reading", "low ar", "hidden", "precision",
          "high cs", "flow", "slider", "accuracy", "acc", "consistency"]

# (регулярка по названию подборки, навык). Правила идут по порядку, совпавший кусок вырезается:
# «jump stream» не засчитается ещё и как jumps + streams, «flow aim» - как jumps.
RULES = [
    (r"jump\s*-?\s*streams?|spaced\s+streams?", "jumpstream"),
    (r"finger\s*-?\s*control", "fingercontrol"),
    (r"flow\s*-?\s*aim", "flow"),
    (r"low\s*-?\s*ar\b", "reading"),
    (r"high\s*-?\s*cs\b|small\s+circles?|tiny\s+circles?", "precision"),
    (r"high\s*-?\s*bpm", "speed"),
    (r"slider\s*-?\s*velocity|\bsv\b", "tech"),
    (r"hidden\s+gems?", None),                          # «скрытые жемчужины» - не про HD
    (r"death\s*-?\s*streams?|\bstreams?\b|\bstreaming\b|\bstreamy\b|стрим\w*", "streams"),
    (r"\bjumps?\b|\baim\b|\baiming\b|\baimslop\b|\bspaced\b|\bаим\w*|джамп\w*", "jumps"),
    # alt, bursts и speed - разные навыки: bursts бывают не только в speed-картах, alt - быстрые ноты с прыжками
    (r"\balt\b|\balternat(?:e|ing|ion)\b", "alt"),
    (r"\bbursts?\b", "bursts"),
    (r"\bspeed\b|\btapping\b|\bsingle\s*-?\s*tap\w*", "speed"),
    (r"\bstamina\b|\bmarathons?\b|\bendurance\b|\bстамин\w*", "stamina"),
    (r"\btech\b|\btechnical\b|\btechy\b|\bgimmick\w*|\bтех\b|\bтехнич\w*", "tech"),
    (r"\bpoly\s*rhythm\w*|\brhythm\w*", "fingercontrol"),
    (r"\breading\b|\bread\b|\bhidden\b|\boverlaps?\b|\bридинг\w*", "reading"),
    (r"\bprecision\b", "precision"),
    (r"\bflow\b|\bsliders?\b", "flow"),
    (r"\bacc\b|\baccuracy\b|\bconsistency\b", "accuracy"),
]
_RULES = [(re.compile(p, re.I), skill) for p, skill in RULES]

MIN_MAPS, MAX_MAPS = 5, 1500      # меньше - случайность, больше - свалка «всё подряд»
MIN_OSU = 0.7                     # доля карт osu!standard в подборке
MIN_SEEN = 2                      # в базу идут карты, которые лежат хотя бы в стольких подборках


def _log(*a):
    print(*a, flush=True)


def classify(name):
    """Навыки по названию подборки: {'tech': 1.0}, {'jumps': 0.5, 'tech': 0.5} или {}."""
    text = " %s " % (name or "").lower()
    found = []
    for rx, skill in _RULES:
        if rx.search(text):
            text = rx.sub(" ", text)
            if skill and skill not in found:
                found.append(skill)
    return {s: round(1.0 / len(found), 3) for s in found}


def _api(path, **params):
    r = net.get(API + path, params=params or None, timeout=90)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _load(path, default=None):
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- сборка ----

def _brief(c):
    return dict(id=c["id"], name=c.get("name") or "", fav=c.get("favourites") or 0,
                n=c.get("beatmapCount") or 0, modes=c.get("modes") or {},
                modified=(c.get("dateLastModified") or {}).get("_seconds") or 0,
                uploader=(c.get("uploader") or {}).get("username") or "")


def discover(log=_log):
    """Ищет подборки по словам навыков; возвращает {id: сведения о подборке}."""
    found = _search(SEARCH, log)
    _save(os.path.join(RAW_DIR, "found.json"), found)
    return found


def _search(words, log=_log):
    found = {}
    for word in words:
        cursor, pages = None, 0
        while pages < 100:
            params = {"search": word, "perPage": 50}
            if cursor:
                params["cursor"] = cursor
            d = _api("search", **params)
            if not d:
                break
            for c in d.get("collections") or []:
                found[str(c["id"])] = _brief(c)
            pages += 1
            if not d.get("hasMore") or not d.get("nextPageCursor"):
                break
            cursor = d["nextPageCursor"]
        log("  «%s»: страниц %d, всего подборок %d" % (word, pages, len(found)))
    return found


def chosen(found):
    """Подборки для базы: навык в названии, в основном osu!standard, разумный размер."""
    out = []
    for c in found.values():
        skills = classify(c["name"])
        n = _osu_size(c)
        if skills and n:
            out.append(dict(c, skills=skills, n=n))
    out.sort(key=lambda c: (-c["fav"], c["id"]))
    return out


def _osu_size(c):
    """Размер подборки, если она в основном из карт osu!standard и разумного размера, иначе None."""
    modes = {k: v for k, v in c["modes"].items() if isinstance(v, (int, float))}
    total = sum(modes.values())
    n = max(c["n"], total)
    if not total or modes.get("osu", 0) / total < MIN_OSU or not (MIN_MAPS <= n <= MAX_MAPS):
        return None
    return n


def fetch(c):
    """Состав подборки: {'maps': [bid, ...]} (кэш: заново качаем, только если подборку меняли)."""
    path = os.path.join(RAW_DIR, "c%d.json" % c["id"])
    old = _load(path)
    if old and old.get("modified") == c["modified"]:
        return old
    d = _api(str(c["id"]))
    if not d:
        return old                              # сайт не ответил - остаётся прежняя версия
    maps = sorted(set(b["id"] for s in d.get("beatmapsets") or [] for b in s.get("beatmaps") or []
                      if b.get("id")))
    data = dict(id=c["id"], modified=c["modified"], maps=maps)
    _save(path, data)
    return data


def _tail(name):
    """Название без приставок «Автор - »: копии чужих подборок называются «Автор - Название»."""
    return re.sub(r"\W+", "", re.split(r"\s+-\s+", name.strip())[-1].lower())


def members(todo, log=_log):
    """Скачанные подборки без копий: [(подборка, frozenset(bid))]."""
    kept, exact, by_tail, dupes = [], set(), {}, 0
    for c in todo:
        data = _load(os.path.join(RAW_DIR, "c%d.json" % c["id"]))
        if not data:
            continue
        maps = frozenset(data["maps"])
        if len(maps) < MIN_MAPS:
            continue
        fp = hash(tuple(sorted(maps)))
        tail = _tail(c["name"])
        if fp in exact or any(len(maps & o) >= 0.8 * len(maps | o) for o in by_tail.get(tail, ())):
            dupes += 1
            continue
        exact.add(fp)
        by_tail.setdefault(tail, []).append(maps)
        kept.append((c, maps))
    log("подборок: %d, копий чужих пропущено: %d" % (len(kept), dupes))
    return kept


# Подборки «farm», «pp farm», «DT farm»: игроки сами собирают карты, на которых легко набить pp.
# По ним тренер отличает фарм от честно трудных карт, когда ищет лучшие скоры профиля (coach).
FARM_JSON = os.path.join(RAW_DIR, "farm.json")
FARM_SEARCH = ["farm", "pp farm", "pp maps"]
_FARM_RX = re.compile(r"farm|\bpp\b|\dpp\b|фарм", re.I)
FARM_TOP = 400                  # самые популярные подборки; дальше - в основном копии и пустышки
FARM_TTL = 30 * 86400


def farm_index(log=_log, refresh=False):
    """{"maps": {bid: вес}, "count": {bid: подборок}} - карты из подборок про фарм pp, вес как у подборок
    навыков (_weight). Кэш на 30 дней; первая сборка - около 10 минут (одна подборка в 1.4 с)."""
    cached = _load(FARM_JSON)
    if cached and not refresh and time.time() - cached.get("built", 0) < FARM_TTL:
        return cached
    found = _search(FARM_SEARCH, log)
    if not found:                               # сайт не ответил - пустая база сочла бы фарм честными картами
        log("osu!Collector не ответил — беру прошлую базу фарм-карт" if cached else "osu!Collector не ответил")
        return cached or {"maps": {}}
    todo = [dict(c, n=_osu_size(c)) for c in found.values() if _FARM_RX.search(c["name"]) and _osu_size(c)]
    todo.sort(key=lambda c: (-c["fav"], c["id"]))
    log("подборок про фарм: %d, беру %d самых популярных" % (len(todo), min(len(todo), FARM_TOP)))
    todo = todo[:FARM_TOP]
    for i, c in enumerate(todo, 1):
        fetch(c)
        if i % 100 == 0:
            log("  %d/%d подборок" % (i, len(todo)))
    weight, count = collections.Counter(), collections.Counter()
    kept = members(todo, log)
    for c, bids in kept:
        for bid in bids:
            weight[bid] += _weight(c["fav"], len(bids))
            count[bid] += 1
    data = dict(built=int(time.time()), collections=len(kept), maps={str(b): round(w, 3) for b, w in weight.items()},
                count={str(b): n for b, n in count.items()})
    _save(FARM_JSON, data)
    return data


def fresh_meta(bids):
    """Данные карт с зеркала osu.direct, не больше 100 за раз: {bid: [sid, режим, звёзды, bpm, длина,
    статус, игр, md5] или None, если карты нет} или None, если зеркало не ответило."""
    r = net.get("https://osu.direct/api/v2/beatmaps", params={"ids": ",".join(map(str, bids))}, timeout=60)
    try:
        rows = r.json() if r is not None else None
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    got = {b.get("id"): b for b in rows if isinstance(b, dict)}
    out = {}
    for bid in bids:
        b = got.get(bid)
        out[bid] = None if b is None else [
            b.get("beatmapset_id"), b.get("mode_int", -1), round(b.get("difficulty_rating") or 0, 2),
            round(b.get("bpm") or 0, 1), b.get("total_length") or 0, STATUS.get(b.get("status"), 0),
            b.get("playcount") or 0, b.get("checksum") or ""]
    return out


def fetch_meta(bids, log=_log, refresh=False):
    """Данные карт с зеркала osu.direct, по 100 за запрос; известные повторно не спрашиваем, если не
    refresh. {bid: [sid, режим, звёзды, bpm, длина, статус, игр, md5] или None, если карты нет}."""
    meta = _load(META_JSON, {})
    todo = list(bids) if refresh else [b for b in bids if str(b) not in meta]
    log("данные карт: уже есть %d, запросить %d (%d запросов)"
        % (len(bids) - len(todo), len(todo), math.ceil(len(todo) / 100)))
    failed = 0
    for k in range(0, len(todo), 100):
        chunk = todo[k:k + 100]
        rows = fresh_meta(chunk)
        if rows is None:
            failed += 1                         # у этих карт остаются прежние данные, если были
            continue
        meta.update((str(bid), row) for bid, row in rows.items())
        if k // 100 % 25 == 24:
            _save(META_JSON, meta)
            log("  %d/%d" % (k + len(chunk), len(todo)))
    _save(META_JSON, meta)
    if failed:
        log("  зеркало не ответило на запросов: %d - у этих карт нет свежих данных, запусти сборку ещё раз"
            % failed)
    return meta


def compile_index(kept, meta, log=_log):
    """Одна база: карты osu!standard с данными и подборки как списки номеров этих карт."""
    maps, pos, cols = [], {}, []
    for c, bids in kept:
        idx = []
        for bid in sorted(bids):
            row = meta.get(str(bid))
            if not row or row[1] != 0 or not row[7] or not row[0]:
                continue                        # нет данных, не osu!standard или карта удалена
            if bid not in pos:
                sid, _mode, sr, bpm, length, status, playcount, md5 = row
                pos[bid] = len(maps)
                maps.append([bid, sid, md5, sr, bpm, length, status, playcount])
            idx.append(pos[bid])
        if len(idx) >= 2:
            cols.append([c["id"], c["fav"], len(bids), c["skills"], idx])
    log("в базе: подборок %d, карт %d" % (len(cols), len(maps)))
    return dict(v=1, built=int(time.time()), collections=cols, maps=maps)


def build(log=_log, limit=None, rediscover=True, refresh=False):
    found = discover(log) if rediscover else _load(os.path.join(RAW_DIR, "found.json"), {})
    todo = chosen(found)
    if limit:
        todo = todo[:limit]
    log("подборок к загрузке: %d (запросов не больше стольких же)" % len(todo))
    started = time.time()
    # два потока, но общий ограничитель net.BUCKETS не даёт чаще одного запроса в 1.4 с
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        for i, _data in enumerate(ex.map(fetch, todo), 1):
            if i % 50 == 0 or i == len(todo):
                log("  %d/%d подборок, %.0f мин" % (i, len(todo), (time.time() - started) / 60))
    kept = members(todo, log)
    seen = collections.Counter(b for _c, bids in kept for b in bids)
    log("разных карт: %d, из них в %d+ подборках: %d"
        % (len(seen), MIN_SEEN, sum(1 for n in seen.values() if n >= MIN_SEEN)))
    meta = fetch_meta(sorted(b for b, n in seen.items() if n >= MIN_SEEN), log, refresh)
    index = compile_index(kept, meta, log)
    _save(INDEX_JSON, index)
    return index


# ------------------------------------------------------------ база в памяти --

SET_SHARE = 0.5     # другая сложность того же набора в подборке - довод вполсилы
# «Народная» оценка 0..100 = 100 * (1 - exp(-вес / K)). K берётся из самой базы, чтобы шкала не плыла
# при пересборке: для навыка - две медианы веса размеченных карт (типичная карта навыка получает
# около 40, часто собираемая - 90+); для похожести - вес 50-го соседа образцов (около 70), но не
# меньше K_SIM_MIN, чтобы одна общая подборка не давала большой прибавки.
K_SIM_MIN = 2.0


def _weight(fav, n):
    """Вес подборки для навыка: популярная (в избранном) надёжнее, огромная - размытее."""
    return (1 + math.log2(1 + fav)) / (1 + math.log10(max(n, 10) / 10))


def _sim_weight(fav, n):
    """Вес подборки для похожести: соседство в большой подборке значит меньше, чем в маленькой."""
    return (1 + math.log2(1 + fav)) / math.sqrt(max(n, 10) / 10)


def _curve(e, k):
    return 100.0 * (1.0 - math.exp(-e / k))


class Index:
    """База в памяти: карты, подборки и вклад каждой подборки в навыки карт."""

    def __init__(self, raw):
        self.built = raw.get("built", 0)
        self.maps = raw["maps"]          # [bid, sid, md5, звёзды, bpm, длина, статус, игр]
        self.cols = raw["collections"]   # [id, в избранном, карт, {навык: доля}, [номера карт]]
        self.pos = {m[0]: i for i, m in enumerate(self.maps)}
        self.by_set = {}
        for i, m in enumerate(self.maps):
            self.by_set.setdefault(m[1], []).append(i)
        self.member = [[] for _m in self.maps]
        self.ev = {}                     # навык -> {карта: сумма весов подборок}
        for ci, (_cid, fav, n, skills, idx) in enumerate(self.cols):
            w = _weight(fav, n)
            for i in idx:
                self.member[i].append(ci)
                for s, share in skills.items():
                    ev = self.ev.setdefault(s, {})
                    ev[i] = ev.get(i, 0.0) + w * share
        self.k = {s: max(1.0, 2 * sorted(ev.values())[len(ev) // 2]) for s, ev in self.ev.items()}

    def _lookup(self, table, bid, sid):
        """Вес карты: её собственный или лучшей сложности того же набора (вполсилы)."""
        i = self.pos.get(bid)
        best, where = (table.get(i, 0.0), i) if i is not None else (0.0, None)
        for j in self.by_set.get(sid, ()):
            if j != i and table.get(j, 0.0) * SET_SHARE > best:
                best, where = table[j] * SET_SHARE, j
        return best, where

    def skill_info(self, bid, sid, keys):
        """Лучший из навыков: (оценка 0..100, число подборок, навык, id главной подборки)."""
        best = (0.0, 0, None, None)
        for s in keys:
            e, i = self._lookup(self.ev.get(s, {}), bid, sid)
            v = _curve(e, self.k.get(s, 1.0))
            if e > 0 and v > best[0]:
                cols = [ci for ci in self.member[i] if s in self.cols[ci][3]]
                top = max(cols, key=lambda ci: _weight(self.cols[ci][1], self.cols[ci][2]))
                best = (v, len(cols), s, self.cols[top][0])
        return best

    def presence(self, bid):
        """Вес карты во всех подборках навыков вместе - знаменатель доли фарма (farm_index)."""
        i = self.pos.get(bid)
        return sum(_weight(self.cols[ci][1], self.cols[ci][2]) for ci in self.member[i]) if i is not None else 0.0

    def skill_weights(self, keys):
        out = {}
        for s in keys:
            for i, e in self.ev.get(s, {}).items():
                if e > out.get(i, 0.0):
                    out[i] = e
        return out

    def niche(self, keys):
        """Популярность наборов среди любителей навыков: {набор: {подборка: вес}}. Подборка
        засчитывается набору один раз, сколько бы его сложностей в ней ни лежало."""
        out = {}
        for ci, (_cid, fav, n, skills, idx) in enumerate(self.cols):
            share = sum(skills.get(s, 0.0) for s in keys)
            if share:
                w = _weight(fav, n) * share
                for sid in set(self.maps[i][1] for i in idx):
                    out.setdefault(sid, {})[ci] = w
        return out

    def cooc(self, ref_sets):
        """Совместная встречаемость с наборами-образцами:
        {'weights': {карта: вес}, 'hits': {подборка: образцов}, 'k': масштаб оценки}."""
        hits = {}
        for sid in set(ref_sets):
            cols = set()
            for i in self.by_set.get(sid, ()):
                cols.update(self.member[i])
            for ci in cols:
                hits[ci] = hits.get(ci, 0) + 1
        weights = {}
        for ci, k in hits.items():
            _cid, fav, n, _skills, idx = self.cols[ci]
            w = k * _sim_weight(fav, n)
            for i in idx:
                weights[i] = weights.get(i, 0.0) + w
        ranked = sorted(weights.values(), reverse=True)
        k = max(K_SIM_MIN, ranked[min(49, len(ranked) - 1)] / 1.2) if ranked else K_SIM_MIN
        return dict(weights=weights, hits=hits, k=k)

    def cooc_info(self, co, bid, sid):
        """Соседство с образцами: (оценка 0..100, число общих подборок, id главной из них)."""
        e, i = self._lookup(co["weights"], bid, sid)
        if e <= 0:
            return 0.0, 0, None
        cols = [ci for ci in self.member[i] if ci in co["hits"]]
        top = max(cols, key=lambda ci: co["hits"][ci] * _sim_weight(self.cols[ci][1], self.cols[ci][2]))
        return _curve(e, co["k"]), len(cols), self.cols[top][0]

    def candidate(self, i):
        """Кандидат в формате trainer; исполнитель, название и сложность берутся потом из .osu."""
        bid, sid, md5, sr, bpm, length, status, playcount = self.maps[i]
        return dict(bid=bid, sid=sid, md5=md5, sr=sr, diff="", title="", artist="", mapper="",
                    bpm=bpm, length=length, status=STATUS_NAME.get(status, ""), playcount=playcount,
                    genre=0, local=False, slot="", src="collector")

    def top(self, weights, ok, exclude=()):
        """Карты по убыванию веса, прошедшие фильтр ok(кандидат)."""
        for i in sorted(weights, key=weights.get, reverse=True):
            c = self.candidate(i)
            if c["bid"] not in exclude and ok(c):
                yield c


def refreshed(cands, pages):
    """Кандидаты из базы со свежими данными osu.direct: звёзды меняются при пересчётах рейтинга osu!,
    игр становится больше, карты обновляют. По 100 карт за запрос, не больше pages запросов; карт,
    которых на osu! больше нет, среди них не будет. Если зеркало не ответило, бросает OSError."""
    cands = iter(cands)
    for _n in range(pages):
        batch = list(itertools.islice(cands, 100))
        if not batch:
            return
        rows = fresh_meta([c["bid"] for c in batch])
        if rows is None:
            raise OSError("osu.direct не ответил")
        for c in batch:
            row = rows[c["bid"]]
            if row and row[1] == 0 and row[7]:
                _sid, _mode, sr, bpm, length, status, playcount, md5 = row
                c.update(md5=md5, sr=sr, bpm=bpm, length=length, status=STATUS_NAME.get(status, ""),
                         playcount=playcount)
                yield c


_INDEX = {"key": None, "index": None}
_LOCK = threading.Lock()


def source():
    """Самая свежая база: собранная здесь (cache/collector.json) или из комплекта (data/)."""
    paths = [p for p in (INDEX_JSON, SEED) if os.path.exists(p)]
    return max(paths, key=os.path.getmtime) if paths else None


def load():
    """База в памяти или None, если её нет. Перечитывается, только когда файл сменился."""
    path = source()
    if not path:
        return None
    key = (path, os.path.getmtime(path))
    with _LOCK:
        if _INDEX["key"] != key:
            raw = _load(path)
            _INDEX.update(key=key, index=Index(raw) if raw else None)
        return _INDEX["index"]


def page(collection_id):
    return "https://osucollector.com/collections/%d" % collection_id


# ---------------------------------------------------------- командная строка --

def plan(log=_log, rediscover=True):
    """Сколько подборок потребует сборка - без скачивания их состава."""
    found = discover(log) if rediscover else _load(os.path.join(RAW_DIR, "found.json"), {})
    todo = chosen(found)
    per_skill = {}
    for c in todo:
        for s in c["skills"]:
            per_skill.setdefault(s, []).append(c)
    log("найдено подборок: %d, подходят: %d, карт в них: %d"
        % (len(found), len(todo), sum(c["n"] for c in todo)))
    for s, cs in sorted(per_skill.items(), key=lambda kv: -len(kv[1])):
        log("  %-14s %4d подборок, карт %6d | %s" % (s, len(cs), sum(c["n"] for c in cs),
                                                     "; ".join(c["name"][:30] for c in cs[:6])))
    return todo


def stats(log=_log):
    index = load()
    if not index:
        log("базы нет: python collector.py build")
        return
    log("база: %s, собрана %s" % (source(), time.strftime("%Y-%m-%d", time.localtime(index.built))))
    log("подборок %d, карт %d" % (len(index.cols), len(index.maps)))
    for s, ev in sorted(index.ev.items(), key=lambda kv: -len(kv[1])):
        vals = sorted(ev.values())
        log("  %-14s карт %6d | вес: медиана %.2f, 90%% %.2f, 99%% %.2f | K %.2f"
            % (s, len(vals), vals[len(vals) // 2], vals[int(len(vals) * 0.9)], vals[int(len(vals) * 0.99)],
               index.k[s]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    again = "--cached" not in sys.argv
    if cmd == "plan":
        plan(rediscover=again)
    elif cmd == "build":
        lim = [int(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--limit=")]
        build(limit=lim[0] if lim else None, rediscover=again, refresh="--refresh-meta" in sys.argv)
    elif cmd == "stats":
        stats()
