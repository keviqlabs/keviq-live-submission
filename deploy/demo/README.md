# Hosted demo

One URL behind a shared basic-auth login, for people without a Meta account or a model key. It runs the console on
synthetic replay data: product cards, labelled comments, the seller's approval queue and the order flow to a receipt.

- Stack: PostgreSQL + app + worker in the compose project `keviq-demo` (its own volume, its own networks).
- The app is published on `127.0.0.1:<app-port>` only. The database is not published. Nothing binds 80 or 443.
- The host's existing nginx terminates TLS, asks for the login and proxies to the loopback port
  (`nginx-demo.conf.template`).
- It costs 0 USD and cannot act on Facebook: the containers refuse to start when `MODEL_API_KEY` or
  `FACEBOOK_PAGE_TOKEN` is non-empty (`guard.py`, run by `entrypoint.sh` before any command), the spend caps are 0,
  and the worker sits on an internal network with no route out. The app has no credentials. It sits on a routed
  network, which also gives it outbound access, only so that its port can be published.

| File | Purpose |
|---|---|
| `compose.yml` | the stack; project name fixed to `keviq-demo` |
| `Dockerfile`, `Dockerfile.dockerignore` | one image for app and worker. The ignore file is an allowlist (code and synthetic data only), so `.env` files, keys and local media stay out. Only BuildKit reads it; `demo.sh` refuses to build without BuildKit |
| `entrypoint.sh`, `guard.py` | start guard |
| `demo.sh` | up, seed, reset, replay, status, logs, down, uninstall (usage at the top of the file) |
| `demo.env.example` | settings; copy to `deploy/demo/.env` (gitignored) |
| `nginx-demo.conf.template` | vhost for the host's nginx (installed as `zz-keviq-demo.conf`); placeholders `DEMO_HOST`, `DEMO_APP_PORT` |

## What a judge sees

`demo.sh seed` replays each session in `DEMO_SESSIONS` into `DEMO_SLOTS` separate sessions named
`<session>-judge<N>`. The page at `/` lists them all. Its hint to start a session with `make replay` is for
developers; on the hosted demo the operator replays sessions with `demo.sh seed` or `demo.sh replay`.

- **Each judge gets their own slots.** Give judge N the links `/console/demo1-judgeN` and
  `/console/ask-variant-judgeN`. Everyone shares one login, so slots are assigned by link, not enforced; a judge who
  opens another slot sees that judge's state.
- **demo1** shows four product cards, one of them retired (illustrations: this data set has no product photos),
  fourteen labelled comments and six orders: two confirmed with a receipt, two awaiting the customer, and two waiting
  for the seller (a discount request, and a total above the approval limit). Approving one sends it to "awaiting customer". In demo1, the "Customer agrees
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

Placeholders:

- `<demo-host>`: the demo hostname, with a DNS A record pointing at the host.
- `<app-port>`: a free loopback port.
- `<repo-url>`, `<commit>`: where to fetch the code, and the commit to run.
- `<checkout>`: where the code lives on the host, e.g. `/opt/keviq-demo`.
- `<user-with-docker-access>`: the account that runs docker (root or a member of the `docker` group). It runs
  steps 2 and 6 and the periodic reset, and it must be able to read `deploy/demo/.env`.
- `<judge-user>`: the shared login name.
- `<existing-host-1>`, `<existing-host-2>`: the vhosts already on the host.
- `<vhost-file>`, `<vhost-link>`: where the new vhost goes. nginx loads vhosts from the folders named by the
  `include` lines in `nginx.conf` (step 1). Use the folder whose `include` line comes last, so that the file loads
  after every other vhost. For `sites-enabled` (Debian and Ubuntu), `<vhost-file>` is
  `/etc/nginx/sites-available/zz-keviq-demo.conf` and `<vhost-link>` is `/etc/nginx/sites-enabled/zz-keviq-demo.conf`.
  For `conf.d`, `<vhost-file>` is `/etc/nginx/conf.d/zz-keviq-demo.conf` and there is no `<vhost-link>`: skip
  every command that names it.

