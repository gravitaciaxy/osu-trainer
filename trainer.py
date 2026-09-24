# -*- coding: utf-8 -*-
"""
osu!drill - подбор карт по навыку, похожести или турнирным слотам, скачивание и коллекция в osu!lazer.

Примеры:
    python trainer.py --skill streams --stars 5.2-6.0 --count 30
    python trainer.py --skill fingercontrol,tech --like "2591748,2823535" --stars 4.8-6 --count 40
    python trainer.py --tournament NM2,NM3,NM4 --stars 6.0-6.6 --count 40 --digits 5,6
    python trainer.py --skill jumps --stars 5-6 --genres 10,11 --words "speedcore,dnb"
    python trainer.py --popular --skill tech --stars 5-7 --count 40
"""
import argparse
import concurrent.futures as cf
import itertools
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyze  # noqa: E402
import collector  # noqa: E402
import config  # noqa: E402
import net  # noqa: E402
import pools  # noqa: E402
import skills  # noqa: E402
from i18n import _, number, set_lang  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CACHE = config.CACHE_DIR
DL = config.DOWNLOAD_DIR
BACKUPS = config.BACKUP_DIR
STATUS = {"ranked": 1, "approved": 2, "qualified": 3, "loved": 4}

# жанры osu! (genre_id): название по-русски, по-английски и по-испански
GENRES = {
    2: ("Игры", "Video game", "Videojuegos"), 3: ("Аниме", "Anime", "Anime"),
    4: ("Рок", "Rock", "Rock"), 5: ("Поп", "Pop", "Pop"),
    10: ("Электроника", "Electronic", "Electrónica"), 11: ("Метал", "Metal", "Metal"),
    9: ("Хип-хоп", "Hip hop", "Hip hop"), 12: ("Классика", "Classical", "Clásica"),
    14: ("Джаз", "Jazz", "Jazz"), 13: ("Фолк", "Folk", "Folk"),
    7: ("Необычное", "Novelty", "Novedad"), 6: ("Другое", "Other", "Otro"),
    1: ("Не указан", "Unspecified", "Sin especificar"),
}


def rng(text, default=(None, None)):
    if text is None or text == "":
        return default
    if isinstance(text, (list, tuple)):
        return float(text[0]), float(text[1])
    text = str(text).replace(",", ".")
    if "-" in text.strip("-"):
        a, b = text.split("-", 1)
        return float(a), float(b)
    return float(text), float(text)


def csv(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).replace(";", ",").split(",") if v.strip()]


# ------------------------------------------------------ параметры задачи ----

# имя: (тип, минимум, максимум). Всё, чего здесь нет, из интерфейса не принимается.
PARAMS = {
    "skill": (str,), "like": (str,), "tournament": (str,), "years": (str,), "tours": (str,),
    "digits": (str,), "tiers": (str,), "sources": (str,), "stars": (str,), "name": (str,),
    "lang": (str,), "bpm": (str,), "stream_bpm": (str,), "length": (str,), "status": (str,),
    "genres": (str,), "words": (str,),
    "per_tournament": (int, 1, 30), "max_lookups": (int, 50, 5000), "count": (int, 1, 300),
    "split": (float, 0.1, 5), "like_weight": (float, 0, 1), "crowd_weight": (float, 0, 1),
    "min_pc": (int, 0, 10 ** 9),
    "pop_weight": (float, 0, 50), "pool": (int, 20, 1500), "depth": (int, 50, 3000),
    "per_set": (int, 1, 10), "per_set_probe": (int, 1, 10), "min_score": (float, 0, 100),
    "threads": (int, 1, 8),
    "dry_run": (bool,), "no_download": (bool,), "no_collection": (bool,),
    "local_only": (bool,), "skip_owned": (bool,), "popular": (bool,),
}
SERVER_LIMITS = {"count": 100, "pool": 400, "depth": 400, "max_lookups": 900, "threads": 4}


def coerce_params(p, server=False):
    """Проверяет и приводит параметры из интерфейса; лишнее отбрасывает."""
    a = defaults()
    for key, value in (p or {}).items():
        key = str(key).replace("-", "_")
        spec = PARAMS.get(key)
        if spec is None or value is None or value == "":
            continue
        kind = spec[0]
        try:
            if kind is bool:
                value = value is True or str(value).lower() in ("1", "true", "yes", "on")
            elif kind is str:
                value = str(value)[:2000].strip()
            else:
                value = kind(value)
                if len(spec) == 3:
                    value = max(spec[1], min(spec[2], value))
        except (TypeError, ValueError):
            continue
        setattr(a, key, value)
    if server:
        for key, cap in SERVER_LIMITS.items():
            setattr(a, key, min(getattr(a, key), cap))
        a.server = True
        a.dry_run = True
        a.local_only = a.skip_owned = False
        a.no_download = a.no_collection = True
    return a


