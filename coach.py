# -*- coding: utf-8 -*-
"""
Личный тренер osu!drill - вкладка «Тренер» в программе (включается настройкой "coach": true).

Следит только за тем, что lazer сам сохраняет после каждой карты: результатом в client.realm и
файлом повтора. Память игры не читается, игра не трогается. По повтору каждая нота разбирается
заново (replay.py), попытки собираются в сессии (перерыв больше 30 минут - новая), находятся
слабые паттерны. Дальше - лестницы навыков с порогами («пока не сдал ступень, дальше нельзя»),
вступительный тест по турнирному пулу и контрольные карты «старого тебя».

Проваленные и брошенные попытки lazer не сохраняет - тренер видит только пройденные карты.
"""
import datetime
import json
import math
import os
import re
import statistics
import threading
import time

import analyze
import config
import osu_api
import pools
import replay
import trainer

COACH_DIR = os.path.join(config.CACHE_DIR, "coach")
PLAYS_DIR = os.path.join(COACH_DIR, "plays")
STATE_JSON = os.path.join(COACH_DIR, "state.json")
SCORES_JSON = os.path.join(COACH_DIR, "scores.json")
METRICS_JSON = os.path.join(COACH_DIR, "metrics.json")

SESSION_GAP = 30 * 60           # перерыв больше - новая сессия
NEW_DAYS = 7                    # «новая» карта - не игранная столько дней
CONTROL_EVERY_DAYS = 14         # контрольный день
OLD_DAYS = 60                   # результат старше - «старый ты»
NEED, OF = 3, 5                 # ступень сдана, если порог взят на NEED картах из OF
WARMUP_N = 3
DEFAULT_THRESHOLD = dict(acc=0.96, misses=2)
FAILS_TO_ESCAPE = 3             # столько несданных тренировок подряд - тренер предлагает выход
DIFF_MODS = {"DT", "NC", "HT", "DC", "HR", "EZ", "DA", "WU", "WD", "AS"}
DAY = 86400

_lock = threading.RLock()
_mem = dict(sig=None, read_at=0, scores=[], updated=None, error=None)

# ------------------------------------------------------------ справочники ----

# метка паттерна -> (как назвать, какую лестницу качать)
TAG_INFO = {
    "burst": ("короткие bursts (3–8 нот)", "speed"),
    "stream": ("streams (9–16 нот)", "streams"),
    "long_stream": ("длинные streams (17–32 ноты)", "streams"),
    "deathstream": ("deathstreams (33+ нот)", "stamina"),
    "stream_end": ("концы длинных streams", "stamina"),
    "jumpstream": ("jumpstreams (streams с большим spacing)", "jumpstream"),
    "jump": ("прыжки", "jumps"),
    "big_jump": ("дальние прыжки", "jumps"),
    "rhythm_change": ("смены ритма", "fingercontrol"),
    "odd_snap": ("необычные деления (1/3, 1/6)", "tech"),
    "slider": ("начала sliders", "flow"),
    "after_slider": ("ноты сразу после slider", "flow"),
    "sv_change": ("смены скорости sliders (SV)", "tech"),
    "after_break": ("первые ноты после паузы", None),
}

# лестницы: главный параметр навыка и ступени по нему; звёзды - только рамка сверху
LADDERS = {
    "streams": dict(title="Streams", skill="streams", param="stream_bpm", unit="BPM streams", fmt="%.0f",
                    steps=[150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250],
                    need=dict(stream_ratio=0.2, max_run=9)),
    "speed": dict(title="Speed / bursts", skill="speed", param="nps_max", unit="нот/с на пике", fmt="%.0f",
                  steps=[6, 7, 8, 9, 10, 11, 12, 13]),
    "stamina": dict(title="Stamina", skill="stamina", param="length", unit="секунд, streams от 30%", fmt="%.0f",
                    steps=[90, 120, 150, 180, 210, 240, 300, 360], need=dict(stream_ratio=0.3)),
    "jumps": dict(title="Jumps / aim", skill="jumps", param="aim_velocity", unit="скорость курсора", fmt="%.0f",
                  steps=[8, 10, 12, 14, 16, 18, 20, 22, 24]),
    "jumpstream": dict(title="Jumpstreams", skill="jumpstream", param="stream_spacing", unit="spacing в streams",
                       fmt="%.1f×", steps=[0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8],
                       need=dict(stream_bpm=140, stream_ratio=0.2)),
    "fingercontrol": dict(title="Finger control", skill="fingercontrol", param="switch_ratio", unit="смен ритма",
                          fmt="%.0f%%", pct=True, steps=[0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34]),
    "tech": dict(title="Tech", skill="tech", param="odd_ratio", unit="нот на 1/3, 1/6", fmt="%.0f%%", pct=True,
                 steps=[0.02, 0.04, 0.06, 0.08, 0.10, 0.13, 0.16]),
    "reading": dict(title="Reading", skill="reading", param="ar", unit="AR", fmt="%.1f", descending=True,
                    steps=[9.3, 9.0, 8.7, 8.4, 8.1, 7.8, 7.5]),
    "precision": dict(title="Precision", skill="precision", param="cs", unit="CS", fmt="%.1f",
                      steps=[4.2, 4.5, 4.8, 5.1, 5.4, 5.7, 6.0]),
    "flow": dict(title="Flow / sliders", skill="flow", param="slider_ratio", unit="sliders", fmt="%.0f%%", pct=True,
                 steps=[0.25, 0.35, 0.45, 0.55, 0.65]),
    "accuracy": dict(title="Accuracy", skill="accuracy", param="od", unit="OD", fmt="%.1f",
                     steps=[8.0, 8.5, 9.0, 9.3, 9.6, 10.0]),
    # лестницы с модом: заводятся, если тест показал просадку именно с этим модом
    "dt": dict(title="DT — скорость", skill="streams", mod="DT", param="dt_stream_bpm", unit="BPM streams с DT",
               fmt="%.0f", steps=[180, 195, 210, 225, 240, 255, 270, 285], need=dict(stream_ratio=0.15),
               stars_scale=1 / 1.45),
    "hr": dict(title="HR — точность", skill="jumps", mod="HR", param="hr_cs", unit="CS с HR", fmt="%.1f",
               steps=[5.2, 5.5, 5.8, 6.1, 6.4, 6.7], stars_shift=-0.4),
    "hd": dict(title="HD — чтение", skill="jumps,streams", mod="HD", param="nps", unit="нот/с в среднем",
               fmt="%.1f", steps=[4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]),
}


