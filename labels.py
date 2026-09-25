# -*- coding: utf-8 -*-
"""
Отметки навыков карт как поправка к формулам подбора - в тренере, в программе и на сайте.

Отметка - к каким навыкам карта (такой, какой её сыграли) относится на самом деле и не фарм ли это.
Отметки ставит автор в тренере; обезличенная копия (без попыток, точности и времени) уходит на сайт,
а программы без тренера берут её оттуда. По отметкам подбор меняется так:
  1. порог навыка: если формула систематически видит навык не там, где отметки, её оценки этого навыка
     сдвигаются для всех карт - чем больше отметок около порога, тем смелее;
  2. похожие карты: где формула ошиблась на похожей отмеченной карте, похожим картам оценка сдвигается
     так, чтобы формула увидела их так же (с запасом), - чем похожее, тем сильнее;
  3. фарм: похожие на отмеченные фарм-карты подбор даёт только по просьбе («только фарм-карты»).
"""
import hashlib
import json
import math
import os
import secrets
import threading
import time

import config
import net
import skills
from i18n import _

# признаки похожести карт: как у «похоже на эти карты» плюс bursts/alt, длина и OD
KNN_FEATURES = dict(skills.PROFILE_FEATURES, burst_ratio=(0.1, 1.5), burst_bpm=(25.0, 0.8), alt_ratio=(0.05, 1.5),
                    length=(60.0, 0.6), od=(0.5, 0.4))
KNN_SIGMA = 0.6         # похожесть exp(-(d/σ)²): у сыгранных карт ближайшая соседка в среднем на d≈0.65
KNN_MARGIN = 15         # отмеченная карта должна оказаться по нужную сторону порога навыка с таким запасом
KNN_SHRINK = 0.3        # одна далёкая отметка сдвигает мало
FARM_SIGMA = 0.9        # фарм - «район» пошире: одна отметка задевает карты до d≈1
CAL_RANGE = 30          # положение порога навыка решают отметки не дальше этого от него
CAL_PRIOR = 10          # столько отметок около порога - сдвиг порога наполовину
CAL_MAX = 20

# метрики, которые нужны формулам навыков (skills.py) и похожести; остальное на сайт не уходит
METRIC_KEYS = sorted(set(KNN_FEATURES) | {
    "long_runs", "aim_share", "stream_spacing", "nps", "sv_var", "odd_ratio", "slider_anchors", "tap_entropy", "hidden"})

GLOBAL_DIR = os.path.join(config.CACHE_DIR, "labels")
GLOBAL_JSON = os.path.join(GLOBAL_DIR, "global.json")
TOKEN_FILE = os.path.join(GLOBAL_DIR, "token")
FETCH_EVERY = 12 * 3600     # программа без тренера берёт общие отметки с сайта не чаще
MAX_LABELS = 5000
BATCH_BYTES = 48000         # сайт и nginx принимают запросы до 64 КБ


def raw_scores(m):
    out = {}
    for key, cfg in skills.SKILLS.items():
        try:
            out[key] = cfg["score"](m)[0]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def distance(a, b):
    num = den = 0.0
    for k, (spread, w) in KNN_FEATURES.items():
        d = (a.get(k, 0) - b.get(k, 0)) / spread
        num += w * d * d
        den += w
    return math.sqrt(num / den)


