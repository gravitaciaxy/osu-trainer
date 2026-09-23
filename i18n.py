# -*- coding: utf-8 -*-
"""
Перевод сообщений. Исходные строки - русские, английские - в словаре EN.

Термины osu! (streams, jumps, tech, finger control, sliders, spacing, SV, BPM) в русском тексте
пишутся по-английски, как их называют игроки; для остального - обычные русские слова.
"""
import threading

_state = threading.local()

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
    "пик %.1f нот/с, %.0f BPM": "peak %.1f notes/s, %.0f BPM",
    "%.1f мин, streams %.0f%%, %.1f нот/с": "%.1f min, streams %.0f%%, %.1f notes/s",
    "разброс SV %.2f, необычных делений ритма %.0f%%": "SV spread %.2f, unusual snaps %.0f%%",
    "смен ритма %.0f%%, разнообразие ритма %.1f": "rhythm changes %.0f%%, rhythm variety %.1f",
    "AR %.1f, %.1f нот/с": "AR %.1f, %.1f notes/s",
    "CS %.1f, OD %.1f": "CS %.1f, OD %.1f",
    "sliders %.0f%%, spacing %.1f×": "sliders %.0f%%, spacing %.1f×",
    "OD %.1f, %.0f BPM, мало streams": "OD %.1f, %.0f BPM, few streams",
    "похожесть %.0f%% (отличается: %s)": "similarity %.0f%% (differs in: %s)",
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
    "Отменено.": "Cancelled.",
    "Ошибка: %s": "Error: %s",
    "Загружаю подборку с %s...": "Loading the selection from %s...",
    "Сайт %s не в списке разрешённых": "Site %s is not in the allowed list",
    "Подборка не найдена или устарела": "The selection was not found or has expired",
    "Подборка «%s», карт: %d": "Selection \"%s\", maps: %d",
}


def set_lang(lang):
    _state.lang = "en" if str(lang).lower().startswith("en") else "ru"


def get_lang():
    return getattr(_state, "lang", "ru")


def _(text, *args):
    """Переводит строку-шаблон на текущий язык потока и подставляет аргументы."""
    if get_lang() == "en":
        text = EN.get(text, text)
    return text % args if args else text
