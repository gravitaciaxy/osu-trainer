# -*- coding: utf-8 -*-
"""Настройки и автопоиск путей osu!lazer на любом ПК (Windows / macOS / Linux)."""
import glob
import json
import os
import platform
import re
import shutil

VERSION = "1.2.1"

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON = os.path.join(TOOL_DIR, "config.json")
CACHE_DIR = os.path.join(TOOL_DIR, "cache")
DOWNLOAD_DIR = os.path.join(TOOL_DIR, "downloads")
BACKUP_DIR = os.path.join(TOOL_DIR, "backups")

# формат файла client.realm, с которым совместим встроенный realm@20.1.0
REALM_FILE_FORMAT = 24

DEFAULTS = {
    "osu_data": "",          # папка данных lazer (пусто = найти автоматически)
    "osu_exe": "",           # исполняемый файл lazer (пусто = найти автоматически)
    "contact": "",           # контакт для User-Agent Liquipedia (email / discord), по желанию
    "project_url": "",       # страница проекта (например, репозиторий на GitHub) для User-Agent
    "language": "en",
    "port": 8730,
    # сайты, которым разрешено предлагать коллекции для кнопки «Добавить в игру»
    "sites": ["https://osu.gravitacia.art"],
    "coach": False,          # личная вкладка «Тренер» (разбор своих попыток, лестницы навыков)
    # ключи своего OAuth-приложения osu! для тренера (закреплённые скоры), по желанию
    "osu_client_id": "",
    "osu_client_secret": "",
    "osu_user": "",
    # ключ, с которым тренер автора отправляет отметки навыков на сайт (сайт создаёт его сам: cache/labels/token)
    "labels_token": "",
}

# репозиторий на GitHub (подставляется в команду установки и ссылки)
REPO = "gravitaciaxy/osu-trainer"
SITE_URL = "https://osu.gravitacia.art"


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_JSON):
        try:
            with open(CONFIG_JSON, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    return cfg


def save(values):
    cfg = load()
    cfg.update({k: v for k, v in values.items() if k in DEFAULTS})
    with open(CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    return cfg


# ---------------------------------------------------------------- поиск -----

def _default_data_dirs():
    system = platform.system()
    home = os.path.expanduser("~")
    if system == "Windows":
        return [os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")), "osu")]
    if system == "Darwin":
        return [os.path.join(home, "Library", "Application Support", "osu")]
    xdg = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    return [os.path.join(xdg, "osu"), os.path.join(home, ".var", "app", "sh.ppy.osu", "data", "osu")]


def _custom_storage(base):
    """lazer позволяет перенести данные; путь лежит в storage.ini (FullPath = ...)."""
    ini = os.path.join(base, "storage.ini")
    if not os.path.exists(ini):
        return None
    try:
        with open(ini, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r"\s*FullPath\s*=\s*(.+?)\s*$", line)
                if m and os.path.isdir(m.group(1)):
                    return m.group(1)
    except OSError:
        pass
    return None


def find_osu_data():
    cfg = load()
    for cand in (os.environ.get("OSU_DATA"), cfg.get("osu_data")):
        if cand and os.path.exists(os.path.join(cand, "client.realm")):
            return cand
    for base in _default_data_dirs():
        custom = _custom_storage(base)
        for cand in (custom, base):
            if cand and os.path.exists(os.path.join(cand, "client.realm")):
                return cand
    return None


def find_osu_exe():
    cfg = load()
    for cand in (os.environ.get("OSU_EXE"), cfg.get("osu_exe")):
        if cand and os.path.exists(cand):
            return cand
    system = platform.system()
    home = os.path.expanduser("~")
    cands = []
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        cands += [os.path.join(local, "osulazer", "current", "osu!.exe")]
        cands += sorted(glob.glob(os.path.join(local, "osulazer", "app-*", "osu!.exe")), reverse=True)
    elif system == "Darwin":
        cands += ["/Applications/osu!.app/Contents/MacOS/osu!",
                  os.path.join(home, "Applications", "osu!.app", "Contents", "MacOS", "osu!")]
    else:
        cands += sorted(glob.glob(os.path.join(home, "Applications", "osu*.AppImage")))
        cands += sorted(glob.glob(os.path.join(home, "*.AppImage")))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def find_node():
    """Node.js: сначала портативный из папки программы (ставит install.ps1), потом системный."""
    exe = "node.exe" if platform.system() == "Windows" else os.path.join("bin", "node")
    for cand in (os.environ.get("OSU_TRAINER_NODE"), os.path.join(TOOL_DIR, "runtime", "node", exe)):
        if cand and os.path.exists(cand):
            return cand
    return shutil.which("node")


def realm_file_format(realm_path):
    """Версия формата файла Realm из заголовка (байты 16-23: 'T-DB', формат x2, флаги)."""
    try:
        with open(realm_path, "rb") as f:
            h = f.read(24)
    except OSError:
        return None
    if len(h) < 24 or h[16:20] != b"T-DB":
        return None
    return h[20 + (h[23] & 1)]


def user_agent(*extra):
    """Честный User-Agent: программа, версия и страница проекта, где можно связаться с автором.
    Браузером не притворяемся - сервисы должны видеть, кто к ним ходит."""
    parts = ["osu!drill", "+https://github.com/%s" % REPO] + [p for p in extra if p]
    return "osu-trainer/%s (%s)" % (VERSION, "; ".join(parts))


def liquipedia_user_agent():
    """Liquipedia требует контакт в User-Agent: страница проекта есть всегда, свой сайт и почта или
    Discord из настроек - по желанию."""
    cfg = load()
    return user_agent(cfg.get("project_url", "").strip(), cfg.get("contact", "").strip())


def status():
    """Сводка для интерфейса: что найдено, что нет."""
    data = find_osu_data()
    exe = find_osu_exe()
    realm = os.path.join(data, "client.realm") if data else None
    fmt = realm_file_format(realm) if realm else None
    return dict(
        version=VERSION,
        osu_data=data, osu_exe=exe, realm=realm,
        realm_format=fmt, realm_ok=(fmt == REALM_FILE_FORMAT),
        expected_format=REALM_FILE_FORMAT,
        system=platform.system(),
        config=load(),
    )
