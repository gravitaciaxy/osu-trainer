# -*- coding: utf-8 -*-
"""Навыки: поисковые запросы, формулы оценки карты по метрикам, описания для интерфейса."""
from i18n import _, get_lang


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def sc(v, lo, hi):
    """Нормализует значение из диапазона [lo, hi] в 0..1."""
    if hi == lo:
        return 0.0
    return clamp((v - lo) / (hi - lo))


# понятные названия метрик для пояснений «чем отличается»: (ru, en, es)
FEATURE_NAMES = {
    "bpm": ("BPM", "BPM", "BPM"), "nps": ("плотность нот", "note density", "densidad de notas"),
    "nps_max": ("пиковая плотность", "peak density", "densidad máxima"), "stream_ratio": ("доля streams", "stream share", "proporción de streams"),
    "stream_bpm": ("BPM streams", "stream BPM", "BPM de streams"), "max_run": ("длина streams", "stream length", "longitud de streams"),
    "switch_ratio": ("смены ритма", "rhythm changes", "cambios de ritmo"), "odd_ratio": ("необычные деления", "unusual snaps", "snaps inusuales"),
    "rhythm_entropy": ("разнообразие ритма", "rhythm variety", "variedad rítmica"), "sv_var": ("SV", "SV", "SV"),
    "tap_entropy": ("разнообразие быстрого ритма", "fast rhythm variety", "variedad del ritmo rápido"),
    "slider_ratio": ("доля sliders", "slider share", "proporción de sliders"), "slider_anchors": ("форма sliders", "slider shapes", "forma de los sliders"),
    "aim_velocity": ("скорость курсора", "cursor speed", "velocidad del cursor"), "aim_spacing": ("spacing", "spacing", "spacing"),
    "cs": ("CS", "CS", "CS"), "ar": ("AR", "AR", "AR"),
}


# -- функции оценки: возвращают (0..100, краткое пояснение) ------------------

def _streams(m):
    s = (45 * sc(m["stream_ratio"], 0.15, 0.60) + 25 * sc(m["stream_bpm"], 140, 240)
         + 20 * sc(m["max_run"], 8, 48) + 10 * sc(m["long_runs"], 1, 20))
    return s, _("streams %.0f%% нот, %.0f BPM, самая длинная цепочка %d",
                m["stream_ratio"] * 100, m["stream_bpm"], m["max_run"])


def _jumps(m):
    s = (50 * sc(m["aim_velocity"], 7, 22) + 25 * sc(m["aim_spacing"], 1.4, 4.0)
         + 15 * sc(m["aim_share"], 0.25, 0.65) + 10 * sc(1 - m["stream_ratio"], 0.5, 0.95))
    s -= 45 * clamp((m["stream_ratio"] - 0.35) / 0.4)
    return max(s, 0), _("spacing %.1f×, скорость курсора %.0f", m["aim_spacing"], m["aim_velocity"])


def _jumpstream(m):
    s = (35 * sc(m["stream_ratio"], 0.2, 0.55) + 30 * sc(m["stream_spacing"], 0.6, 1.6)
         + 20 * sc(m["stream_bpm"], 160, 220) + 15 * sc(m["aim_velocity"], 8, 18))
    return s, _("spacing в streams %.2f×, streams %.0f%%", m["stream_spacing"], m["stream_ratio"] * 100)


def _speed(m):
    # быстрое нажатие без прыжков: сколько нот идёт быстро и почти без spacing, как быстро и пиковая плотность.
    # Быстрые ноты с прыжками - это alt, не speed
    s = 55 * sc(m["tap_share"], 0.25, 0.50) + 25 * sc(m["tap_bpm"], 150, 220) + 20 * sc(m["nps_max"], 6, 11)
    s -= 50 * clamp((m["alt_share"] - 0.06) / 0.2)
    return max(s, 0), _("быстрых нот без прыжков %.0f%%, %.0f BPM, пик %.1f нот/с",
                        m["tap_share"] * 100, m["tap_bpm"], m["nps_max"])