class Model:
    """Поправки подбора по набору отметок. Потокобезопасна: подбор оценивает карты в нескольких потоках."""

    def __init__(self, entries=()):
        self.items = []
        for e in entries:
            m = e.get("metrics") if isinstance(e, dict) else None
            if isinstance(m, dict):
                self.items.append(dict(m=m, skills=set(e.get("skills") or ()), farm=bool(e.get("farm")),
                                       title=str(e.get("title") or ""), scores=raw_scores(m)))
        self.farm = any(it["farm"] for it in self.items)
        self.calibration = self._calibrate()
        self.shift = {k: v["shift"] for k, v in self.calibration.items()}
        self._local = threading.local()

    def __len__(self):
        return len(self.items)

    def _calibrate(self):
        """Где порог навыка по отметкам: для каждого навыка - порог, при котором формула лучше всего совпадает
        с отметками около него (сбалансированно по «да» и «нет»). Сдвиг оценок - к нему, но с оглядкой на число
        отметок: пока их мало, формула почти не трогается."""
        out = {}
        for key, cfg in skills.SKILLS.items():
            mn = cfg["min_score"]
            pts = [(it["scores"][key], key in it["skills"]) for it in self.items
                   if key in it["scores"] and abs(it["scores"][key] - mn) <= CAL_RANGE]
            pos = sum(1 for _s, y in pts if y)
            neg = len(pts) - pos
            if pos < 3 or neg < 3:
                continue
            best = None
            for t in range(mn - 25, mn + 26):
                tp = sum(1 for s, y in pts if y and s >= t)
                tn = sum(1 for s, y in pts if not y and s < t)
                cand = (tp / pos + tn / neg, -abs(t - mn), t)
                best = max(best, cand) if best else cand
            shift = max(-CAL_MAX, min(CAL_MAX, (mn - best[2]) * len(pts) / (len(pts) + CAL_PRIOR)))
            if abs(shift) >= 1:
                out[key] = dict(shift=round(shift, 1), n=len(pts), agree=round(best[0] / 2, 3))
        return out

    def _dists(self, m):
        """Расстояния до отмеченных карт - один раз на карту: подбор спрашивает о ней по каждому навыку и о фарме."""
        c = getattr(self._local, "d", None)
        if c is not None and c[0] is m:
            return c[1]
        ds = [(distance(m, it["m"]), it) for it in self.items]
        self._local.d = (m, ds)
        return ds

    def deltas(self, m, keys=None):
        """Сдвиги оценок навыков карты: {навык: (сдвиг, похожая отмеченная карта или None)} - порог навыка
        по отметкам плюс поправка по похожим картам, где формула на них ошиблась."""
        if not self.items or not m:
            return {}
        near = [(math.exp(-(d / KNN_SIGMA) ** 2), it) for d, it in self._dists(m)]
        near = [(w, it) for w, it in near if w >= 0.05]
        out = {}
        for key in keys or skills.SKILLS:
            mn = skills.SKILLS[key]["min_score"]
            sh = self.shift.get(key, 0.0)
            num = den = 0.0
            best = None
            for w, it in near:
                if key == "reading" and bool(m.get("hidden")) != bool(it["m"].get("hidden")):
                    continue            # отметка чтения с HD о карте без HD ничего не говорит (и наоборот)
                s = it["scores"].get(key, 0.0) + sh
                if (key in it["skills"]) == (s >= mn):
                    target = s          # формула видит эту карту так же, как отметка
                else:
                    target = mn + KNN_MARGIN if key in it["skills"] else mn - KNN_MARGIN
                num += w * (target - s)
                den += w
                if target != s and (best is None or w > best[0]):
                    best = (w, it["title"])
            knn = num / (den + KNN_SHRINK) if best else 0.0
            if abs(sh + knn) >= 1:
                out[key] = (sh + knn, best[1] if best and abs(knn) >= 1 else None)
        return out

    def adjust(self, cfg, m, s, why):
        """Хук подбора (a.adjust в trainer.make_scorer): оценка навыка с поправкой по отметкам."""
        key = next((k for k, v in skills.SKILLS.items() if v is cfg), None)
        d = self.deltas(m, [key]).get(key) if key else None
        if not d:
            return s, why
        note = (_("по отметкам автора: %+.0f (похожа на «%s»)", d[0], d[1]) if d[1]
                else _("по отметкам автора: %+.0f (порог навыка)", d[0]))
        return max(0.0, min(100.0, s + d[0])), "%s · %s" % (why, note)

    def farm_score(self, m):
        """Насколько карта похожа на отмеченные фарм-карты: (доля фарма среди похожих отметок 0..1, самая
        похожая фарм-карта). Похожие отметки «не фарм» долю разбавляют."""
        if not self.farm or not m:
            return 0.0, None
        num = den = 0.0
        best = None
        for d, it in self._dists(m):
            w = math.exp(-(d / FARM_SIGMA) ** 2)
            if w < 0.05:
                continue
            den += w
            if it["farm"]:
                num += w
                if best is None or w > best[0]:
                    best = (w, it["title"])
        return (num / (den + KNN_SHRINK), best[1]) if best else (0.0, None)

    def is_farm(self, m):
        return self.farm_score(m)[0] >= 0.5

    def farm_filter(self, mode):
        """Фильтр метрик для подбора: похожие на фарм-карты - только по просьбе (mode "only")."""
        if mode == "only":
            if not self.farm:
                raise RuntimeError(_("Фарм-карт пока не отмечено"))
            return self.is_farm
        return (lambda m: not self.is_farm(m)) if self.farm else None


# ----------------------------------------------------- обезличенная копия ----

def entry_key(label_key):
    return hashlib.sha1(label_key.encode("utf-8")).hexdigest()[:16]


