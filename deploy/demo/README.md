# Hosted demo

One URL behind a shared basic-auth login, for people without a Meta account or a model key. It runs the console on
synthetic replay data: product cards, labelled comments, the seller's approval queue and the order flow to a receipt.

- Stack: PostgreSQL + app + worker in the compose project `keviq-demo` (its own volume, its own networks).
- The app is published on `127.0.0.1:<app-port>` only. The database is not published. Nothing binds 80 or 443.
- The host's existing nginx terminates TLS, asks for the login and proxies to the loopback port
  (`nginx-demo.conf.template`).
- It costs 0 USD and cannot reach Facebook: the containers refuse to start when `MODEL_API_KEY` or
  `FACEBOOK_PAGE_TOKEN` is non-empty (`guard.py`, run by `entrypoint.sh` before any command), the worker's spend
  caps are 0, and the worker sits on an internal network with no route out. The app has no credentials; it needs a
  routed network only so that its port can be published.

| File | Purpose |
|---|---|
| `compose.yml` | the stack; project name fixed to `keviq-demo` |
| `Dockerfile`, `Dockerfile.dockerignore` | one image for app and worker; `.env` files and keys never enter it |
| `entrypoint.sh`, `guard.py` | start guard |
| `demo.sh` | up, seed, reset, replay, status, logs, down, uninstall (usage at the top of the file) |
| `demo.env.example` | settings; copy to `deploy/demo/.env` (gitignored) |
| `nginx-demo.conf.template` | vhost for the host's nginx; placeholders `DEMO_HOST`, `DEMO_APP_PORT` |

## What a judge sees

`demo.sh seed` replays each session in `DEMO_SESSIONS` into `DEMO_SLOTS` separate sessions named
`<session>-judge<N>`. The page at `/` lists them all.

- **Each judge gets their own slots.** Give judge N the links `/console/demo1-judgeN` and
  `/console/ask-variant-judgeN`. Everyone shares one login, so slots are assigned by link, not enforced; a judge who
  opens another slot sees that judge's state.
- **demo1** shows four product cards (illustrations: this data set has no product photos), fourteen labelled
  comments, two orders that already have a receipt, and two orders waiting for the seller (a discount request, and a
  total above the approval limit). Approving one sends it to "awaiting customer". In demo1, the "Customer agrees
  (sim)" button records the reply, but the order stays at "awaiting customer". demo1's orders come from the recorded
  replay, and the worker does not act on recorded orders.
- **ask-variant** shows orders that the worker builds from comments, without a model. One order is already confirmed
  with a receipt, and two are awaiting the customer. "Customer agrees (sim)" takes either of them to confirmed, with
  a receipt.
- **Two browsers on the same order:** decisions on an order are serialized. The first one wins; the second gets
  HTTP 409 ("… is CONFIRM_SENT, not waiting for the seller"), and the order's event stream carries one transition.
- **Reset** wipes every slot (the database volume included) and replays them again. The result is the same seeded
  state every time.

## Runbook

Placeholders: `<demo-host>` (the demo hostname, with a DNS A record pointing at the host), `<app-port>` (a free
loopback port), `<checkout>` (where the repository lives on the host, e.g. `/opt/keviq-demo`), `<judge-user>`, and
`<existing-host-1>`, `<existing-host-2>` (the vhosts already on the host). Commands that use docker run as a user
that may use docker (root or the `docker` group). Steps 1 and 7 change nothing.

### 1. Read-only pre-checks

```sh
command -v docker && docker version --format '{{.Server.Version}}' && docker compose version   # else: step 7
nproc; free -h                       # the stack is capped at 3 x (512 MiB, 1 CPU); want >= 2 GiB available
df -h / /var/lib/docker              # want >= 5 GB free for images and the volume (estimate)
ss -ltnH '( sport = :<app-port> )'   # must print nothing: the port is free
ss -ltn | grep -E ':(80|443) '       # 80 and 443 belong to nginx; leave them
cat /run/nginx.pid                   # record the nginx master PID
grep -nE 'include .*(sites-enabled|conf\.d)' /etc/nginx/nginx.conf   # where vhosts are loaded from
ls -l /etc/nginx/sites-enabled/; ls -ld /var/www/certbot
sudo nginx -T 2>/dev/null | grep -E 'server_name .*<demo-host>'      # must print nothing
test ! -e /etc/nginx/sites-available/keviq-demo.conf && echo "name free"
{ for h in <existing-host-1> <existing-host-2>; do curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/"; done
  for p in 25 465 587 993; do timeout 3 bash -c "</dev/tcp/127.0.0.1/$p" && echo "port $p open" || echo "port $p closed"; done
} > ~/keviq-demo-precheck.txt; cat ~/keviq-demo-precheck.txt
```

