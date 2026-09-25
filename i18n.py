# -*- coding: utf-8 -*-
"""
Перевод сообщений. Исходные строки - русские; английские - в словаре EN, испанские - в ES.
Язык по умолчанию - английский.

Термины osu! (streams, jumps, tech, finger control, sliders, spacing, SV, BPM) во всех языках
пишутся по-английски, как их называют игроки; для остального - обычные слова языка.
"""
import threading

_state = threading.local()

LANGS = ("en", "es", "ru")
DEFAULT_LANG = "en"

EN = {
    "Задача: %s | звёзды %g-%g | карт: %d": "Task: %s | stars %g-%g | maps: %d",
    "Читаю библиотеку osu!...": "Reading the osu! library...",
    "  установлено сложностей: %d": "  installed difficulties: %d",
    "Строю профиль, карт-образцов: %d...": "Building a profile, reference maps: %d...",
    "  не удалось разобрать карту %s": "  could not parse map %s",
    "Не удалось разобрать ни одну карту-образец": "None of the reference maps could be parsed",
    "  профиль: %.0f BPM, streams %.0f%%, смен ритма %.0f%%, sliders %.0f%%":
        "  profile: %.0f BPM, streams %.0f%%, rhythm changes %.0f%%, sliders %.0f%%",
    "Собираю кандидатов...": "Collecting candidates...",
    "  подходящих установленных карт: %d": "  matching installed maps: %d",
    "  запросов к зеркалу: %d, кандидатов: %d": "  mirror requests: %d, candidates: %d",
    "Кандидатов не найдено - ослабь фильтры": "No candidates found - loosen the filters",
    "Анализирую карты: %d...": "Analysing maps: %d...",
    "  ...%d/%d": "  ...%d/%d",
    "Ничего не подошло - ослабь фильтры или понизь порог": "Nothing matched - loosen the filters or lower the threshold",
    "Отобрано карт: %d": "Selected maps: %d",
    "Скачиваю наборы карт: %d...": "Downloading beatmap sets: %d...",
    "  готово %s (%.1f МБ)": "  done %s (%.1f MB)",
    "  не скачался %s": "  failed to download %s",
    "Отправляю в osu! наборов: %d...": "Sending sets to osu!: %d...",
    "  osu! добавит их в фоне": "  osu! will import them in the background",
    "Нет в игре карт: %d, скачивание отключено": "Maps not installed: %d, downloading is disabled",
    "Резервная копия базы: %s": "Database backup: %s",
    "Коллекция \"%s\" создана: +%d, всего %d": "Collection \"%s\" created: +%d, total %d",
    "Коллекция \"%s\" дополнена: +%d, всего %d": "Collection \"%s\" updated: +%d, total %d",
    "Готово.": "Done.",
    "Ищу в турнирных пулах: %s": "Searching tournament pools: %s",
    "  проверено %d/%d карт пула, подходящих: %d": "  checked %d/%d pool maps, matching: %d",
    "  карт в выбранных слотах: %d, отобрано: %d": "  maps in selected slots: %d, selected: %d",
    "  зеркало osu.direct не отвечает - остальные карты пулов пропущены":
        "  the osu.direct mirror is not responding - skipping the rest of the pool maps",
    "Нужен навык, карты-образцы или турнирные слоты": "Pick a skill, reference maps or tournament slots",
    "Не найдена папка osu!lazer (client.realm). Укажи её в настройках.":
        "osu!lazer data folder (client.realm) not found. Set it in the settings.",
    "Не найден Node.js - он нужен для работы с базой osu!. Установи с nodejs.org.":
        "Node.js not found - it is required to access the osu! database. Install it from nodejs.org.",
    "Ошибка доступа к базе osu!: %s": "osu! database error: %s",
    "Турнирные": "Tournament",
    "Похожие": "Similar",
    "streams %.0f%% нот, %.0f BPM, самая длинная цепочка %d": "streams %.0f%% of notes, %.0f BPM, longest run %d",
    "spacing %.1f×, скорость курсора %.0f": "spacing %.1f×, cursor speed %.0f",
    "spacing в streams %.2f×, streams %.0f%%": "stream spacing %.2f×, streams %.0f%%",
    "bursts %.0f%% нот, %.0f BPM, пик %.1f нот/с": "bursts %.0f%% of notes, %.0f BPM, peak %.1f notes/s",
    "%.1f мин, streams %.0f%%, %.1f нот/с": "%.1f min, streams %.0f%%, %.1f notes/s",
    "разброс SV %.2f, необычных делений ритма %.0f%%": "SV spread %.2f, unusual snaps %.0f%%",
    "смен ритма %.0f%%, разнообразие быстрого ритма %.1f": "rhythm changes %.0f%%, fast rhythm variety %.1f",
    "AR %.1f, %.1f нот/с": "AR %.1f, %.1f notes/s",
    "CS %.1f, OD %.1f": "CS %.1f, OD %.1f",
    "sliders %.0f%%, spacing %.1f×": "sliders %.0f%%, spacing %.1f×",
    "OD %.1f, %.0f BPM, мало streams": "OD %.1f, %.0f BPM, few streams",
    "похожесть %.0f%% (отличается: %s)": "similarity %.0f%% (differs in: %s)",
    "по отметкам автора: %+.0f (похожа на «%s»)": "author's labels: %+.0f (similar to “%s”)",
    "по отметкам автора: %+.0f (порог навыка)": "author's labels: %+.0f (skill threshold)",
    "Фарм-карт пока не отмечено": "No farm maps have been marked yet",
    "osu!wiki: страниц турниров — %d": "osu!wiki: tournament pages — %d",
    "  osu!wiki: %d/%d страниц": "  osu!wiki: %d/%d pages",
    "  ошибка разбора %s: %s": "  parse error in %s: %s",
    "  osu!wiki: записей %d": "  osu!wiki: %d entries",
    "Liquipedia: загрузка (не чаще 1 запроса в 2 с, по правилам API)...":
        "Liquipedia: downloading (max 1 request per 2 s, per API terms)...",
    "  Liquipedia: турниров osu!standard: %d": "  Liquipedia: %d osu!standard tournaments",
    "  Liquipedia: %d/%d страниц": "  Liquipedia: %d/%d pages",
    "Liquipedia не отвечает (код %s)": "Liquipedia is not responding (code %s)",
    "  Liquipedia: записей %d": "  Liquipedia: %d entries",
    "Итого: карт в пулах — %d, турнирных изданий — %d": "Total: pool maps — %d, tournament editions — %d",
    "Скачиваю готовую турнирную базу из репозитория...": "Downloading the ready-made tournament database from the repository...",
    "Не удалось скачать турнирную базу - попробуй позже": "Could not download the tournament database - try again later",
    "Отменено.": "Cancelled.",
    "Ошибка: %s": "Error: %s",
    "Загружаю подборку с %s...": "Loading the selection from %s...",
    "Сайт %s не в списке разрешённых": "Site %s is not in the allowed list",
    "Подборка не найдена или устарела": "The selection was not found or has expired",
    "Подборка «%s», карт: %d": "Selection \"%s\", maps: %d",
    "  из коллекций игроков (osu!Collector): %d": "  from player collections (osu!Collector): %d",
    "  зеркало osu.direct не отвечает - остальные карты из коллекций игроков пропущены":
        "  the osu.direct mirror is not responding - skipping the rest of the maps from player collections",
    "  карт-образцов нет в коллекциях игроков osu!Collector":
        "  the reference maps are not in any osu!Collector player collection",
    "osu!Collector: подборок «%s»: %d": "osu!Collector: \"%s\" collections: %d",
    "osu!Collector: общих подборок с образцами: %d": "osu!Collector: collections shared with the reference maps: %d",
    "Популярные": "Popular",
    "Самые популярные песни среди любителей %s - по коллекциям игроков osu!Collector...":
        "The most popular songs among %s fans - by osu!Collector player collections...",
    "Самые играемые карты osu! - по данным osu.direct...": "The most played osu! maps - data from osu.direct...",
    "  просмотрено наборов: %d, песен: %d": "  sets checked: %d, songs: %d",
    "Зеркало osu.direct не отвечает - попробуй позже": "The osu.direct mirror is not responding - try again later",
    "Нет базы коллекций игроков osu!Collector: python collector.py build":
        "The osu!Collector player collection database is missing: python collector.py build",
    "игр на osu!: %s": "plays on osu!: %s",
}

