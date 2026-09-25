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
import collections
import datetime
import json
import math
import os
import random
import re
import statistics
import threading
import time

import analyze
import collector
import config
import labels
import net
import osu_api
import pools
import replay
import skills
import trainer

COACH_DIR = os.path.join(config.CACHE_DIR, "coach")
PLAYS_DIR = os.path.join(COACH_DIR, "plays")
STATE_JSON = os.path.join(COACH_DIR, "state.json")
SCORES_JSON = os.path.join(COACH_DIR, "scores.json")
METRICS_JSON = os.path.join(COACH_DIR, "metrics.json")
LABELS_JSON = os.path.join(COACH_DIR, "labels.json")     # твои отметки навыков карт

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
    # по BPM самих bursts: по плотности нот сюда попадали alt-карты (частые 1/2 с прыжками)
    "speed": dict(title="Speed / bursts", skill="speed", param="burst_bpm", unit="BPM bursts", fmt="%.0f",
                  steps=[160, 170, 180, 190, 200, 210, 220, 230, 240, 250],
                  need=dict(burst_ratio=0.2), limit=dict(alt_ratio=0.1)),
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


_metrics_cache = {}


def map_metrics(file_hash, mods=None, key=""):
    """Метрики карты (как у подбора) по файлу .osu из игры - для скиллсета и стартовой ступени.
    С mods - метрики карты с модами попытки (key - их подпись в кэше)."""
    cache = _metrics_cache
    if not cache:
        cache.update(_load(METRICS_JSON, {}) or {})
    ck = file_hash + ("|" + key if key else "")
    if ck in cache and (cache[ck] is None or "alt_ratio" in cache[ck]):
        return cache[ck]                        # записи до появления bursts/alt считаются заново
    path = _files(file_hash)
    m = None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = analyze.metrics(f.read(), mods)
        except Exception:
            m = None
    cache[ck] = m
    _save(METRICS_JSON, cache)
    return m


def play_mods(s):
    """Моды попытки, которые меняют саму карту: скорость, HR/EZ, Difficulty Adjust и HD (читать труднее).
    -> (моды для analyze.metrics или None, подпись для кэша, как назвать)."""
    mi = replay.mod_info(s.get("mods"))
    mods = dict(rate=round(mi["rate"], 3), hr=mi["hr"], ez=mi["ez"], da=mi["da"] or None, hidden="HD" in mi["acronyms"])
    if mods["rate"] == 1.0 and not (mods["hr"] or mods["ez"] or mods["da"] or mods["hidden"]):
        return None, "", ""
    names = [a for a in mi["acronyms"] if a in DIFF_MODS or a == "HD"]
    if mods["rate"] not in (1.0, 1.5, 0.75):
        names.append("×%g" % mods["rate"])
    return mods, json.dumps(mods, sort_keys=True), " ".join(names)


def play_metrics(s):
    """Метрики карты такой, какой её сыграли: с DT/HT (и их скоростью), HR/EZ, Difficulty Adjust и HD."""
    if not s.get("fileHash"):
        return None
    mods, key, _name = play_mods(s)
    return map_metrics(s["fileHash"], mods, key)


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


def fair(s):
    """Попытка без автоигры, relax/autopilot и модов, которые двигают круги - такую можно засчитывать."""
    return not replay.mod_info(s.get("mods"))["unsupported"]


def skill_scores(m):
    """Оценки навыков карты 0..100 - те же формулы, по которым подбор ищет карты, с поправкой по твоим
    отметкам похожих карт (raw и formula - без поправки)."""
    adj = personal(m)
    out = []
    for key, cfg in skills.SKILLS.items():
        try:
            raw, why = cfg["score"](m)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        v = max(0.0, min(100.0, raw + adj[key][0])) if key in adj else raw
        out.append(dict(key=key, title=cfg["title"], score=round(v), main=v >= cfg["min_score"], why=why,
                        raw=round(raw), formula=raw >= cfg["min_score"], like=adj[key][1] if key in adj else None))
    out.sort(key=lambda x: -x["score"])
    return out


_skillsets = {}


def skillset(s):
    """К чему относится сыгранная карта: главные навыки и ступени лестниц, на которые она попадает."""
    if s["id"] not in _skillsets:
        _skillsets[s["id"]] = _skillset(s)
    return _skillsets[s["id"]]


def _skillset(s):
    """Скиллсет карты с модами попытки: с DT это другие streams и прыжки, с HR - меньше круги и выше AR/OD,
    с HD карта ещё и на чтение. Лестницы без мода принимают и попытки с модами - по их настоящим цифрам."""
    m = play_metrics(s)
    if not m:
        return None
    sc = skill_scores(m)
    lab = label_for(s)                  # твоя отметка этой карты с этими модами важнее формул
    main = [x for x in sc if x["key"] in lab["skills"]] if lab else [x for x in sc if x["main"]][:3]
    keys = {x["key"] for x in main}
    fits = []
    for key, lad in LADDERS.items():
        mod = lad.get("mod")
        if not fair(s) or (mod and not mods_ok(s, mod)):
            continue
        if not (keys & set(lad["skill"].split(","))) or not meets(lad, m):
            continue
        v = param_value(lad, m)
        step = next((i for i in range(len(lad["steps"])) if in_step(lad, i, v)), None)
        if step is not None:
            fits.append(dict(ladder=key, title=lad["title"], step=step, label=step_label(lad, step), value=_fmt(lad, v)))
    return dict(main=main, scores=sc, fits=fits, mods=play_mods(s)[2], labeled=bool(lab), bpm=m["bpm"], length=m["length"],
                stream_ratio=m["stream_ratio"], stream_bpm=m["stream_bpm"], burst_ratio=m.get("burst_ratio"),
                burst_bpm=m.get("burst_bpm"), alt_ratio=m.get("alt_ratio"), max_run=m["max_run"],
                aim_share=m["aim_share"], ar=round(m["ar"], 1), cs=round(m["cs"], 1), od=round(m["od"], 1))


