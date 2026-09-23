# -*- coding: utf-8 -*-
"""
osu!trainer - подбор карт по навыку, похожести или турнирным слотам, скачивание и коллекция в osu!lazer.

Примеры:
    python trainer.py --skill streams --stars 5.2-6.0 --count 30
    python trainer.py --skill fingercontrol,tech --like "2591748,2823535" --stars 4.8-6 --count 40
    python trainer.py --tournament NM2,NM3,NM4 --stars 6.0-6.6 --count 40 --digits 5,6
    python trainer.py --skill jumps --stars 5-6 --genres 10,11 --words "speedcore,dnb"
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyze  # noqa: E402
import config  # noqa: E402
import net  # noqa: E402
import pools  # noqa: E402
import skills  # noqa: E402
from i18n import _, set_lang  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CACHE = config.CACHE_DIR
DL = config.DOWNLOAD_DIR
BACKUPS = config.BACKUP_DIR
STATUS = {"ranked": 1, "approved": 2, "qualified": 3, "loved": 4}

# жанры osu! (genre_id): русское и английское название
GENRES = {
    2: ("Игры", "Video game"), 3: ("Аниме", "Anime"), 4: ("Рок", "Rock"), 5: ("Поп", "Pop"),
    10: ("Электроника", "Electronic"), 11: ("Метал", "Metal"), 9: ("Хип-хоп", "Hip hop"),
    12: ("Классика", "Classical"), 14: ("Джаз", "Jazz"), 13: ("Фолк", "Folk"),
    7: ("Необычное", "Novelty"), 6: ("Другое", "Other"), 1: ("Не указан", "Unspecified"),
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
    "split": (float, 0.1, 5), "like_weight": (float, 0, 1), "min_pc": (int, 0, 10 ** 9),
    "pop_weight": (float, 0, 50), "pool": (int, 20, 1500), "depth": (int, 50, 3000),
    "per_set": (int, 1, 10), "per_set_probe": (int, 1, 10), "min_score": (float, 0, 100),
    "threads": (int, 1, 8),
    "dry_run": (bool,), "no_download": (bool,), "no_collection": (bool,),
    "local_only": (bool,), "skip_owned": (bool,),
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
            if getattr(a, key, None) is not None:
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


def gather_online(queries, a, stars, have_md5, log):
    seen_sets, cands, pages = set(), {}, 0
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
                        cands[b["id"]] = dict(
                            bid=b["id"], sid=s["id"], md5=b["checksum"],
                            sr=b["difficulty_rating"], diff=b["version"],
                            title=s["title"], artist=s["artist"], mapper=s.get("creator", ""),
                            bpm=b.get("bpm") or s.get("bpm"), length=b["total_length"],
                            status=s.get("status", ""), playcount=b.get("playcount", 0),
                            genre=s.get("genre_id") or 0, local=False, slot="")
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


def make_scorer(cfgs, profile, a):
    sim = skills.profile_scorer(profile) if profile else None

    def scorer(m, playcount):
        parts, whys = [], []
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
            total += a.pop_weight * skills.sc(math.log10(max(playcount, 1)), 4.0, 6.0)
        return min(total, 100.0), " | ".join(whys)

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
        score, why = scorer(m, c.get("playcount", 0))
        out = dict(c)
        out.update(score=round(score, 1), why=why, metrics=m)
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


# ------------------------------------------------------- подбор и запись ----

def prepare(a):
    set_lang(getattr(a, "lang", "ru"))
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
        if not a.skill and not a.like:
            raise RuntimeError(_("Нужен навык, карты-образцы или турнирные слоты"))
        cfgs = [skills.resolve(x)[1] for x in csv(a.skill)]
        title = " + ".join(skills.title(c, a.lang) for c in cfgs) if cfgs else _("Похожие")
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
    else:
        profile = None
        if a.like:
            ids = [int(x) for x in csv(str(a.like).replace(" ", ",")) if x.isdigit()][:20]
            log(_("Строю профиль, карт-образцов: %d...", len(ids)))
            ref_metrics = []
            for bid in ids:
                text = net.osu_file(bid, CACHE)
                m = analyze.metrics(text) if text else None
                if m:
                    ref_metrics.append(m)
                else:
                    log(_("  не удалось разобрать карту %s", bid))
            if not ref_metrics:
                raise RuntimeError(_("Не удалось разобрать ни одну карту-образец"))
            profile = skills.build_profile(ref_metrics)
            log(_("  профиль: %.0f BPM, streams %.0f%%, смен ритма %.0f%%, sliders %.0f%%",
                  profile["bpm"], profile["stream_ratio"] * 100,
                  profile["switch_ratio"] * 100, profile["slider_ratio"] * 100))
        scorer = make_scorer(cfgs, profile, a)

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
            cands = gather_online(queries, a, stars, have_md5, log)
        if not cands:
            raise RuntimeError(_("Кандидатов не найдено - ослабь фильтры"))

        log(_("Анализирую карты: %d...", len(cands)))
        scored, done = [], 0
        with cf.ThreadPoolExecutor(max_workers=a.threads) as ex:
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
    p = argparse.ArgumentParser(description="osu!trainer: подбор карт и коллекция в osu!lazer")
    g = p.add_argument_group("что ищем")
    g.add_argument("--skill", help="навыки через запятую: streams, jumps, tech, fingercontrol, ...")
    g.add_argument("--like", help="id сложностей-образцов через запятую (ищем похожие)")
    g.add_argument("--tournament", help="турнирные слоты: NM2,NM3,NM4 или целиком моды: NM,HD")
    g = p.add_argument_group("музыка и карта")
    g.add_argument("--stars", required=True, help="звёзды, напр. 5.2-6.0")
    g.add_argument("--bpm", default=None, help="BPM карты, напр. 170-220")
    g.add_argument("--length", default="30-600", help="длина в секундах, напр. 60-240")
    g.add_argument("--genres", help="жанры (id): 2 игры, 3 аниме, 4 рок, 5 поп, 9 хип-хоп, "
                                     "10 электроника, 11 метал, 12 классика, 13 фолк, 14 джаз")
    g.add_argument("--words", help="слова в тегах/названии через запятую: touhou,speedcore")
    g = p.add_argument_group("турнирные фильтры")
    g.add_argument("--years", help="годы турниров, напр. 2021-2026")
    g.add_argument("--tours", help="турниры (часть названия) через запятую, напр. OWC,5 Digit")
    g.add_argument("--digits", help="рейтинг участников: open,3,4,5,6,unknown")
    g.add_argument("--tiers", help="уровень турнира по Liquipedia: S,A,B,C,D")
    g.add_argument("--sources", help="источники: osu!wiki,Liquipedia")
    g.add_argument("--per-tournament", type=int, default=3, help="макс. карт с одного турнира")
    g.add_argument("--max-lookups", type=int, default=600, help="сколько карт пула проверять")
    g = p.add_argument_group("навыки")
    g.add_argument("--like-weight", type=float, default=0.7, help="вес похожести (0-1)")
    g.add_argument("--min-pc", type=int, default=0, help="минимум игр у сложности")
    g.add_argument("--pop-weight", type=float, default=0.0, help="бонус за популярность (0-20)")
    g.add_argument("--stream-bpm", default=None, help="BPM streams, напр. 180-210")
    g.add_argument("--status", default="ranked", help="ranked,loved,approved,qualified")
    g.add_argument("--pool", type=int, default=260, help="сколько кандидатов проверить")
    g.add_argument("--depth", type=int, default=150, help="глубина поиска")
    g.add_argument("--per-set", type=int, default=1, help="макс. сложностей одной песни")
    g.add_argument("--per-set-probe", type=int, default=2, help="сложностей песни на проверку")
    g.add_argument("--min-score", type=float, default=None, help="порог соответствия 0-100")
    g.add_argument("--local-only", action="store_true", help="только установленные карты")
    g.add_argument("--skip-owned", action="store_true", help="только новые карты")
    g.add_argument("--threads", type=int, default=4, help="потоков проверки")
    g = p.add_argument_group("общие")
    g.add_argument("--count", type=int, default=30, help="сколько карт в коллекции")
    g.add_argument("--name", help="имя коллекции")
    g.add_argument("--split", type=float, default=None, help="разбить на коллекции по звёздам с шагом")
    g.add_argument("--lang", default="ru", help="язык сообщений: ru / en")
    g.add_argument("--dry-run", action="store_true", help="только показать подборку")
    g.add_argument("--no-download", action="store_true", help="не скачивать карты")
    g.add_argument("--no-collection", action="store_true", help="не создавать коллекцию")
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