# ------------------------------------------------------------- хранение ----

def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def enabled():
    return bool(config.load().get("coach"))


def load_state():
    st = _load(STATE_JSON, {}) or {}
    st.setdefault("v", 1)
    st.setdefault("ladders", {})
    st.setdefault("active", None)
    st.setdefault("trainings", [])
    st.setdefault("tests", [])
    st.setdefault("control", {"maps": {}, "days": []})
    return st


def save_state(st):
    _save(STATE_JSON, st)


def _files(h):
    data = config.find_osu_data()
    return os.path.join(data, "files", h[0], h[:2], h) if data and h else None


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


# -------------------------------------------------------------- попытки ----

SCORES_VERSION = 3
REREAD_EVERY = 120              # на всякий случай перечитывать результаты раз в 2 минуты


def realm_signature(realm):
    """Признак изменения базы. Время файла не годится: lazer держит client.realm открытым и пишет
    через отображение в память, а Windows тогда не обновляет время изменения. Зато при каждой записи
    Realm меняет корневую ссылку в заголовке файла (первые 24 байта)."""
    st = os.stat(realm)
    with open(realm, "rb") as f:
        head = f.read(24)
    return "%d:%d:%s" % (st.st_mtime_ns, st.st_size, head.hex())


def load_scores(sig, force=False):
    cached = _load(SCORES_JSON)
    if not force and cached and cached.get("sig") == sig and cached.get("v") == SCORES_VERSION:
        return cached["scores"]
    out = os.path.join(COACH_DIR, "scores_dump.json")
    os.makedirs(COACH_DIR, exist_ok=True)
    trainer.realm_cmd("scores", out)
    with open(out, encoding="utf-8") as f:
        scores = json.load(f)
    for s in scores:
        s["ts"] = _ts(s["date"])
        try:
            s["mods_list"] = sorted(m["acronym"].upper() for m in json.loads(s["mods"] or "[]"))
        except (ValueError, KeyError, TypeError):
            s["mods_list"] = []
        try:
            st = json.loads(s["stats"] or "{}")
        except ValueError:
            st = {}
        s["stats"] = st
        s["misses"] = st.get("miss", 0)
        s["breaks"] = st.get("large_tick_miss", 0)
        s["maxStats"] = None
    scores.sort(key=lambda s: s["ts"])
    _save(SCORES_JSON, {"v": SCORES_VERSION, "sig": sig, "scores": scores})
    return scores


def analysis(s):
    """Разбор попытки по повтору (кэшируется на диске)."""
    path = os.path.join(PLAYS_DIR, s["id"] + ".json")
    a = _load(path)
    if a and a.get("v") == replay.ANALYSIS_VERSION:
        return a
    osu, osr = _files(s.get("fileHash")), _files(s.get("replay"))
    if not osu or not osr or not os.path.exists(osu) or not os.path.exists(osr):
        return None
    try:
        with open(osu, encoding="utf-8", errors="replace") as f:
            a = replay.analyze(f.read(), osr, s["mods"], s["stats"])
    except Exception as e:            # битый повтор или редкий формат карты - просто без разбора
        a = dict(v=replay.ANALYSIS_VERSION, error=str(e)[:200])
    _save(path, a)
    return a


def map_metrics(file_hash):
    """Метрики карты (как у подбора) по файлу .osu из игры - для стартовой ступени."""
    cache = _load(METRICS_JSON, {}) or {}
    if file_hash in cache:
        return cache[file_hash]
    path = _files(file_hash)
    m = None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = analyze.metrics(f.read())
        except Exception:
            m = None
    cache[file_hash] = m
    _save(METRICS_JSON, cache)
    return m


def scores():
    return _mem["scores"]


def refresh(force=False):
    """Перечитать результаты, разобрать новые попытки, проверить тренировки. True - было что-то новое."""
    with _lock:
        try:
            realm = trainer.realm_path()
            sig = realm_signature(realm)
        except (RuntimeError, OSError) as e:
            _mem["error"] = str(e)
            return False
        stale = time.time() - (_mem.get("read_at") or 0) > REREAD_EVERY
        if not force and not stale and sig == _mem.get("sig"):
            return False
        try:
            sc = load_scores(sig, force=force or stale)
        except RuntimeError as e:
            _mem["error"] = str(e)
            return False
        _mem["read_at"] = time.time()
        if not force and _mem.get("scores") and [s["id"] for s in sc] == [s["id"] for s in _mem["scores"]]:
            _mem["sig"] = sig               # новых попыток нет - разбирать и пересчитывать нечего
            return False
        for s in sc:
            analysis(s)
        st = load_state()
        evaluate(st, sc)
        save_state(st)
        _mem.update(sig=sig, scores=sc, updated=time.time(), error=None)
        return True


# ------------------------------------------------------ сводки и разборы ----

def mods_ok(s, mod):
    ms = set(s["mods_list"])
    if mod:
        return mod in ms or (mod == "DT" and "NC" in ms)
    return not (ms & DIFF_MODS)


def row(s, a=None):
    """Строка попытки для списков."""
    a = a if a is not None else analysis(s)
    out = dict(id=s["id"], ts=s["ts"], title=s["title"], artist=s["artist"], diff=s["diff"], sr=s["sr"],
               acc=round(s["acc"], 4), rank=s["rank"], misses=s["misses"], breaks=s["breaks"], combo=s["combo"],
               mods=s["mods_list"], bid=s["bid"], sid=s["sid"], md5=s["md5"], pp=s.get("pp"))
    if a and not a.get("error"):
        sm = a["summary"]
        out.update(ur=sm["ur"], mean=sm["mean"], analyzed=True,
                   exact=bool(a.get("check") and a["check"]["diff"] == 0))
        base = sm["err"] or 0.0001
        weak = sorted(((v["err"] / base, k) for k, v in a["tags"].items() if v["n"] >= 12 and v["err"] > base * 1.3),
                      reverse=True)[:2]
        out["weak"] = [k for _r, k in weak]
    else:
        out["analyzed"] = False
    return out


