# -*- coding: utf-8 -*-
"""
Зачёт ступени лестницы по одной попытке - разбор того, что случилось, а не «точность от 96% и не больше 2 промахов».

Два числа честны для коротких прыжковых карт, а на длинных streams и тех-картах - нет: там точность естественно ниже,
промахов на длину больше, а одиночный срыв посреди чистой игры считался так же, как развал на самом паттерне ступени.
Теперь попытка разбирается по повтору (лента нот replay.analyze с метками паттернов).

Промахи рядом друг с другом - один эпизод. Эпизод - это срыв (промах в чистом месте: случайность, а не навык) или
«не справился» (три промаха рядом, или два, или один, но вокруг много 100 и 50). Дальше ступень спрашивает о четырёх вещах.

1. Навык ступени. У каждой лестницы свои ноты: streams у Streams, прыжки у Jumps, смены ритма у Finger control и т.д.
   «Не справился» на них - ступень не сдана (на длинной карте прощается одно такое место на каждые 700 нот). Если на
   нотах навыка одиночных промахов больше, чем бывает от случайных срывов, - навык пока нестабилен, это тоже не сдано.
   Точность на нотах навыка - не ниже нормы для них.
2. Срывы. Их допускается по одному на 150 нот, но не меньше двух: так короткая фарм-карта судится как раньше.
3. Другие паттерны. «Не справился» на них - про другой навык, ступень это не валит. Но если таких мест много (больше
   одного на 350 нот), карта в целом не сыграна.
4. Точность. Считается по окнам OD 8, чтобы OD карты не решала (у лестницы Accuracy - по окнам самой карты), а норма
   складывается из состава нот: на streams и тех-ритмах точность естественно ниже, чем на прыжках.

Нормы и допуски сняты с попыток автора (134 разобранных повтора, сентябрь 2026): норма точности каждого вида нот - на
2-3 пункта ниже медианы его лучшей четверти попыток; промахов в его хороших попытках - от 0.6 до 1.3% нот.
"""
import collections
import math
import statistics

VERSION = 1
REF_WINDOWS = (31.5, 75.5, 119.5)   # окна 300/100/50 при OD 8, мс - как считает lazer (floor(окно) - 0.5)
W = (0.0, 0.2, 0.5, 1.0)            # вес ошибки: 300, 100, 50, промах - как в разборе повтора
LAPSE_EVERY, LAPSE_MIN = 150, 2     # срывов допускается один на столько нот, но не меньше двух
FAILS_EVERY = 700                   # «не справился» на нотах навыка прощается одно на столько нот
OTHER_EVERY = 350                   # «не справился» на других паттернах - одно на столько нот, но хотя бы одно
EP_OBJ, EP_MS = 8, 2500             # промахи ближе друг к другу - один эпизод
NEAR = 10                           # окрестность эпизода: столько нот до и после
# «грязно вокруг» (100 и 50 в окрестности) - во столько раз и на столько больше обычного для этой попытки:
# одному промаху нужно много грязи вокруг, двум подряд - поменьше, три и больше - «не справился» сразу
DIRTY = {1: (3.0, 0.08), 2: (2.0, 0.04)}
MESS_GAP = 0.08                     # вся карта хуже своей нормы на столько - «карта в целом не сыграна»
FOCUS_MIN = 30                      # нот навыка меньше - точность по ним не судится (мало данных)
SOFT_ACC = 0.02                     # каждая ступень «мягче порог»: норма точности ниже на столько,
P_UNSTABLE = (0.05, 0.01, 0.002, 0.0005)    # ...допусков на один больше, «нестабильно» - только уж совсем