# ------------------------------------------------------------------ realm ---

def realm_path():
    data = config.find_osu_data()
    if not data:
        raise RuntimeError(_("Не найдена папка osu!lazer (client.realm). Укажи её в настройках."))
    return os.path.join(data, "client.realm")


def realm_cmd(cmd, arg=None):
    node = config.find_node()
    if not node:
        raise RuntimeError(_("Не найден Node.js - он нужен для работы с базой osu!. Установи с nodejs.org."))
    args = [node, os.path.join(config.TOOL_DIR, "realm_tool.js"), cmd, realm_path()]
    if arg:
        args.append(arg)
    kw = {}
    if platform.system() == "Windows":
        kw["creationflags"] = 0x08000000        # без мигающего окна консоли
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", cwd=config.TOOL_DIR, **kw)
    if r.returncode != 0:
        raise RuntimeError(_("Ошибка доступа к базе osu!: %s", (r.stderr.strip() or r.stdout.strip())))
    return r.stdout.strip()


def local_library():
    out = os.path.join(CACHE, "local.json")
    os.makedirs(CACHE, exist_ok=True)
    realm_cmd("local", out)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def collections():
    return json.loads(realm_cmd("list") or "[]")


def backup_realm():
    os.makedirs(BACKUPS, exist_ok=True)
    dst = os.path.join(BACKUPS, time.strftime("client-%Y%m%d-%H%M%S.realm"))
    shutil.copy2(realm_path(), dst)
    for name in sorted(os.listdir(BACKUPS))[:-10]:
        os.remove(os.path.join(BACKUPS, name))
    return dst


def import_into_osu(paths):
    """Отдаёт .osz игре: запуск osu! с файлами (Windows) или открытие системным обработчиком."""
    exe = config.find_osu_exe()
    system = platform.system()
    if exe and system == "Windows":
        for i in range(0, len(paths), 12):
            subprocess.Popen([exe] + paths[i:i + 12])
            time.sleep(4)
        return
    for p in paths:
        if system == "Windows":
            os.startfile(p)
        elif system == "Darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
        time.sleep(0.5)


# ------------------------------------------------------------- кандидаты ----

def set_passes(s, a):
    """Фильтры уровня набора: жанр и слова в тегах."""
    genres = [int(g) for g in csv(a.genres) if g.isdigit()]
    if genres and (s.get("genre_id") or 0) not in genres:
        return False
    words = [w.lower() for w in csv(a.words)]
    if words:
        info = {"tags": s.get("tags"), "title": s.get("title"), "artist": s.get("artist"),
                "source": s.get("source")}
        if not pools.text_match(info, words):
            return False
    return True


def mirror_cand(s, b):
    """Кандидат из набора s и его сложности b в формате osu!api v2 (так отвечают зеркала)."""
    return dict(bid=b["id"], sid=s["id"], md5=b["checksum"], sr=b["difficulty_rating"], diff=b["version"],
                title=s["title"], artist=s["artist"], mapper=s.get("creator", ""),
                bpm=b.get("bpm") or s.get("bpm") or 0, length=b["total_length"],
                status=s.get("status", ""), playcount=b.get("playcount", 0),
                genre=s.get("genre_id") or 0, local=False, slot="")


def map_filter(a, stars, statuses, have_md5, installed_only=False):
    """Проверка карты по данным базы или зеркала: звёзды, длина, BPM, статус, число игр, есть ли в игре."""
    def ok(c):
        return (stars[0] - 0.02 <= c["sr"] <= stars[1] + 0.02
                and a.length[0] <= c["length"] <= a.length[1]
                and a.bpm[0] <= c["bpm"] <= a.bpm[1]
                and c["status"] in statuses
                and c["playcount"] >= a.min_pc
                and not (a.skip_owned and c["md5"] in have_md5)
                and (not installed_only or c["md5"] in have_md5))
    return ok