ES = {
    "Задача: %s | звёзды %g-%g | карт: %d": "Tarea: %s | estrellas %g-%g | mapas: %d",
    "Читаю библиотеку osu!...": "Leyendo la biblioteca de osu!...",
    "  установлено сложностей: %d": "  dificultades instaladas: %d",
    "Строю профиль, карт-образцов: %d...": "Creando un perfil, mapas de referencia: %d...",
    "  не удалось разобрать карту %s": "  no se pudo analizar el mapa %s",
    "Не удалось разобрать ни одну карту-образец": "No se pudo analizar ningún mapa de referencia",
    "  профиль: %.0f BPM, streams %.0f%%, смен ритма %.0f%%, sliders %.0f%%":
        "  perfil: %.0f BPM, streams %.0f%%, cambios de ritmo %.0f%%, sliders %.0f%%",
    "Собираю кандидатов...": "Buscando candidatos...",
    "  подходящих установленных карт: %d": "  mapas instalados que coinciden: %d",
    "  запросов к зеркалу: %d, кандидатов: %d": "  peticiones al mirror: %d, candidatos: %d",
    "Кандидатов не найдено - ослабь фильтры": "No se encontraron candidatos: relaja los filtros",
    "Анализирую карты: %d...": "Analizando mapas: %d...",
    "  ...%d/%d": "  ...%d/%d",
    "Ничего не подошло - ослабь фильтры или понизь порог": "Nada coincide: relaja los filtros o baja el umbral",
    "Отобрано карт: %d": "Mapas seleccionados: %d",
    "Скачиваю наборы карт: %d...": "Descargando beatmapsets: %d...",
    "  готово %s (%.1f МБ)": "  listo %s (%.1f MB)",
    "  не скачался %s": "  no se pudo descargar %s",
    "Отправляю в osu! наборов: %d...": "Enviando beatmapsets a osu!: %d...",
    "  osu! добавит их в фоне": "  osu! los importará en segundo plano",
    "Нет в игре карт: %d, скачивание отключено": "Mapas no instalados: %d, la descarga está desactivada",
    "Резервная копия базы: %s": "Copia de seguridad de la base de datos: %s",
    "Коллекция \"%s\" создана: +%d, всего %d": "Colección \"%s\" creada: +%d, total %d",
    "Коллекция \"%s\" дополнена: +%d, всего %d": "Colección \"%s\" actualizada: +%d, total %d",
    "Готово.": "Listo.",
    "Ищу в турнирных пулах: %s": "Buscando en los mappools de torneos: %s",
    "  проверено %d/%d карт пула, подходящих: %d": "  revisados %d/%d mapas del pool, coinciden: %d",
    "  карт в выбранных слотах: %d, отобрано: %d": "  mapas en los slots elegidos: %d, seleccionados: %d",
    "  зеркало osu.direct не отвечает - остальные карты пулов пропущены":
        "  el mirror osu.direct no responde: se omite el resto de mapas de los pools",
    "Нужен навык, карты-образцы или турнирные слоты": "Elige una habilidad, mapas de referencia o slots de torneo",
    "Не найдена папка osu!lazer (client.realm). Укажи её в настройках.":
        "No se encontró la carpeta de datos de osu!lazer (client.realm). Indícala en los ajustes.",
    "Не найден Node.js - он нужен для работы с базой osu!. Установи с nodejs.org.":
        "No se encontró Node.js: hace falta para acceder a la base de datos de osu!. Instálalo desde nodejs.org.",
    "Ошибка доступа к базе osu!: %s": "Error de la base de datos de osu!: %s",
    "Турнирные": "Torneo",
    "Похожие": "Similares",
    "streams %.0f%% нот, %.0f BPM, самая длинная цепочка %d": "streams %.0f%% de las notas, %.0f BPM, racha más larga %d",
    "spacing %.1f×, скорость курсора %.0f": "spacing %.1f×, velocidad del cursor %.0f",
    "spacing в streams %.2f×, streams %.0f%%": "spacing en streams %.2f×, streams %.0f%%",
    "bursts %.0f%% нот, %.0f BPM, пик %.1f нот/с": "bursts %.0f%% de las notas, %.0f BPM, pico de %.1f notas/s",
    "%.1f мин, streams %.0f%%, %.1f нот/с": "%.1f min, streams %.0f%%, %.1f notas/s",
    "разброс SV %.2f, необычных делений ритма %.0f%%": "variación de SV %.2f, snaps inusuales %.0f%%",
    "смен ритма %.0f%%, разнообразие быстрого ритма %.1f": "cambios de ritmo %.0f%%, variedad del ritmo rápido %.1f",
    "AR %.1f, %.1f нот/с": "AR %.1f, %.1f notas/s",
    "CS %.1f, OD %.1f": "CS %.1f, OD %.1f",
    "sliders %.0f%%, spacing %.1f×": "sliders %.0f%%, spacing %.1f×",
    "OD %.1f, %.0f BPM, мало streams": "OD %.1f, %.0f BPM, pocos streams",
    "похожесть %.0f%% (отличается: %s)": "similitud %.0f%% (difiere en: %s)",
    "по отметкам автора: %+.0f (похожа на «%s»)": "marcas del autor: %+.0f (parecido a «%s»)",
    "по отметкам автора: %+.0f (порог навыка)": "marcas del autor: %+.0f (umbral de la habilidad)",
    "Фарм-карт пока не отмечено": "Todavía no hay mapas de farm marcados",
    "osu!wiki: страниц турниров — %d": "osu!wiki: páginas de torneos — %d",
    "  osu!wiki: %d/%d страниц": "  osu!wiki: %d/%d páginas",
    "  ошибка разбора %s: %s": "  error al analizar %s: %s",
    "  osu!wiki: записей %d": "  osu!wiki: %d entradas",
    "Liquipedia: загрузка (не чаще 1 запроса в 2 с, по правилам API)...":
        "Liquipedia: descargando (máx. 1 petición cada 2 s, según sus normas de API)...",
    "  Liquipedia: турниров osu!standard: %d": "  Liquipedia: %d torneos de osu!standard",
    "  Liquipedia: %d/%d страниц": "  Liquipedia: %d/%d páginas",
    "Liquipedia не отвечает (код %s)": "Liquipedia no responde (código %s)",
    "  Liquipedia: записей %d": "  Liquipedia: %d entradas",
    "Итого: карт в пулах — %d, турнирных изданий — %d": "Total: mapas en pools — %d, ediciones de torneos — %d",
    "Скачиваю готовую турнирную базу из репозитория...": "Descargando la base de torneos ya preparada desde el repositorio...",
    "Не удалось скачать турнирную базу - попробуй позже": "No se pudo descargar la base de torneos: inténtalo más tarde",
    "Отменено.": "Cancelado.",
    "Ошибка: %s": "Error: %s",
    "Загружаю подборку с %s...": "Cargando la selección desde %s...",
    "Сайт %s не в списке разрешённых": "El sitio %s no está en la lista de permitidos",
    "Подборка не найдена или устарела": "La selección no existe o ha caducado",
    "Подборка «%s», карт: %d": "Selección \"%s\", mapas: %d",
    "  из коллекций игроков (osu!Collector): %d": "  de colecciones de jugadores (osu!Collector): %d",
    "  зеркало osu.direct не отвечает - остальные карты из коллекций игроков пропущены":
        "  el mirror osu.direct no responde: se omite el resto de mapas de las colecciones de jugadores",
    "  карт-образцов нет в коллекциях игроков osu!Collector":
        "  los mapas de referencia no están en ninguna colección de jugadores de osu!Collector",
    "osu!Collector: подборок «%s»: %d": "osu!Collector: colecciones «%s»: %d",
    "osu!Collector: общих подборок с образцами: %d": "osu!Collector: colecciones compartidas con los mapas de referencia: %d",
    "Популярные": "Populares",
    "Самые популярные песни среди любителей %s - по коллекциям игроков osu!Collector...":
        "Las canciones más populares entre los fans de %s, según las colecciones de jugadores de osu!Collector...",
    "Самые играемые карты osu! - по данным osu.direct...": "Los mapas de osu! más jugados, según osu.direct...",
    "  просмотрено наборов: %d, песен: %d": "  beatmapsets revisados: %d, canciones: %d",
    "Зеркало osu.direct не отвечает - попробуй позже": "El mirror osu.direct no responde: inténtalo más tarde",
    "Нет базы коллекций игроков osu!Collector: python collector.py build":
        "Falta la base de colecciones de jugadores de osu!Collector: python collector.py build",
    "игр на osu!: %s": "partidas en osu!: %s",
}

TRANSLATIONS = {"en": EN, "es": ES}


def normalize(lang):
    lang = str(lang or "").lower()[:2]
    return lang if lang in LANGS else DEFAULT_LANG


def set_lang(lang):
    _state.lang = normalize(lang)


def get_lang():
    return getattr(_state, "lang", DEFAULT_LANG)


def pick(lang, ru, en, es):
    """Выбор одной из трёх готовых строк по языку (для коротких подписей вне словарей)."""
    return {"ru": ru, "es": es}.get(normalize(lang), en)


def number(n):
    """Число с разделителями разрядов текущего языка: 1,234,567 / 1 234 567 / 1.234.567."""
    s = format(int(n), ",")
    return {"ru": s.replace(",", " "), "es": s.replace(",", ".")}.get(get_lang(), s)


def _(text, *args):
    """Переводит строку-шаблон на текущий язык потока и подставляет аргументы."""
    table = TRANSLATIONS.get(get_lang())
    if table is not None:
        text = table.get(text) or EN.get(text, text)
    return text % args if args else text