STREAMS = {"burst", "stream", "long_stream", "deathstream"}
TECH = {"odd_snap", "rhythm_change", "sv_change"}
# виды нот: как назвать, лестница про них и норма точности (окна OD 8, промах - ноль)
KINDS = {
    "after_break": dict(name="первые ноты после паузы", on="на нотах после паузы", with_="с нотами после паузы",
                        norm=0.95),
    "jumpstream": dict(name="jumpstreams", on="на jumpstreams", with_="с jumpstreams", ladder="jumpstream", norm=0.88),
    "long_stream": dict(name="длинные streams", on="на длинных streams", with_="с длинными streams", ladder="stamina",
                        norm=0.88),
    "stream": dict(name="streams", on="на streams", with_="со streams", ladder="streams", norm=0.915),
    "jump": dict(name="прыжки", on="на прыжках", with_="с прыжками", ladder="jumps", norm=0.955),
    "tech": dict(name="тех-ритмы", on="на тех-ритмах", with_="с тех-ритмами", ladder="tech", norm=0.885),
    "slider": dict(name="sliders", on="на sliders", with_="со sliders", ladder="flow", norm=0.945),
    "plain": dict(name="простые ноты", on="на простых нотах", with_="с простыми нотами", norm=0.955),
}
# ноты навыка лестницы: метки паттернов из разбора повтора (без меток - вся карта); gap - промежуток до ноты, мс
FOCUS = {
    "streams": dict(tags=STREAMS, name="streams", on="на streams", with_="со streams"),
    "dt": dict(tags=STREAMS, name="streams с DT", on="на streams с DT", with_="со streams с DT"),
    "stamina": dict(tags=STREAMS, name="streams", on="на streams", with_="со streams", endurance=True),
    "bursts": dict(tags={"burst"}, name="bursts", on="на bursts", with_="с bursts"),
    "speed": dict(tags=STREAMS, without={"jumpstream"}, gap=(0, 130), name="быстрые ноты без прыжков",
                  on="на быстрых нотах", with_="с быстрыми нотами"),
    "alt": dict(tags={"jump", "jumpstream"}, gap=(95, 140), name="alt (быстрые ноты с прыжком)", on="на alt",
                with_="с alt"),
    "jumps": dict(tags={"jump", "big_jump"}, name="прыжки", on="на прыжках", with_="с прыжками"),
    "jumpstream": dict(tags={"jumpstream"}, name="jumpstreams", on="на jumpstreams", with_="с jumpstreams"),
    "fingercontrol": dict(tags={"rhythm_change"}, name="смены ритма", on="на сменах ритма", with_="со сменами ритма"),
    "tech": dict(tags=TECH, name="тех-ритмы", on="на тех-ритмах", with_="с тех-ритмами"),
    "flow": dict(tags={"slider", "after_slider"}, name="sliders", on="на sliders", with_="со sliders", sliderbreaks=True),
    "reading": dict(),
    "precision": dict(),
    "accuracy": dict(game_windows=True, fails_other=True),     # развал на прыжках - не про точность
    "hr": dict(),
    "hd": dict(),
}
WHOLE = dict(name="вся карта", on="на карте", with_="с картой")
# почему промах (replay.CAUSE_*): 1 - нажатие засчиталось следующей ноте (сбился в потоке - рано или поздно, скажет
# сдвиг нажатий вокруг), 2 - попал в круг, но вне окна, 3 - вовремя, но мимо круга, 4 - не нажал
CAUSE = {1: "сбился в потоке — нажатие ушло на следующую ноту", 2: "нажал слишком рано или поздно", 3: "мимо круга",
         4: "не нажал"}


def _kind(tags):
    if "after_break" in tags:
        return "after_break"
    if "jumpstream" in tags:
        return "jumpstream"
    if tags & {"long_stream", "deathstream"}:
        return "long_stream"
    if tags & STREAMS:
        return "stream"
    if tags & {"jump", "big_jump"}:
        return "jump"
    if tags & TECH:
        return "tech"
    if tags & {"slider", "after_slider"}:
        return "slider"
    return "plain"


def _in_focus(spec, o):
    if "tags" not in spec:
        return True
    if not (o["tags"] & spec["tags"]) or o["tags"] & spec.get("without", set()):
        return False
    lo, hi = spec.get("gap", (None, None))
    return lo is None or (o["gap"] is not None and lo <= o["gap"] <= hi)