Steps 1 and 7 change nothing on the host, apart from the notes file `~/keviq-demo-precheck.txt` that step 1 writes.

### 1. Read-only pre-checks

```sh
command -v docker && docker version --format '{{.Server.Version}}' && docker compose version   # else: step 7
docker buildx version                # BuildKit; demo.sh refuses to build without it
nproc; free -h                       # the stack is capped at 3 x (512 MiB, 1 CPU); want >= 2 GiB available
df -h / /var/lib/docker              # want >= 5 GB free for images and the volume (estimate)
ss -ltnH '( sport = :<app-port> )'   # must print nothing: the port is free
ss -ltn | grep -E ':(80|443) '       # 80 and 443 belong to nginx; leave them
cat /run/nginx.pid                   # record the nginx master PID
grep -nE 'include .*(sites-enabled|conf\.d)' /etc/nginx/nginx.conf   # where vhosts are loaded from: pick <vhost-file>
ls -l /etc/nginx/sites-enabled/ /etc/nginx/conf.d/; ls -ld /var/www/certbot
sudo nginx -T 2>/dev/null | grep -nE 'server_name .*<demo-host>|zone=keviq_demo|keviq-demo\.htpasswd'   # must print nothing
sudo nginx -T 2>/dev/null | grep -nE 'default_server|ssl_protocols|server_tokens'   # for the record (see Limits)
for f in <vhost-file> <vhost-link> /etc/nginx/keviq-demo.htpasswd; do test -e "$f" && echo "TAKEN $f" || echo "free $f"; done
{ for h in <existing-host-1> <existing-host-2>; do curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/"; done
  # which server answers a name that has no vhost (the default server); the demo must not change it
  curl -s -o /dev/null -w "default80 %{http_code} %{redirect_url}\n" -H 'Host: keviq-nohost.invalid' http://127.0.0.1/
  curl -sk -o /dev/null -w "default443 %{http_code} %{redirect_url}\n" --resolve keviq-nohost.invalid:443:127.0.0.1 https://keviq-nohost.invalid/
  echo | openssl s_client -connect 127.0.0.1:443 -servername keviq-nohost.invalid 2>/dev/null | openssl x509 -noout -subject | sed 's/^/default443 certificate /'
  for p in 25 465 587 993; do timeout 3 bash -c "</dev/tcp/127.0.0.1/$p" && echo "port $p open" || echo "port $p closed"; done
} > ~/keviq-demo-precheck.txt; cat ~/keviq-demo-precheck.txt
```

Stop and report, changing nothing, if any of these is true:

- docker or `docker buildx` is missing;
- less than 2 GiB is available;
- the port is taken;
- the `nginx -T` check prints anything: `<demo-host>` already has a vhost, or the rate-limit zone name or the login
  file is already in use;
- the `for f` line prints `TAKEN`.

### 2. Start the stack and check it from the host

Run this step as `<user-with-docker-access>`. The build pulls `python:3.12-slim`, `postgres:17-alpine` and Python
packages, so the host needs outbound HTTPS.

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
sudo tar -czf ~/nginx-before-keviq-demo.tgz -C /etc nginx   # copy of the whole nginx config, for step 6
sed -e 's/DEMO_HOST/<demo-host>/g' -e 's/DEMO_APP_PORT/<app-port>/g' deploy/demo/nginx-demo.conf.template > ~/zz-keviq-demo.conf
sed '/^# ----- BEGIN 443/,$d' ~/zz-keviq-demo.conf | sudo tee <vhost-file> >/dev/null
sudo ln -s <vhost-file> <vhost-link>

