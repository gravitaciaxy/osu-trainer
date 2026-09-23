# osu!trainer

**RU** | [EN](#english)

Подбирает карты osu! под навык, который хочешь тренировать, или под слоты турнирных пулов,
скачивает их и создаёт коллекцию в **osu!lazer**.

- **Навыки** — Streams, Jumps / aim, Tech, Finger control, Reading и ещё 6. Карты выбираются не
  по тегам: каждая кандидатка скачивается и разбирается по объектам — деления ритма, spacing,
  смены ритма, SV, плотность нот.
- **«Хочу похожее»** — вставь ссылки на любимые карты, и найдутся карты с похожим профилем.
- **Турнирные пулы** — 32 000+ карт из 450+ турниров (osu!wiki + Liquipedia). Выбираешь слоты
  NM2 / HD1 / DT3 / TB…, звёзды, рейтинг участников (open, 3/4/5/6-digit), уровень турнира и годы,
  а подборка собирается из разных турниров.
- **Фильтры** — звёзды, BPM и длина двусторонними ползунками, жанр музыки, слова в тегах
  (touhou, vocaloid, speedcore…).

## Онлайн-версия

**https://osu.gravitacia.art** — подбор работает прямо на сайте. Карты можно скачать по ссылкам,
а кнопка «Добавить в игру» кладёт подборку в osu! через установленный на компьютере osu!trainer.

## Установка одной командой

**Windows** — открой PowerShell и вставь:
```powershell
irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
```
Установщик поставит программу в `%LOCALAPPDATA%\osu-trainer`, создаст ярлык «osu!trainer» и запустит
её. Если в системе нет Python или Node.js, он скачает их портативные версии в папку программы —
в систему ничего не устанавливается. Повторный запуск той же команды обновляет программу.

**macOS / Linux** (нужны `python3` 3.9+ и `node` 18+):
```sh
curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh
```

**Вручную:** `git clone https://github.com/gravitaciaxy/osu-trainer`, затем `start.bat`
(или `./start.sh`). Нужны Python 3.9+ и Node.js 18+, сторонние Python-пакеты не нужны.

Папка osu!lazer находится автоматически, в том числе если данные игры перенесены. Если не
нашлась — укажи её в «Настройках».

## Как пользоваться

1. Выбери вкладку: **Навыки** или **Турнирные пулы**. У каждого фильтра есть подсказка — значок «?».
2. Настрой звёзды, BPM, длину, жанр и количество карт.
3. **Только показать** — посмотреть подборку, ничего не меняя. **Собрать коллекцию** — скачать
   недостающие карты, отправить их в игру и создать коллекцию.

Коллекция появляется в игре сразу. Во время игры lazer откладывает импорт карт — они появятся,
когда выйдешь в меню. Коллекция с тем же именем не дублируется: новые карты дописываются в неё.

Есть и командная строка:
```sh
python trainer.py --skill streams --stars 5.2-6.0 --count 30
python trainer.py --skill fingercontrol,tech --like "2591748,2823535" --stars 4.8-6 --count 40 --pop-weight 12
python trainer.py --tournament NM2,NM3,NM4 --digits 5,6 --stars 5.5-6.5 --years 2021-2026
python trainer.py --skill jumps --stars 5-6 --genres 10,11 --words speedcore,dnb
python trainer.py --help
```

## Безопасность твоей базы

- Перед каждой записью копия `client.realm` сохраняется в `backups/` (последние 10).
- Чтение базы — только чтение, можно при запущенной игре.
- Запись разрешена, только если формат файла базы совпадает с поддерживаемым (сейчас 24 —
  Realm 20.1.0, как в lazer). Если игра сменит формат, программа откажется писать, а не испортит базу.
- Интерфейс слушает только `127.0.0.1` и отклоняет запросы с посторонних сайтов. Подборки
  принимаются только с сайтов из списка в настройках, и каждую нужно подтвердить.

## Откуда данные

- Поиск и скачивание карт: зеркала [osu.direct](https://osu.direct) и [catboy.best](https://catboy.best),
  файлы `.osu` — с [osu.ppy.sh](https://osu.ppy.sh). Все запросы идут с ограничением частоты.
- Турнирные пулы: [osu!wiki](https://osu.ppy.sh/wiki/Tournaments) (CC BY-NC 4.0) и
  [Liquipedia](https://liquipedia.net/osu) (CC BY-SA 3.0). Готовая база лежит в `data/`, обновляется
  кнопкой в настройках. К Liquipedia программа обращается по их
  [правилам API](https://liquipedia.net/api-terms-of-use): не чаще раза в 2 секунды и с кэшем.

## Свой сайт

Тот же код работает как сайт (нужен только Python). На сервере с nginx — одна команда от root:
```sh
curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/deploy/server-install.sh | sh
```
Подробности, HTTPS и обновление — в [deploy/](deploy/README.md).

## Если что-то не так

| Проблема | Что делать |
|---|---|
| «osu!lazer не найден» | Укажи в настройках папку, где лежит `client.realm`. |
| «Node.js не найден» / «не установлены зависимости» | Повтори команду установки. |
| «Формат базы не поддерживается» | Вышла версия lazer с новым форматом базы — обнови osu!trainer той же командой. |
| Карты не появились в игре | Выйди из игры в меню — lazer импортирует карты только там. |
| Сайт не видит программу при «Добавить в игру» | Запусти osu!trainer и разреши сайту доступ к локальной сети, если браузер спросит; или нажми «Уже установлен — открыть». |
| Порт 8730 занят | Программа сама возьмёт следующий свободный, или `start.bat --port 9000`. |

Проект не связан с ppy Pty Ltd. osu! — торговая марка ppy Pty Ltd. Лицензия — MIT.

---

<a name="english"></a>
# osu!trainer (English)

Finds osu! maps for the skill you want to practise or for tournament pool slots, downloads them and
creates a collection in **osu!lazer**.

- **Skills** — Streams, Jumps / aim, Tech, Finger control, Reading and 6 more. Maps are not picked by
  tags: every candidate is downloaded and analysed object by object (snaps, spacing, rhythm changes,
  SV, note density).
- **"More like these"** — paste links to maps you like and get maps with a similar profile.
- **Tournament pools** — 32,000+ maps from 450+ tournaments (osu!wiki + Liquipedia): pick NM2 / HD1 /
  DT3 / TB… slots, stars, player rank range (open, 3/4/5/6-digit), tier and years.
- **Filters** — stars, BPM and length as two-handle sliders, music genre, words in tags.

**Online version:** https://osu.gravitacia.art — select maps on the website, download them from the
links, or press "Add to game" to put the selection into osu! through osu!trainer on your computer.

## One-line install

- **Windows** (PowerShell): `irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex`
  — installs to `%LOCALAPPDATA%\osu-trainer`, adds a shortcut and starts it. Portable Python and
  Node.js are downloaded into the app folder if missing; nothing is installed system-wide.
- **macOS / Linux** (needs `python3` 3.9+ and `node` 18+):
  `curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh`
- **Manually:** `git clone`, then `start.bat` / `./start.sh`. No third-party Python packages needed.

## Safety

`client.realm` is backed up before every write; reads are read-only; writes happen only when the
database file format matches the supported one (24, Realm 20.1.0 as in lazer). The UI listens on
`127.0.0.1` only, rejects cross-site requests, and accepts selections only from allowed sites after
you confirm them.

## Data sources

Maps: osu.direct, catboy.best, `.osu` files from osu.ppy.sh (rate-limited). Tournament pools:
[osu!wiki](https://osu.ppy.sh/wiki/Tournaments) (CC BY-NC 4.0) and [Liquipedia](https://liquipedia.net/osu)
(CC BY-SA 3.0), queried per the Liquipedia [API terms](https://liquipedia.net/api-terms-of-use).

## Self-hosting

`python ui.py --server --public-url https://your.domain` runs the website mode (Python only). On a
server with nginx, one command as root sets everything up:
`curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/deploy/server-install.sh | sh`
— see [deploy/](deploy/README.md).

Not affiliated with ppy Pty Ltd. osu! is a trademark of ppy Pty Ltd. MIT licensed.
