"""Парсер .osu и расчёт метрик скилл-сетов для osu!standard."""
import math
import re
from collections import Counter


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


def metrics(text):
    """Возвращает словарь объективных метрик карты."""
    meta, timing, objs = parse_osu(text)
    if meta.get("Mode", "0") not in ("0", ""):
        return None
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

    # --- стримы: цепочки 1/4 (и 1/6, 1/8) ---
    def is_stream_gap(snap):
        return 0.10 <= snap <= 0.30

    runs, cur = [], []
    for g in gaps:
        if is_stream_gap(g[1]):
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
    stream_bpm = (15000.0 / (sum(g[0] for g in stream_gaps) / len(stream_gaps))
                  if stream_gaps else 0)
    stream_spacing = (sum(g[2] for g in stream_gaps) / len(stream_gaps)
                      if stream_gaps else 0)
    long_runs = sum(1 for r in runs if len(r) >= 16)

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

    # --- смена ритма (finger control) ---
    switches = sum(1 for i in range(1, len(gaps))
                   if abs(gaps[i][1] - gaps[i - 1][1]) > 0.1)
    switch_ratio = switches / len(gaps)

    return dict(
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
        aim_velocity=round(aim_velocity, 2),
        aim_spacing=round(aim_spacing, 2),
        aim_share=round(aim_share, 3),
        odd_ratio=round(odd_ratio, 3),
        rhythm_entropy=round(entropy, 2),
        sv_var=round(sv_var, 3),
        slider_ratio=round(slider_ratio, 3),
        slider_anchors=round(anchors, 2),
        switch_ratio=round(switch_ratio, 3),
    )