def _bursts(m):
    # доля нот в bursts (3-8 нот) и их скорость; без заметной доли bursts их BPM ничего не значит.
    # Плотность alt-карт (частые 1/2 с прыжками) - не bursts
    here = sc(m["burst_ratio"], 0.03, 0.10)
    s = 50 * sc(m["burst_ratio"], 0.08, 0.35) + here * (35 * sc(m["burst_bpm"], 140, 220) + 15 * sc(m["nps_max"], 5, 10))
    s -= 60 * clamp((m["alt_ratio"] - 0.08) / 0.2)
    return max(s, 0), _("bursts %.0f%% нот, %.0f BPM, пик %.1f нот/с",
                        m["burst_ratio"] * 100, m["burst_bpm"], m["nps_max"])


def _alt(m):
    # быстрые ноты с прыжком между ними (95-140 мс): одним пальцем не успеть, надо чередовать и целиться.
    # Без заметной доли таких нот их скорость ничего не значит; длинные spaced streams - это jumpstream
    here = sc(m["alt_share"], 0.02, 0.08)
    s = (40 * sc(m["alt_share"], 0.04, 0.20)
         + here * (25 * sc(m["alt_bpm"], 215, 280) + 20 * sc(m["alt_run"], 4, 12) + 15 * sc(m["aim_spacing"], 1.4, 2.4)))
    s -= 40 * clamp((m["stream_ratio"] - 0.4) / 0.3)
    return max(s, 0), _("alt %.0f%% промежутков, %.0f BPM по 1/2, цепочки до %d нот",
                        m["alt_share"] * 100, m["alt_bpm"], m["alt_run"])


def _stamina(m):
    s = (30 * sc(m["length"], 120, 330) + 35 * sc(m["stream_ratio"], 0.25, 0.7)
         + 20 * sc(m["stream_bpm"], 150, 220) + 15 * sc(m["nps"], 4.5, 8.5))
    return s, _("%.1f мин, streams %.0f%%, %.1f нот/с", m["length"] / 60, m["stream_ratio"] * 100, m["nps"])


def _tech(m):
    s = (35 * sc(m["sv_var"], 0.08, 0.45) + 30 * sc(m["odd_ratio"], 0.02, 0.15)
         + 20 * sc(m["rhythm_entropy"], 1.8, 3.4) + 15 * sc(m["slider_anchors"], 1.5, 4.5))
    return s, _("разброс SV %.2f, необычных делений ритма %.0f%%", m["sv_var"], m["odd_ratio"] * 100)


def _fingercontrol(m):
    s = (45 * sc(m["switch_ratio"], 0.12, 0.35) + 25 * sc(m["tap_entropy"], 0.05, 0.8)
         + 15 * sc(m["odd_ratio"], 0.01, 0.10) + 15 * sc(m["nps_max"], 5, 10))
    return s, _("смен ритма %.0f%%, разнообразие быстрого ритма %.1f", m["switch_ratio"] * 100, m["tap_entropy"])


def _reading(m):
    s = 55 * sc(9.4 - m["ar"], 0.2, 1.8) + 25 * sc(m["nps"], 4, 8) + 20 * sc(m["rhythm_entropy"], 1.8, 3.2)
    why = _("AR %.1f, %.1f нот/с", m["ar"], m["nps"])
    if m.get("hidden"):     # сыграно с HD (разбор тренера): круга подхода нет, ноты гаснут до удара - и чем ниже AR, тем труднее
        s = min(100.0, s + 25 + 10 * clamp((9.5 - m["ar"]) / 2))
        why += " + HD"
    return s, why


def _precision(m):
    s = 65 * sc(m["cs"], 4.0, 5.6) + 20 * sc(m["aim_velocity"], 8, 18) + 15 * sc(m["od"], 8, 10)
    return s, _("CS %.1f, OD %.1f", m["cs"], m["od"])