def gather_online(queries, a, stars, have_md5, log, seed=()):
    """Кандидаты с зеркала; seed - уже найденные (из коллекций игроков), зеркало добирает до a.pool."""
    cands = {c["bid"]: c for c in seed}
    seen_sets, pages = set(c["sid"] for c in seed), 0
    statuses = [STATUS[s] for s in csv(a.status) if s in STATUS] or [1]
    extra = csv(a.words)[:3]           # слова из фильтра тоже ищем на зеркале
    for status in statuses:
        for query in queries + extra:
            offset = 0
            while offset < a.depth and len(cands) < a.pool:
                sets = net.search(query, stars, status, offset)
                pages += 1
                if not sets:
                    break
                for s in sets:
                    if s["id"] in seen_sets:
                        continue
                    seen_sets.add(s["id"])
                    if not set_passes(s, a):
                        continue
                    diffs = []
                    for b in s.get("beatmaps", []):
                        if b.get("mode_int") != 0:
                            continue
                        if not (stars[0] - 0.02 <= b["difficulty_rating"] <= stars[1] + 0.02):
                            continue
                        if not (a.length[0] <= b["total_length"] <= a.length[1]):
                            continue
                        bpm = b.get("bpm") or s.get("bpm") or 0
                        if not (a.bpm[0] <= bpm <= a.bpm[1]):
                            continue
                        diffs.append(b)
                    diffs.sort(key=lambda b: -b["difficulty_rating"])
                    for b in diffs[:a.per_set_probe]:
                        if a.skip_owned and b["checksum"] in have_md5:
                            continue
                        if b.get("playcount", 0) < a.min_pc:
                            continue
                        cands[b["id"]] = mirror_cand(s, b)
                offset += 50
            if len(cands) >= a.pool:
                break
    log(_("  запросов к зеркалу: %d, кандидатов: %d", pages, len(cands)))
    return list(cands.values())


def gather_local(a, stars, library):
    files = os.path.join(config.find_osu_data(), "files")
    words = [w.lower() for w in csv(a.words)]
    out = []
    for b in library:
        if b["ruleset"] != "osu" or not (stars[0] <= b["sr"] <= stars[1]):
            continue
        if not (a.length[0] <= b["len"] <= a.length[1]):
            continue
        if not (a.bpm[0] <= b["bpm"] <= a.bpm[1]):
            continue
        if words and not pools.text_match(b, words):
            continue
        h = b["fileHash"]
        path = os.path.join(files, h[0], h[:2], h)
        if not os.path.exists(path):
            continue
        out.append(dict(bid=b["onlineId"], sid=b["setId"], md5=b["md5"], sr=b["sr"],
                        diff=b["diff"], title=b["title"], artist=b["artist"], mapper="",
                        bpm=b["bpm"], length=b["len"], status="local",
                        playcount=0, local=True, path=path, slot=""))
    return out


CROWD_SHARE = 0.5      # до половины кандидатов - из коллекций игроков, остальных ищет зеркало


def gather_crowd(index, keys, co, refs, a, stars, have_md5, log):
    """Кандидаты из коллекций игроков osu!Collector: самые «народные» карты навыка и соседи образцов."""
    if [g for g in csv(a.genres) if g.isdigit()] or csv(a.words):
        return []           # жанра и тегов в базе osu!Collector нет - такие фильтры проверит зеркало
    ok = map_filter(a, stars, set(s for s in csv(a.status) if s in STATUS) or {"ranked"}, have_md5)
    tables = [t for t in (co and co["weights"], keys and index.skill_weights(keys)) if t]
    limit = max(10, int(a.pool * CROWD_SHARE)) // max(len(tables), 1)
    out, seen = [], set(refs)
    for table in tables:
        for c in index.top(table, ok, limit, a.per_set_probe, exclude=seen):
            seen.add(c["bid"])
            out.append(c)
    log(_("  из коллекций игроков (osu!Collector): %d", len(out)))
    return out


def crowd_booster(index, keys, cfgs, co, lang):
    """Довод коллекций игроков для кандидата: (0..100, пояснение, id подборки) или None."""
    names = {k: skills.title(cfg, lang) for k, cfg in zip(keys, cfgs)}

    def boost(c):
        best = None
        if keys:
            v, n, skill, cid = index.skill_info(c["bid"], c["sid"], keys)
            if v > 0:
                best = (v, _("osu!Collector: подборок «%s»: %d", names[skill], n), cid)
        if co:
            v, n, cid = index.cooc_info(co, c["bid"], c["sid"])
            if v > 0 and (best is None or v > best[0]):
                best = (v, _("osu!Collector: общих подборок с образцами: %d", n), cid)
        return best

    return boost


def make_scorer(cfgs, profile, a, boost=None):
    sim = skills.profile_scorer(profile) if profile else None

    def scorer(m, c):
        parts, whys, extra = [], [], {}
        if sim:
            s_sim, why = sim(m)
            parts.append((s_sim, a.like_weight))
            whys.append(why)
        if cfgs:
            best = max((cfg["score"](m) for cfg in cfgs), key=lambda r: r[0])
            parts.append((best[0], 1.0 - (a.like_weight if sim else 0.0)))
            whys.append(best[1])
        total = sum(v * w for v, w in parts) / sum(w for _w, w in parts)
        if a.pop_weight:
            total += a.pop_weight * skills.sc(math.log10(max(c.get("playcount", 0), 1)), 4.0, 6.0)
        crowd = boost(c) if boost else None
        if crowd:
            # «мягкое ИЛИ»: чем ниже оценка по формуле, тем сильнее её поднимает мнение игроков;
            # карт, которых нет в коллекциях, это не касается
            total = 100 - (100 - min(total, 100.0)) * (1 - a.crowd_weight * crowd[0] / 100)
            whys.append(crowd[1])
            extra["page"] = collector.page(crowd[2])
        return min(total, 100.0), " | ".join(whys), extra

    return scorer