def row(s, a=None):
    """Строка попытки для списков."""
    a = a if a is not None else analysis(s)
    ss = skillset(s)
    out = dict(id=s["id"], ts=s["ts"], title=s["title"], artist=s["artist"], diff=s["diff"], sr=s["sr"],
               acc=round(s["acc"], 4), rank=s["rank"], misses=s["misses"], breaks=s["breaks"], combo=s["combo"],
               mods=s["mods_list"], bid=s["bid"], sid=s["sid"], md5=s["md5"], pp=s.get("pp"),
               kind=[x["title"] for x in ss["main"][:2]] if ss else [])
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


# ------------------------------------------------------ отметки навыков ----
# После карты тренер спрашивает, на какие навыки она на самом деле и не фарм ли это. Отметка заменяет
# скиллсет этой карты (с этими модами), а по всем отметкам (labels.Model) подбор сдвигает пороги навыков
# и оценки похожих карт и держит фарм только по просьбе. Так учатся подсказки тренера, случайные карты,
# тренировки и «Подбор карт» в программе, а обезличенная копия отметок уходит на сайт - и там подбор
# меняется так же, для всех.

ASK_HOURS = 12          # спрашивать про попытки не старше

_labels = dict(data=None, model=None)
_sync = dict(timer=None, status=None, lock=threading.Lock())


def load_labels():
    if _labels["data"] is None:
        d = _load(LABELS_JSON)
        if d is None:                   # первый запуск: спрашивать с этого часа, старые попытки - нет
            d = dict(since=time.time() - 3600)
            _save(LABELS_JSON, dict(d, labels={}, asked={}, ask=True))
        d.setdefault("labels", {})
        d.setdefault("asked", {})
        d.setdefault("ask", True)
        d.setdefault("since", 0)
        _labels["data"] = d
    return _labels["data"]


def save_labels(d, sync=False):
    now = time.time()
    d["asked"] = {k: v for k, v in d["asked"].items() if now - v < 60 * DAY}
    _save(LABELS_JSON, d)
    _labels.update(data=d, model=None)
    _skillsets.clear()
    if sync:
        sync_labels()


def label_key(s):
    return "%s|%s" % (s["md5"], play_mods(s)[1])


def label_for(s):
    return load_labels()["labels"].get(label_key(s))


def label_model():
    """Поправки подбора по твоим отметкам (пересобираются после каждой новой отметки)."""
    model = _labels["model"]
    if model is None:
        model = _labels["model"] = labels.Model(load_labels()["labels"].values())
    return model


def personal(m, keys=None):
    """Сдвиги оценок навыков по твоим отметкам: {навык: (сдвиг, похожая отмеченная карта или None)}."""
    return label_model().deltas(m, keys)


def farm_score(m):
    return label_model().farm_score(m)


def is_farm(m):
    return label_model().is_farm(m)


def has_farm():
    return label_model().farm


def farm_filter(mode):
    return label_model().farm_filter(mode)


def personal_adjust(cfg, m, s, why):
    """Хук подбора (a.adjust в trainer.make_scorer): оценка навыка с поправкой по твоим отметкам."""
    return label_model().adjust(cfg, m, s, why)


def public_labels():
    """Отметки для сайта: labels.clean оставит только нужное подбору - без попыток, точности и времени."""
    return [dict(lab, k=labels.entry_key(key)) for key, lab in load_labels()["labels"].items()]


def sync_labels(delay=5.0):
    """Отметки -> сайт: в фоне, через несколько секунд после последней правки. Ключ - labels_token в
    config.json (его выдаёт сайт)."""
    token = str(config.load().get("labels_token") or "")
    if not token:
        _sync["status"] = dict(ok=False, error="нет ключа для сайта (labels_token в config.json)")
        return
    with _sync["lock"]:
        if _sync["timer"]:
            _sync["timer"].cancel()
        t = threading.Timer(delay, _sync_now, args=(token,))
        t.daemon = True
        _sync["timer"] = t
        t.start()


def _sync_now(token):
    try:
        n, up, gone = labels.upload(public_labels(), token)
        _sync["status"] = dict(ok=True, n=n, up=up, gone=gone, ts=time.time())
    except (OSError, RuntimeError, ValueError) as e:
        _sync["status"] = dict(ok=False, error=str(e), ts=time.time())


def ask_queue(sc=None, every=False):
    """Свежие попытки, про которые стоит спросить: эта карта с этими модами ещё не отмечена, и про попытку
    не спрашивали. Новые первыми, по одной на карту (every - все попытки)."""
    d = load_labels()
    if not d["ask"]:
        return []
    sc = sc if sc is not None else scores()
    now, out, seen = time.time(), [], set()
    for s in reversed(sc):
        if now - s["ts"] > ASK_HOURS * 3600 or s["ts"] < d["since"]:
            break
        if s["id"] in d["asked"] or not fair(s):
            continue
        k = label_key(s)
        if k in d["labels"] or (k in seen and not every) or not skillset(s):
            continue
        seen.add(k)
        out.append(s)
    return out


