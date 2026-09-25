"""Парсер .osu и расчёт метрик скилл-сетов для osu!standard."""
import math
from collections import Counter

STREAM_MAX_MS = 150     # 1/4 медленнее ~100 BPM - уже не streams (чаще всего слоупарт с половинным BPM)
FAST_MS = 130           # быстрый промежуток между нотами: 1/4 от 115 BPM, 1/3 от 154 BPM
PAUSE_MS = 400          # промежуток длиннее - пауза, а не ритм
SNAPS = (1 / 16, 1 / 12, 1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 4, 1.0)


def _sections(text):
    sec, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sec[cur] = []
        elif cur:
            sec[cur].append(line)
    return sec


def metadata(text):
    """Раздел [Metadata] .osu: Title, Artist, Creator, Version, Tags, BeatmapSetID..."""
    meta, inside = {}, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            if inside:
                break
            inside = line == "[Metadata]"
        elif inside and ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def parse_osu(text):
    sec = _sections(text)
    meta = {}
    for key in ("General", "Metadata", "Difficulty"):
        for line in sec.get(key, []):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()

    timing = []
    for line in sec.get("TimingPoints", []):
        p = line.split(",")
        if len(p) < 2:
            continue
        try:
            t, beat = float(p[0]), float(p[1])
        except ValueError:
            continue
        uninherited = len(p) < 7 or p[6].strip() == "1"
        timing.append((t, beat, uninherited))
    timing.sort(key=lambda x: x[0])

    objs = []
    for line in sec.get("HitObjects", []):
        p = line.split(",")
        if len(p) < 4:
            continue
        try:
            x, y, t, typ = float(p[0]), float(p[1]), float(p[2]), int(p[3])
        except ValueError:
            continue
        kind = "spinner" if typ & 8 else ("slider" if typ & 2 else "circle")
        end_x, end_y, anchors = x, y, 0
        if kind == "slider" and len(p) > 5 and "|" in p[5]:
            pts = p[5].split("|")[1:]
            anchors = len(pts)
            slides = int(p[6]) if len(p) > 6 and p[6].isdigit() else 1
            if slides % 2 == 1 and pts:
                try:
                    end_x, end_y = (float(v) for v in pts[-1].split(":"))
                except ValueError:
                    pass
        objs.append(dict(x=x, y=y, t=t, kind=kind, ex=end_x, ey=end_y, anchors=anchors))
    objs.sort(key=lambda o: o["t"])
    return meta, timing, objs


def _beat_len_at(timing, t):
    cur = None
    for tt, beat, uninh in timing:
        if uninh and tt <= t + 2:
            cur = beat
    if cur is None:
        for tt, beat, uninh in timing:
            if uninh:
                return beat
    return cur or 500.0


def _sv_points(timing):
    return [-100.0 / beat for _, beat, uninh in timing if not uninh and beat < 0]


def _ar_ms(ar):
    return 1200 + 120 * (5 - ar) if ar < 5 else 1200 - 150 * (ar - 5)


def _ms_ar(ms):
    return 5 - (ms - 1200) / 120 if ms > 1200 else 5 + (1200 - ms) / 150


def apply_mods(meta, timing, objs, mods):
    """Карта такой, какой её играют с модами: скорость (DT/HT и их настройки), HR/EZ, Difficulty Adjust.
    С другой скоростью AR и OD считаются по настоящему времени появления и окнам попадания."""
    od = float(meta.get("OverallDifficulty", 8) or 8)
    cs = float(meta.get("CircleSize", 4) or 4)
    ar = float(meta.get("ApproachRate", od) or od)
    da = mods.get("da") or {}
    cs = float(da.get("circle_size", cs))
    ar = float(da.get("approach_rate", ar))
    od = float(da.get("overall_difficulty", od))
    if mods.get("hr"):
        cs, ar, od = min(cs * 1.3, 10.0), min(ar * 1.4, 10.0), min(od * 1.4, 10.0)
    if mods.get("ez"):
        cs, ar, od = cs * 0.5, ar * 0.5, od * 0.5
    rate = mods.get("rate") or 1.0
    if rate != 1.0:
        ar = _ms_ar(_ar_ms(ar) / rate)
        od = (80 - (80 - 6 * od) / rate) / 6
        objs = [dict(o, t=o["t"] / rate) for o in objs]
        timing = [(t / rate, beat / rate if uninh else beat, uninh) for t, beat, uninh in timing]
    return dict(meta, CircleSize=cs, ApproachRate=ar, OverallDifficulty=od), timing, objs


