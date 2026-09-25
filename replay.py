# -*- coding: utf-8 -*-
"""
Разбор попытки по сохранённому повтору (.osr) и карте (.osu), только стандартная библиотека.

Для каждого круга и начала slider восстанавливается то же решение, что принял lazer: попал или нет,
на сколько рано или поздно, где был курсор, почему промах. Сверено с подсчётом самой игры: на
71 попытке без модов и с HD совпали 66 точно, остальные расходятся на 1-4 объекта. Что нужно для
совпадения: окна попадания floor(окно) - 0.5 мс, радиус круга x1.00041, укладка стопок (stacking)
и notelock lazer (нельзя попасть по кругу, пока не наступило время предыдущего несбитого).
Концы и тики slider не моделируются: частоты кадров повтора (60 Гц) для этого не хватает, их
количество берётся из статистики игры.

Всё в «времени трека» (как в повторе); для показа миллисекунды делятся на скорость (DT, HT).
"""
import bisect
import json
import lzma
import math
import statistics
import struct

ANALYSIS_VERSION = 3
MISS_WINDOW = 400.0
STACK_DISTANCE = 3.0
STREAM_MAX_MS = 150.0
FAST_MS = 130.0

RESULT_CODE = {"great": 0, "ok": 1, "meh": 2, "miss": 3}
# почему промах: 1 - пропустил ноту (нажал следующую раньше, notelock lazer), 2 - нажал на круг,
# но слишком рано или поздно, 3 - нажал вовремя, но мимо круга, 4 - не нажал
CAUSE_SKIP, CAUSE_TIMING, CAUSE_AIM, CAUSE_NOCLICK = 1, 2, 3, 4

TAGS = ("burst", "stream", "long_stream", "deathstream", "stream_end", "jumpstream", "jump", "big_jump",
        "rhythm_change", "odd_snap", "slider", "after_slider", "sv_change", "after_break")


def _f32(v):
    """lazer хранит сложность во float - без этого окна попадания съезжают на границах."""
    return struct.unpack("<f", struct.pack("<f", float(v)))[0]


# ------------------------------------------------------------------ .osr ----

class _Reader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def take(self, n):
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def i64(self):
        return struct.unpack("<q", self.take(8))[0]

    def uleb(self):
        res = shift = 0
        while True:
            byte = self.u8()
            res |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return res
            shift += 7

    def s(self):
        flag = self.u8()
        if flag == 0:
            return None
        return self.take(self.uleb()).decode("utf-8", "replace")


def read_osr(path):
    """Повтор: кадры (время, x, y, кнопки), моды-биты, блок lazer (JSON) если есть."""
    with open(path, "rb") as f:
        b = f.read()
    r = _Reader(b)
    mode = r.u8()
    version = r.i32()
    md5 = r.s()
    r.s()                                   # имя игрока не нужно
    r.s()
    [r.u16() for _ in range(6)]
    r.i32()
    r.u16()
    r.u8()
    mods = r.i32()
    r.s()
    r.i64()
    comp = r.take(r.i32())
    r.i64()
    if mods & (1 << 23):                    # Target Practice
        r.take(8)
    lazer = None
    if len(b) - r.i >= 4:
        n = r.i32()
        try:
            lazer = json.loads(lzma.decompress(r.take(n), format=lzma.FORMAT_ALONE).decode("utf-8"))
        except (lzma.LZMAError, ValueError):
            lazer = None
    frames = []
    if comp:
        text = lzma.decompress(comp, format=lzma.FORMAT_ALONE).decode("ascii", "ignore")
        t = 0.0
        for part in text.split(","):
            p = part.split("|")
            if len(p) < 4 or p[0] == "-12345":
                continue
            try:
                t += float(p[0])
                frames.append((t, float(p[1]), float(p[2]), int(float(p[3]))))
            except ValueError:
                continue
    return dict(mode=mode, version=version, md5=md5, mods_bits=mods, frames=frames, lazer=lazer)


# ------------------------------------------------------------------ .osu ----