Stop and report, changing nothing, if any of these is true:

- docker is missing;
- less than 2 GiB is available;
- the port is taken;
- `<demo-host>` already has a vhost;
- `keviq-demo.conf` already exists.

If nginx loads vhosts from `conf.d` instead of `sites-enabled`, use `/etc/nginx/conf.d/keviq-demo.conf` as the one
new file in step 3, and skip the symlink.

### 2. Start the stack and check it from the host

The build pulls `python:3.12-slim`, `postgres:17-alpine` and Python packages, so the host needs outbound HTTPS.

```sh
git clone <repo-url> <checkout> && cd <checkout> && git checkout <commit>   # or unpack a `git archive` of <commit>
cp deploy/demo/demo.env.example deploy/demo/.env && chmod 600 deploy/demo/.env
# edit deploy/demo/.env: DEMO_APP_PORT=<app-port>, DEMO_POSTGRES_PASSWORD=<output of: openssl rand -hex 16>
deploy/demo/demo.sh reset            # build, start guard, start, seed (about a minute on a laptop)
deploy/demo/demo.sh status
curl -s http://127.0.0.1:<app-port>/healthz                            # {"ok":true,"store":"pg",...}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<app-port>/  # 200
ss -ltn | grep ':<app-port> '        # must show 127.0.0.1:<app-port>; never 0.0.0.0, * or [::]
```

Docker-published ports bypass UFW. That is why `compose.yml` binds the app to 127.0.0.1. Do not change it to
`<port>:<port>`.

### 3. vhost (port 80 only) and the login file

```sh
cd <checkout>
sed -e 's/DEMO_HOST/<demo-host>/g' -e 's/DEMO_APP_PORT/<app-port>/g' deploy/demo/nginx-demo.conf.template > ~/keviq-demo.conf
sed '/^# ----- BEGIN 443/,$d' ~/keviq-demo.conf | sudo tee /etc/nginx/sites-available/keviq-demo.conf >/dev/null
sudo ln -s /etc/nginx/sites-available/keviq-demo.conf /etc/nginx/sites-enabled/keviq-demo.conf

# Login file: outside the repository, readable by nginx's workers only. The password is typed, never echoed or stored in shell history.
read -r -p 'judge user: ' U; read -rs -p 'judge password: ' P; echo
printf '%s:%s\n' "$U" "$(printf '%s' "$P" | openssl passwd -6 -stdin)" | sudo tee /etc/nginx/keviq-demo.htpasswd >/dev/null
unset P
sudo chown root:www-data /etc/nginx/keviq-demo.htpasswd && sudo chmod 640 /etc/nginx/keviq-demo.htpasswd

sudo nginx -t && sudo systemctl reload nginx        # reload, never restart; if -t fails: remove the symlink, stop
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://<demo-host>/   # 301 https://<demo-host>/
```

### 4. Certificate, then the 443 block

```sh
sudo certbot certonly --webroot -w /var/www/certbot -d <demo-host>
sudo ls /etc/letsencrypt/live/<demo-host>/          # fullchain.pem privkey.pem
sudo install -m 644 ~/keviq-demo.conf /etc/nginx/sites-available/keviq-demo.conf   # full file: 80 + 443
sudo nginx -t && sudo systemctl reload nginx
ls /etc/letsencrypt/renewal-hooks/deploy/           # renewal uses the host's existing certbot timer and reload hook
```

### 5. Post-checks

```sh
cat /run/nginx.pid                   # the same PID as in step 1 (reload keeps the master)
{ for h in <existing-host-1> <existing-host-2>; do curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/"; done
  for p in 25 465 587 993; do timeout 3 bash -c "</dev/tcp/127.0.0.1/$p" && echo "port $p open" || echo "port $p closed"; done
} | diff ~/keviq-demo-precheck.txt - && echo "existing vhosts and mail ports unchanged"
curl -s -o /dev/null -w '%{http_code}\n' https://<demo-host>/                          # 401
curl -s -o /dev/null -w '%{http_code}\n' -u <judge-user> https://<demo-host>/          # asks for the password; 200
curl -s https://<demo-host>/healthz                                                    # 200 without the login
curl -sN -u <judge-user> --max-time 5 'https://<demo-host>/api/sessions/demo1-judge1/events?cursor=0' | head -n 3   # SSE lines
curl -s -o /dev/null -w '%{http_code}\n' -u <judge-user> -X POST https://<demo-host>/api/sessions/demo1-judge1/events   # 403
```