# Login file: outside the repository, readable by nginx's workers only. The password is generated and printed once,
# here, for the judges; only its hash is stored, and it never appears in shell history. Works in sh and bash.
U=<judge-user>; P="$(openssl rand -base64 18)"; echo "judge password (shown once): $P"
printf '%s:%s\n' "$U" "$(printf '%s' "$P" | openssl passwd -6 -stdin)" | sudo tee /etc/nginx/keviq-demo.htpasswd >/dev/null
unset P
sudo chown root:www-data /etc/nginx/keviq-demo.htpasswd && sudo chmod 640 /etc/nginx/keviq-demo.htpasswd

sudo nginx -t && sudo systemctl reload nginx        # reload, never restart; if -t fails: step 6, stop
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://<demo-host>/   # 301 https://<demo-host>/
```

### 4. Certificate, then the 443 block

```sh
sudo certbot certonly --webroot -w /var/www/certbot -d <demo-host> --deploy-hook 'systemctl reload nginx'
sudo ls /etc/letsencrypt/live/<demo-host>/          # fullchain.pem privkey.pem
sudo install -m 644 ~/zz-keviq-demo.conf <vhost-file>   # full file: 80 + 443
sudo nginx -t && sudo systemctl reload nginx
```

A `certonly --webroot` certificate does not reload nginx on its own when it is renewed. certbot stores the
`--deploy-hook` with this certificate's renewal settings, so the host's existing certbot timer renews it and then
reloads nginx.

### 5. Post-checks

```sh
cat /run/nginx.pid                   # the same PID as in step 1 (reload keeps the master)
{ for h in <existing-host-1> <existing-host-2>; do curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/"; done
  curl -s -o /dev/null -w "default80 %{http_code} %{redirect_url}\n" -H 'Host: keviq-nohost.invalid' http://127.0.0.1/
  curl -sk -o /dev/null -w "default443 %{http_code} %{redirect_url}\n" --resolve keviq-nohost.invalid:443:127.0.0.1 https://keviq-nohost.invalid/
  echo | openssl s_client -connect 127.0.0.1:443 -servername keviq-nohost.invalid 2>/dev/null | openssl x509 -noout -subject | sed 's/^/default443 certificate /'
  for p in 25 465 587 993; do timeout 3 bash -c "</dev/tcp/127.0.0.1/$p" && echo "port $p open" || echo "port $p closed"; done
} | diff ~/keviq-demo-precheck.txt - && echo "existing vhosts, default servers and mail ports unchanged"
curl -s -o /dev/null -w '%{http_code}\n' https://<demo-host>/                          # 401
curl -s -o /dev/null -w '%{http_code}\n' -u <judge-user> https://<demo-host>/          # asks for the password; 200
curl -s -o /dev/null -w '%{http_code}\n' https://<demo-host>/healthz                   # 200 without the login
curl -sN -u <judge-user> --max-time 5 'https://<demo-host>/api/sessions/demo1-judge1/events?cursor=0' | head -n 3   # SSE lines
curl -s -o /dev/null -w '%{http_code}\n' -u <judge-user> -X POST https://<demo-host>/api/sessions/demo1-judge1/events   # 403
```

If `diff` prints anything, the demo changed what another vhost or an unknown name gets: run step 6 and report.

Then open `https://<demo-host>/` in a browser, log in, open `demo1-judge1` and approve one order.

nginx limits each client address to 10 requests per second, with bursts of up to 40 more, and answers 429 above
that (`limit_req` in the vhost). A page load stays well under it.

### 6. Rollback

```sh
sudo rm <vhost-link>                 # conf.d: sudo rm <vhost-file>
sudo nginx -t && sudo systemctl reload nginx
cd <checkout> && docker compose -p keviq-demo -f deploy/demo/compose.yml --env-file deploy/demo/.env down -v --remove-orphans
```

The demo URL then falls through to whatever nginx serves for an unknown name, and the stack and its data are
gone. Uninstall (below) removes the rest. If nginx still behaves differently from step 1, compare `/etc/nginx` with
the copy from step 3 (`tar -tzvf ~/nginx-before-keviq-demo.tgz`); restoring it means extracting it over `/etc`
and removing the files the demo added.

### 7. No Docker on the host