def clean(e):
    """Отметка в том виде, в каком она лежит на сайте: только то, что нужно подбору, без попыток и времени."""
    if not isinstance(e, dict) or not isinstance(e.get("metrics"), dict):
        return None
    mm = {}
    for k in METRIC_KEYS:
        v = e["metrics"].get(k)
        if isinstance(v, bool):
            mm[k] = v
        elif isinstance(v, (int, float)) and math.isfinite(v):
            mm[k] = round(float(v), 3)
    if len(mm) < len(METRIC_KEYS) - 1:          # без hidden можно, без формульных метрик - нет
        return None
    try:
        bid, sid = int(e.get("bid") or 0), int(e.get("sid") or 0)
    except (TypeError, ValueError):
        return None
    return dict(k=str(e.get("k") or "")[:40], skills=[k for k in skills.SKILLS if k in set(e.get("skills") or ())],
                farm=bool(e.get("farm")), metrics=mm, title=str(e.get("title") or "")[:200],
                artist=str(e.get("artist") or "")[:200], diff=str(e.get("diff") or "")[:200],
                mods=str(e.get("mods") or "")[:40], bid=bid, sid=sid)


def entry_hash(e):
    return hashlib.sha1(json.dumps(e, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------ общие отметки ----

_glob = dict(model=None, mtime=None, fetching=False)
_lock = threading.Lock()


def global_model():
    """Общие отметки: на сайте - присланные автором, в программе без тренера - скачанные с сайта."""
    try:
        mt = os.path.getmtime(GLOBAL_JSON)
    except OSError:
        mt = None
    if _glob["model"] is None or mt != _glob["mtime"]:
        data = _load(GLOBAL_JSON) or {}
        _glob.update(model=Model((data.get("labels") or {}).values()), mtime=mt)
    return _glob["model"]


def server_token():
    """Ключ, с которым автор присылает отметки на сайт: сайт создаёт его сам при первом запуске."""
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    t = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t)
    return t


def digest():
    data = _load(GLOBAL_JSON) or {}
    return {k: entry_hash(e) for k, e in (data.get("labels") or {}).items()}


def public_list():
    data = _load(GLOBAL_JSON) or {}
    return dict(labels=list((data.get("labels") or {}).values()), updated=data.get("updated"))


def apply_upload(body):
    """Сайт: присланные автором отметки - добавить/заменить (upsert) и убрать (delete)."""
    with _lock:
        data = _load(GLOBAL_JSON) or {}
        labs = data.get("labels") or {}
        for k in body.get("delete") or []:
            labs.pop(str(k), None)
        for e in body.get("upsert") or []:
            c = clean(e)
            if c and c["k"]:
                labs[c["k"]] = c
        if len(labs) > MAX_LABELS:
            raise ValueError("too many labels")
        _save(GLOBAL_JSON, dict(labels=labs, updated=time.time()))
        return len(labs)


def upload(entries, token):
    """Тренер автора -> сайт: докачать новые и изменённые отметки, убрать удалённые. -> сколько на сайте."""
    base = config.SITE_URL.rstrip("/")
    r = net.fetch(base + "/api/labels", params={"digest": 1}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError("сайт не отдал список отметок (%d)" % r.status_code)
    remote = r.json().get("digest") or {}
    local = {}
    for e in entries:
        c = clean(e)
        if c and c["k"]:
            local[c["k"]] = c
    changed = [e for k, e in local.items() if remote.get(k) != entry_hash(e)]
    batches, cur, size = [], [], 0
    for e in changed:
        n = len(json.dumps(e, ensure_ascii=False).encode("utf-8"))
        if cur and size + n > BATCH_BYTES:
            batches.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        batches.append(cur)
    gone = [k for k in remote if k not in local]
    if gone and not batches:
        batches.append([])
    h = {"X-Requested-With": "osu-trainer", "Authorization": "Bearer " + token}
    total = len(remote)
    for i, batch in enumerate(batches):
        r = net.post_json(base + "/api/labels", dict(upsert=batch, delete=gone if i == 0 else []), headers=h, timeout=30)
        if r.status_code != 200:
            raise RuntimeError("сайт не принял отметки (%d)" % r.status_code)
        total = r.json().get("n", total)
    return total, len(changed), len(gone)


def refresh_global(force=False):
    """Программа без тренера: общие отметки с сайта, не чаще раза в 12 часов, в фоне."""
    try:
        fresh = time.time() - os.path.getmtime(GLOBAL_JSON) < FETCH_EVERY
    except OSError:
        fresh = False
    if (fresh and not force) or _glob["fetching"]:
        return
    _glob["fetching"] = True

    def work():
        try:
            r = net.fetch(config.SITE_URL.rstrip("/") + "/api/labels", timeout=30)
            if r.status_code == 200:
                labs = {}
                for e in r.json().get("labels") or []:
                    c = clean(e)
                    if c and c["k"]:
                        labs[c["k"]] = c
                _save(GLOBAL_JSON, dict(labels=labs, updated=time.time()))
        except (OSError, ValueError):
            pass            # сайта нет - подбор работает по одним формулам
        finally:
            _glob["fetching"] = False

    threading.Thread(target=work, daemon=True).start()