def score_candidate(c, scorer, a):
    try:
        if c.get("local"):
            with open(c["path"], encoding="utf-8", errors="ignore") as f:
                text = f.read()
        else:
            text = net.osu_file(c["bid"], CACHE)
        if not text:
            return None
        m = analyze.metrics(text)
        if not m:
            return None
        if a.stream_bpm[0] is not None:
            if not (a.stream_bpm[0] <= m["stream_bpm"] <= a.stream_bpm[1]):
                return None
        score, why, extra = scorer(m, c)
        out = dict(c)
        if c.get("src") == "collector":         # в базе коллекций названий нет - они есть в самом .osu
            meta = analyze.metadata(text)
            out.update(title=meta.get("Title", ""), artist=meta.get("Artist", ""),
                       mapper=meta.get("Creator", ""), diff=meta.get("Version", ""))
        out.update(score=round(score, 1), why=why, metrics=m, **extra)
        return out
    except Exception:
        return None


# ------------------------------------------------------ турнирные мапулы ----

def pick_tournament(a, stars, log):
    slots = [s.upper() for s in csv(str(a.tournament).replace(" ", ","))]
    mods = [s for s in slots if s.isalpha()]
    exact = [s for s in slots if not s.isalpha()]
    years = rng(a.years, None)
    years = (int(years[0]), int(years[1])) if years else None
    desc = ", ".join(slots)
    if years:
        desc += ", %d-%d" % years
    if a.digits:
        desc += ", " + "/".join(csv(a.digits))
    if a.tours:
        desc += ", " + ", ".join(csv(a.tours))
    log(_("Ищу в турнирных пулах: %s", desc))

    def progress(done, total, found):
        log(_("  проверено %d/%d карт пула, подходящих: %d", done, total, found))

    bpm = a.bpm if a.bpm != (0, 10000) else None
    length = a.length if a.length != (0, 100000) else None
    found, total = pools.query(slots=exact or None, mods=mods or None, stars=stars, years=years,
                               tournaments=csv(a.tours) or None, digits=csv(a.digits) or None,
                               tiers=csv(a.tiers) or None, sources=csv(a.sources) or None,
                               need=a.count, max_lookups=a.max_lookups, progress=progress,
                               per_tournament=a.per_tournament,
                               genres=[int(g) for g in csv(a.genres) if g.isdigit()] or None,
                               words=[w.lower() for w in csv(a.words)] or None,
                               bpm=bpm, length=length)
    log(_("  карт в выбранных слотах: %d, отобрано: %d", total, len(found)))
    out = []
    for e in found:
        rank = pools.digit_label(e.get("digits"))
        where = "%s %s · %s · %s" % (e["tournament"], e["edition"], e["round"] or "-", e["slot"])
        if rank != "?":
            where += " · " + rank
        if e.get("tier"):
            where += " · tier " + e["tier"]
        out.append(dict(bid=e["bid"], sid=e["sid"], md5=e["md5"], sr=e["sr"],
                        diff=e.get("version", ""), title=e.get("title", ""),
                        artist=e.get("artist", ""), mapper="", bpm=e.get("bpm", 0),
                        length=e.get("length", 0), status=e.get("status", ""),
                        playcount=e.get("playcount", 0), genre=e.get("genre", 0), local=False,
                        score=0.0, slot=e["slot"], why=where, page=e.get("page", ""), metrics={}))
    return out


# ------------------------------------------------------ популярные песни ----

POPULAR_PAGES = 10          # не больше стольких запросов к зеркалу по 100 наборов
# звёзды в базе osu!Collector - с её сборки, а пересчёты рейтинга сдвигают их на десятые (у 99% карт
# меньше чем на 0.6): по базе карты отбираются с таким запасом, точно - по свежим данным зеркала
SR_DRIFT = 0.6


def _norm(text):
    return re.sub(r"\W+", "", (text or "").lower())


def song_key(s):
    """Песня набора: исполнитель и название без приписок в скобках - (TV Size), [Cut Ver.], (lapix Remix),
    чтобы разные наборы одной песни не повторялись в подборке."""
    title = s.get("title") or ""
    short = re.sub(r"(\s*[(\[][^()\[\]]*[)\]])+\s*$", "", title)
    return _norm(s.get("artist")), _norm(short) or _norm(title)