If `command -v docker` prints nothing, or `docker compose version` or `docker buildx version` fails, stop here.
Nothing has been changed. Report "Docker (or its buildx plugin) is not installed on the host" to the Founder.
Installing Docker on the shared host is the Founder's decision: it adds a daemon and its own firewall rules. This
runbook does not install it.

## Operations

| Task | Command (from `<checkout>`) |
|---|---|
| status | `deploy/demo/demo.sh status` |
| logs | `deploy/demo/demo.sh logs` or `deploy/demo/demo.sh logs app` (also `worker`, `postgres`); json-file, 3 x 10 MB per container. nginx logs to the host's default access/error log. |
| reset to the seeded state | `deploy/demo/demo.sh reset`. It takes about a minute, during which nginx answers 502. |
| watch one session arrive live | `deploy/demo/demo.sh replay demo1 demo1-live` (speed 1), then open `/console/demo1-live` |
| update to a new commit | `git fetch && git checkout <commit> && deploy/demo/demo.sh reset` (rebuilds the image and wipes the data). `deploy/demo/demo.sh up` instead keeps the data. If `<checkout>` came from a `git archive`, unpack the new archive into a new folder, copy `deploy/demo/.env` across, run `deploy/demo/demo.sh reset` there, and point the periodic reset at the new folder. |
| stop, keep the data | `deploy/demo/demo.sh down` |
| uninstall | step 6, then `deploy/demo/demo.sh uninstall` (volume and image), `sudo rm <vhost-file> /etc/nginx/keviq-demo.htpasswd` (for `conf.d`, `<vhost-file>` is already gone), optionally `sudo certbot delete --cert-name <demo-host>`, and remove `<checkout>` |

### Periodic reset

Each reset wipes whatever the judges did in their slots and takes the app away for about a minute. Schedule it at a
quiet hour, and run it by hand before each judging round. Here is a systemd timer that resets daily at 03:00 host
time; `OnCalendar=*-*-* 00/3:00:00` would reset every three hours. `User=` must be able to run docker and to read
`deploy/demo/.env`, which step 2 makes mode 600: run step 2 as that user, or `chown` the file to it.

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
  that gives the networks `internal` and `published` explicit `ipam` subnets, then set
  `DEMO_COMPOSE_OVERRIDE=<absolute path>` in `deploy/demo/.env` (or in the shell); `demo.sh` adds it with `-f`.
- `demo: docker buildx is missing` or `DOCKER_BUILDKIT=0 selects the legacy builder` on `up`: the legacy builder
  ignores `Dockerfile.dockerignore` and would copy `.env` files and local media into the image, so `demo.sh` stops
  before building. Install the buildx plugin, or unset `DOCKER_BUILDKIT`.
- 500 after a successful login usually means the login file is missing, or is not readable by `www-data`. 502 means
  the stack is down or a reset is running.

### Limits

- One shared login: any judge can take seller actions in any slot (decisions, price and item edits, simulated
  customer messages). Slots are assigned by link, and the daily reset restores them.
- The app does not check where a request comes from. A page on another site, opened by a judge whose browser holds
  the login, could post simulated customer messages into a slot (that route also accepts plain-text and form bodies)
  or start a new session. The data is synthetic and nothing is sent out; the next reset removes it.
- TLS versions on port 443 come from the host's default server for that port, not from this vhost. If the
  `ssl_protocols` line that step 1 records allows TLSv1 or TLSv1.1, the demo accepts them too: report it rather than
  editing another vhost.

What has been run: `demo.sh reset` on a laptop (Docker Desktop, BuildKit, with a `DEMO_COMPOSE_OVERRIDE` for
subnets), then health, the seeded slots and `demo.sh uninstall`; the vhost in nginx 1.24.0 and 1.27.5 containers with
self-signed certificates and a stand-in for the host's other vhost: both stages, the default-server probes, the
login, the rate limit, the 403 on the event intake, and, on 1.24 in front of the running stack, SSE and one approval.
It has not been run on the target host.