def _acc(objs, game):
    """Точность как в osu! (300 - 1, 100 - 1/3, 50 - 1/6, промах - 0): по окнам самой карты или OD 8."""
    if not objs:
        return None
    v = 0.0
    for o in objs:
        if o["res"] == 3:
            continue
        if game or o["off"] is None:
            v += (1.0, 1 / 3, 1 / 6)[o["res"]]
        else:
            d = abs(o["off"])
            v += 1.0 if d <= REF_WINDOWS[0] else 1 / 3 if d <= REF_WINDOWS[1] else 1 / 6 if d <= REF_WINDOWS[2] else 0.0
    return v / len(objs)


def _norm(objs):
    return statistics.fmean(KINDS[o["kind"]]["norm"] for o in objs) if objs else None


def _poisson_tail(k, lam):
    """P(X >= k) для X ~ Пуассон(lam): насколько вероятно столько промахов от одних случайных срывов."""
    if k <= 0:
        return 1.0
    term = total = math.exp(-lam)
    for i in range(1, k):
        term *= lam / i
        total += term
    return max(0.0, 1.0 - total)


def _plural(n, one, few, many):
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def _mmss(ms):
    s = max(0, int(round(ms / 1000)))
    return "%d:%02d" % (s // 60, s % 60)


def _pct(x):
    return "%.1f%%" % (x * 100)


def _objects(a):
    names = a["tag_names"]
    objs, prev = [], None
    for e in a["timeline"]:
        tags = {names[k] for k in range(len(names)) if e[4] >> k & 1}
        objs.append(dict(t=e[0], res=e[1], off=e[2], cause=e[3], tags=tags, gap=None if prev is None else e[0] - prev,
                         kind=_kind(tags)))
        prev = e[0]
    return objs


def _pieces(objs):
    """Серии нот навыка: подряд идущие ноты навыка без паузы и без резкого замедления ритма."""
    out, cur, fast = [], [], 1e9
    for i, o in enumerate(objs):
        if not o["focus"]:
            if cur:
                out.append(cur)
            cur = []
            continue
        g = o["gap"]
        if cur and g is not None and g <= 600 and (len(cur) < 2 or g <= 1.5 * fast):
            cur.append(i)
            fast = g if len(cur) == 2 else min(fast, g)
        else:
            if cur:
                out.append(cur)
            cur, fast = [i], 1e9
    if cur:
        out.append(cur)
    return out


def _episodes(objs):
    """Эпизоды промахов с окрестностью: сколько грязи вокруг, сколько чистых нот было до, куда съехали нажатия."""
    groups = []
    for i, o in enumerate(objs):
        if o["res"] != 3:
            continue
        if groups and i - groups[-1][-1] <= EP_OBJ and o["t"] - objs[groups[-1][-1]]["t"] <= EP_MS:
            groups[-1].append(i)
        else:
            groups.append([i])
    hits = [W[o["res"]] for o in objs if o["res"] != 3]
    base = statistics.fmean(hits) if hits else 0.0
    eps = []
    for g in groups:
        idx = set(g)
        near = [j for j in range(max(0, g[0] - NEAR), min(len(objs), g[-1] + NEAR + 1)) if j not in idx]
        local = statistics.fmean(W[objs[j]["res"]] for j in near) if near else 0.0
        run = 0
        for j in range(g[0] - 1, -1, -1):
            if objs[j]["res"] != 0:
                break
            run += 1
        offs = [objs[j]["off"] for j in near if objs[j]["res"] != 3 and objs[j]["off"] is not None]
        dirty = DIRTY.get(len(g))
        eps.append(dict(i=g, t=objs[g[0]]["t"], t_end=objs[g[-1]]["t"], n=len(g), run=run,
                        dirty=not dirty or local >= max(dirty[0] * base, base + dirty[1]),
                        focus=2 * sum(1 for j in g if objs[j]["focus"]) >= len(g),
                        kind=collections.Counter(objs[j]["kind"] for j in g).most_common(1)[0][0],
                        cause=collections.Counter(objs[j]["cause"] for j in g).most_common(1)[0][0],
                        drift=statistics.fmean(offs) if len(offs) >= 4 else None))
    return eps


def judge(a, key, soft=0, sliderbreaks=0):
    """Зачёт попытки для лестницы key по разбору повтора a. soft - сколько раз ступень смягчали («мягче порог»),
    sliderbreaks - slider breaks попытки (где они, повтор не показывает; у лестницы sliders это срывы навыка).
    -> dict: ok, level (clean / pass / fail), reason, title, lines (объяснение), focus, lapses, other, acc, episodes."""
    spec = dict(FOCUS.get(key, {}))
    soft = max(0, min(3, int(soft or 0)))
    objs = _objects(a)
    for o in objs:
        o["focus"] = _in_focus(spec, o)
    n = len(objs)
    focus = [o for o in objs if o["focus"]]
    whole = "tags" not in spec or not focus         # у лестницы нет своих нот - или в повторе их не нашлось
    if whole:
        if "tags" in spec:
            spec["missing"] = spec["name"]
        spec.update(WHOLE)
        for o in objs:
            o["focus"] = True
        focus = objs
    game = bool(spec.get("game_windows"))
    eps = _episodes(objs)

    # срывы, которых слишком много для случайности: на нотах навыка и на каждом другом виде нот
    def unstable(mine, n_mine, rest, n_rest):
        if mine < 3 or n_mine < 20:
            return False
        rate = max(rest / n_rest if n_rest >= 50 else 0.0, 1.0 / LAPSE_EVERY)
        return _poisson_tail(mine, rate * n_mine) < P_UNSTABLE[soft]
    lapses = [e for e in eps if not e["dirty"]]
    f_lapses = sum(1 for e in lapses if e["focus"])
    sb = sliderbreaks if spec.get("sliderbreaks") else 0
    focus_unstable = not whole and unstable(f_lapses + sb, len(focus), len(lapses) - f_lapses, n - len(focus))
    kind_n = collections.Counter(o["kind"] for o in objs if not o["focus"])
    kind_l = collections.Counter(e["kind"] for e in lapses if not e["focus"])
    other_unstable = {k for k, c in kind_l.items() if k != "after_break" and
                      unstable(c, kind_n[k], len(lapses) - c, n - kind_n[k])}
    for e in eps:
        if e["dirty"]:          # не справился: на нотах навыка - против ступени, на других - про другой навык
            e["type"] = "fail" if e["focus"] and not spec.get("fails_other") else "other"
        else:                   # срыв; слишком частые срывы на нотах навыка - уже нестабильный навык
            e["type"] = "unstable" if e["focus"] and focus_unstable else "lapse"
            e["pattern"] = not e["focus"] and e["kind"] in other_unstable

    # сравнивается то, что видно на странице: точность до десятой доли процента
    f_acc, f_norm = round(_acc(focus, game), 3), round(_norm(focus) - soft * SOFT_ACC, 3)
    all_acc, all_norm = round(_acc(objs, game), 3), round(_norm(objs) - soft * SOFT_ACC, 3)
    pieces = None if whole else _pieces(objs)
    broken = sum(1 for p in pieces if any(objs[i]["res"] == 3 for i in p)) if pieces else 0
    fails = [e for e in eps if e["type"] == "fail"]
    fails_allowed = n // FAILS_EVERY + soft
    lap = [e for e in eps if e["type"] == "lapse"]
    lapses_allowed = max(LAPSE_MIN, round(n / LAPSE_EVERY)) + soft
    other = [e for e in eps if e["type"] == "other"]
    other_allowed = max(1, n // OTHER_EVERY) + soft
    endurance = _endurance(objs, focus) if spec.get("endurance") else None

    checks = []                                     # (что не так, заголовок) по важности
    if len(fails) > fails_allowed:
        checks.append(("focus", "не справился " + spec["with_"]))
    elif focus_unstable:
        checks.append(("unstable", spec["name"] + " — нестабильно"))
    if endurance and endurance["drop"]:
        checks.append(("endurance", "не хватило выносливости"))
    if not whole and len(focus) >= FOCUS_MIN and f_acc < f_norm:
        checks.append(("focus_acc", "точность " + spec["on"]))
    if whole and all_acc < all_norm:
        checks.append(("acc", "точность"))
    if len(lap) > lapses_allowed:
        checks.append(("lapses", "много срывов"))
    if len(other) > other_allowed:
        kind = collections.Counter(e["kind"] for e in other).most_common(1)[0][0]
        checks.append(("other", "не справился " + KINDS[kind]["with_"]))
    if not whole and all_acc < all_norm - MESS_GAP:
        checks.append(("mess", "карта в целом не сыграна"))
    ok = not checks
    misses = sum(1 for o in objs if o["res"] == 3)
    level = "fail" if not ok else "clean" if not misses and not sliderbreaks else "pass"
    out = dict(v=VERSION, ok=ok, level=level, reason=checks[0][0] if checks else None,
               title=checks[0][1] if checks else "чисто" if level == "clean" else "сдано",
               fails=[c[0] for c in checks], soft=soft, objects=n, misses=misses, sliderbreaks=sliderbreaks,
               focus=dict(name=spec["name"], whole=whole, notes=len(focus), pieces=len(pieces) if pieces else None,
                          broken=broken, fails=len(fails), fails_allowed=fails_allowed, unstable=focus_unstable,
                          acc=round(f_acc, 4), norm=round(f_norm, 4),
                          mean=_mean_off(focus), other_mean=_mean_off([o for o in objs if not o["focus"]])),
               lapses=dict(n=len(lap), allowed=lapses_allowed),
               other=dict(n=len(other), allowed=other_allowed,
                          kinds=[dict(kind=k, name=KINDS[k]["name"], ladder=KINDS[k].get("ladder"), n=c)
                                 for k, c in collections.Counter(e["kind"] for e in other).most_common()]),
               acc=dict(game=round(_acc(objs, True), 4), norm8=round(_acc(objs, False), 4), norm=round(all_norm, 4),
                        by_game=game),
               endurance=endurance,
               episodes=[dict(t=round(e["t"]), until=round(e["t_end"]), n=e["n"], type=e["type"], kind=e["kind"],
                              cause=e["cause"]) for e in eps])
    out["lines"] = _lines(out, spec, eps, other_unstable)
    return out


def _mean_off(objs):
    offs = [o["off"] for o in objs if o["res"] != 3 and o["off"] is not None]
    return round(statistics.fmean(offs), 1) if len(offs) >= 20 else None


def _endurance(objs, focus):
    """Хватает ли выносливости: ошибки на нотах навыка в последней трети карты против первых двух третей."""
    if len(focus) < 60:
        return None
    t0, t1 = objs[0]["t"], objs[-1]["t"]
    cut = t0 + (t1 - t0) * 2 / 3
    early = [W[o["res"]] for o in focus if o["t"] < cut]
    late = [W[o["res"]] for o in focus if o["t"] >= cut]
    if len(early) < 30 or len(late) < 20:
        return None
    e, l_ = statistics.fmean(early), statistics.fmean(late)
    return dict(early=round(e, 4), late=round(l_, 4), ratio=round(l_ / e, 2) if e > 0 else None,
                drop=l_ >= 1.8 * e and l_ - e >= 0.03)


def _ep_text(e):
    """Одна строка про эпизод: когда, сколько промахов, почему."""
    span = (e["t_end"] - e["t"]) / 1000
    when = _mmss(e["t"]) + ("–" + _mmss(e["t_end"]) if span >= 1.5 else "")
    if e["n"] == 1:
        what = "промах"
    else:
        what = "%d %s %s" % (e["n"], _plural(e["n"], "промах", "промаха", "промахов"),
                             "подряд" if span < 1.5 else "за %.0f с" % max(2, span))
    extra = []
    if CAUSE.get(e["cause"]):
        extra.append(CAUSE[e["cause"]])
    if e["dirty"] and e["n"] < 3:
        extra.append("вокруг много 100 и 50")
    if e["drift"] is not None and abs(e["drift"]) >= 15:
        extra.append("вокруг нажимал на %.0f мс %s" % (abs(e["drift"]), "раньше" if e["drift"] < 0 else "позже"))
    if not e["dirty"] and e["run"] >= 20:
        extra.append("после %d %s" % (e["run"], _plural(e["run"], "чистой ноты", "чистых нот", "чистых нот")))
    return "%s — %s%s" % (when, what, " (" + ", ".join(extra) + ")" if extra else "")


def _list(eps, limit=4):
    s = "; ".join(_ep_text(e) for e in eps[:limit])
    return s + ("; и ещё %d" % (len(eps) - limit) if len(eps) > limit else "")


def _lines(v, spec, eps, other_unstable):
    """Объяснение зачёта - несколько строк, от главного."""
    f, lines = v["focus"], []
    if spec.get("missing"):
        lines.append("Нот навыка (%s) в повторе не нашлось — судится вся карта." % spec["missing"])
    if not f["whole"]:
        head = "Ноты навыка — %s: %d %s" % (spec["name"], f["notes"], _plural(f["notes"], "нота", "ноты", "нот"))
        if f["pieces"]:
            head += " в %d %s, %s" % (f["pieces"], _plural(f["pieces"], "серии", "сериях", "сериях"),
                                      "все без промахов" if not f["broken"] else "с промахом — %d" % f["broken"])
        if f["notes"] >= FOCUS_MIN:
            head += "; точность на них %s при норме %s" % (_pct(f["acc"]), _pct(f["norm"]))
        lines.append(head + ".")
    fails = [e for e in eps if e["type"] == "fail"]
    if fails:
        fa = f["fails_allowed"]
        lines.append("Не справился %s: %s." % (spec["with_"], _list(fails)) +
                     (" На карте такой длины прощается %d %s — %s." % (
                         fa, _plural(fa, "такое место", "таких места", "таких мест"),
                         "это в пределах" if len(fails) <= fa else "здесь больше") if fa else ""))
    sb = v["sliderbreaks"] if spec.get("sliderbreaks") else 0
    unst = [e for e in eps if e["type"] == "unstable"]
    if unst:
        lines.append("Одиночных промахов %s — %d%s: больше, чем бывает от случайных срывов, навык пока нестабилен (%s)." % (
            spec["on"], len(unst) + sb, " вместе со slider breaks" if sb else "", _list(unst, 3)))
    elif sb:
        lines.append("Slider breaks: %d — на лестнице sliders это срывы на нотах навыка." % sb)
    lap = [e for e in eps if e["type"] == "lapse"]
    lv = v["lapses"]
    if lap:
        lines.append("Срывы — %d при допуске %d на %d нот: %s." % (lv["n"], lv["allowed"], v["objects"], _list(lap)) +
                     (" Промахи в чистых местах — случайность, а не навык." if lv["n"] <= lv["allowed"] else
                      " По отдельности это случайности, но для одной карты их слишком много — игра рваная."))
        pat = collections.Counter(e["kind"] for e in lap if e.get("pattern"))
        if pat:
            lines.append("Из них %s — больше, чем бывает от случайных срывов: это уже слабость другого навыка." % ", ".join(
                "%d %s" % (c, KINDS[k]["on"]) for k, c in pat.most_common()))
    elif not v["misses"]:
        lines.append("Промахов нет." if not v["sliderbreaks"] or sb else "Промахов нет, slider breaks: %d." % v["sliderbreaks"])
    other = [e for e in eps if e["type"] == "other"]
    if other:
        by = collections.OrderedDict()
        for e in other:
            by.setdefault(e["kind"], []).append(e)
        parts = ["%s: %s" % (KINDS[k]["name"], _list(es, 3)) for k, es in by.items()]
        ov = v["other"]
        lines.append(("Не справился, но не с навыком ступени — на зачёт не влияет: %s." if ov["n"] <= ov["allowed"] else
                      "Не справился с другими паттернами в %d местах при допуске %d на %d нот — карта в целом не сыграна: %%s."
                      % (ov["n"], ov["allowed"], v["objects"])) % "; ".join(parts))
    if f["mean"] is not None and f["other_mean"] is not None and not f["whole"] and abs(f["mean"] - f["other_mean"]) >= 8:
        d = f["mean"] - f["other_mean"]
        lines.append("%s ты %s: в среднем на %.0f мс %s, чем на остальной карте." % (
            spec["on"][0].upper() + spec["on"][1:], "спешишь" if d < 0 else "запаздываешь", abs(d),
            "раньше" if d < 0 else "позже"))
    en = v["endurance"]
    if en and en["ratio"]:
        lines.append(("К концу карты ошибок %s в %.1f раза больше, чем в начале — не хватает выносливости." if en["drop"]
                      else "Выносливость: в последней трети карты ошибок %s в %.1f раза от начала — держишь.") % (
            spec["on"], en["ratio"]))
    a = v["acc"]
    if a["by_game"]:
        lines.append("Точность %s при норме %s — по окнам самой карты: лестница Accuracy как раз про них." % (
            _pct(a["game"]), _pct(a["norm"])))
    else:
        lines.append("Точность всей карты %s; по окнам OD 8 — %s при норме %s для такого состава нот%s." % (
            _pct(a["game"]), _pct(a["norm8"]), _pct(a["norm"]),
            "" if f["whole"] or a["norm8"] >= a["norm"] - MESS_GAP else ": это намного ниже, карта в целом не сыграна"))
    return lines


def fallback(s, soft=0):
    """Без разбора повтора (повтора нет или он битый): только точность и промахи игры, допуск промахов - по длине."""
    soft = max(0, min(3, int(soft or 0)))
    st = s.get("stats") or {}
    n = sum(int(st.get(k, 0) or 0) for k in ("great", "ok", "meh", "miss")) or 300
    allowed = max(LAPSE_MIN, round(n / LAPSE_EVERY)) + soft
    norm = 0.96 - soft * SOFT_ACC
    fails = (["acc"] if s["acc"] < norm else []) + (["lapses"] if s["misses"] > allowed else [])
    ok = s["rank"] >= 0 and not fails
    return dict(v=VERSION, ok=ok, level=("clean" if not s["misses"] else "pass") if ok else "fail",
                reason=fails[0] if fails else None, fails=fails, fallback=True, soft=soft, objects=n,
                misses=s["misses"], sliderbreaks=s.get("breaks", 0),
                title="сдано" if ok else "точность" if s["acc"] < norm else "много промахов",
                lines=["Повтор не разобран — зачёт только по итогу игры: точность %s при норме %s, промахов %d при "
                       "допуске %d на %d нот." % (_pct(s["acc"]), _pct(norm), s["misses"], allowed, n)])


def rule_points(soft=0):
    """Правило зачёта одной карты по пунктам - для страницы тренера (с допусками и смягчением, если было)."""
    out = ["Навык ступени держится: на его нотах нет мест, где ты не справился, — это три промаха рядом или промах "
           "среди 100 и 50. На длинной карте прощается одно такое место на %d нот." % FAILS_EVERY,
           "Одиночных промахов на нотах навыка не больше, чем бывает от случайных срывов, и точность на них не ниже нормы.",
           "Срывы — промахи в чистых местах — прощаются: один на %d нот, но не меньше %d. Места, где не справился с другим "
           "навыком, ступень не валят, если их не больше одного на %d нот." % (LAPSE_EVERY, LAPSE_MIN, OTHER_EVERY),
           "Точность — по окнам OD 8, чтобы OD карты не решала. Норма зависит от состава нот: на streams и тех-ритмах "
           "она ниже, чем на прыжках."]
    if soft:
        out.append("Порог смягчён %d %s: норма точности ниже на %d%%, каждого допуска на %d больше." % (
            soft, _plural(soft, "раз", "раза", "раз"), round(soft * SOFT_ACC * 100), soft))
    return out


def rules(soft=0):
    """То же правило одной строкой - для журнала тренировки."""
    return " ".join(rule_points(soft))