def parse_osu(text):
    m = dict(version=14, tps=[], hos=[], StackLeniency=0.7, CircleSize=5.0, OverallDifficulty=5.0,
             ApproachRate=None, SliderMultiplier=1.4, SliderTickRate=1.0, Mode=0)
    lines = text.lstrip("﻿").splitlines()
    if lines and "v" in lines[0]:
        try:
            m["version"] = int(lines[0].strip().rsplit("v", 1)[-1])
        except ValueError:
            pass
    sec = None
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("[") and s.endswith("]"):
            sec = s
            continue
        if sec in ("[General]", "[Difficulty]") and ":" in s:
            k, v = (p.strip() for p in s.split(":", 1))
            if k in ("StackLeniency", "CircleSize", "OverallDifficulty", "ApproachRate", "SliderMultiplier",
                     "SliderTickRate", "Mode"):
                try:
                    m[k] = float(v)
                except ValueError:
                    pass
        elif sec == "[TimingPoints]":
            p = s.split(",")
            if len(p) < 2:
                continue
            try:
                uninherited = int(p[6]) == 1 if len(p) > 6 else True
                m["tps"].append((float(p[0]), float(p[1]), uninherited))
            except ValueError:
                continue
        elif sec == "[HitObjects]":
            p = s.split(",")
            if len(p) < 4:
                continue
            try:
                ho = dict(x=float(p[0]), y=float(p[1]), t=float(p[2]), type=int(p[3]))
                if ho["type"] & 2 and len(p) > 7:
                    cp = p[5].split("|")
                    ho["curve"] = cp[0]
                    ho["ctrl"] = [tuple(float(c) for c in pt.split(":")) for pt in cp[1:] if ":" in pt]
                    ho["slides"] = max(1, int(p[6]))
                    ho["length"] = float(p[7])
                elif ho["type"] & 2:
                    continue
                elif ho["type"] & 8:
                    ho["end"] = float(p[5])
            except (ValueError, IndexError):
                continue
            m["hos"].append(ho)
    if m["ApproachRate"] is None:
        m["ApproachRate"] = m["OverallDifficulty"]
    if m["version"] < 5:                    # у совсем старых карт lazer сдвигает время на 24 мс
        m["tps"] = [(t + 24, b, u) for t, b, u in m["tps"]]
        for ho in m["hos"]:
            ho["t"] += 24
            if "end" in ho:
                ho["end"] += 24
    m["tps"].sort(key=lambda x: x[0])
    m["hos"].sort(key=lambda h: h["t"])
    return m


# ---------------------------------------------------------- пути slider ----