def popular_crowd(a, keys, cfgs, rough, ok, log):
    """Песни, которые любители навыков чаще всего кладут в свои подборки на osu!Collector.
    Наборы одной песни складываются, подборка считается один раз; из песни берутся сложности,
    которые эти игроки собирают чаще всего. rough - фильтр по данным базы, ok - по свежим."""
    index = collector.load()
    if not index:
        raise RuntimeError(_("Нет базы коллекций игроков osu!Collector: python collector.py build"))
    names = " + ".join(skills.title(c, a.lang) for c in cfgs)
    log(_("Самые популярные песни среди любителей %s - по коллекциям игроков osu!Collector...", names))
    sets = index.niche(keys)

    def collected():
        """Наборы по убыванию популярности с собранными сложностями под фильтры: (набор, {сложность: вес})."""
        for sid in sorted(sets, key=lambda k: -sum(sets[k].values())):
            bids = {}
            for i in index.by_set.get(sid, ()):
                w = sum(index.ev.get(s, {}).get(i, 0.0) for s in keys)
                if w > 0 and rough(index.candidate(i)):
                    bids[index.maps[i][0]] = w
            if bids:
                yield sid, bids

    songs, looked, queue = {}, 0, collected()
    for _n in range(POPULAR_PAGES):
        chunk = list(itertools.islice(queue, 100))
        if not chunk:
            break
        info = net.sets_by_id([sid for sid, _b in chunk])
        if info is None:
            raise RuntimeError(_("Зеркало osu.direct не отвечает - попробуй позже"))
        looked += len(chunk)
        for sid, bids in chunk:
            s = info.get(sid)
            if not s or not set_passes(s, a):
                continue
            song = songs.setdefault(song_key(s), dict(cols={}, maps=[]))
            song["cols"].update(sets[sid])
            for b in s.get("beatmaps", []):
                if b.get("id") not in bids:
                    continue
                c = mirror_cand(s, b)           # звёзды, md5 и статус - свежие, с зеркала
                if ok(c):
                    c["niche"] = bids[b["id"]]
                    song["maps"].append(c)
        # запас по наборам: другой набор той же песни может стоять ниже и добавить ей подборок
        if sum(1 for g in songs.values() if g["maps"]) >= a.count and looked >= 2 * a.count + 20:
            break
    songs = sorted((g for g in songs.values() if g["maps"]), key=lambda g: -sum(g["cols"].values()))
    log(_("  просмотрено наборов: %d, песен: %d", looked, len(songs)))
    for g in songs:
        why = _("osu!Collector: подборок «%s»: %d", names, len(g["cols"]))
        page = collector.page(index.cols[max(g["cols"], key=g["cols"].get)][0])
        g["maps"].sort(key=lambda c: -c["niche"])
        for c in g["maps"]:
            c.update(why=why, page=page)
    return [g["maps"] for g in songs]


def popular_plays(a, stars, statuses, ok, log):
    """Самые играемые карты: наборы с osu.direct по убыванию игр их самой играемой сложности.
    Ни одна сложность набора не сыграна больше этого числа, поэтому, как только оно у очередного
    набора меньше, чем у последней нужной песни, подборка точная и дальше искать незачем."""
    log(_("Самые играемые карты osu! - по данным osu.direct..."))
    filters = ["beatmaps.difficulty_rating %g TO %g" % (max(stars[0] - 0.02, 0), stars[1] + 0.02)]
    if a.bpm != (0, 10000):
        filters.append("beatmaps.bpm %g TO %g" % a.bpm)
    if a.length != (0, 100000):
        filters.append("beatmaps.total_length %g TO %g" % a.length)
    genres = [int(g) for g in csv(a.genres) if g.isdigit()]
    if genres:
        filters.append("(%s)" % " OR ".join("genre_id = %d" % g for g in genres))
    status = ",".join(str(STATUS[s]) for s in sorted(statuses))
    songs, looked = {}, 0
    for page in range(POPULAR_PAGES):
        sets = net.direct_search(filters, "beatmaps.playcount:desc", page * 100, status)
        if sets is None:
            if not page:
                raise RuntimeError(_("Зеркало osu.direct не отвечает - попробуй позже"))
            break
        looked += len(sets)
        for s in sets:
            if not set_passes(s, a):          # слова в тегах зеркало так не ищет - проверяем сами
                continue
            maps = [c for c in (mirror_cand(s, b) for b in s.get("beatmaps", []) if b.get("mode_int") == 0)
                    if ok(c)]
            if maps:
                songs.setdefault(song_key(s), []).extend(maps)
        if len(sets) < 100:
            break
        edge = max((b.get("playcount", 0) for b in sets[-1].get("beatmaps", [])), default=0)
        best = sorted((max(c["playcount"] for c in g) for g in songs.values()), reverse=True)
        if len(best) >= a.count and best[a.count - 1] >= edge:
            break
    log(_("  просмотрено наборов: %d, песен: %d", looked, len(songs)))
    out = sorted(songs.values(), key=lambda g: -max(c["playcount"] for c in g))
    for g in out:
        g.sort(key=lambda c: -c["playcount"])
        for c in g:
            c["why"] = _("игр на osu!: %s", number(c["playcount"]))
    return out