def _flow(m):
    s = (45 * sc(m["slider_ratio"], 0.25, 0.6) + 25 * sc(m["sv_var"], 0.05, 0.3)
         + 30 * sc(1 - min(m["aim_spacing"] / 3.0, 1), 0.3, 0.8))
    return s, _("sliders %.0f%%, spacing %.1f×", m["slider_ratio"] * 100, m["aim_spacing"])


def _accuracy(m):
    s = 40 * sc(m["od"], 8.0, 10.0) + 30 * sc(1 - m["stream_ratio"], 0.5, 0.95) + 30 * sc(m["bpm"], 150, 210)
    return s, _("OD %.1f, %.0f BPM, мало streams", m["od"], m["bpm"])


# Названия навыков - как их называют игроки (по-английски) во всех языках интерфейса.
SKILLS = {
    "streams": dict(
        title="Streams", title_en="Streams",
        desc="Длинные цепочки нот на 1/4. Учитывается доля нот в streams, их BPM и длина самых длинных цепочек.",
        desc_en="Long 1/4 chains. Scored by the share of notes in streams, stream BPM and the longest runs.",
        desc_es="Cadenas largas de notas a 1/4. Se valora la proporción de notas en streams, su BPM y la longitud de las rachas más largas.",
        aliases=["stream", "стрим", "стримы"],
        queries=["stream", "streams", "deathstream", "stamina stream", "high bpm stream"],
        score=_streams, min_score=55),
    "jumps": dict(
        title="Jumps / aim", title_en="Jumps / aim",
        desc="Большой spacing на 1/2 и 1/1: как далеко и как быстро нужно вести курсор.",
        desc_en="Wide spacing on 1/2 and 1/1: how far and how fast the cursor has to travel.",
        desc_es="Spacing amplio a 1/2 y 1/1: cuánto y qué tan rápido hay que mover el cursor.",
        aliases=["jump", "aim", "аим", "джамп", "джампы", "прыжки"],
        queries=["jump", "jumps", "spaced", "aim", "jump aim", "wide aim"],
        score=_jumps, min_score=55),
    "jumpstream": dict(
        title="Jumpstreams", title_en="Jumpstreams",
        desc="Streams с большим расстоянием между нотами — aim прямо внутри streams.",
        desc_en="Streams with large spacing between notes — aiming while streaming.",
        desc_es="Streams con mucha distancia entre notas: aim dentro de los propios streams.",
        aliases=["js", "джампстрим"],
        queries=["jumpstream", "jump stream", "stream jump", "alt jump"],
        score=_jumpstream, min_score=55),
    "speed": dict(
        title="Speed", title_en="Speed",
        desc="Быстрое нажатие без прыжков: много быстрых нот подряд с небольшим spacing (streams, bursts, трели) "
             "и высокая пиковая плотность. Быстрые ноты с прыжками — это Alt.",
        desc_en="Fast tapping without jumps: lots of fast notes with small spacing (streams, bursts, trills) "
                "and high peak density. Fast notes with jumps are Alt.",
        desc_es="Pulsación rápida sin saltos: muchas notas rápidas seguidas con poco spacing (streams, bursts, "
                "trinos) y alta densidad máxima. Las notas rápidas con saltos son Alt.",
        aliases=["скорость", "спид", "tapping", "singletap"],
        queries=["speed", "fast", "tapping", "high bpm", "spam"],
        score=_speed, min_score=55),
    "bursts": dict(
        title="Bursts", title_en="Bursts",
        desc="Короткие быстрые цепочки (3–8 нот) на высоком BPM. Бывают не только в speed-картах, но и в "
             "прыжковых и тех-картах. Alt-карты — частые ноты с прыжками — сюда не попадают.",
        desc_en="Short fast chains (3–8 notes) at high BPM. They show up not only in speed maps but also in jump "
                "and tech maps. Alt maps — fast notes with jumps — don't count.",
        desc_es="Cadenas cortas y rápidas (3–8 notas) a BPM alto. No solo aparecen en mapas de speed, también en "
                "mapas de saltos y de tech. Los mapas de alt —notas rápidas con saltos— no cuentan.",
        aliases=["burst", "бёрсты", "берсты", "бурсты"],
        queries=["burst", "bursts", "burst jumps"],
        score=_bursts, min_score=55),
    "alt": dict(
        title="Alt", title_en="Alt",
        desc="Быстрые ноты с прыжком между ними — 1/2 от ~215 BPM или 1/4 на медленном BPM: одним пальцем уже "
             "не успеть, надо чередовать и при этом целиться.",
        desc_en="Fast notes with a jump between them — 1/2 from ~215 BPM or 1/4 at low BPM: too fast to single-tap, "
                "so you alternate while aiming.",
        desc_es="Notas rápidas con un salto entre ellas — 1/2 desde ~215 BPM o 1/4 a BPM bajo: demasiado rápido "
                "para un solo dedo, hay que alternar y apuntar a la vez.",
        aliases=["альт", "alternate", "alt jumps"],
        queries=["alt", "alternate", "alt jumps", "high bpm jumps"],
        score=_alt, min_score=45),
    "stamina": dict(
        title="Stamina", title_en="Stamina",
        desc="Длинные карты с большой долей streams и высокой средней плотностью нот.",
        desc_en="Long maps with a high share of streams and high average density.",
        desc_es="Mapas largos con mucha proporción de streams y alta densidad media de notas.",
        aliases=["стамина", "выносливость", "marathon"],
        queries=["stamina", "marathon", "endurance stream", "long stream"],
        score=_stamina, min_score=55),
    "tech": dict(
        title="Tech", title_en="Tech",
        desc="Резкие смены скорости sliders (SV), нестандартные деления ритма (1/3, 1/6), сложные формы sliders.",
        desc_en="Slider velocity changes, unusual snaps (1/3, 1/6), complex slider shapes.",
        desc_es="Cambios bruscos de velocidad de los sliders (SV), divisiones de ritmo poco comunes (1/3, 1/6) y sliders de formas complejas.",
        aliases=["технические", "тех", "technical", "sv"],
        queries=["tech", "technical", "gimmick", "sv", "slider velocity"],
        score=_tech, min_score=50),
    "fingercontrol": dict(
        title="Finger control", title_en="Finger control",
        desc="Частые смены ритма в быстрых местах: bursts, триоли, 1/3 и 1/6 вперемешку с 1/4 — нужен точный "
             "контроль пальцев. Медленные части и паузы не считаются.",
        desc_en="Frequent rhythm changes at speed: bursts, triplets, 1/3 and 1/6 mixed with 1/4 — needs precise "
                "finger control. Slow sections and pauses don't count.",
        desc_es="Cambios de ritmo frecuentes a velocidad: bursts, tresillos, 1/3 y 1/6 mezclados con 1/4. Exige un "
                "control preciso de los dedos. Las partes lentas y las pausas no cuentan.",
        aliases=["fc", "фингер", "фингерконтроль", "finger control"],
        queries=["finger control", "fingercontrol", "rhythm complex", "polyrhythm"],
        score=_fingercontrol, min_score=45),
    "reading": dict(
        title="Reading", title_en="Reading",
        desc="Низкий AR и высокая плотность: карту трудно прочитать, а не нажать.",
        desc_en="Low AR and high density: hard to read rather than hard to hit.",
        desc_es="AR bajo y alta densidad: el mapa es difícil de leer más que de acertar.",
        aliases=["ридинг", "чтение", "low ar"],
        queries=["reading", "low ar", "overlap", "hidden practice"],
        score=_reading, min_score=45),
    "precision": dict(
        title="Precision", title_en="Precision",
        desc="Маленькие круги (высокий CS) — точность попадания курсором.",
        desc_en="Small circles (high CS) — cursor precision.",
        desc_es="Círculos pequeños (CS alto): precisión al apuntar con el cursor.",
        aliases=["точность", "прецижн", "cs"],
        queries=["precision", "small circles", "high cs"],
        score=_precision, min_score=45),
    "flow": dict(
        title="Flow / sliders", title_en="Flow / sliders",
        desc="Много sliders и плавное движение курсора при небольшом spacing.",
        desc_en="Lots of sliders and smooth movement with modest spacing.",
        desc_es="Muchos sliders y movimiento fluido del cursor con poco spacing.",
        aliases=["флоу", "слайдеры", "sliders"],
        queries=["flow", "flow aim", "slider", "sliders"],
        score=_flow, min_score=45),
    "accuracy": dict(
        title="Accuracy", title_en="Accuracy",
        desc="Высокий OD и ровный ритм без streams — тренировка точности нажатий.",
        desc_en="High OD and steady rhythm without streams — hit timing practice.",
        desc_es="OD alto y ritmo estable sin streams: práctica de precisión al pulsar.",
        aliases=["акк", "acc", "аккураси", "точность нажатий"],
        queries=["accuracy", "acc practice", "consistency"],
        score=_accuracy, min_score=45),
}


