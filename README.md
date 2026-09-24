# osu!drill

**English** | [Русский](#русский) | [Español](#español)

*Formerly osu!trainer — renamed so it isn't confused with
[FunOrange's osu-trainer](https://github.com/FunOrange/osu-trainer). The repository keeps its old address.*

Finds osu! maps for the skill you want to practise, maps similar to the ones you like, the most
popular songs among fans of a skill, or maps from real tournament pools — and turns them into a ready
collection in **osu!lazer**.

- **Skills** — Streams, Jumps / aim, Tech, Finger control, Reading and 6 more. Maps are not picked by
  tags: every candidate is downloaded and analysed object by object (snaps, spacing, rhythm changes,
  SV, note density).
- **"More like these"** — paste links to maps you like and get maps with a similar profile
  (BPM, stream share, rhythm changes, sliders, spacing).
- **Player collections** — thousands of lists from [osu!Collector](https://osucollector.com) that
  players named "tech", "streams", "aim"… Maps from such lists become candidates and get a score
  boost; with reference maps, so do the maps that most often share lists with them.
- **Popular** — the most popular songs among fans of a skill: pick Tech and get the maps tech players
  put into their osu!Collector collections most often, the classics of the scene first (different
  mapsets of one song count together). With no skill picked — the most played osu! maps, which you
  can narrow down by genre or words in tags. Maps are not analysed, so it takes seconds.
- **Tournament pools** — 32,000+ maps from 450+ tournaments (osu!wiki + Liquipedia). Pick slots like
  NM2 / HD1 / DT3 / TB, stars, player rank range (open, 3/4/5/6-digit), tournament tier and years;
  the selection is drawn from different tournaments.
- **Filters** — stars, BPM and length as two-handle sliders, music genre, words in tags
  (touhou, vocaloid, speedcore…).
- Interface in English, Spanish and Russian.

## Online version

**https://osu.gravitacia.art** — pick maps right on the website. Download them from the links in the
table, or press **Add to game** to put the selection into osu! through osu!drill on your computer.
The "Guide" button on the site explains every step.

## One-line install

**Windows** — open PowerShell and paste:
```powershell
irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
```
It installs to `%LOCALAPPDATA%\osu-trainer`, creates an "osu!drill" shortcut and starts the app.
If Python or Node.js is missing, portable copies are downloaded into the app folder — nothing is
installed system-wide. Run the same command again to update.

**macOS / Linux** (needs `python3` 3.9+ and `node` 18+):
```sh
curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh
```

**Manually:** `git clone https://github.com/gravitaciaxy/osu-trainer`, then `start.bat` (or
`./start.sh`). Needs Python 3.9+ and Node.js 18+; no third-party Python packages.

The osu!lazer data folder is found automatically, including a relocated one. If it isn't, set it in
**Settings**.

## Usage

1. Pick a tab: **Skills**, **Popular** or **Tournament pools**. Every filter has a "?" tooltip.
2. Set stars, BPM, length, genre and the number of maps.
3. **Preview only** shows the selection without changing anything. **Build collection** downloads
   missing maps, sends them to osu! and creates the collection.

The collection shows up in the game right away. During gameplay osu!lazer pauses imports — new maps
finish importing when you're back in the menu. A collection with the same name is not duplicated:
new maps are added to it.

Command line:
```sh
python trainer.py --skill streams --stars 5.2-6.0 --count 30
python trainer.py --skill fingercontrol,tech --like "2591748,2823535" --stars 4.8-6 --count 40 --pop-weight 12
python trainer.py --tournament NM2,NM3,NM4 --digits 5,6 --stars 5.5-6.5 --years 2021-2026
python trainer.py --skill jumps --stars 5-6 --genres 10,11 --words speedcore,dnb
python trainer.py --popular --skill tech --stars 5-7 --count 40
python trainer.py --popular --stars 5-6 --words touhou
python trainer.py --help
```

## Is it safe for my osu!?

- `client.realm` is backed up to `backups/` before every write (the last 10 are kept).
- Reads are read-only and work while the game is running.
- Writes happen only if the database file format matches the supported one (currently 24 —
  Realm 20.1.0, as in lazer). If osu! moves to a new format, the app refuses to write instead of
  touching the database.
- The local app listens on `127.0.0.1` only and rejects requests from other websites. Selections are
  accepted only from sites in the allowed list, and each one has to be confirmed.

## Data sources

- Maps: [osu.direct](https://osu.direct) and [catboy.best](https://catboy.best) mirrors, `.osu` files
  from [osu.ppy.sh](https://osu.ppy.sh). All requests are rate-limited.
- Tournament pools: [osu!wiki](https://osu.ppy.sh/wiki/Tournaments) (CC BY-NC 4.0) and
  [Liquipedia](https://liquipedia.net/osu) (CC BY-SA 3.0). A prebuilt database ships in `data/`;
  update it from Settings. Liquipedia is queried per its
  [API terms](https://liquipedia.net/api-terms-of-use): at most one request every 2 seconds, cached.
- Player collections: [osu!Collector](https://osucollector.com) — public collections whose names
  mention a skill; map details come from osu.direct. A prebuilt index ships in `data/`, so the app
  itself never queries osu!Collector; `python collector.py build` refreshes the index (about 1.5 hours:
  one request at a time, at most one every 1.4 seconds, cached). Star ratings change when osu!
  recalculates them, so maps from the index are re-checked on osu.direct before they are used;
  `python collector.py build --cached --refresh-meta` refreshes only the map details (about 15 minutes).

## Your own website

The same code runs as a website (Python only). On a server with nginx, one command as root:
```sh
curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/deploy/server-install.sh | sh
```
Details, HTTPS and updates: [deploy/](deploy/README.md).

## Troubleshooting

| Problem | What to do |
|---|---|
| "osu!lazer not found" | Set the folder that contains `client.realm` in Settings. |
| "Node.js not found" / "dependencies are missing" | Run the install command again. |
| "Database format is not supported" | A new lazer version changed the database format — update osu!drill with the same command. |
| Maps don't show up in the game | Leave gameplay and go to the menu — lazer imports maps only there. |
| The website can't see the app on "Add to game" | Start osu!drill and allow local network access if the browser asks; or press "Already installed — open". |
| Port 8730 is busy | The app picks the next free port, or run `start.bat --port 9000`. |

Not affiliated with ppy Pty Ltd. osu! is a trademark of ppy Pty Ltd. MIT licensed.

---

<a name="русский"></a>
# osu!drill — по-русски

*Раньше назывался osu!trainer; переименован, чтобы не путать с
[osu-trainer от FunOrange](https://github.com/FunOrange/osu-trainer). Адрес репозитория прежний.*

Подбирает карты osu! под навык, который хочешь тренировать, карты, похожие на любимые, самые
популярные песни среди любителей навыка или карты из настоящих турнирных пулов — и создаёт из них
коллекцию в **osu!lazer**.

- **Навыки** — Streams, Jumps / aim, Tech, Finger control, Reading и ещё 6. Карты выбираются не по
  тегам: каждая кандидатка скачивается и разбирается по объектам — деления ритма, spacing, смены
  ритма, SV, плотность нот.
- **«Хочу похожее»** — вставь ссылки на любимые карты, и найдутся карты с похожим профилем.
- **Коллекции игроков** — тысячи подборок с [osu!Collector](https://osucollector.com), которые игроки
  назвали «tech», «streams», «aim»…: карты из них идут в кандидаты и получают прибавку к оценке, а для
  карт-образцов — карты, которые чаще всего лежат с ними в одних подборках.
- **Популярные** — самые популярные песни среди любителей навыка: выбираешь Tech — получаешь карты,
  которые tech-игроки чаще всего кладут в свои подборки на osu!Collector, сверху классика жанра (разные
  мапсеты одной песни считаются вместе). Без навыка — самые играемые карты osu!, их можно сузить жанром
  и словами в тегах. Карты не разбираются, поэтому подбор занимает секунды.
- **Турнирные пулы** — 32 000+ карт из 450+ турниров (osu!wiki + Liquipedia): слоты NM2 / HD1 / DT3 /
  TB, звёзды, рейтинг участников (open, 3/4/5/6-digit), уровень турнира и годы.
- **Фильтры** — звёзды, BPM и длина двусторонними ползунками, жанр, слова в тегах.

**Онлайн-версия:** https://osu.gravitacia.art — подбор прямо на сайте; карты можно скачать по ссылкам
или нажать «Добавить в игру». Кнопка «Инструкция» на сайте объясняет все шаги.

**Установка одной командой.** Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
```
macOS / Linux: `curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh`

Программа ставится в `%LOCALAPPDATA%\osu-trainer`, при необходимости докачивает портативные Python и
Node.js (в систему ничего не ставится), создаёт ярлык и запускается. Повторный запуск команды —
обновление.

**Как пользоваться:** выбери вкладку «Навыки», «Популярные» или «Турнирные пулы», настрой фильтры
(у каждого есть подсказка «?»), нажми «Только показать» или «Собрать коллекцию». Во время игры lazer
откладывает импорт карт — они появятся, когда выйдешь в меню.

**Безопасность:** перед каждой записью `client.realm` копируется в `backups/`; запись разрешена, только
если формат базы совпадает с поддерживаемым; программа слушает только `127.0.0.1` и принимает подборки
только с разрешённых сайтов после подтверждения.

---

<a name="español"></a>
# osu!drill — en español

*Antes se llamaba osu!trainer; cambió de nombre para no confundirse con
[osu-trainer de FunOrange](https://github.com/FunOrange/osu-trainer). El repositorio sigue en la misma dirección.*

Elige mapas de osu! para la habilidad que quieres entrenar, mapas parecidos a los que te gustan, las
canciones más populares entre los fans de una habilidad o mapas de mappools reales de torneos, y crea con
ellos una colección en **osu!lazer**.

- **Habilidades** — Streams, Jumps / aim, Tech, Finger control, Reading y 6 más. Los mapas no se eligen
  por etiquetas: cada candidato se descarga y se analiza objeto por objeto (snaps, spacing, cambios de
  ritmo, SV, densidad de notas).
- **«Quiero algo parecido»** — pega enlaces a mapas que te gusten y encuentra mapas con un perfil similar.
- **Colecciones de jugadores** — miles de listas de [osu!Collector](https://osucollector.com) que los
  jugadores llamaron «tech», «streams», «aim»…: sus mapas entran como candidatos y suben de puntuación;
  con mapas de referencia, también los que más a menudo comparten lista con ellos.
- **Populares** — las canciones más populares entre los fans de una habilidad: elige Tech y obtén los
  mapas que los jugadores de tech más ponen en sus colecciones de osu!Collector, primero los clásicos de
  la escena (los distintos mapsets de una misma canción cuentan juntos). Sin habilidad: los mapas de osu!
  más jugados, que puedes acotar por género o por palabras en las etiquetas. Los mapas no se analizan,
  así que tarda unos segundos.
- **Mappools de torneos** — más de 32 000 mapas de más de 450 torneos (osu!wiki + Liquipedia): slots
  NM2 / HD1 / DT3 / TB, estrellas, rango de los jugadores (open, 3/4/5/6-digit), nivel y años.
- **Filtros** — estrellas, BPM y duración con deslizadores de dos extremos, género, palabras en etiquetas.

**Versión web:** https://osu.gravitacia.art — elige mapas en la web, descárgalos con los enlaces o pulsa
«Añadir al juego». El botón «Guía» explica cada paso.

**Instalación con un solo comando.** Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.ps1 | iex
```
macOS / Linux: `curl -fsSL https://raw.githubusercontent.com/gravitaciaxy/osu-trainer/main/install.sh | sh`

Se instala en `%LOCALAPPDATA%\osu-trainer`, descarga versiones portátiles de Python y Node.js si hacen
falta (no instala nada en el sistema), crea un acceso directo y se abre. Volver a ejecutar el comando
lo actualiza.

**Uso:** elige la pestaña «Habilidades», «Populares» o «Mappools de torneos», ajusta los filtros (cada
uno tiene su «?»), pulsa «Solo mostrar» o «Crear colección». Durante el juego osu!lazer pausa las
importaciones; terminan al volver al menú.

**Seguridad:** antes de cada escritura se copia `client.realm` en `backups/`; solo se escribe si el
formato de la base coincide con el compatible; la aplicación solo escucha en `127.0.0.1` y acepta
selecciones solo de sitios permitidos y tras tu confirmación.