def pick_popular(a, stars, keys, cfgs, have_md5, log):
    """Самые популярные песни: среди любителей навыков (коллекции игроков) или вообще (число игр)."""
    statuses = set(s for s in csv(a.status) if s in STATUS) or {"ranked", "loved"}
    ok = map_filter(a, stars, statuses, have_md5, installed_only=a.local_only)
    if keys:
        rough = map_filter(a, (stars[0] - SR_DRIFT, stars[1] + SR_DRIFT), statuses, have_md5, a.local_only)
        songs = popular_crowd(a, keys, cfgs, rough, ok, log)
    else:
        songs = popular_plays(a, stars, statuses, ok, log)
    picked = []
    for maps in songs:
        for c in maps[:a.per_set]:
            c.update(score=0.0, metrics={})
            picked.append(c)
            if len(picked) >= a.count:
                return picked
    return picked


# ------------------------------------------------------- подбор и запись ----

def prepare(a):
    set_lang(getattr(a, "lang", "en"))
    os.makedirs(CACHE, exist_ok=True)
    if not getattr(a, "server", False):      # на сервере карты не скачиваются, папка только для чтения
        os.makedirs(DL, exist_ok=True)
    a.stars_range = rng(a.stars)
    a.bpm = rng(a.bpm, (0, 10000))
    a.length = rng(a.length, (0, 100000))
    a.stream_bpm = rng(a.stream_bpm, (None, None))
    return a