def label_view(s, queue=0):
    """Что показать в окошке «что это была за карта»."""
    ss = skillset(s)
    lab = label_for(s)
    auto = [x["key"] for x in ss["scores"] if x["main"]][:3]
    fs, like = farm_score(play_metrics(s))
    return dict(id=s["id"], title=s["title"], artist=s["artist"], diff=s["diff"], sr=s["sr"], bid=s["bid"],
                mods=s["mods_list"], mods_name=ss["mods"], acc=round(s["acc"], 4), misses=s["misses"], ts=s["ts"],
                auto=auto, chosen=lab["skills"] if lab else auto, labeled=bool(lab), queue=queue,
                farm_auto=fs >= 0.5, farm=bool(lab.get("farm")) if lab else fs >= 0.5, farm_like=like,
                scores=[dict(key=x["key"], title=x["title"], score=x["score"], why=x["why"], like=x["like"])
                        for x in ss["scores"]])


def ask_view(sc):
    q = ask_queue(sc)
    return label_view(q[0], len(q)) if q else None


def play_label(pid):
    s = next((x for x in scores() if x["id"] == pid), None)
    if not s or not skillset(s):
        raise RuntimeError("Для этой попытки нет карты - скиллсет не посчитать")
    return label_view(s)


def set_label(pid, chosen=None, skip=False, farm=False):
    """Отметка навыков попытки (для этой карты с этими модами) или «пропустить». farm - это фарм-карта:
    похожие подбор даёт только по просьбе."""
    with _lock:
        s = next((x for x in scores() if x["id"] == pid), None)
        if not s:
            raise RuntimeError("Нет такой попытки")
        d = load_labels()
        k = label_key(s)
        for x in ask_queue(every=True):     # и про другие попытки этой карты с этими модами не спрашивать
            if label_key(x) == k:
                d["asked"][x["id"]] = time.time()
        d["asked"][pid] = time.time()
        if not skip:
            ss = skillset(s)
            if not ss:
                raise RuntimeError("Для этой попытки нет карты - скиллсет не посчитать")
            d["labels"][label_key(s)] = dict(
                skills=[k for k in skills.SKILLS if k in set(chosen or [])],
                auto=[x["key"] for x in ss["scores"] if x["main"]][:3],          # что предложил тренер
                formula=[x["key"] for x in ss["scores"] if x["formula"]][:3],    # что видят одни формулы
                farm=bool(farm), id=pid, ts=time.time(), title=s["title"], artist=s["artist"], diff=s["diff"], bid=s["bid"],
                sid=s["sid"],
                mods=ss["mods"], metrics=play_metrics(s))
        save_labels(d, sync=not skip)
        if not skip:
            recredit(s)


def skip_all():
    with _lock:
        d = load_labels()
        for s in ask_queue(every=True):
            d["asked"][s["id"]] = time.time()
        save_labels(d)


def set_asking(on):
    with _lock:
        d = load_labels()
        d["ask"] = bool(on)
        save_labels(d)


def recredit(s):
    """Отметка поменяла скиллсет случайной карты - пересчитать её зачёт в лестницах. Зачёт, после которого
    ступень уже поменялась, остаётся как есть."""
    st = load_state()
    r = st.get("random") or {}
    entries = [h for h in r.get("history", []) + ([r["current"]] if r.get("current") else [])
               if (h.get("result") or {}).get("id") == s["id"]]
    if not entries:
        return
    kept = []
    for key, ld in st["ladders"].items():
        mine = [x for x in ld.get("random", []) if x["id"] == s["id"]]
        if not mine:
            continue
        if all(x["step"] == ld["step"] for x in mine):
            ld["random"] = [x for x in ld["random"] if x["id"] != s["id"]]
        else:
            kept.append(key)
    old = entries[0]["result"].get("ladders") or []
    new = [k for k in old if k["ladder"] in kept] + credit_random(st, s)
    kind = row(s)["kind"]
    for h in entries:
        h["result"].update(ladders=new, kind=kind)
    save_state(st)


def labels_view():
    """Отметки для «Профиля»: сколько, как часто формулы совпали с тобой и где расходятся чаще всего."""
    d = load_labels()
    labs = sorted(d["labels"].values(), key=lambda x: -x["ts"])
    per = {}
    for lab in labs:
        user, formula = set(lab["skills"]), set(lab.get("formula", []))
        for k in user | formula:
            p = per.setdefault(k, dict(key=k, title=skills.SKILLS[k]["title"], both=0, extra=0, missed=0))
            p["both" if k in user and k in formula else "extra" if k in formula else "missed"] += 1
    return dict(n=len(labs), ask=d["ask"], agree=sum(1 for x in labs if set(x["skills"]) == set(x.get("formula", []))),
                skills=sorted(per.values(), key=lambda p: (-(p["extra"] + p["missed"]), p["title"])),
                farm=[dict(title=x["title"], artist=x["artist"], diff=x["diff"], id=x["id"]) for x in labs if x.get("farm")],
                recent=[dict(title=x["title"], artist=x["artist"], diff=x["diff"], mods=x.get("mods", ""), id=x["id"],
                             farm=bool(x.get("farm")),
                             skills=[skills.SKILLS[k]["title"] for k in x["skills"]],
                             formula=[skills.SKILLS[k]["title"] for k in x.get("formula", [])]) for x in labs[:12]],
                # пороги навыков, сдвинутые по отметкам (для всех карт), и отправка на сайт
                calibration=[dict(key=k, title=skills.SKILLS[k]["title"], **v) for k, v in label_model().calibration.items()],
                sync=_sync["status"], token=bool(config.load().get("labels_token")))