def aggregate(analyses):
    """Сумма разборов: ошибки по паттернам, причины промахов, тайминг, отрезки, прицел."""
    tot = dict(n=0, w=0.0, great=0, ok=0, meh=0, miss=0)
    tags, causes, sections = {}, dict(skip=0, timing=0, aim=0, noclick=0), [[0, 0.0] for _ in range(10)]
    offs_w = offs_n = 0.0
    ur_list = []
    over = under = jumps = 0
    for a in analyses:
        if not a or a.get("error"):
            continue
        sm = a["summary"]
        tot["n"] += sm["n"]
        tot["w"] += sm["err"] * sm["n"]
        for k in ("great", "ok", "meh", "miss"):
            tot[k] += sm[k]
        for k in causes:
            causes[k] += sm["causes"].get(k, 0)
        if sm["mean"] is not None:
            hits = sm["n"] - sm["miss"]
            offs_w += sm["mean"] * hits
            offs_n += hits
        if sm["ur"] is not None:
            ur_list.append(sm["ur"])
        for tg, v in a["tags"].items():
            t = tags.setdefault(tg, dict(n=0, w=0.0, miss=0, mean_w=0.0, mean_n=0))
            t["n"] += v["n"]
            t["w"] += v["err"] * v["n"]
            t["miss"] += v["miss"]
            if v["mean"] is not None:
                h = v["n"] - v["miss"]
                t["mean_w"] += v["mean"] * h
                t["mean_n"] += h
        for i, sec in enumerate(a["sections"][:10]):
            if sec.get("n"):
                sections[i][0] += sec["n"]
                sections[i][1] += sec["err"] * sec["n"]
        aim = a.get("aim") or {}
        if aim.get("jumps"):
            jumps += aim["jumps"]
            over += round((aim.get("jump_over") or 0) * aim["jumps"])
            under += round((aim.get("jump_under") or 0) * aim["jumps"])
    rate = tot["w"] / tot["n"] if tot["n"] else 0
    tag_rows = []
    for tg, t in tags.items():
        r = t["w"] / t["n"] if t["n"] else 0
        tag_rows.append(dict(tag=tg, name=TAG_INFO.get(tg, (tg, None))[0], n=t["n"], rate=round(r, 4),
                             ratio=round(r / rate, 2) if rate else None, miss=t["miss"],
                             mean=round(t["mean_w"] / t["mean_n"], 1) if t["mean_n"] else None,
                             ladder=TAG_INFO.get(tg, (None, None))[1]))
    tag_rows.sort(key=lambda x: -(x["ratio"] or 0))
    return dict(n=tot["n"], rate=round(rate, 4), counts={k: tot[k] for k in ("great", "ok", "meh", "miss")},
                causes=causes, mean=round(offs_w / offs_n, 1) if offs_n else None,
                ur=round(statistics.median(ur_list), 1) if ur_list else None, tags=tag_rows,
                sections=[round(w / n, 4) if n else None for n, w in sections],
                jumps=jumps, over=over, under=under)


def weaknesses(agg, min_n=25):
    """Слабые места с объяснением и лестницей, которую стоит качать."""
    out = []
    for t in agg["tags"]:
        if t["n"] < min_n or not t["ratio"] or t["ratio"] < 1.2 or t["rate"] - agg["rate"] < 0.01:
            continue
        text = "ошибок в %.1f раза больше, чем в среднем" % t["ratio"]
        if t["mean"] is not None and t["mean"] <= -6:
            text += "; ты спешишь — нажимаешь в среднем на %.0f мс раньше" % -t["mean"]
        elif t["mean"] is not None and t["mean"] >= 6:
            text += "; ты запаздываешь — в среднем на %.0f мс" % t["mean"]
        out.append(dict(tag=t["tag"], name=t["name"], ratio=t["ratio"], n=t["n"], text=text, ladder=t["ladder"]))
    return out[:5]


def insights(agg):
    """Общие наблюдения: причины промахов, прицел, усталость, тайминг."""
    lines = []
    miss = sum(agg["causes"].values())
    if miss >= 3:
        c = agg["causes"]
        parts = []
        if c["skip"]:
            parts.append("%d%% — пропуск ноты: нажал следующую раньше, сбился в потоке" % round(100 * c["skip"] / miss))
        if c["aim"]:
            parts.append("%d%% — нажал вовремя, но мимо круга" % round(100 * c["aim"] / miss))
        if c["timing"]:
            parts.append("%d%% — попал в круг, но слишком рано или поздно" % round(100 * c["timing"] / miss))
        if c["noclick"]:
            parts.append("%d%% — не нажал совсем" % round(100 * c["noclick"] / miss))
        lines.append("Промахи (%d): %s." % (miss, "; ".join(parts)))
    if agg["jumps"] >= 40 and (agg["over"] or agg["under"]):
        o, u = agg["over"] / agg["jumps"], agg["under"] / agg["jumps"]
        if abs(o - u) >= 0.07:
            lines.append("На прыжках чаще %s: %d%% нажатий за центром круга по ходу движения, %d%% — не долетая." % (
                "перелёт" if o > u else "недолёт", round(100 * o), round(100 * u)))
    sec = [s for s in agg["sections"] if s is not None]
    if len(sec) == 10:
        early, late = statistics.fmean(sec[:7]), statistics.fmean(sec[7:])
        if early > 0 and late / early >= 1.3 and late - early >= 0.02:
            lines.append("К концу карт ошибок больше в %.1f раза — не хватает выносливости или концентрации." % (late / early))
    if agg["mean"] is not None and abs(agg["mean"]) >= 5:
        lines.append("В среднем ты нажимаешь %s на %.0f мс — стоит проверить offset или просто следить за этим." % (
            "раньше" if agg["mean"] < 0 else "позже", abs(agg["mean"])))
    return lines


def sessions(sc=None):
    sc = sc if sc is not None else scores()
    groups, cur = [], []
    for s in sc:
        if cur and s["ts"] - cur[-1]["ts"] > SESSION_GAP:
            groups.append(cur)
            cur = []
        cur.append(s)
    if cur:
        groups.append(cur)
    return groups


def session_list(limit=40):
    out = []
    for g in reversed(sessions()):
        an = [analysis(s) for s in g]
        agg = aggregate(an)
        wk = weaknesses(agg, min_n=20)
        out.append(dict(id=g[0]["id"], start=g[0]["ts"], end=g[-1]["ts"], plays=len(g),
                        acc=round(statistics.fmean(s["acc"] for s in g), 4), misses=sum(s["misses"] for s in g),
                        ur=agg["ur"], weak=[w["name"] for w in wk[:2]]))
        if len(out) >= limit:
            break
    return out