def select(a, log=print):
    """Подбирает карты. Возвращает (список карт, имя коллекции). Игру не трогает."""
    stars = a.stars_range
    if a.tournament:
        title = _("Турнирные") + " " + str(a.tournament).replace(" ", "")
        cfgs = []
    else:
        if not a.skill and not a.like and not a.popular:
            raise RuntimeError(_("Нужен навык, карты-образцы или турнирные слоты"))
        resolved = [skills.resolve(x) for x in csv(a.skill)]
        keys = [k for k, _cfg in resolved]
        cfgs = [cfg for _k, cfg in resolved]
        title = " + ".join(skills.title(c, a.lang) for c in cfgs)
        if a.popular:
            title = (_("Популярные") + " " + title).strip()
        elif not title:
            title = _("Похожие")
    name = a.name or ("%s %g-%g*" % (title, stars[0], stars[1]))
    log(_("Задача: %s | звёзды %g-%g | карт: %d", title, stars[0], stars[1], a.count))

    have_md5 = set()
    library = []
    if not getattr(a, "server", False):
        log(_("Читаю библиотеку osu!..."))
        library = local_library()
        have_md5 = set(b["md5"] for b in library)
        log(_("  установлено сложностей: %d", len(library)))

    if a.tournament:
        picked = pick_tournament(a, stars, log)
    elif a.popular:
        picked = pick_popular(a, stars, keys, cfgs, have_md5, log)
    else:
        profile, ids, ref_sets = None, [], set()
        if a.like:
            ids = [int(x) for x in csv(str(a.like).replace(" ", ",")) if x.isdigit()][:20]
            log(_("Строю профиль, карт-образцов: %d...", len(ids)))
            ref_metrics = []
            for bid in ids:
                text = net.osu_file(bid, CACHE)
                m = analyze.metrics(text) if text else None
                if m:
                    ref_metrics.append(m)
                    sid = analyze.metadata(text).get("BeatmapSetID", "")
                    if sid.isdigit():
                        ref_sets.add(int(sid))
                else:
                    log(_("  не удалось разобрать карту %s", bid))
            if not ref_metrics:
                raise RuntimeError(_("Не удалось разобрать ни одну карту-образец"))
            profile = skills.build_profile(ref_metrics)
            log(_("  профиль: %.0f BPM, streams %.0f%%, смен ритма %.0f%%, sliders %.0f%%",
                  profile["bpm"], profile["stream_ratio"] * 100,
                  profile["switch_ratio"] * 100, profile["slider_ratio"] * 100))
        index = collector.load() if a.crowd_weight > 0 else None
        co = index.cooc(ref_sets) if index and ref_sets else None
        if co is not None and not co["hits"]:
            log(_("  карт-образцов нет в коллекциях игроков osu!Collector"))
            co = None
        boost = crowd_booster(index, keys, cfgs, co, a.lang) if index else None
        scorer = make_scorer(cfgs, profile, a, boost)

        queries = []
        for cfg in cfgs:
            for q in cfg["queries"]:
                if q not in queries:
                    queries.append(q)
        queries.append("")

        log(_("Собираю кандидатов..."))
        if a.local_only and library:
            cands = gather_local(a, stars, library)
            log(_("  подходящих установленных карт: %d", len(cands)))
        else:
            seed = gather_crowd(index, keys, co, ids, a, stars, have_md5, log) if index else []
            cands = gather_online(queries, a, stars, have_md5, log, seed)
        if not cands:
            raise RuntimeError(_("Кандидатов не найдено - ослабь фильтры"))

        log(_("Анализирую карты: %d...", len(cands)))
        scored, done = [], 0
        # язык сообщений хранится в потоке - потокам разбора его нужно передать
        with cf.ThreadPoolExecutor(max_workers=a.threads, initializer=set_lang,
                                   initargs=(getattr(a, "lang", "en"),)) as ex:
            for res in ex.map(lambda c: score_candidate(c, scorer, a), cands):
                done += 1
                if done % 25 == 0:
                    log(_("  ...%d/%d", done, len(cands)))
                if res:
                    scored.append(res)
        scored.sort(key=lambda c: -c["score"])

        if a.min_score is not None:
            min_score = a.min_score
        elif a.like:
            min_score = 0.0
        else:
            min_score = min(c["min_score"] for c in cfgs)

        picked, per_set = [], {}
        for c in scored:
            if c["score"] < min_score:
                break
            if per_set.get(c["sid"], 0) >= a.per_set:
                continue
            per_set[c["sid"]] = per_set.get(c["sid"], 0) + 1
            picked.append(c)
            if len(picked) >= a.count:
                break

    if not picked:
        raise RuntimeError(_("Ничего не подошло - ослабь фильтры или понизь порог"))

    for c in picked:
        c["owned"] = c["md5"] in have_md5 if have_md5 else None
    log("")
    log(_("Отобрано карт: %d", len(picked)))
    for i, c in enumerate(picked, 1):
        mark = "*" if c["owned"] else "+"
        log("%3d. %s %.2f* %s - %s [%s] - %s"
            % (i, mark, c["sr"], c["artist"], c["title"], c["diff"], c["why"]))
    return picked, name


