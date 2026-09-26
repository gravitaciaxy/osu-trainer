# -*- coding: utf-8 -*-
"""
Сборка релиза: dist/osu-trainer.zip (+ копия с номером версии) - без кэша, бэкапов и личных настроек.

    python release.py                      собрать архив
    python release.py --repo USER/osu-trainer   подставить адрес репозитория в установщики и README
"""
import gzip
import os
import shutil
import sys
import zipfile

import config

FILES = [
    "README.md", "LICENSE", "package.json", "package-lock.json", ".gitignore",
    "setup.bat", "start.bat", "setup.sh", "start.sh", "install.ps1", "install.sh", "release.py",
    "analyze.py", "coach.py", "collector.py", "config.py", "feedback.py", "i18n.py", "labels.py", "liquipedia.py", "net.py",
    "osu_api.py", "pools.py", "replay.py", "skills.py",
    "trainer.py", "ui.py", "verdict.py", "realm_tool.js", "index.html", "coach.html",
    "deploy/README.md", "deploy/deploy.sh", "deploy/nginx-osu.conf", "deploy/osu-trainer.service",
    "deploy/server-install.sh", "deploy/telegram-setup.sh",
]
SEEDS = [("cache/pools.json", "data/pools.json.gz"), ("cache/beatmaps.json", "data/beatmaps.json.gz"),
         ("cache/collector.json", "data/collector.json.gz")]
REPO_FILES = ["config.py", "install.ps1", "install.sh", "README.md"]
EXECUTABLE = (".sh",)


def set_repo(repo):
    old = config.REPO
    for f in REPO_FILES:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace(old, repo))
    print("репозиторий: %s -> %s в %s" % (old, repo, ", ".join(REPO_FILES)))


def check_ascii(path):
    """Windows PowerShell 5.1 читает файлы без BOM в ANSI-кодировке, а BOM ломает «irm | iex»."""
    with open(path, "rb") as f:
        bad = [i for i, b in enumerate(f.read()) if b > 127]
    if bad:
        raise SystemExit("%s: не-ASCII символы (первый на байте %d) - PowerShell 5.1 не разберёт файл"
                         % (path, bad[0]))


def build():
    check_ascii("install.ps1")
    os.makedirs("data", exist_ok=True)
    for src, dst in SEEDS:
        if os.path.exists(src):
            with open(src, "rb") as f, gzip.open(dst, "wb", compresslevel=9) as out:
                shutil.copyfileobj(f, out)
            print("база: %s -> %s (%d КБ)" % (src, dst, os.path.getsize(dst) // 1024))
    missing = [f for f in FILES if not os.path.exists(f)]
    if missing:
        raise SystemExit("нет файлов: " + ", ".join(missing))
    os.makedirs("dist", exist_ok=True)
    name = "osu-trainer"
    path = os.path.join("dist", name + ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in FILES + [dst for _src, dst in SEEDS if os.path.exists(dst)]:
            info = zipfile.ZipInfo.from_file(f, arcname=name + "/" + f)
            info.external_attr = (0o100755 if f.endswith(EXECUTABLE) else 0o100644) << 16
            with open(f, "rb") as fh:
                z.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    versioned = os.path.join("dist", "%s-%s.zip" % (name, config.VERSION))
    shutil.copyfile(path, versioned)
    print("релиз: %s (%d КБ), копия: %s" % (os.path.abspath(path), os.path.getsize(path) // 1024, versioned))
    if "OWNER/" in config.REPO:
        print("внимание: адрес репозитория ещё не задан - python release.py --repo USER/osu-trainer")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(config.TOOL_DIR)
    if "--repo" in sys.argv:
        set_repo(sys.argv[sys.argv.index("--repo") + 1])
        return
    build()


if __name__ == "__main__":
    main()