def session_view(sid):
    for g in sessions():
        if g[0]["id"] == sid:
            an = [analysis(s) for s in g]
            agg = aggregate(an)
            wk = weaknesses(agg, min_n=20)
            base = aggregate([analysis(s) for s in scores() if time.time() - s["ts"] < 30 * DAY])
            accs = [s["acc"] for s in g]
            third = max(1, len(g) // 3)
            fatigue = None
            if len(g) >= 6:
                fatigue = dict(first=round(statistics.fmean(accs[:third]), 4), last=round(statistics.fmean(accs[-third:]), 4))
            advice = []
            for w in wk:
                if w["ladder"] and w["ladder"] not in [a["ladder"] for a in advice]:
                    advice.append(dict(ladder=w["ladder"], title=LADDERS[w["ladder"]]["title"], because=w["name"]))
            return dict(id=sid, start=g[0]["ts"], end=g[-1]["ts"], plays=[row(s, a) for s, a in zip(g, an)],
                        agg=agg, weak=wk, insights=insights(agg), fatigue=fatigue, advice=advice[:3],
                        base_rate=base["rate"])
    return None


def play_view(pid):
    for s in scores():
        if s["id"] == pid:
            a = analysis(s)
            prev = [row(x) for x in scores() if x["md5"] == s["md5"] and x["id"] != pid]
            return dict(row=row(s, a), analysis=a, attempts=prev[-10:],
                        weak=weaknesses(aggregate([a]), min_n=12) if a and not a.get("error") else [],
                        insights=insights(aggregate([a])) if a and not a.get("error") else [])
    return None


# -------------------------------------------------------------- лестницы ----

def param_value(lad, m):
    p = lad["param"]
    if p == "dt_stream_bpm":
        return m["stream_bpm"] * 1.5
    if p == "hr_cs":
        return min(10.0, m["cs"] * 1.3)
    return m.get(p, 0)


def meets(lad, m):
    return all(m.get(k, 0) >= v for k, v in lad.get("need", {}).items())


def step_window(lad, i):
    s = lad["steps"]
    if lad.get("descending"):
        return (s[i + 1] if i + 1 < len(s) else 0.0), s[i]
    return s[i], (s[i + 1] if i + 1 < len(s) else 1e9)


def in_step(lad, i, v):
    lo, hi = step_window(lad, i)
    return lo < v <= hi if lad.get("descending") else lo <= v < hi


def _fmt(lad, v):
    return lad["fmt"] % (v * 100 if lad.get("pct") else v)


def step_label(lad, i):
    lo, hi = step_window(lad, i)
    if lad.get("descending"):
        return "%s %s–%s" % (lad["unit"], _fmt(lad, hi), _fmt(lad, lo)) if lo else "%s ниже %s" % (lad["unit"], _fmt(lad, hi))
    if hi >= 1e9:
        return "от %s %s" % (_fmt(lad, lo), lad["unit"])
    return "%s–%s %s" % (_fmt(lad, lo), _fmt(lad, hi), lad["unit"])


def comfort_stars(sc=None):
    sc = sc if sc is not None else scores()
    now = time.time()
    recent = [s for s in sc if now - s["ts"] < 30 * DAY and s.get("sr") and mods_ok(s, None)]
    good = [s["sr"] for s in recent if s["acc"] >= 0.94 and s["rank"] >= 0]
    pool = good or [s["sr"] for s in recent] or [s["sr"] for s in sc if s.get("sr")]
    return round(statistics.median(pool), 2) if pool else 5.0


def stars_window(lad, c):
    c2 = c * lad.get("stars_scale", 1.0) + lad.get("stars_shift", 0.0)
    return max(1.0, round(c2 - 1.2, 1)), round(c2 + 1.0, 1)


def initial_step(lad, sc):
    """Стартовая ступень - по картам, которые ты недавно проходил с точностью от 93%."""
    now = time.time()
    vals = []
    for s in sc:
        if now - s["ts"] > 60 * DAY or s["acc"] < 0.93 or s["rank"] < 0 or not mods_ok(s, lad.get("mod")):
            continue
        m = map_metrics(s["fileHash"]) if s.get("fileHash") else None
        if m and meets(lad, m):
            vals.append(param_value(lad, m))
    if not vals:
        return 0
    vals.sort()
    target = vals[int(len(vals) * (0.4 if lad.get("descending") else 0.6))]
    for i in range(len(lad["steps"])):
        if in_step(lad, i, target):
            return i
    return 0


def threshold(st, key):
    return (st["ladders"].get(key) or {}).get("threshold") or dict(DEFAULT_THRESHOLD)


def add_ladder(st, key, sc):
    if key not in LADDERS:
        raise RuntimeError("Нет такой лестницы: %s" % key)
    if key not in st["ladders"]:
        step = initial_step(LADDERS[key], sc)
        st["ladders"][key] = dict(step=step, best=step, start=step, created=time.time(), fails_in_row=0,
                                  escape=False, threshold=None)
    if not st["active"]:
        st["active"] = key
    return st["ladders"][key]


def passed(s, thr):
    return s["rank"] >= 0 and s["acc"] >= thr["acc"] and s["misses"] <= thr["misses"]


def _first_plays(sc, md5s, since, mod):
    """Первая сохранённая попытка каждой карты после since (с нужными модами)."""
    out = {}
    for s in sc:
        if s["md5"] in md5s and s["md5"] not in out and s["ts"] >= since and mods_ok(s, mod):
            out[s["md5"]] = s
    return out


def evaluate(st, sc):
    """Проверка открытых тренировок, теста и контрольных дней по новым результатам."""
    for tr in st["trainings"]:
        lad = LADDERS.get(tr["ladder"])
        if not lad:
            continue
        thr = tr.get("threshold") or threshold(st, tr["ladder"])
        firsts = _first_plays(sc, {m["md5"] for m in tr["maps"]}, tr["created"], lad.get("mod"))
        res = {}
        for md5, s in firsts.items():
            a = analysis(s)
            res[md5] = dict(id=s["id"], acc=round(s["acc"], 4), misses=s["misses"], ts=s["ts"], ok=passed(s, thr),
                            ur=a["summary"]["ur"] if a and not a.get("error") else None)
        tr["results"] = res
        if tr["status"] != "open":
            continue
        n_ok = sum(1 for r in res.values() if r["ok"])
        n_bad = len(res) - n_ok
        total = len(tr["maps"])
        need = min(NEED, total)
        status = None
        if n_ok >= need:
            status = "passed"
        elif n_bad > total - need or (tr.get("finished") and n_ok < need):
            status = "failed"
        if status:
            tr["status"] = status
            tr["closed"] = time.time()
            ld = st["ladders"].get(tr["ladder"])
            if ld:
                if status == "passed":
                    if tr["step"] == ld["step"] and ld["step"] < len(lad["steps"]) - 1:
                        ld["step"] += 1
                    ld["best"] = max(ld.get("best", 0), ld["step"])
                    ld["fails_in_row"] = 0
                    ld["escape"] = False
                else:
                    ld["fails_in_row"] = ld.get("fails_in_row", 0) + 1
                    if ld["fails_in_row"] >= FAILS_TO_ESCAPE:
                        ld["escape"] = True
    for test in st["tests"]:
        evaluate_test(test, sc, st)
    evaluate_control(st, sc)


def escape(st, key, choice):
    ld = st["ladders"][key]
    if choice == "back":
        ld["step"] = max(0, ld["step"] - 1)
    elif choice == "soften":
        thr = threshold(st, key)
        ld["threshold"] = dict(acc=round(max(0.90, thr["acc"] - 0.02), 3), misses=thr["misses"] + 1)
    ld["fails_in_row"] = 0
    ld["escape"] = False


def pick_maps(lad, step, stars, exclude, count, log):
    """Новые карты ступени: подбор osu!drill по навыку + фильтр по главному параметру."""
    lo, hi = step_window(lad, step)
    params = dict(skill=lad["skill"], stars="%.2f-%.2f" % stars, count=count * 4, pool=300, depth=250,
                  crowd_weight=0.5, per_set=1, status="ranked,loved", lang="ru", min_score=25, dry_run=True)
    a = trainer.coerce_params(params)
    a.metric_filter = lambda m: meets(lad, m) and in_step(lad, step, param_value(lad, m))
    a.exclude_md5 = set(exclude)
    trainer.prepare(a)
    picked, _name = trainer.select(a, log)
    out, sids = [], set()
    for c in picked:
        if c["md5"] in exclude or c["sid"] in sids:
            continue
        sids.add(c["sid"])
        v = param_value(lad, c["metrics"])
        out.append(dict(bid=c["bid"], sid=c["sid"], md5=c["md5"], sr=c["sr"], title=c["title"], artist=c["artist"],
                        diff=c["diff"], value=round(v, 3), label=_fmt(lad, v), why=c.get("why", "")))
        if len(out) >= count:
            break
    return out


def played_recently(sc, days=NEW_DAYS):
    now = time.time()
    return {s["md5"] for s in sc if s["md5"] and now - s["ts"] < days * DAY}


def reserved_md5(st):
    """Карты, которые не должны попадать в тренировки: контрольные и карты теста."""
    out = set(st["control"]["maps"])
    for t in st["tests"]:
        out |= {m["md5"] for m in t["maps"] if m.get("md5")}
    return out


def build_training(key, log, write=True):
    with _lock:
        st = load_state()
        sc = scores()
        add_ladder(st, key, sc)
        st["active"] = key
        ld = st["ladders"][key]
        lad = LADDERS[key]
        step = ld["step"]
        c = comfort_stars(sc)
        stars = stars_window(lad, c)
        exclude = played_recently(sc) | reserved_md5(st)
        for tr in st["trainings"]:          # и не повторять карты прошлых тренировок этой лестницы
            if tr["ladder"] == key:
                exclude |= {m["md5"] for m in tr["maps"]}
        save_state(st)
    log("Тренировка: %s, ступень %d — %s. Звёзды %.1f–%.1f (твой уровень ~%.1f★)." % (
        lad["title"], step + 1, step_label(lad, step), stars[0], stars[1], c))
    maps = pick_maps(lad, step, stars, exclude, OF, log)
    if len(maps) < OF:
        log("Нашлось только %d новых карт этой ступени — расширяю звёзды и ищу ещё." % len(maps))
        wider = (max(1.0, stars[0] - 0.8), stars[1] + 0.8)
        more = pick_maps(lad, step, wider, exclude | {m["md5"] for m in maps}, OF - len(maps), log)
        maps += more
    if not maps:
        raise RuntimeError("Для этой ступени не нашлось новых карт — попробуй позже или смени лестницу")
    warm = []
    if step > 0:
        log("Разминка: ступенью ниже — %s." % step_label(lad, step - 1))
        try:
            warm = pick_maps(lad, step - 1, stars, exclude | {m["md5"] for m in maps}, WARMUP_N, log)
        except RuntimeError:
            warm = []
    with _lock:
        st = load_state()
        n = sum(1 for t in st["trainings"] if t["ladder"] == key) + 1
        label = step_label(lad, step)
        tr = dict(id="t%d" % int(time.time()), ladder=key, step=step, created=time.time(), maps=maps, warmup=warm,
                  status="open", results={}, threshold=dict(threshold(st, key)),
                  name="osu!drill · %s %d · %s" % (lad["title"], n, label),
                  warmup_name="osu!drill · разминка · %s" % lad["title"] if warm else None)
    if write:
        a = trainer.coerce_params({"lang": "ru"})
        if warm:
            trainer.apply(list(warm), tr["warmup_name"], a, log)
        trainer.apply(list(maps), tr["name"], a, log)
    with _lock:
        st = load_state()
        evaluate(st, scores())
        for t in st["trainings"]:           # прошлая открытая тренировка этой лестницы закрывается:
            if t["ladder"] == key and t["status"] == "open":    # почти не сыгранная - без штрафа
                if len(t.get("results", {})) < min(NEED, len(t["maps"])):
                    t["status"], t["closed"] = "abandoned", time.time()
                else:
                    t["finished"] = time.time()
        st["trainings"].append(tr)
        evaluate(st, scores())
        save_state(st)
    log("Коллекция «%s» в игре. Порог ступени: %d карты из %d с точностью от %.0f%% и не больше %d промахов." % (
        tr["name"], min(NEED, len(maps)), len(maps), tr["threshold"]["acc"] * 100, tr["threshold"]["misses"]))
    return tr


def finish_training(tid):
    with _lock:
        st = load_state()
        for tr in st["trainings"]:
            if tr["id"] == tid and tr["status"] == "open":
                tr["finished"] = time.time()
        evaluate(st, scores())
        save_state(st)


def ladder_view(st, key):
    lad = LADDERS[key]
    ld = st["ladders"][key]
    trs = [t for t in st["trainings"] if t["ladder"] == key]
    history = []
    for t in trs:
        res = list(t.get("results", {}).values())
        history.append(dict(id=t["id"], step=t["step"], created=t["created"], status=t["status"],
                            ok=sum(1 for r in res if r["ok"]), played=len(res), total=len(t["maps"]),
                            acc=round(statistics.fmean(r["acc"] for r in res), 4) if res else None,
                            misses=round(statistics.fmean(r["misses"] for r in res), 1) if res else None,
                            ur=round(statistics.fmean(r["ur"] for r in res if r.get("ur")), 1)
                            if any(r.get("ur") for r in res) else None))
    return dict(key=key, title=lad["title"], mod=lad.get("mod"), step=ld["step"], best=ld.get("best", ld["step"]),
                start=ld.get("start", 0), steps=[step_label(lad, i) for i in range(len(lad["steps"]))],
                threshold=threshold(st, key), escape=ld.get("escape"), fails_in_row=ld.get("fails_in_row", 0),
                active=st["active"] == key, history=history,
                open=next((t for t in reversed(trs) if t["status"] == "open"), None))


# ------------------------------------------------------ вступительный тест ----

ROUNDS = ("round of 32", "round of 16", "ro32", "ro16")


def find_pool(digits="6", exclude=()):
    """Свежий пул RO32/RO16 турнира для игроков этого ранга (с NM, HD, HR и DT)."""
    groups = {}
    for e in pools.load():
        if not pools._match_digits(e, {digits}) or e["year"] < 2023:
            continue
        r = (e.get("round") or "").lower()
        if not any(k in r for k in ROUNDS):
            continue
        key = (e["tournament"], e["edition"], e["round"])
        if key in exclude:
            continue
        groups.setdefault(key, []).append(e)

    def quality(key):
        es = groups[key]
        mods = [e["mod"] for e in es]
        full = all(mods.count(m) >= 1 for m in ("NM", "HD", "HR", "DT")) and mods.count("NM") >= 3
        return (full, es[0]["year"], "32" in key[2], len(es))
    if not groups:
        raise RuntimeError("В турнирной базе нет подходящего пула — обнови её в настройках")
    best = max(groups, key=quality)
    order = {"NM": 0, "HD": 1, "HR": 2, "DT": 3, "FM": 4, "TB": 5}
    es = sorted(groups[best], key=lambda e: (order.get(e["mod"], 9), e["slot"]))
    return best, es


def build_test(log, write=True):
    with _lock:
        st = load_state()
        done = {(t["tournament"], t["edition"], t["round"]) for t in st["tests"]}
    key, entries = find_pool("6", exclude=done)
    log("Вступительный тест: %s %s, %s — %d карт." % (key[0], key[1], key[2], len(entries)))
    cache = pools._bm_cache()
    maps = []
    for e in entries:
        info = pools.resolve_beatmap(e["bid"], cache)
        if not info or info.get("mode") != 0 or not info.get("md5"):
            log("  карта %s недоступна — пропускаю" % e["bid"])
            continue
        maps.append(dict(bid=e["bid"], sid=info["sid"], md5=info["md5"], slot=e["slot"], mod=e["mod"],
                         title=info["title"], artist=info["artist"], diff=info["version"], sr=info["sr"]))
    pools._save_bm(cache)
    if not maps:
        raise RuntimeError("Не удалось получить карты пула — зеркало не отвечает, попробуй позже")
    name = "osu!drill · тест · %s %s · %s" % (key[0], key[1], key[2])
    if write:
        trainer.apply(list(maps), name.strip(), trainer.coerce_params({"lang": "ru"}), log)
    with _lock:
        st = load_state()
        st["tests"].append(dict(id="x%d" % int(time.time()), created=time.time(), tournament=key[0],
                                edition=key[1], round=key[2], name=name, maps=maps, status="open", results={}))
        save_state(st)
    log("Играй карты с модом своего слота: HD1 — с HD, HR1 — с HR, DT1 — с DT; NM и TB — без модов, FM — как хочешь.")
    return maps


def evaluate_test(test, sc, st):
    firsts = {}
    for s in sc:
        if s["md5"] in {m["md5"] for m in test["maps"]} and s["md5"] not in firsts and s["ts"] >= test["created"]:
            firsts[s["md5"]] = s
    res = {}
    for m in test["maps"]:
        s = firsts.get(m["md5"])
        if s:
            res[m["md5"]] = dict(id=s["id"], acc=round(s["acc"], 4), misses=s["misses"], mods=s["mods_list"], ts=s["ts"])
    test["results"] = res
    enough = len(res) >= max(3, math.ceil(0.7 * len(test["maps"])))
    if test["status"] == "open" and (enough and len(res) == len(test["maps"]) or test.get("finished")):
        if res:
            test["status"] = "done"
            test["diagnosis"] = diagnose(test, sc)
            for key in test["diagnosis"]["ladders"]:
                add_ladder(st, key, sc)
            if test["diagnosis"]["ladders"]:
                st["active"] = test["diagnosis"]["ladders"][0]
    elif test["status"] == "open" and enough:
        test["ready"] = True


def diagnose(test, sc):
    by_id = {s["id"]: s for s in sc}
    plays = [by_id[r["id"]] for r in test["results"].values() if r["id"] in by_id]
    an = [analysis(s) for s in plays]
    agg = aggregate(an)
    wk = weaknesses(agg, min_n=20)
    groups = {}
    for s in plays:
        ms = set(s["mods_list"])
        g = "DT" if ms & {"DT", "NC"} else "HR" if "HR" in ms else "HD" if "HD" in ms else "NM"
        groups.setdefault(g, []).append(s["acc"])
    mods = {g: round(statistics.fmean(v), 4) for g, v in groups.items()}
    ladders, reasons = [], []
    for w in wk:
        if w["ladder"] and w["ladder"] not in ladders:
            ladders.append(w["ladder"])
            reasons.append("%s: %s" % (w["name"], w["text"]))
    nm = mods.get("NM")
    for g, key in (("DT", "dt"), ("HR", "hr"), ("HD", "hd")):
        if nm is not None and g in mods and mods[g] < nm - 0.02:
            ladders.append(key)
            reasons.append("С %s точность %.1f%% против %.1f%% без модов" % (g, mods[g] * 100, nm * 100))
    if not ladders:                         # явной слабости нет - начинаем с того, где ошибок больше всего
        top = next((t for t in agg["tags"] if t["ladder"] and t["n"] >= 20), None)
        if top:
            ladders.append(top["ladder"])
            reasons.append("Явной слабости нет; больше всего ошибок — %s" % top["name"])
    return dict(created=time.time(), plays=len(plays), agg=agg, weak=wk, insights=insights(agg), mods=mods,
                ladders=ladders[:3], reasons=reasons[:4])


def finish_test(tid):
    with _lock:
        st = load_state()
        for t in st["tests"]:
            if t["id"] == tid and t["status"] == "open":
                t["finished"] = time.time()
        evaluate(st, scores())
        save_state(st)


# ------------------------------------------------------- контрольные карты ----

ID_RE = re.compile(r"#osu/(\d+)|/(?:b|beatmaps)/(\d+)")


def parse_ids(text):
    ids = []
    for m in ID_RE.finditer(text or ""):
        ids.append(int(m.group(1) or m.group(2)))
    for tok in re.split(r"[\s,;]+", re.sub(r"https?://\S+", " ", text or "")):
        if tok.isdigit() and len(tok) >= 3:
            ids.append(int(tok))
    out = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out


def old_best(sc, md5):
    """Лучший старый результат на карте (старше OLD_DAYS дней) - «старый ты»."""
    old = [s for s in sc if s["md5"] == md5 and time.time() - s["ts"] > OLD_DAYS * DAY and s["rank"] >= 0]
    if not old:
        return None
    s = max(old, key=lambda x: (x["acc"], -x["misses"]))
    return dict(acc=round(s["acc"], 4), misses=s["misses"], ts=s["ts"], rank=s["rank"], mods=s["mods_list"], source="lazer")


def add_control(text, log=print):
    ids = parse_ids(text)
    if not ids:
        raise RuntimeError("Не нашёл номеров карт — вставь ссылки вида osu.ppy.sh/beatmapsets/…#osu/123")
    sc = scores()
    local = {s["bid"]: s for s in sc if s.get("bid")}
    cache = pools._bm_cache()
    added = []
    with _lock:
        st = load_state()
        from_api = {x["md5"]: x for x in (st["control"].get("api_suggest") or {}).get("items", [])}
        for bid in ids[:30]:
            s = local.get(bid)
            if s:
                info = dict(md5=s["md5"], sid=s["sid"], title=s["title"], artist=s["artist"], version=s["diff"], sr=s["sr"])
            else:
                info = pools.resolve_beatmap(bid, cache)
                if not info or info.get("mode") != 0:
                    log("карта %d не найдена" % bid)
                    continue
            md5 = info["md5"]
            api = from_api.get(md5)
            st["control"]["maps"][md5] = dict(bid=bid, sid=info["sid"], md5=md5, title=info["title"],
                                              artist=info["artist"], diff=info["version"], sr=info["sr"],
                                              source="osu!" if api else "manual", added=time.time(),
                                              old=api["old"] if api else old_best(sc, md5))
            added.append(md5)
        pools._save_bm(cache)
        save_state(st)
    return added


def auto_control(st, sc):
    """Кандидаты из истории lazer: старые хорошие результаты, которые сейчас не повторить."""
    now = time.time()
    best_old, best_new = {}, {}
    for s in sc:
        if not s["md5"] or s["rank"] < 0:
            continue
        d = best_old if now - s["ts"] > OLD_DAYS * DAY else best_new if now - s["ts"] < 30 * DAY else None
        if d is not None and (s["md5"] not in d or s["acc"] > d[s["md5"]]["acc"]):
            d[s["md5"]] = s
    out = []
    for md5, s in best_old.items():
        if md5 in st["control"]["maps"] or s["acc"] < 0.9:
            continue
        new = best_new.get(md5)
        if new is None or new["acc"] < s["acc"] - 0.005:
            out.append(dict(bid=s["bid"], md5=md5, title=s["title"], artist=s["artist"], diff=s["diff"], sr=s["sr"],
                            old=dict(acc=round(s["acc"], 4), misses=s["misses"], ts=s["ts"]),
                            now=dict(acc=round(new["acc"], 4), misses=new["misses"]) if new else None))
    return sorted(out, key=lambda x: -x["sr"])[:10]


def api_user():
    """Ник или номер в osu!: из настроек, иначе - владелец результатов в lazer."""
    name = str(config.load().get("osu_user") or "").strip()
    if name:
        return name
    ids = [s.get("uid") for s in scores() if s.get("uid")]
    return str(max(set(ids), key=ids.count)) if ids else ""


def save_keys(client_id, secret, user):
    config.save(dict(osu_client_id=str(client_id or "").strip(), osu_client_secret=str(secret or "").strip(),
                     osu_user=str(user or "").strip()))
    osu_api._token.update(value=None, until=0)
    if osu_api.configured():
        osu_api.token()                     # сразу проверить, что ключи рабочие


def import_pinned(log):
    """Закреплённые скоры профиля osu! -> контрольные карты; «старый ты» - сам закреплённый скор."""
    if not osu_api.configured():
        raise RuntimeError("Сначала добавь ключи приложения osu! (вкладка «Контрольные»)")
    u = osu_api.user(api_user())
    log("Профиль osu!: %s" % u.get("username"))
    got = osu_api.pinned(int(u["id"]))
    n = 0
    with _lock:
        st = load_state()
        for s in got:
            b, bs = s.get("beatmap") or {}, s.get("beatmapset") or {}
            md5 = b.get("checksum")
            if not md5 or (b.get("mode") not in (None, "osu")):
                continue
            prev = st["control"]["maps"].get(md5, {})
            st["control"]["maps"][md5] = dict(
                bid=b.get("id"), sid=b.get("beatmapset_id") or bs.get("id"), md5=md5, title=bs.get("title", ""),
                artist=bs.get("artist", ""), diff=b.get("version", ""), sr=round(b.get("difficulty_rating") or 0, 2),
                source="pinned", added=prev.get("added", time.time()), old=osu_api.score_brief(s))
            n += 1
        save_state(st)
    log("Закреплённых скоров osu!standard: %d — добавлены в контрольные карты." % n)
    return n


def find_unbeaten(log):
    """Карты, сыгранные за месяц, где старый результат из профиля osu! лучше нынешнего."""
    if not osu_api.configured():
        raise RuntimeError("Сначала добавь ключи приложения osu! (вкладка «Контрольные»)")
    u = osu_api.user(api_user())
    uid, now = int(u["id"]), time.time()
    recent = {}
    for s in scores():
        if s.get("bid") and s["rank"] >= 0 and now - s["ts"] < 30 * DAY:
            if s["bid"] not in recent or s["acc"] > recent[s["bid"]]["acc"]:
                recent[s["bid"]] = s
    bids = sorted(recent, key=lambda b: -recent[b]["sr"])[:60]
    log("Сравниваю с профилем %s карты, сыгранные за месяц: %d..." % (u.get("username"), len(bids)))
    items = []
    for i, bid in enumerate(bids, 1):
        best = osu_api.best_on_map(bid, uid)
        if best:
            old, s = osu_api.score_brief(best), recent[bid]
            if now - old["ts"] > OLD_DAYS * DAY and old["acc"] > s["acc"] + 0.005:
                items.append(dict(bid=bid, md5=s["md5"], title=s["title"], artist=s["artist"], diff=s["diff"], sr=s["sr"],
                                  old=old, now=dict(acc=round(s["acc"], 4), misses=s["misses"]), source="osu!"))
        if i % 10 == 0:
            log("  ...%d/%d" % (i, len(bids)))
    with _lock:
        st = load_state()
        st["control"]["api_suggest"] = dict(ts=now, items=items)
        save_state(st)
    log("Карт, где старый ты сильнее: %d." % len(items))
    return len(items)


def remove_control(md5):
    with _lock:
        st = load_state()
        st["control"]["maps"].pop(md5, None)
        save_state(st)


def build_control_day(log, write=True):
    with _lock:
        st = load_state()
        maps = list(st["control"]["maps"].values())
    if not maps:
        raise RuntimeError("Контрольных карт пока нет — добавь их по ссылкам")
    name = "osu!drill · контроль · %s" % time.strftime("%d.%m.%Y")
    if write:
        trainer.apply([dict(m, local=False) for m in maps], name, trainer.coerce_params({"lang": "ru"}), log)
    with _lock:
        st = load_state()
        st["control"]["days"].append(dict(created=time.time(), name=name, md5s=[m["md5"] for m in maps], results={}))
        save_state(st)
    log("Контрольный день начат: сыграй каждую карту по одному разу, первая попытка идёт в зачёт.")
    return name


def evaluate_control(st, sc):
    ctl = st["control"]
    for md5, m in ctl["maps"].items():
        if not m.get("old"):
            m["old"] = old_best(sc, md5) or m.get("old")
    for day in ctl["days"]:
        firsts = {}
        for s in sc:
            if s["md5"] in day["md5s"] and s["md5"] not in firsts and day["created"] <= s["ts"] <= day["created"] + 3 * DAY:
                firsts[s["md5"]] = s
        day["results"] = {md5: dict(id=s["id"], acc=round(s["acc"], 4), misses=s["misses"], ts=s["ts"], rank=s["rank"])
                          for md5, s in firsts.items()}


def control_view(st, sc):
    ctl = st["control"]
    days = ctl["days"]
    last = days[-1]["created"] if days else None
    rows = []
    for md5, m in ctl["maps"].items():
        hist = [dict(ts=d["created"], **d["results"][md5]) for d in days if md5 in d.get("results", {})]
        base = m.get("old") or (dict(acc=hist[0]["acc"], misses=hist[0]["misses"], ts=hist[0]["ts"], source="first")
                                if hist else None)
        rows.append(dict(m, history=hist, base=base, latest=hist[-1] if hist else None))
    gaps = [r["latest"]["acc"] - r["base"]["acc"] for r in rows if r["latest"] and r["base"] and r["base"].get("source") != "first"]
    suggest = auto_control(st, sc)
    have = set(ctl["maps"]) | {x["md5"] for x in suggest}
    suggest += [x for x in (ctl.get("api_suggest") or {}).get("items", []) if x["md5"] not in have]
    return dict(maps=rows, days=[dict(created=d["created"], name=d["name"], played=len(d.get("results", {})),
                                      total=len(d["md5s"])) for d in days],
                last=last, due=(last is None or time.time() - last >= CONTROL_EVERY_DAYS * DAY),
                days_left=None if last is None else max(0, CONTROL_EVERY_DAYS - int((time.time() - last) // DAY)),
                gap=round(statistics.fmean(gaps), 4) if gaps else None, suggest=suggest,
                api=dict(configured=osu_api.configured(), user=str(config.load().get("osu_user") or ""),
                         checked=(ctl.get("api_suggest") or {}).get("ts")))


# ------------------------------------------------------------- витрина ----

def plan(st, sc):
    items = []
    tests = st["tests"]
    if not tests or tests[-1]["status"] != "done":
        t = tests[-1] if tests else None
        items.append(dict(kind="test", status=t["status"] if t else "none",
                          played=len(t["results"]) if t else 0, total=len(t["maps"]) if t else 0,
                          ready=bool(t and t.get("ready"))))
    key = st["active"]
    if key and key in st["ladders"]:
        lv = ladder_view(st, key)
        items.append(dict(kind="warmup", ladder=key, title=lv["title"],
                          label=lv["steps"][max(0, lv["step"] - 1)], has=bool(lv["open"] and lv["open"].get("warmup"))))
        items.append(dict(kind="training", ladder=key, title=lv["title"], step=lv["step"], label=lv["steps"][lv["step"]],
                          open=lv["open"], threshold=lv["threshold"], escape=lv["escape"]))
    items.append(dict(kind="free"))
    cv = control_view(st, sc)
    items.append(dict(kind="control", due=cv["due"], days_left=cv["days_left"], maps=len(cv["maps"])))
    return items


def state_view():
    with _lock:
        st = load_state()
        sc = scores()
        recent = [row(s) for s in sc[-25:]][::-1]
        return dict(
            updated=_mem["updated"], error=_mem["error"], plays=len(sc), comfort=comfort_stars(sc),
            plan=plan(st, sc), active=st["active"],
            ladders=[ladder_view(st, k) for k in st["ladders"]],
            ladder_defs=[dict(key=k, title=v["title"], mod=v.get("mod"), unit=v["unit"]) for k, v in LADDERS.items()],
            tests=st["tests"], control=control_view(st, sc), sessions=session_list(), recent=recent,
            need=NEED, of=OF, default_threshold=DEFAULT_THRESHOLD,
            tag_names={k: v[0] for k, v in TAG_INFO.items()})


def progress_view():
    with _lock:
        st = load_state()
        sc = scores()
        sess = []
        for g in sessions(sc)[-30:]:
            agg = aggregate([analysis(s) for s in g])
            sess.append(dict(start=g[0]["ts"], plays=len(g), acc=round(statistics.fmean(s["acc"] for s in g), 4),
                             ur=agg["ur"], rate=agg["rate"], misses=round(statistics.fmean(s["misses"] for s in g), 1)))
        return dict(sessions=sess, ladders=[ladder_view(st, k) for k in st["ladders"]], control=control_view(st, sc))


def set_active(key):
    with _lock:
        st = load_state()
        add_ladder(st, key, scores())
        st["active"] = key
        save_state(st)


def set_escape(key, choice):
    with _lock:
        st = load_state()
        if key in st["ladders"]:
            escape(st, key, choice)
        save_state(st)