def apply(picked, name, a, log=print):
    """Скачивает недостающие карты, отправляет в osu! и записывает коллекцию."""
    library = local_library()
    have_md5 = set(b["md5"] for b in library)
    for c in picked:
        c["owned"] = c["md5"] in have_md5
    result = dict(name=name, picked=picked, downloaded=0, collection=None)

    to_get = [c for c in picked if not c["owned"] and not c.get("local")]
    if to_get and not a.no_download:
        sids = sorted(set(int(c["sid"]) for c in to_get if c.get("sid")))
        log("")
        log(_("Скачиваю наборы карт: %d...", len(sids)))
        paths = []
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            for sid, path in zip(sids, ex.map(lambda s: net.osz(s, DL), sids)):
                if path:
                    paths.append(path)
                    log(_("  готово %s (%.1f МБ)", sid, os.path.getsize(path) / 1048576))
                else:
                    log(_("  не скачался %s", sid))
        result["downloaded"] = len(paths)
        if paths:
            log(_("Отправляю в osu! наборов: %d...", len(paths)))
            import_into_osu(paths)
            log(_("  osu! добавит их в фоне"))
    elif to_get:
        log(_("Нет в игре карт: %d, скачивание отключено", len(to_get)))

    if a.no_collection:
        return result

    stars = getattr(a, "stars_range", None)
    groups = {}
    if a.split and stars:
        for c in picked:
            lo = stars[0] + int((c["sr"] - stars[0]) / a.split) * a.split
            groups.setdefault("%s %.1f-%.1f" % (name, lo, lo + a.split), []).append(c["md5"])
    else:
        groups[name] = [c["md5"] for c in picked]

    log("")
    log(_("Резервная копия базы: %s", backup_realm()))
    payload = [{"name": n, "hashes": h} for n, h in groups.items()]
    pf = os.path.join(CACHE, "collection_payload.json")
    with open(pf, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    res = json.loads(realm_cmd("add", pf))
    for r in res:
        if r["created"]:
            log(_("Коллекция \"%s\" создана: +%d, всего %d", r["name"], r["added"], r["total"]))
        else:
            log(_("Коллекция \"%s\" дополнена: +%d, всего %d", r["name"], r["added"], r["total"]))
    result["collection"] = res
    log("")
    log(_("Готово."))
    return result


def run(a, log=print):
    prepare(a)
    picked, name = select(a, log)
    if not getattr(a, "server", False):
        with open(os.path.join(config.TOOL_DIR, "last_run.json"), "w", encoding="utf-8") as f:
            json.dump(picked, f, ensure_ascii=False, indent=1)
    if a.dry_run or getattr(a, "server", False):
        return dict(name=name, picked=picked, downloaded=0, collection=None)
    return apply(picked, name, a, log)


def build_parser():
    p = argparse.ArgumentParser(description="osu!drill: pick maps and build an osu!lazer collection")
    g = p.add_argument_group("what to look for")
    g.add_argument("--skill", help="comma-separated skills: streams, jumps, tech, fingercontrol, ...")
    g.add_argument("--like", help="comma-separated reference difficulty ids (find similar maps)")
    g.add_argument("--tournament", help="tournament slots: NM2,NM3,NM4 or whole mods: NM,HD")
    g.add_argument("--popular", action="store_true",
                   help="the most popular songs: among fans of --skill (osu!Collector player collections) "
                        "or, without --skill, the most played maps")
    g = p.add_argument_group("map and music")
    g.add_argument("--stars", required=True, help="star range, e.g. 5.2-6.0")
    g.add_argument("--bpm", default=None, help="map BPM, e.g. 170-220")
    g.add_argument("--length", default="30-600", help="length in seconds, e.g. 60-240")
    g.add_argument("--genres", help="genre ids: 2 video game, 3 anime, 4 rock, 5 pop, 9 hip hop, "
                                     "10 electronic, 11 metal, 12 classical, 13 folk, 14 jazz")
    g.add_argument("--words", help="comma-separated words in tags/title: touhou,speedcore")
    g = p.add_argument_group("tournament filters")
    g.add_argument("--years", help="tournament years, e.g. 2021-2026")
    g.add_argument("--tours", help="comma-separated tournaments (part of the name), e.g. OWC,5 Digit")
    g.add_argument("--digits", help="player rank range: open,3,4,5,6,unknown")
    g.add_argument("--tiers", help="Liquipedia tournament tier: S,A,B,C,D")
    g.add_argument("--sources", help="sources: osu!wiki,Liquipedia")
    g.add_argument("--per-tournament", type=int, default=3, help="max maps from one tournament")
    g.add_argument("--max-lookups", type=int, default=600, help="how many pool maps to check")
    g = p.add_argument_group("skills")
    g.add_argument("--like-weight", type=float, default=0.7, help="similarity weight (0-1)")
    g.add_argument("--crowd-weight", type=float, default=0.5,
                   help="weight of player collections from osu!Collector (0-1, 0 = off)")
    g.add_argument("--min-pc", type=int, default=0, help="minimum playcount of the difficulty")
    g.add_argument("--pop-weight", type=float, default=0.0, help="popularity bonus (0-20)")
    g.add_argument("--stream-bpm", default=None, help="stream BPM, e.g. 180-210")
    g.add_argument("--status", default=None,
                   help="ranked,loved,approved,qualified (default: ranked; with --popular: ranked,loved)")
    g.add_argument("--pool", type=int, default=260, help="how many candidates to check")
    g.add_argument("--depth", type=int, default=150, help="search depth")
    g.add_argument("--per-set", type=int, default=1, help="max difficulties from one song")
    g.add_argument("--per-set-probe", type=int, default=2, help="difficulties per song to check")
    g.add_argument("--min-score", type=float, default=None, help="match threshold 0-100")
    g.add_argument("--local-only", action="store_true", help="installed maps only")
    g.add_argument("--skip-owned", action="store_true", help="new maps only")
    g.add_argument("--threads", type=int, default=4, help="analysis threads")
    g = p.add_argument_group("general")
    g.add_argument("--count", type=int, default=30, help="how many maps in the collection")
    g.add_argument("--name", help="collection name")
    g.add_argument("--split", type=float, default=None, help="split into collections by stars with this step")
    g.add_argument("--lang", default="en", help="message language: en / es / ru")
    g.add_argument("--dry-run", action="store_true", help="only show the selection")
    g.add_argument("--no-download", action="store_true", help="don't download maps")
    g.add_argument("--no-collection", action="store_true", help="don't create a collection")
    return p


def defaults():
    a = build_parser().parse_args(["--stars", "5-6"])
    a.server = False
    return a


def main():
    try:
        a = build_parser().parse_args()
        a.server = False
        run(a)
    except (RuntimeError, ValueError) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