Then open `https://<demo-host>/` in a browser, log in, open `demo1-judge1` and approve one order.

### 6. Rollback

```sh
sudo rm /etc/nginx/sites-enabled/keviq-demo.conf
sudo nginx -t && sudo systemctl reload nginx
cd <checkout> && docker compose -p keviq-demo -f deploy/demo/compose.yml --env-file deploy/demo/.env down -v --remove-orphans
```

The demo URL then falls through to whatever nginx serves for an unknown name, and the stack and its data are
gone. Uninstall (below) removes the rest.

### 7. No Docker on the host

If `command -v docker` prints nothing, or `docker compose version` fails, stop here. Nothing has been changed. Report
"Docker is not installed on the host" to the Founder. Installing Docker on a production mail server is the Founder's
decision: it adds a daemon and its own firewall rules. This runbook does not install it.

## Operations

| Task | Command (from `<checkout>`) |
|---|---|
| status | `deploy/demo/demo.sh status` |
| logs | `deploy/demo/demo.sh logs` or `deploy/demo/demo.sh logs app` (also `worker`, `postgres`); json-file, 3 x 10 MB per container. nginx logs to the host's default access/error log. |
| reset to the seeded state | `deploy/demo/demo.sh reset`. It takes about a minute, during which nginx answers 502. |
| watch one session arrive live | `deploy/demo/demo.sh replay demo1 demo1-live` (speed 1), then open `/console/demo1-live` |
| update to a new commit | `git fetch && git checkout <commit> && deploy/demo/demo.sh reset` (rebuilds the image and wipes the data). `deploy/demo/demo.sh up` instead keeps the data. |
| stop, keep the data | `deploy/demo/demo.sh down` |
| uninstall | step 6, then `deploy/demo/demo.sh uninstall` (volume and image), `sudo rm /etc/nginx/sites-available/keviq-demo.conf /etc/nginx/keviq-demo.htpasswd`, optionally `sudo certbot delete --cert-name <demo-host>`, and remove `<checkout>` |

### Periodic reset

Each reset wipes whatever the judges did in their slots and takes the app away for about a minute. Schedule it at a
quiet hour, and run it by hand before each judging round. Here is a systemd timer that resets daily at 03:00 host
time; `OnCalendar=*-*-* 00/3:00:00` would reset every three hours.

```ini
# /etc/systemd/system/keviq-demo-reset.service
[Unit]
Description=Reset the hosted demo to its seeded state
[Service]
Type=oneshot
User=<user-with-docker-access>
WorkingDirectory=<checkout>
ExecStart=<checkout>/deploy/demo/demo.sh reset

# /etc/systemd/system/keviq-demo-reset.timer
[Unit]
Description=Daily reset of the hosted demo
[Timer]
OnCalendar=*-*-* 03:00:00
[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload && sudo systemctl enable --now keviq-demo-reset.timer
systemctl list-timers keviq-demo-reset.timer; journalctl -u keviq-demo-reset.service -n 50
```

The cron equivalent is
`0 3 * * * cd <checkout> && deploy/demo/demo.sh reset >>$HOME/keviq-demo-reset.log 2>&1`.

### Settings and troubleshooting

- `deploy/demo/.env`: `DEMO_SESSIONS` (folders under `data/sessions/`), `DEMO_SLOTS` (judges), `DEMO_SEED_SPEED`.
  A shell variable of the same name overrides the file.
- Through the public URL, the raw event intake (`POST /api/sessions/<id>/events`) answers 403. Seeding and replays
  run on the host against the loopback port.
- The login header is removed before the request reaches the app. The app refuses seller actions that carry an
  `Authorization` header.
- `all predefined address pools have been fully subnetted` on `up`: Docker has no free subnet. Write a compose file
  that gives the networks `internal` and `published` explicit `ipam` subnets, then point
  `DEMO_COMPOSE_OVERRIDE=<path>` at it; `demo.sh` adds it with `-f`.
- 500 after a successful login usually means the login file is missing, or is not readable by `www-data`. 502 means
  the stack is down or a reset is running.

This setup has been run on a laptop (Docker Desktop) behind nginx 1.27 in a container, using a self-signed
certificate. It has not yet been run on the target host.