def _lerp(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _bezier(pts, n=48):
    out = []
    for i in range(n + 1):
        t, q = i / n, list(pts)
        while len(q) > 1:
            q = [_lerp(q[j], q[j + 1], t) for j in range(len(q) - 1)]
        out.append(q[0])
    return out


def _perfect(p0, p1, p2, n=48):
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-6:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    rad = math.hypot(ax - ux, ay - uy)
    a0, a2 = math.atan2(ay - uy, ax - ux), math.atan2(cy - uy, cx - ux)
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    sweep = a2 - a0
    if cross > 0:
        while sweep < 0:
            sweep += 2 * math.pi
    else:
        while sweep > 0:
            sweep -= 2 * math.pi
    return [(ux + rad * math.cos(a0 + sweep * i / n), uy + rad * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def _catmull(pts, n=24):
    out = []
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else (2 * p2[0] - p1[0], 2 * p2[1] - p1[1])
        for k in range(n):
            t = k / n
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in (0, 1)))
    out.append(pts[-1])
    return out


def _polyline(ho):
    pts = [(ho["x"], ho["y"])] + list(ho.get("ctrl", []))
    c = ho.get("curve", "B")
    if len(pts) < 2:
        return pts * 2
    if c == "L":
        return pts
    if c == "P" and len(pts) == 3:
        arc = _perfect(*pts)
        if arc:
            return arc
    if c == "C":
        return _catmull(pts)
    out, seg = [], [pts[0]]
    for p in pts[1:]:
        if p == seg[-1]:
            out += _bezier(seg)
            seg = [p]
        else:
            seg.append(p)
    out += _bezier(seg)
    return out


def _point_at(poly, length):
    acc = 0.0
    for a, b in zip(poly, poly[1:]):
        d = math.dist(a, b)
        if d and acc + d >= length:
            return _lerp(a, b, (length - acc) / d)
        acc += d
    a, b = poly[-2], poly[-1]
    d = math.dist(a, b) or 1.0
    return b[0] + (b[0] - a[0]) / d * (length - acc), b[1] + (b[1] - a[1]) / d * (length - acc)


def _control_at(tps, t):
    """Длина доли и SV в момент t."""
    beat, sv = None, 1.0
    for time, bl, uninherited in tps:
        if time > t and beat is not None:
            break
        if uninherited:
            beat, sv = bl, 1.0
        elif time <= t:
            sv = min(10.0, max(0.1, 100.0 / -bl)) if bl < 0 else 1.0
    return beat or 500.0, sv


# ------------------------------------------------------------------ моды ----

def mod_info(mods_json):
    """Моды lazer ([{"acronym": "DT", "settings": {...}}]) -> что они меняют для разбора."""
    try:
        mods = json.loads(mods_json) if isinstance(mods_json, str) else (mods_json or [])
    except ValueError:
        mods = []
    acr = {}
    for m in mods:
        if isinstance(m, dict) and m.get("acronym"):
            acr[m["acronym"].upper()] = m.get("settings") or {}
    rate, approx = 1.0, False
    for k in ("DT", "NC"):
        if k in acr:
            rate = float(acr[k].get("speed_change", 1.5))
    for k in ("HT", "DC"):
        if k in acr:
            rate = float(acr[k].get("speed_change", 0.75))
    if "WU" in acr or "WD" in acr:              # скорость меняется по ходу - берём среднюю
        s = acr.get("WU") if "WU" in acr else acr.get("WD")
        rate = (float(s.get("initial_rate", 1.0)) + float(s.get("final_rate", 1.5 if "WU" in acr else 0.75))) / 2
        approx = True
    if "AS" in acr:
        approx = True
    # автоигра, relax/autopilot и моды, которые двигают круги или меняют правила нажатий
    auto = bool({"AT", "CN", "RX", "AP", "TP", "RD", "MR", "MG", "RP", "TR", "WG", "DP", "FR", "BU", "SG", "AL",
                 "SI", "GR", "DF", "BR"} & set(acr))
    return dict(acronyms=sorted(acr), settings=acr, rate=rate, hr="HR" in acr, ez="EZ" in acr,
                classic="CL" in acr, da=acr.get("DA"), approx=approx, unsupported=auto)


def _difficulty(m, mi):
    cs, od, ar = m["CircleSize"], m["OverallDifficulty"], m["ApproachRate"]
    if mi["da"]:
        cs = float(mi["da"].get("circle_size", cs))
        od = float(mi["da"].get("overall_difficulty", od))
        ar = float(mi["da"].get("approach_rate", ar))
    if mi["hr"]:
        cs, od, ar = min(cs * 1.3, 10.0), min(od * 1.4, 10.0), min(ar * 1.4, 10.0)
    if mi["ez"]:
        cs, od, ar = cs * 0.5, od * 0.5, ar * 0.5
    return _f32(cs), _f32(od), _f32(ar)


def _diff_range(d, mn, mid, mx):
    if d > 5:
        return mid + (mx - mid) * (d - 5) / 5
    if d < 5:
        return mid - (mid - mn) * (5 - d) / 5
    return mid


# ------------------------------------------------------------- объекты ----

def _objects(m, flip):
    objs = []
    for ho in m["hos"]:
        x, y = ho["x"], (384 - ho["y"] if flip else ho["y"])
        o = dict(t=ho["t"], pos=(x, y))
        if ho["type"] & 1:
            o.update(kind="circle", end_t=ho["t"], end_pos=o["pos"])
        elif ho["type"] & 2:
            beat, sv = _control_at(m["tps"], ho["t"])
            vel = 100 * m["SliderMultiplier"] * sv / beat
            h2 = dict(ho, y=y, ctrl=[(cx, 384 - cy if flip else cy) for cx, cy in ho.get("ctrl", [])])
            poly = _polyline(h2)
            endp = _point_at(poly, ho["length"]) if len(poly) > 1 else o["pos"]
            dur = ho["slides"] * ho["length"] / vel if vel > 0 else 0
            o.update(kind="slider", end_t=ho["t"] + dur, end_pos=endp if ho["slides"] % 2 == 1 else o["pos"],
                     path_end=endp, sv=sv, slides=ho["slides"])
        elif ho["type"] & 8:
            o.update(kind="spinner", end_t=ho.get("end", ho["t"]), end_pos=(256, 192))
        else:
            continue
        o["stack"] = 0
        objs.append(o)
    return objs


def _stacking(objs, leniency, preempt):
    """OsuBeatmapProcessor.applyStacking (карты версии 6+)."""
    for i in range(len(objs) - 1, 0, -1):
        oi = objs[i]
        if oi["stack"] != 0 or oi["kind"] == "spinner":
            continue
        thr = preempt * leniency
        j = i
        if oi["kind"] == "circle":
            while True:
                j -= 1
                if j < 0:
                    break
                on = objs[j]
                if on["kind"] == "spinner":
                    continue
                if oi["t"] - on["end_t"] > thr:
                    break
                if on["kind"] == "slider" and math.dist(on["end_pos"], oi["pos"]) < STACK_DISTANCE:
                    off = oi["stack"] - on["stack"] + 1
                    for k in range(j + 1, i + 1):
                        if math.dist(on["end_pos"], objs[k]["pos"]) < STACK_DISTANCE:
                            objs[k]["stack"] -= off
                    break
                if math.dist(on["pos"], oi["pos"]) < STACK_DISTANCE:
                    on["stack"] = oi["stack"] + 1
                    oi = on
        elif oi["kind"] == "slider":
            while True:
                j -= 1
                if j < 0:
                    break
                on = objs[j]
                if on["kind"] == "spinner":
                    continue
                if oi["t"] - on["t"] > thr:
                    break
                if math.dist(on["end_pos"], oi["pos"]) < STACK_DISTANCE:
                    on["stack"] = oi["stack"] + 1
                    oi = on


def _stacking_old(objs, leniency, preempt):
    """applyStackingOld - карты версии до 6."""
    for i, cur in enumerate(objs):
        if cur["stack"] != 0 and cur["kind"] != "slider":
            continue
        start = cur["end_t"]
        slider_stack = 0
        pos2 = cur.get("path_end", cur["pos"]) if cur["kind"] == "slider" else cur["pos"]
        for j in range(i + 1, len(objs)):
            if objs[j]["t"] - preempt * leniency > start:
                break
            if math.dist(objs[j]["pos"], cur["pos"]) < STACK_DISTANCE:
                cur["stack"] += 1
                start = objs[j]["t"]
            elif math.dist(objs[j]["pos"], pos2) < STACK_DISTANCE:
                slider_stack += 1
                objs[j]["stack"] -= slider_stack
                start = objs[j]["t"]


# --------------------------------------------------------------- паттерны ----

def _beat_at(tps, t):
    beat = None
    for time, bl, uninherited in tps:
        if uninherited:
            if time <= t + 2 or beat is None:
                beat = bl
            else:
                break
    return beat or 500.0


def _tag(hits, tps, radius):
    """Метки паттернов для каждого круга/начала slider."""
    n = len(hits)
    gaps = [None] * n                   # (мс, доля такта, расстояние в диаметрах) до предыдущего
    for i in range(1, n):
        a, b = hits[i - 1], hits[i]
        dt = b["t"] - a["t"]
        beat = _beat_at(tps, a["t"])
        dist = math.dist(b["spos"], a["send"]) / (2 * radius)
        gaps[i] = (dt, dt / beat if beat > 0 else 0, dist)
    stream = [False] * n
    for i in range(1, n):
        g = gaps[i]
        stream[i] = g is not None and 0.10 <= g[1] <= 0.30 and 0 < g[0] <= STREAM_MAX_MS
    # цепочки: нота i в цепочке, если связана с соседом стрим-промежутком
    i = 1
    while i < n:
        if not stream[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and stream[j + 1]:
            j += 1
        start, end = i - 1, j               # ноты start..end
        length = end - start + 1
        if length >= 3:
            kind = ("burst" if length <= 8 else "stream" if length <= 16 else
                    "long_stream" if length <= 32 else "deathstream")
            for k in range(start, end + 1):
                hits[k]["tags"].add(kind)
                if length >= 17 and k >= start + int(length * 0.6):
                    hits[k]["tags"].add("stream_end")
                if k > start and gaps[k][2] >= 0.9:
                    hits[k]["tags"].add("jumpstream")
        i = j + 1
    prev_sv = None
    for i in range(n):
        h = hits[i]
        g = gaps[i]
        if h["kind"] == "slider":
            h["tags"].add("slider")
            if prev_sv is not None and abs(h["sv"] - prev_sv) / max(prev_sv, 0.1) >= 0.25:
                h["tags"].add("sv_change")
            prev_sv = h["sv"]
        if i > 0 and hits[i - 1]["kind"] == "slider":
            h["tags"].add("after_slider")
        if g is None:
            continue
        dt, snap, dist = g
        if dt >= 1500:
            h["tags"].add("after_break")
            continue
        if 0.35 <= snap <= 1.6 and dist >= 1.4:
            h["tags"].add("jump")
            if dist >= 2.5:
                h["tags"].add("big_jump")
        if snap < 2.5 and min(abs(snap - k) for k in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)) > 0.06:
            h["tags"].add("odd_snap")
        if i + 1 < n and gaps[i + 1] is not None:
            lo, hi = min(dt, gaps[i + 1][0]), max(dt, gaps[i + 1][0])
            if lo <= FAST_MS and hi <= 400 and hi >= 1.2 * lo:
                h["tags"].add("rhythm_change")


# ----------------------------------------------------------------- разбор ----

def judge(osu_text, osr_path, mods_json):
    """Решение по каждому кругу и началу slider. Возвращает (карта, объекты, служебное)."""
    m = parse_osu(osu_text)
    if int(m.get("Mode", 0)) != 0:
        raise ValueError("не osu!standard")
    rep = read_osr(osr_path)
    mi = mod_info(mods_json)
    if mi["unsupported"]:
        raise ValueError("моды автоматизации - разбирать нечего")
    cs, od, ar = _difficulty(m, mi)
    scale = (1 - 0.7 * (cs - 5) / 5) / 2 * 1.00041
    radius = 64 * scale
    preempt = _diff_range(ar, 1800, 1200, 450)
    win = {k: math.floor(_diff_range(od, *v)) - 0.5
           for k, v in (("great", (80, 50, 20)), ("ok", (140, 100, 60)), ("meh", (200, 150, 100)))}
    objs = _objects(m, mi["hr"])
    if m["version"] >= 6:
        _stacking(objs, m["StackLeniency"], preempt)
    else:
        _stacking_old(objs, m["StackLeniency"], preempt)
    hits = [o for o in objs if o["kind"] != "spinner"]
    for o in hits:
        off = o["stack"] * scale * -6.4
        o["spos"] = (o["pos"][0] + off, o["pos"][1] + off)
        o["send"] = (o["end_pos"][0] + off, o["end_pos"][1] + off)
        o["res"] = None
        o["tags"] = set()
    _tag(hits, m["tps"], radius)

    frames = rep["frames"]
    presses = []
    for a, b in zip(frames, frames[1:]):
        new = (b[3] & ~a[3]) & 3
        for bit in (1, 2):
            if new & bit:
                presses.append((b[0], b[1], b[2], bit))

    def result_for(dt):
        dt = abs(dt)
        for k in ("great", "ok", "meh"):
            if dt <= win[k]:
                return k
        return "miss" if dt <= MISS_WINDOW else None

    n, lo = len(hits), 0
    for t, x, y, bit in presses:
        while lo < n and (hits[lo]["res"] is not None or t - hits[lo]["t"] > win["meh"]):
            if hits[lo]["res"] is None:
                hits[lo]["res"] = "miss"
            lo += 1
        for idx in range(lo, n):
            o = hits[idx]
            if o["t"] - preempt > t:
                break
            if o["res"] is not None or math.dist((x, y), o["spos"]) > radius:
                continue
            res = result_for(t - o["t"])
            if res is None:
                break
            k = idx - 1
            while k >= 0 and hits[k]["t"] >= o["t"]:
                k -= 1
            prev = hits[k] if k >= 0 else None
            if prev is not None and prev["res"] is None and (mi["classic"] or t < prev["t"]):
                break                           # notelock: нажатие не засчитано
            o["res"], o["offset"], o["press"] = res, t - o["t"], (x, y)
            for p in hits[lo:idx]:
                if p["res"] is None and p["t"] < o["t"]:
                    p["res"], p["forced"] = "miss", True
            break
    for o in hits:
        if o["res"] is None:
            o["res"] = "miss"
    # причины промахов и направление ошибки прицела
    press_times = [p[0] for p in presses]
    for i, o in enumerate(hits):
        o["cause"] = 0
        if o["res"] != "miss":
            continue
        if o.get("forced"):
            o["cause"] = CAUSE_SKIP
        elif o.get("offset") is not None:
            o["cause"] = CAUSE_TIMING
        else:
            a = bisect.bisect_left(press_times, o["t"] - win["meh"])
            b = bisect.bisect_right(press_times, o["t"] + win["meh"])
            near = presses[a:b]
            if near:
                best = min(near, key=lambda p: math.dist((p[1], p[2]), o["spos"]))
                o["cause"] = CAUSE_AIM
                o["press"] = (best[1], best[2])
                o["near_offset"] = best[0] - o["t"]
            else:
                o["cause"] = CAUSE_NOCLICK
    for i, o in enumerate(hits):
        if o.get("press") is None:
            continue
        ex, ey = o["press"][0] - o["spos"][0], o["press"][1] - o["spos"][1]
        o["aim_r"] = math.hypot(ex, ey) / radius
        if i > 0:
            vx, vy = o["spos"][0] - hits[i - 1]["send"][0], o["spos"][1] - hits[i - 1]["send"][1]
            d = math.hypot(vx, vy)
            if d > radius:                          # только если к кругу нужно было лететь
                o["aim_along"] = (ex * vx + ey * vy) / d / radius
                o["aim_across"] = (ex * -vy + ey * vx) / d / radius
    info = dict(cs=cs, od=od, ar=ar, radius=radius, windows=win, rate=mi["rate"], mods=mi["acronyms"],
                mods_approx=mi["approx"], classic=mi["classic"], spinners=sum(1 for o in objs if o["kind"] == "spinner"),
                presses=len(presses), replay_md5=rep["md5"], duration=(hits[-1]["t"] - hits[0]["t"]) if hits else 0)
    return m, hits, info


def _stats(objs, rate):
    c = {k: 0 for k in RESULT_CODE}
    offs = []
    for o in objs:
        c[o["res"]] += 1
        if o["res"] != "miss" and o.get("offset") is not None:
            offs.append(o["offset"] / rate)
    n = len(objs)
    acc = (300 * c["great"] + 100 * c["ok"] + 50 * c["meh"]) / (300 * n) if n else 0
    weight = (c["miss"] + 0.5 * c["meh"] + 0.2 * c["ok"]) / n if n else 0
    return dict(n=n, great=c["great"], ok=c["ok"], meh=c["meh"], miss=c["miss"], acc=round(acc, 4),
                err=round(weight, 4), mean=round(statistics.fmean(offs), 1) if offs else None,
                ur=round(10 * statistics.pstdev(offs), 1) if len(offs) > 1 else None)


def analyze(osu_text, osr_path, mods_json, game_stats=None):
    """Полный разбор попытки для тренера (JSON)."""
    m, hits, info = judge(osu_text, osr_path, mods_json)
    rate = info["rate"]
    summary = _stats(hits, rate)
    offs = [o["offset"] / rate for o in hits if o["res"] != "miss" and o.get("offset") is not None]
    summary["early"] = round(sum(1 for v in offs if v < 0) / len(offs), 3) if offs else None
    causes = {c: sum(1 for o in hits if o["cause"] == c) for c in (CAUSE_SKIP, CAUSE_TIMING, CAUSE_AIM, CAUSE_NOCLICK)}
    summary["causes"] = {"skip": causes[CAUSE_SKIP], "timing": causes[CAUSE_TIMING],
                         "aim": causes[CAUSE_AIM], "noclick": causes[CAUSE_NOCLICK]}
    check = None
    if game_stats:
        g = game_stats if isinstance(game_stats, dict) else json.loads(game_stats)
        game = [g.get("great", 0), g.get("ok", 0), g.get("meh", 0), g.get("miss", 0)]
        ours = [summary["great"] + info["spinners"], summary["ok"], summary["meh"], summary["miss"]]
        check = dict(game=game, ours=ours, diff=sum(abs(a - b) for a, b in zip(game, ours)),
                     slider_breaks=g.get("large_tick_miss", 0), tails_missed=max(
                         0, sum(1 for o in hits if o["kind"] == "slider") - g.get("slider_tail_hit", 0)))
    tags = {}
    for tg in TAGS:
        sub = [o for o in hits if tg in o["tags"]]
        if sub:
            tags[tg] = _stats(sub, rate)
    # отрезки карты
    sections = []
    if hits:
        t0, t1 = hits[0]["t"], hits[-1]["t"]
        span = max(t1 - t0, 1)
        for k in range(10):
            a, b = t0 + span * k / 10, t0 + span * (k + 1) / 10
            sub = [o for o in hits if a <= o["t"] < b or (k == 9 and o["t"] == t1)]
            st = _stats(sub, rate) if sub else dict(n=0)
            st["from"] = round((a - t0) / rate / 1000, 1)
            sections.append(st)
    # худшие места: окна по 12 объектов с наибольшим «весом» ошибок
    weights = [(1.0 if o["res"] == "miss" else 0.5 if o["res"] == "meh" else 0.2 if o["res"] == "ok" else 0) for o in hits]
    worst, used = [], set()
    win_n = 12
    cand = []
    for i in range(0, max(1, len(hits) - win_n + 1)):
        w = sum(weights[i:i + win_n])
        if w >= 1.0:
            cand.append((w, i))
    for w, i in sorted(cand, reverse=True):
        if any(j in used for j in range(i, i + win_n)):
            continue
        seg = hits[i:i + win_n]
        tag_count = {}
        for o in seg:
            for tg in o["tags"]:
                tag_count[tg] = tag_count.get(tg, 0) + 1
        worst.append(dict(t=round(seg[0]["t"] / rate / 1000, 1), until=round(seg[-1]["t"] / rate / 1000, 1),
                          weight=round(w, 1), miss=sum(1 for o in seg if o["res"] == "miss"),
                          tags=sorted(tag_count, key=lambda k: -tag_count[k])[:3]))
        used.update(range(i, i + win_n))
        if len(worst) == 3:
            break
    # прицел: недолёт/перелёт на прыжках
    along = [o["aim_along"] for o in hits if "aim_along" in o and "jump" in o["tags"]]
    aim_miss = [o for o in hits if o["cause"] == CAUSE_AIM]
    aim = dict(
        hit_r=round(statistics.median([o["aim_r"] for o in hits if o["res"] != "miss" and "aim_r" in o]), 3)
        if any(o["res"] != "miss" and "aim_r" in o for o in hits) else None,
        jumps=len(along), jump_over=round(sum(1 for v in along if v > 0.15) / len(along), 3) if along else None,
        jump_under=round(sum(1 for v in along if v < -0.15) / len(along), 3) if along else None,
        miss_over=sum(1 for o in aim_miss if o.get("aim_along", 0) > 0.3),
        miss_under=sum(1 for o in aim_miss if o.get("aim_along", 0) < -0.3),
        miss_r=round(statistics.median([o["aim_r"] for o in aim_miss if "aim_r" in o]), 2) if aim_miss else None)
    # компактная лента объектов для графика
    tag_bit = {tg: 1 << k for k, tg in enumerate(TAGS)}
    timeline = [[round(o["t"] / rate), RESULT_CODE[o["res"]],
                 round(o["offset"] / rate, 1) if o.get("offset") is not None else None, o["cause"],
                 sum(tag_bit[tg] for tg in o["tags"]),
                 round(o["aim_along"], 2) if "aim_along" in o else None] for o in hits]
    return dict(v=ANALYSIS_VERSION, summary=summary, check=check, tags=tags, sections=sections, worst=worst,
                aim=aim, timeline=timeline, tag_names=list(TAGS),
                info=dict(cs=round(info["cs"], 2), od=round(info["od"], 2), ar=round(info["ar"], 2),
                          rate=rate, mods=info["mods"], mods_approx=info["mods_approx"],
                          great_ms=info["windows"]["great"], length=round(info["duration"] / rate / 1000, 1)))
