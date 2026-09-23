# Сайт osu!trainer на своём сервере

Сайт работает в «серверном режиме»: подбор карт, анализ и турнирная база крутятся на сервере,
пользователи скачивают карты по ссылкам, а в игру коллекцию кладёт их локальный osu!trainer
(кнопка «Добавить в игру»). Нужен только Python 3.9+ — ни Node.js, ни доступа к игре не нужно.

## Первый раз

1. DNS: A-запись `osu` -> IP сервера (в Cloudflare - режим «DNS only», серое облако).
2. Код и сервис: `sh deploy/deploy.sh vps` (создаст пользователя `osutrainer`, положит код в
   `/opt/osu-trainer`, поставит и запустит systemd-юнит на `127.0.0.1:3002`).
3. nginx:
   ```sh
   sudo cp nginx-osu.conf /etc/nginx/sites-available/osu
   sudo ln -s /etc/nginx/sites-available/osu /etc/nginx/sites-enabled/osu
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d osu.gravitacia.art
   ```
   Другие сайты на сервере конфиг не трогает: у него свой `server_name` и свой порт.
4. (по желанию) прогреть базу: заранее узнать звёзды всех турнирных карт, чтобы подбор по
   турнирам был мгновенным. Около 6 часов в фоне, один раз:
   ```sh
   sudo -u osutrainer sh -c 'cd /opt/osu-trainer && nohup python3 pools.py warm > cache/warm.log 2>&1 &'
   ```

## Обновление

`sh deploy/deploy.sh vps` — код обновится, кэш и сохранённые подборки останутся.

Турнирную базу обновлять раз в пару недель:
`sudo -u osutrainer sh -c 'cd /opt/osu-trainer && python3 pools.py build'`

## Ограничения сайта

Одновременно выполняется 2 подбора, остальные ждут в очереди; с одного адреса — 1 подбор за раз
и не больше 30 в час; до 100 карт и 400 кандидатов на подбор; задача прерывается через 15 минут.
Ссылки на подборки хранятся 60 дней.