def title(cfg, lang="en"):
    return cfg["title"] if str(lang).startswith("ru") else cfg["title_en"]


def resolve(name):
    n = name.strip().lower()
    for key, cfg in SKILLS.items():
        if n == key or n in cfg["aliases"]:
            return key, cfg
    for key, cfg in SKILLS.items():
        if n in key or any(n in a for a in cfg["aliases"]):
            return key, cfg
    raise RuntimeError("Неизвестный навык: %s. Доступные: %s" % (name, ", ".join(SKILLS)))


# ---------------------------------------------- подбор «похоже на эти карты» --

# признак: (нормировочный разброс, вес)
PROFILE_FEATURES = {
    "bpm": (30.0, 0.8),
    "nps": (1.5, 1.0),
    "nps_max": (2.5, 1.2),
    "stream_ratio": (0.15, 1.5),
    "stream_bpm": (30.0, 0.8),
    "max_run": (20.0, 0.8),
    "switch_ratio": (0.12, 2.0),
    "odd_ratio": (0.04, 1.0),
    "rhythm_entropy": (0.4, 1.5),
    "tap_entropy": (0.3, 1.0),
    "sv_var": (0.25, 0.6),
    "slider_ratio": (0.10, 1.0),
    "slider_anchors": (0.5, 0.4),
    "aim_velocity": (3.5, 0.8),
    "aim_spacing": (0.4, 1.0),
    "cs": (0.4, 0.4),
    "ar": (0.4, 0.4),
}


def build_profile(metrics_list):
    """Усредняет метрики карт-образцов."""
    prof = {}
    for key in PROFILE_FEATURES:
        vals = [m[key] for m in metrics_list if key in m]
        prof[key] = sum(vals) / len(vals)
    return prof


def profile_scorer(profile):
    """Возвращает функцию оценки схожести карты с профилем (0..100)."""
    def score(m):
        dist2 = wsum = 0.0
        for key, (spread, weight) in PROFILE_FEATURES.items():
            d = (m[key] - profile[key]) / spread
            dist2 += weight * d * d
            wsum += weight
        dist = (dist2 / wsum) ** 0.5
        value = 100.0 / (1.0 + dist * dist * 0.55)
        top = sorted(((abs(m[k] - profile[k]) / s * w, k)
                      for k, (s, w) in PROFILE_FEATURES.items()), reverse=True)[:2]
        idx = {"ru": 0, "en": 1, "es": 2}.get(get_lang(), 1)
        names = ", ".join(FEATURE_NAMES[k][idx] for _v, k in top)
        return value, _("похожесть %.0f%% (отличается: %s)", value, names)
    return score