def aggregate(analyses):
    """Сумма разборов: ошибки по паттернам, причины промахов, тайминг, отрезки, прицел.

    «Во сколько раз больше ошибок» (ratio) считается внутри каждой попытки - паттерн против всей той же
    карты - и потом усредняется: так тяжёлая карта с кучей ошибок везде не выдаёт свои паттерны за
    твои слабые места."""
    tot = dict(n=0, w=0.0, great=0, ok=0, meh=0, miss=0)
    tags, causes, sections = {}, dict(skip=0, timing=0, aim=0, noclick=0), [[0, 0.0] for _ in range(10)]
    norm = {}
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
            if sm["err"] > 0 and v["n"] >= 8:
                w = min(v["n"], 200)
                nr = norm.setdefault(tg, [0.0, 0.0])
                nr[0] += w * v["err"] / sm["err"]
                nr[1] += w
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
        nr = norm.get(tg)
        ratio = nr[0] / nr[1] if nr and nr[1] else (r / rate if rate else None)
        tag_rows.append(dict(tag=tg, name=TAG_INFO.get(tg, (tg, None))[0], n=t["n"], rate=round(r, 4),
                             ratio=round(ratio, 2) if ratio is not None else None, miss=t["miss"],
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
        if t["n"] < min_n or not t["ratio"] or t["ratio"] < 1.2:
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


# ------------------------------------------------------------- профиль ----

def habits(groups):
    """Привычки по сессиям: нужна ли разминка и после скольких карт падает точность.
    Точность каждой карты сравнивается со средней по её же сессии."""
    rel, first, rest, used = {}, [], [], 0
    for g in groups:
        if len(g) < 5:
            continue
        used += 1
        m = statistics.fmean(s["acc"] for s in g)
        for i, s in enumerate(g):
            rel.setdefault(min(i // 3, 3), []).append(s["acc"] - m)
        first += [s["acc"] - m for s in g[:2]]
        rest += [s["acc"] - m for s in g[2:]]
    out = dict(sessions=used, lines=[], buckets=[])
    if used < 3:
        out["lines"].append("Для выводов о разминке и усталости нужно хотя бы 3 сессии по 5+ карт — пока их %d." % used)
        return out
    labels = ["карты 1–3", "карты 4–6", "карты 7–9", "10-я и дальше"]
    out["buckets"] = [dict(label=labels[b], delta=round(statistics.fmean(v), 4), n=len(v)) for b, v in sorted(rel.items())]
    warm = statistics.fmean(first) - statistics.fmean(rest)
    if warm <= -0.015:
        out["lines"].append("Первые две карты сессии у тебя в среднем на %.1f%% хуже остальных — разминка нужна." % (-warm * 100))
    elif warm >= 0.015:
        out["lines"].append("Первые две карты сессии даже лучше остальных (+%.1f%%): к концу сессии ты устаёшь сильнее, "
                            "чем разогреваешься." % (warm * 100))
    for b in sorted(rel)[1:]:
        v = rel[b]
        if len(v) >= 5 and statistics.fmean(v) <= -0.02:
            out["lines"].append("С %d-й карты сессии точность в среднем на %.1f%% ниже обычной — это хороший момент "
                                "для перерыва." % (3 * b + 1, -statistics.fmean(v) * 100))
            break
    return out


def daily_series(sc, tags, days=21):
    """Индекс слабости паттернов по дням - чтобы было видно, уходит ли слабость."""
    out = {t: [] for t in tags}
    now = time.time()
    for d in range(days - 1, -1, -1):
        lo, hi = now - (d + 1) * DAY, now - d * DAY
        an = [analysis(s) for s in sc if lo <= s["ts"] < hi]
        if not an:
            continue
        by = {t["tag"]: t for t in aggregate(an)["tags"]}
        for tg in tags:
            t = by.get(tg)
            if t and t["n"] >= 30 and t["ratio"]:
                out[tg].append(dict(ts=hi, ratio=t["ratio"], n=t["n"]))
    return out


def focus(sc=None, days=7):
    """Слабое место последних дней (для «Сегодня»)."""
    sc = sc if sc is not None else scores()
    now = time.time()
    an = [analysis(s) for s in sc if now - s["ts"] <= days * DAY]
    wk = weaknesses(aggregate(an), min_n=40)
    top = next((w for w in wk if w["ladder"]), None)
    return dict(top, days=days, plays=len(an)) if top else None


def profile_view():
    """Профиль за 30 дней: слабые места (и сдвиг за неделю), тайминг, промахи, привычки, рост по дням."""
    with _lock:
        sc = scores()
        now = time.time()
        recent = [s for s in sc if now - s["ts"] <= 30 * DAY]
        agg = aggregate([analysis(s) for s in recent])
        weak = weaknesses(agg, min_n=40)
        new = {t["tag"]: t for t in aggregate([analysis(s) for s in recent if now - s["ts"] <= 7 * DAY])["tags"]}
        old = {t["tag"]: t for t in aggregate([analysis(s) for s in recent if now - s["ts"] > 7 * DAY])["tags"]}
        for w in weak:
            a, b = new.get(w["tag"]), old.get(w["tag"])
            if a and b and a["n"] >= 30 and b["n"] >= 30:
                w["week"], w["before"] = a["ratio"], b["ratio"]
        timing = dict(mean=agg["mean"], ur=agg["ur"], hits=agg["n"] - agg["counts"]["miss"],
                      tags=[dict(name=t["name"], mean=t["mean"], n=t["n"]) for t in agg["tags"]
                            if t["mean"] is not None and t["n"] >= 50 and abs(t["mean"]) >= 5])
        if agg["mean"] is not None and abs(agg["mean"]) >= 5 and timing["hits"] >= 1500:
            timing["advice"] = ("Ты стабильно нажимаешь на %.0f мс %s ноты. Если так на всех картах, дело может быть "
                                "в смещении звука (offset): в настройках аудио lazer его можно откалибровать по последней "
                                "сыгранной карте." % (abs(agg["mean"]), "раньше" if agg["mean"] < 0 else "позже"))
        top = [w["tag"] for w in weak[:3]]
        return dict(plays=len(recent), notes=agg["n"], agg=agg, weak=weak, insights=insights(agg), timing=timing,
                    habits=habits(sessions(sc)), series=daily_series(sc, top), series_names={t: TAG_INFO[t][0] for t in top},
                    types=by_type(recent), labels=labels_view())


def by_type(plays):
    """Как ты играешь карты разных типов: главный навык карты -> попыток, точность, промахи, звёзды."""
    groups = {}
    for s in plays:
        ss = skillset(s)
        if not ss or not ss["main"]:
            continue
        k = ss["main"][0]["key"]
        g = groups.setdefault(k, dict(key=k, title=skills.SKILLS[k]["title"], acc=[], misses=[], sr=[]))
        g["acc"].append(s["acc"])
        g["misses"].append(s["misses"])
        g["sr"].append(s["sr"])
    rows = [dict(key=g["key"], title=g["title"], plays=len(g["acc"]), acc=round(statistics.fmean(g["acc"]), 4),
                 misses=round(statistics.fmean(g["misses"]), 1), sr=round(statistics.fmean(g["sr"]), 2),
                 ladder=g["key"] if g["key"] in LADDERS else None)
            for g in groups.values() if len(g["acc"]) >= 2]
    return sorted(rows, key=lambda r: r["acc"])


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
            ss = skillset(s)
            if ss:                              # где эта карта на твоих лестницах
                ladders = load_state()["ladders"]
                ss = dict(ss, fits=[dict(f, yours=ladders[f["ladder"]]["step"] if f["ladder"] in ladders else None)
                                    for f in ss["fits"]])
            return dict(row=row(s, a), analysis=a, attempts=prev[-10:], skillset=ss,
                        weak=weaknesses(aggregate([a]), min_n=12) if a and not a.get("error") else [],
                        insights=insights(aggregate([a])) if a and not a.get("error") else [])
    return None


# -------------------------------------------------------------- лестницы ----

def param_value(lad, m):
    """Главный параметр ступени. У метрик попытки с модами (play_metrics) скорость и CS уже пересчитаны."""
    p = lad["param"]
    if p == "dt_stream_bpm":
        return m["stream_bpm"] * (1.0 if m.get("rate", 1.0) > 1.0 else 1.5)
    if p == "hr_cs":
        return m["cs"] if m.get("hr") else min(10.0, m["cs"] * 1.3)
    return m.get(p, 0)


def meets(lad, m):
    return (all(m.get(k, 0) >= v for k, v in lad.get("need", {}).items())
            and all(m.get(k, 0) <= v for k, v in lad.get("limit", {}).items()))


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
        if now - s["ts"] > 60 * DAY or s["acc"] < 0.93 or s["rank"] < 0 or not fair(s):
            continue
        if lad.get("mod") and not mods_ok(s, lad["mod"]):
            continue
        m = play_metrics(s)                 # с модами попытки: DT-попытка - это её настоящие BPM
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
    evaluate_random(st, sc)


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
    a.metric_filter = lambda m: meets(lad, m) and in_step(lad, step, param_value(lad, m)) and not is_farm(m)
    a.exclude_md5 = set(exclude)
    a.adjust = personal_adjust
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
    rnd = ld.get("random", [])
    window = [x for x in rnd if x["step"] == ld["step"]][-OF:]
    return dict(key=key, title=lad["title"], mod=lad.get("mod"), step=ld["step"], best=ld.get("best", ld["step"]),
                start=ld.get("start", 0), steps=[step_label(lad, i) for i in range(len(lad["steps"]))],
                threshold=threshold(st, key), escape=ld.get("escape"), fails_in_row=ld.get("fails_in_row", 0),
                active=st["active"] == key, history=history, free=free_play(st, key),
                random=dict(window=window, ok=sum(1 for x in window if x["ok"]), total=len(rnd),
                            ups=ld.get("random_ups", 0)),
                open=next((t for t in reversed(trs) if t["status"] == "open"), None))


def free_play(st, key, days=14):
    """Карты текущей ступени, сыгранные вне тренировок и случайных карт (соло и мультиплеер): в зачёт
    не идут, но показывают, как ты тянешь эту ступень в обычной игре."""
    lad, step, thr = LADDERS[key], st["ladders"][key]["step"], threshold(st, key)
    in_trainings = {m["md5"] for t in st["trainings"] if t["ladder"] == key for m in t["maps"]}
    r = st.get("random") or {}
    in_trainings |= {h["md5"] for h in r.get("history", []) + ([r["current"]] if r.get("current") else [])}
    now, out = time.time(), []
    for s in scores():
        if now - s["ts"] > days * DAY or s["md5"] in in_trainings or s["rank"] < 0:
            continue
        ss = skillset(s)
        if ss and any(f["ladder"] == key and f["step"] == step for f in ss["fits"]):
            out.append(dict(id=s["id"], title=s["title"], diff=s["diff"], acc=round(s["acc"], 4), misses=s["misses"],
                            ok=passed(s, thr)))
    return dict(days=days, plays=out[-8:], n=len(out), ok=sum(1 for x in out if x["ok"]),
                acc=round(statistics.fmean(x["acc"] for x in out), 4) if out else None)


# ------------------------------------------------------- случайная карта ----
# Одна карта случайного навыка около твоего уровня: сыграл - тренер видит результат и сам готовит
# следующую. В игре она лежит в коллекции, где всегда ровно одна текущая карта.

RANDOM_COLLECTION = "osu!drill · случайная"
PREPARE_TIMEOUT = 300


def random_state(st):
    r = st.setdefault("random", {})
    r.setdefault("current", None)
    r.setdefault("history", [])
    r.setdefault("auto", False)
    r.setdefault("skill", "any")
    return r


def on_step(target, m):
    lad, step = target
    return meets(lad, m) and in_step(lad, step, param_value(lad, m))


def farm_skills():
    """Навыки твоих фарм-карт в случайном порядке: чем чаще навык среди них, тем вероятнее он первый."""
    cnt = collections.Counter(k for lab in load_labels()["labels"].values() if lab.get("farm") for k in lab["skills"])
    return sorted(cnt, key=lambda k: -cnt[k] * random.random()) or random.sample(list(skills.SKILLS), len(skills.SKILLS))


def farm_pick(exclude, log):
    """Случайная новая фарм-карта около твоего уровня: сначала соседи твоих фарм-карт по подборкам игроков
    (кто держит их в коллекциях, держит там и похожие), потом карты тех же навыков; берётся первая, похожая
    на отмеченные фарм-карты."""
    labs = [lab for lab in load_labels()["labels"].values() if lab.get("farm")]
    if not labs:
        raise RuntimeError("Сначала отметь хотя бы одну фарм-карту — в окошке после карты")
    c = comfort_stars()
    stars = (max(1.0, round(c - 0.5, 2)), round(c + 0.5, 2))
    index = collector.load()
    refs = {lab["bid"] for lab in labs if lab.get("bid")}
    sets = {lab["sid"] for lab in labs if lab.get("sid")}
    co = index.cooc(sets) if index else None
    if co is not None and not co["hits"]:
        co = None
    for key in farm_skills()[:3]:
        cfg = skills.SKILLS[key]
        log("Фарм, как твои отметки: %s, звёзды %.1f–%.1f (твой уровень ~%.1f★)" % (cfg["title"], stars[0], stars[1], c))
        a = trainer.coerce_params(dict(skill=key, stars="%.2f-%.2f" % stars, pool=80, depth=100, crowd_weight=0.5,
                                       status="ranked,loved", lang="ru"))
        trainer.prepare(a)
        try:
            cands = trainer.gather_crowd(index, [key], co, refs, a, stars, set(), log) if index else []
        except (OSError, RuntimeError):
            cands = []
        # другие сложности уже отмеченных песен - не новость, нужны другие песни
        cands = [x for x in cands if x.get("md5") and x["md5"] not in exclude and x["sid"] not in sets]
        near = [x for x in cands if co and index.cooc_info(co, x["bid"], x["sid"])[0] > 0]
        rest = [x for x in cands if not (co and index.cooc_info(co, x["bid"], x["sid"])[0] > 0)]
        random.shuffle(near)
        random.shuffle(rest)
        if near:
            log("  соседей твоих фарм-карт по подборкам игроков: %d" % len(near))
        scorer = trainer.make_scorer([cfg], None, a)
        best = None
        for cand in (near + rest)[:30]:
            res = trainer.score_candidate(cand, scorer, a)
            if not res:
                continue
            fs, like = farm_score(res["metrics"])
            if fs >= 0.5:
                log("  похожа на твою фарм-карту «%s»" % like)
                return key, res, False
            if fs >= 0.25 and (best is None or fs > best[0]):
                best = (fs, res, like)
        if best:        # просишь фарм - лучше самая похожая из проверенных, чем ничего
            log("  ближе всех к твоим фарм-картам — %s (похожа на «%s»)" % (best[1]["title"], best[2]))
            return key, best[1], False
        log("  похожих на твои фарм-карты нет — пробую другой навык")
    raise RuntimeError("Похожих на твои фарм-карты не нашлось — отметь ещё пару фарм-карт или попробуй позже")


def random_pick(skill, exclude, log, steps=None):
    """Случайная новая карта навыка (или случайного навыка) около твоего уровня: кандидаты из подборок
    игроков и поиска, проверяются разбором по одному, пока не найдётся карта с явным навыком. Если на навык
    есть твоя лестница, лучше карта её ступени - тогда она пойдёт в зачёт. Похожие на твои фарм-карты -
    только в режиме «farm», и тогда только они. -> (навык, карта, на ступени ли)."""
    if skill == "farm":
        return farm_pick(exclude, log)
    steps = steps or {}
    c = comfort_stars()
    stars = (max(1.0, round(c - 0.5, 2)), round(c + 0.5, 2))
    keys = [skill] if skill in skills.SKILLS else list(steps) if skill == "ladders" and steps else list(skills.SKILLS)
    random.shuffle(keys)
    index = collector.load()
    for key in keys[:4]:
        cfg = skills.SKILLS[key]
        log("Навык: %s, звёзды %.1f–%.1f (твой уровень ~%.1f★)" % (cfg["title"], stars[0], stars[1], c))
        a = trainer.coerce_params(dict(skill=key, stars="%.2f-%.2f" % stars, pool=80, depth=100, crowd_weight=0.5,
                                       status="ranked,loved", lang="ru"))
        a.adjust = personal_adjust          # твои отметки навыков похожих карт
        trainer.prepare(a)
        cands = []
        try:
            cands = trainer.gather_crowd(index, [key], None, (), a, stars, set(), log) if index else []
        except (OSError, RuntimeError):
            cands = []
        if len(cands) < 8:
            try:
                cands += trainer.gather_online(cfg["queries"][:2] + [""], a, stars, set(), log)
            except (OSError, RuntimeError):
                pass
        cands = [x for x in cands if x.get("md5") and x["md5"] not in exclude]
        random.shuffle(cands)
        scorer = trainer.make_scorer([cfg], None, a)
        target = steps.get(key)
        if target:
            log("  есть твоя лестница — ищу карту ступени %d: %s" % (target[1] + 1, step_label(*target)))
        fallback, extra = None, 0
        for cand in cands[:25]:
            if fallback and extra >= 8:     # карта навыка уже есть - ступень ищем недолго
                break
            extra += 1 if fallback else 0
            res = trainer.score_candidate(cand, scorer, a)
            if not res or res["score"] < cfg["min_score"]:
                continue
            fs, like = farm_score(res["metrics"])
            if fs >= 0.5:
                log("  %s — похожа на твою фарм-карту «%s», пропускаю" % (res["title"], like))
                continue
            if not target or on_step(target, res["metrics"]):
                return key, res, bool(target)
            fallback = fallback or res
        if fallback:
            log("  карты ровно твоей ступени среди кандидатов нет — даю просто карту навыка")
            return key, fallback, False
        log("  подходящей новой карты не нашлось — пробую другой навык")
    raise RuntimeError("Не нашёл подходящей новой карты — попробуй ещё раз")


def _deliver(m, log):
    """Карта в игру: скачать и отдать lazer, если её нет, и сделать коллекцию ровно из неё."""
    if m["md5"] not in {b["md5"] for b in trainer.local_library()}:
        log("Скачиваю %s — %s..." % (m["artist"], m["title"]))
        path = net.osz(m["sid"], trainer.DL)
        if not path:
            return False
        trainer.import_into_osu([path])
        log("  отправил в osu! — во время игры lazer добавит её, когда выйдешь в меню")
    log("Резервная копия базы: %s" % trainer.backup_realm())
    pf = os.path.join(COACH_DIR, "random_payload.json")
    _save(pf, [{"name": RANDOM_COLLECTION, "hashes": [m["md5"]]}])
    trainer.realm_cmd("set", pf)
    return True


def random_next(log, skill=None):
    with _lock:
        st = load_state()
        r = random_state(st)
        if skill:
            r["skill"] = skill
        r["auto"] = True
        r["preparing"] = time.time()
        skill = r["skill"]
        exclude = played_recently(scores()) | reserved_md5(st) | {h["md5"] for h in r["history"]}
        if r["current"]:
            exclude.add(r["current"]["md5"])
        steps = {k: (LADDERS[k], ld["step"]) for k, ld in st["ladders"].items()
                 if k in skills.SKILLS and LADDERS.get(k, {}).get("skill") == k}
        save_state(st)
    pick, error = None, "Карту подобрать не удалось"
    try:
        for _attempt in range(3):
            try:
                key, m, stepped = random_pick(skill, exclude, log, steps)
            except RuntimeError as e:
                error = str(e)
                raise
            pick = dict(bid=m["bid"], sid=m["sid"], md5=m["md5"], sr=m["sr"], title=m["title"], artist=m["artist"],
                        diff=m["diff"], skill=key, skill_title=skills.SKILLS[key]["title"], score=m["score"],
                        why=m.get("why", ""), bpm=m["metrics"]["bpm"], length=m["metrics"]["length"], given=time.time())
            if stepped:
                pick.update(ladder=key, ladder_title=LADDERS[key]["title"], ladder_step=steps[key][1],
                            ladder_label=step_label(*steps[key]))
            if skill == "farm":
                pick["farm"] = True
            if _deliver(pick, log):
                break
            log("  не скачалась — ищу другую")
            exclude.add(pick["md5"])
            pick = None
        if not pick:
            error = "Карты не скачиваются — зеркало не отвечает, попробуй позже"
            raise RuntimeError(error)
    finally:
        with _lock:
            st = load_state()
            r = random_state(st)
            r.pop("preparing", None)
            r.pop("error", None)
            if pick:
                cur = r["current"]
                if cur and not cur.get("result"):       # предыдущую не сыграл - в историю как пропущенную
                    r["history"].append(dict(cur, skipped=True))
                r["current"] = pick
                r["history"] = r["history"][-60:]
            else:           # не вышло - режим встаёт, иначе фон пробовал бы снова каждые 15 секунд
                r["auto"], r["error"] = False, error
            save_state(st)
    log("Готово: %s — %s [%s], %.2f★ · %s." % (pick["artist"], pick["title"], pick["diff"], pick["sr"], pick["skill_title"]))
    if pick.get("ladder"):
        log("Ступень %d лестницы %s (%s): возьмёшь порог — пойдёт в зачёт." % (
            pick["ladder_step"] + 1, pick["ladder_title"], pick["ladder_label"]))
    log("Код для поиска в выборе карты: %s (или коллекция «%s»)" % (pick["bid"], RANDOM_COLLECTION))
    return pick


def random_stop():
    with _lock:
        st = load_state()
        random_state(st)["auto"] = False
        save_state(st)


def credit_random(st, s):
    """Случайная карта в зачёт лестниц - по скиллсету с модами попытки. Карта твоей ступени идёт в зачёт
    как карта тренировки: из последних OF таких карт порог взят на NEED - ступень сдана. Карта выше
    ступени засчитывается, только если порог взят (провал на ней о твоей ступени ничего не говорит),
    ниже - не засчитывается. Проиграть случайными картами нельзя: несданные тренировки они не копят."""
    out = []
    ss = skillset(s)
    for f in (ss["fits"] if ss else []):
        key, ld = f["ladder"], st["ladders"].get(f["ladder"])
        if not ld or s["ts"] < ld.get("created", 0):
            continue                        # до лестницы - по таким попыткам выбрана стартовая ступень
        lad, step, log = LADDERS[key], ld["step"], ld.setdefault("random", [])
        if any(x["id"] == s["id"] for x in log):
            continue
        ok = passed(s, threshold(st, key))
        item = dict(ladder=key, title=lad["title"], step=step, map_step=f["step"], ok=ok)
        if f["step"] < step or (f["step"] > step and not ok):
            out.append(dict(item, counted=False))
            continue
        log.append(dict(id=s["id"], step=step, ok=ok, ts=s["ts"], title=s["title"], diff=s["diff"], value=f["value"]))
        del log[:-60]
        window = [x for x in log if x["step"] == step][-OF:]
        got = sum(1 for x in window if x["ok"])
        up = got >= NEED and step < len(lad["steps"]) - 1
        if up:
            ld["step"] = step + 1
            ld["best"] = max(ld.get("best", 0), ld["step"])
            ld["fails_in_row"], ld["escape"] = 0, False
            ld["random_ups"] = ld.get("random_ups", 0) + 1
        out.append(dict(item, counted=True, got=got, played=len(window), up=up))
    return out


def evaluate_random(st, sc):
    r = st.get("random")
    if not r:
        return
    cur = r.get("current")
    if cur and not cur.get("result"):
        for s in sc:
            if s["md5"] == cur["md5"] and s["ts"] >= cur["given"] - 5:
                rw = row(s)
                cur["result"] = dict(id=s["id"], acc=round(s["acc"], 4), misses=s["misses"], rank=s["rank"], ts=s["ts"],
                                     ur=rw.get("ur"), weak=[TAG_INFO.get(t, (t,))[0] for t in rw.get("weak", [])])
                r["history"].append(dict(cur))
                r["history"] = r["history"][-60:]
                break
    # зачёт лестниц и скиллсет с модами - один раз на попытку (и для сыгранных до появления зачёта)
    by_id, done = {s["id"]: s for s in sc}, {}
    for h in r["history"] + ([cur] if cur else []):
        res = h.get("result")
        s = res and by_id.get(res["id"])
        if not s or "ladders" in res:
            continue
        if s["id"] not in done:
            done[s["id"]] = (credit_random(st, s), row(s)["kind"])
        res["ladders"], res["kind"] = done[s["id"]]
        res["mods"] = s["mods_list"]


def random_pending():
    """Пора готовить следующую: текущая сыграна, режим включён, сборка не идёт (или зависла)."""
    with _lock:
        r = random_state(load_state())
        busy = r.get("preparing") and time.time() - r["preparing"] < PREPARE_TIMEOUT
        return bool(r["auto"] and r["current"] and r["current"].get("result") and not busy)


def random_view(st):
    r = random_state(st)
    busy = bool(r.get("preparing") and time.time() - r["preparing"] < PREPARE_TIMEOUT)
    return dict(current=r["current"], auto=r["auto"], skill=r["skill"], preparing=busy, error=r.get("error"),
                farm_ready=has_farm(),
                history=[h for h in r["history"] if h.get("result")][-8:][::-1],
                played=sum(1 for h in r["history"] if h.get("result")), collection=RANDOM_COLLECTION,
                skills=[dict(key=k, title=v["title"]) for k, v in skills.SKILLS.items()])


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
        if w["ladder"] and w["ladder"] not in ladders and len(ladders) < 3:
            ladders.append(w["ladder"])
            reasons.append("%s: %s" % (w["name"], w["text"]))
    nm = mods.get("NM")
    for g, key in (("DT", "dt"), ("HR", "hr"), ("HD", "hd")):     # лестницы с модом - всегда, сверх трёх
        if nm is not None and g in mods and mods[g] < nm - 0.02:
            ladders.append(key)
            reasons.append("С %s точность %.1f%% против %.1f%% без модов" % (g, mods[g] * 100, nm * 100))
    if not ladders:                         # явной слабости нет - начинаем с того, где ошибок больше всего
        top = next((t for t in agg["tags"] if t["ladder"] and t["n"] >= 20), None)
        if top:
            ladders.append(top["ladder"])
            reasons.append("Явной слабости нет; больше всего ошибок — %s" % top["name"])
    return dict(created=time.time(), plays=len(plays), agg=agg, weak=wk, insights=insights(agg), mods=mods,
                ladders=ladders, reasons=reasons)


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
    f = focus(sc)
    if f:
        items.append(dict(kind="focus", active=key, is_active=(f["ladder"] == key),
                          ladder_title=LADDERS[f["ladder"]]["title"], **f))
    items.append(dict(kind="free", habits=habits(sessions(sc))["lines"]))
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
            random=random_view(st), ask=ask_view(sc), need=NEED, of=OF, default_threshold=DEFAULT_THRESHOLD,
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