def metrics(text, mods=None):
    """Возвращает словарь объективных метрик карты. mods - dict(rate, hr, ez, da, hidden): метрики карты,
    сыгранной с этими модами."""
    meta, timing, objs = parse_osu(text)
    if meta.get("Mode", "0") not in ("0", ""):
        return None
    if mods:
        meta, timing, objs = apply_mods(meta, timing, objs, mods)
    hits = [o for o in objs if o["kind"] != "spinner"]
    if len(hits) < 30 or not timing:
        return None

    cs = float(meta.get("CircleSize", 4) or 4)
    radius = 54.4 - 4.48 * cs

    beats = Counter()
    for i, (t, beat, uninh) in enumerate(timing):
        if uninh:
            end = next((timing[j][0] for j in range(i + 1, len(timing)) if timing[j][2]),
                       hits[-1]["t"])
            beats[round(beat, 2)] += max(end - t, 1)
    main_beat = beats.most_common(1)[0][0] if beats else 500.0
    bpm = 60000.0 / main_beat if main_beat > 0 else 0

    gaps = []          # (dt, snap, norm_dist, index)
    snap_counter = Counter()
    for i in range(1, len(hits)):
        a, b = hits[i - 1], hits[i]
        dt = b["t"] - a["t"]
        if dt <= 0 or dt > 2000:
            continue
        bl = _beat_len_at(timing, a["t"])
        snap = dt / bl if bl > 0 else 0
        dist = math.hypot(b["x"] - a["ex"], b["y"] - a["ey"])
        gaps.append((dt, snap, dist / (2 * radius), i))
        snap_counter[round(snap, 3)] += 1

    if not gaps:
        return None

    # --- стримы: цепочки 1/4 (и 1/6, 1/8) на настоящей скорости ---
    def is_stream_gap(g):
        return 0.10 <= g[1] <= 0.30 and g[0] <= STREAM_MAX_MS

    runs, cur = [], []
    for g in gaps:
        if is_stream_gap(g):
            cur.append(g)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = []
    if len(cur) >= 3:
        runs.append(cur)

    stream_notes = sum(len(r) + 1 for r in runs)
    stream_ratio = stream_notes / len(hits)
    max_run = max((len(r) + 1 for r in runs), default=0)
    stream_gaps = [g for r in runs for g in r]
    # скорость основных стримов - по самым быстрым 40% стрим-промежутков. Среднее по всем врёт на
    # картах со слоупартом (стримы 240 BPM и медленная середина давали «204 BPM»), а редкие
    # короткие bursts на такую долю не влияют
    fastest = sorted(g[0] for g in stream_gaps)[:max(1, len(stream_gaps) * 2 // 5)]
    stream_bpm = 15000.0 / (sum(fastest) / len(fastest)) if stream_gaps else 0
    stream_spacing = (sum(g[2] for g in stream_gaps) / len(stream_gaps)
                      if stream_gaps else 0)
    long_runs = sum(1 for r in runs if len(r) >= 16)

    # --- bursts и alt. Bursts - короткие (3-8 нот) быстрые цепочки без больших прыжков. Alt - быстрые
    # ноты с большим spacing (частые 1/2 с прыжками на высоком BPM, как Meikaruza или Ooedo Ranvu):
    # плотность нот у них высокая, но это не bursts, и подбор speed их отсеивает ---
    burst_runs, cur = [], []
    for g in gaps + [(10 ** 9, 0, 0, 0)]:
        if g[0] <= STREAM_MAX_MS and g[2] <= 1.0:
            cur.append(g)
            continue
        if 2 <= len(cur) <= 7:
            burst_runs.append(cur)
        cur = []
    burst_ratio = sum(len(r) + 1 for r in burst_runs) / len(hits)
    burst_gaps = sorted(g[0] for r in burst_runs for g in r)
    burst_bpm = 15000.0 / burst_gaps[len(burst_gaps) // 2] if burst_gaps else 0
    alt_ratio = sum(1 for g in gaps if g[0] <= STREAM_MAX_MS and g[2] >= 1.2) / len(gaps)

    # --- аим/джампы: скорость курсора на 1/2+ ---
    aim_gaps = [g for g in gaps if 0.35 <= g[1] <= 1.6]
    if aim_gaps:
        vel = sorted(g[2] / (g[0] / 1000.0) for g in aim_gaps)   # радиусов в секунду
        aim_velocity = sum(vel[int(len(vel) * 0.3):]) / max(1, len(vel) - int(len(vel) * 0.3))
        aim_spacing = sum(g[2] for g in aim_gaps) / len(aim_gaps)
        aim_share = len(aim_gaps) / len(gaps)
    else:
        aim_velocity = aim_spacing = aim_share = 0

    # --- плотность / скорость тапа ---
    times = [o["t"] for o in hits]
    span = (times[-1] - times[0]) / 1000.0 or 1
    nps = len(hits) / span
    nps_max, j = 0, 0
    for i, t in enumerate(times):
        while times[j] < t - 3000:
            j += 1
        nps_max = max(nps_max, (i - j + 1) / 3.0)

    # --- рифм. сложность / тех ---
    total_snaps = sum(snap_counter.values())
    odd = sum(c for s, c in snap_counter.items()
              if min(abs(s - k) for k in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)) > 0.06)
    odd_ratio = odd / total_snaps
    entropy = -sum((c / total_snaps) * math.log2(c / total_snaps)
                   for c in snap_counter.values())
    svs = _sv_points(timing)
    sv_var = 0.0
    if len(svs) > 3:
        mean = sum(svs) / len(svs)
        sv_var = math.sqrt(sum((v - mean) ** 2 for v in svs) / len(svs))
    sliders = [o for o in hits if o["kind"] == "slider"]
    slider_ratio = len(sliders) / len(hits)
    anchors = (sum(o["anchors"] for o in sliders) / len(sliders)) if sliders else 0

    # --- смены ритма (finger control): соседние промежутки отличаются хотя бы в 1.2 раза (1/4 -> 1/3
    # уже смена), и быстрый из них - действительно быстрый. Чередование 1/1 и 1/2 в слоупарте
    # и паузы пальцы не нагружают, поэтому не считаются ---
    switches = 0
    for g1, g2 in zip(gaps, gaps[1:]):
        lo, hi = min(g1[0], g2[0]), max(g1[0], g2[0])
        if lo <= FAST_MS and hi <= PAUSE_MS and hi >= 1.2 * lo:
            switches += 1
    switch_ratio = switches / len(gaps)
    # разнообразие именно быстрого ритма: 1/4 вперемешку с 1/3, 1/6, 1/8
    fast = Counter(min(SNAPS, key=lambda k: abs(g[1] - k)) for g in gaps if g[0] <= FAST_MS)
    n_fast = sum(fast.values())
    tap_entropy = sum(c / n_fast * math.log2(n_fast / c) for c in fast.values()) if n_fast else 0.0

    out = dict(
        cs=cs,
        ar=float(meta.get("ApproachRate", meta.get("OverallDifficulty", 9)) or 9),
        od=float(meta.get("OverallDifficulty", 8) or 8),
        hp=float(meta.get("HPDrainRate", 5) or 5),
        bpm=round(bpm, 1),
        objects=len(hits),
        length=round(span, 1),
        nps=round(nps, 2),
        nps_max=round(nps_max, 2),
        stream_ratio=round(stream_ratio, 3),
        stream_bpm=round(stream_bpm, 1),
        stream_spacing=round(stream_spacing, 2),
        max_run=max_run,
        long_runs=long_runs,
        burst_ratio=round(burst_ratio, 3),
        burst_bpm=round(burst_bpm, 1),
        alt_ratio=round(alt_ratio, 3),
        aim_velocity=round(aim_velocity, 2),
        aim_spacing=round(aim_spacing, 2),
        aim_share=round(aim_share, 3),
        odd_ratio=round(odd_ratio, 3),
        rhythm_entropy=round(entropy, 2),
        sv_var=round(sv_var, 3),
        slider_ratio=round(slider_ratio, 3),
        slider_anchors=round(anchors, 2),
        switch_ratio=round(switch_ratio, 3),
        tap_entropy=round(tap_entropy, 2),
    )
    if mods:
        out.update(rate=mods.get("rate") or 1.0, hr=bool(mods.get("hr")), hidden=bool(mods.get("hidden")))
    return out
