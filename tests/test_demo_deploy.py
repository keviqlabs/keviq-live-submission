"""Hosted demo deploy files (deploy/demo/): the start guard refuses model and Page credentials, the compose stack
publishes the app on loopback only and keeps the worker off the routed network, the image build context is an
allowlist that demo.sh only builds with BuildKit, and the nginx vhost keeps SSE streaming, strips the browser's
Authorization header, asks for the login, limits the request rate and keeps the event intake read-only."""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "deploy" / "demo"
GUARD = DEMO / "guard.py"
ENTRYPOINT = DEMO / "entrypoint.sh"
DEMO_SH = DEMO / "demo.sh"
# A value long enough that finding it in the output can only mean the guard printed it.
SENTINEL = "SENTINEL-7f3a9c-never-printed"


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "FACEBOOK_PAGE_TOKEN")}
    return {**env, "PYTHON": sys.executable, **extra}


def run_guard(tmp_path: Path, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD), "--root", str(tmp_path)],
        env=clean_env(**extra),
        capture_output=True,
        text=True,
    )


def test_guard_passes_with_both_credentials_empty(tmp_path):
    r = run_guard(tmp_path, MODEL_API_KEY="", FACEBOOK_PAGE_TOKEN="")
    assert r.returncode == 0, r.stderr


def test_guard_passes_with_both_credentials_unset(tmp_path):
    r = run_guard(tmp_path)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", ["MODEL_API_KEY", "FACEBOOK_PAGE_TOKEN"])
@pytest.mark.parametrize("value", [SENTINEL, "replace-me", " "])
def test_guard_refuses_any_non_empty_credential(tmp_path, name, value):
    r = run_guard(tmp_path, **{name: value})
    assert r.returncode == 1
    assert name in r.stderr
    assert value.strip() == "" or value not in r.stderr  # the value itself is never printed


def test_guard_refuses_a_dotenv_the_app_would_load(tmp_path):
    (tmp_path / ".env").write_text("MODEL_API_KEY=\n")
    r = run_guard(tmp_path, MODEL_API_KEY="", FACEBOOK_PAGE_TOKEN="")
    assert r.returncode == 1
    assert ".env" in r.stderr


def test_entrypoint_runs_the_command_only_when_the_guard_passes(tmp_path):
    marker = tmp_path / "ran"
    cmd = ["sh", str(ENTRYPOINT), "touch", str(marker)]
    refused = subprocess.run(
        cmd, env=clean_env(MODEL_API_KEY="x", DEMO_ROOT=str(tmp_path)), capture_output=True
    )
    assert refused.returncode == 1 and not marker.exists()
    ok = subprocess.run(cmd, env=clean_env(MODEL_API_KEY="", DEMO_ROOT=str(tmp_path)), capture_output=True)
    assert ok.returncode == 0 and marker.exists()


# --- demo.sh, with a stub `docker` on PATH that logs its arguments and DOCKER_BUILDKIT --------------------------

STUB_DOCKER = """#!/bin/sh
printf '%s|%s\\n' "${DOCKER_BUILDKIT-unset}" "$*" >> "$STUB_LOG"
if [ "$1" = buildx ]; then
  [ -n "${STUB_NO_BUILDX:-}" ] && exit 1
  echo "buildx stub"
fi
case "$*" in *" run --rm --no-deps -T app true"*) [ -n "${STUB_GUARD_REFUSES:-}" ] && exit 1 ;; esac
exit 0
"""


def run_demo(
    tmp_path: Path, *args: str, env_lines: str = "", **extra
) -> tuple[subprocess.CompletedProcess, list]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(STUB_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    env_file = tmp_path / "demo.env"
    env_file.write_text("DEMO_APP_PORT=18780\nDEMO_POSTGRES_PASSWORD=not-a-secret\n" + env_lines)
    log = tmp_path / "docker.log"
    log.write_text("")
    drop = ("DOCKER_BUILDKIT", "DEMO_COMPOSE_OVERRIDE", "DEMO_SESSIONS", "DEMO_SLOTS", "DEMO_APP_PORT")
    env = {k: v for k, v in clean_env().items() if k not in drop}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", DEMO_ENV_FILE=str(env_file), STUB_LOG=str(log), **extra
    )
    r = subprocess.run([str(DEMO_SH), *args], env=env, capture_output=True, text=True, cwd=tmp_path)
    return r, [line.split("|", 1) for line in log.read_text().splitlines()]


def test_demo_up_builds_with_buildkit_and_runs_the_guard_before_starting(tmp_path):
    r, calls = run_demo(tmp_path, "up")
    assert r.returncode == 0, r.stderr
    commands = [cmd for _, cmd in calls]
    assert commands[0] == "buildx version"
    compose = [(buildkit, cmd) for buildkit, cmd in calls if cmd.startswith("compose ")]
    assert compose and all(buildkit == "1" for buildkit, _ in compose)
    steps = [cmd.split(" --env-file ", 1)[1].split(" ", 1)[1] for _, cmd in compose]
    assert steps == ["build", "run --rm --no-deps -T app true", "up -d --wait --wait-timeout 180"]


@pytest.mark.parametrize(
    "case",
    [
        {"extra": {"STUB_NO_BUILDX": "1"}, "env_lines": "", "says": "buildx"},
        {"extra": {"DOCKER_BUILDKIT": "0"}, "env_lines": "", "says": "DOCKER_BUILDKIT=0"},
        {"extra": {}, "env_lines": "DOCKER_BUILDKIT=0\n", "says": "DOCKER_BUILDKIT=0"},
    ],
    ids=["no-buildx", "buildkit-off-in-shell", "buildkit-off-in-env-file"],
)
def test_demo_up_refuses_the_legacy_builder(tmp_path, case):
    # The legacy builder ignores Dockerfile.dockerignore and would copy .env files and local media into the image.
    r, calls = run_demo(tmp_path, "up", env_lines=case["env_lines"], **case["extra"])
    assert r.returncode == 1
    assert case["says"] in r.stderr
    assert not [cmd for _, cmd in calls if cmd.startswith("compose ")]


def test_demo_up_starts_nothing_when_the_guard_refuses(tmp_path):
    r, calls = run_demo(tmp_path, "up", STUB_GUARD_REFUSES="1")
    assert r.returncode == 1
    assert "no container was started" in r.stderr
    assert not [cmd for _, cmd in calls if " up -d " in cmd]


def test_demo_reads_the_compose_override_from_the_env_file_and_the_shell(tmp_path):
    from_file = tmp_path / "from-file.yml"
    r, calls = run_demo(tmp_path, "down", env_lines=f"DEMO_COMPOSE_OVERRIDE={from_file}\n")
    assert r.returncode == 0, r.stderr
    assert f"-f {DEMO / 'compose.yml'} -f {from_file} --env-file" in calls[-1][1]
    from_shell = tmp_path / "from-shell.yml"
    r, calls = run_demo(
        tmp_path,
        "down",
        env_lines=f"DEMO_COMPOSE_OVERRIDE={from_file}\n",
        DEMO_COMPOSE_OVERRIDE=str(from_shell),
    )
    assert r.returncode == 0, r.stderr
    assert f"-f {from_shell} --env-file" in calls[-1][1] and str(from_file) not in calls[-1][1]
    r, calls = run_demo(tmp_path, "down")
    assert f"-f {DEMO / 'compose.yml'} --env-file" in calls[-1][1]


# --- image build context ------------------------------------------------------------------------------------

# Everything the image runs: the lock files, the code, the migrations and the synthetic data the replay reads.
BUILD_ALLOWLIST = {
    "pyproject.toml", "uv.lock", ".python-version", "answers", "app", "connectors", "contracts",
    "data/catalog.json", "data/comments", "data/sessions", "deploy/demo", "measure", "meta", "migrations",
    "orders", "worker",
}  # fmt: skip


def test_image_build_context_is_an_allowlist():
    lines = (DEMO / "Dockerfile.dockerignore").read_text().splitlines()
    rules = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert rules[0] == "*"  # nothing enters the image unless it is re-included below
    included = [rule[1:] for rule in rules if rule.startswith("!")]
    assert sorted(included) == sorted(BUILD_ALLOWLIST)
    for path in included:
        assert (ROOT / path).exists(), path
    # Excluded again inside the allowed folders; these must come after the last `!` line (the last match wins).
    tail = rules[max(i for i, rule in enumerate(rules) if rule.startswith("!")) + 1 :]
    for pattern in ("**/.env", "**/.env.*", "**/*.htpasswd", "**/*.pem", "**/*.key", "**/*.mp4", "**/*.jpg"):
        assert pattern in tail, pattern


def dockerignore_includes() -> list[str]:
    lines = (DEMO / "Dockerfile.dockerignore").read_text().splitlines()
    rules = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return [rule[1:] for rule in rules if rule.startswith("!")]


def imported_top_level_names(source: str) -> set[str]:
    """Top-level module names of the absolute imports in one source file; relative imports are skipped."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def is_first_party(name: str) -> bool:
    return (ROOT / name / "__init__.py").is_file() or (ROOT / f"{name}.py").is_file()


def test_image_includes_every_first_party_package_the_runtime_imports():
    included = set(dockerignore_includes())
    code_folders = sorted(path for path in included if (ROOT / path).is_dir())
    imports: dict[str, set[str]] = {}  # first-party name -> the files that import it
    for folder in code_folders:
        for file in sorted((ROOT / folder).rglob("*.py")):
            for name in imported_top_level_names(file.read_text()):
                if is_first_party(name):
                    imports.setdefault(name, set()).add(str(file.relative_to(ROOT)))
    assert {"app", "worker"} <= set(imports), sorted(imports)  # the scan found the code it is meant to check
    missing = {name: sorted(files) for name, files in imports.items() if name not in included}
    assert not missing, f"imported at run time but absent from Dockerfile.dockerignore: {missing}"
    not_declared = {name for name in imports if name not in BUILD_ALLOWLIST}
    assert not not_declared, f"imported at run time but absent from BUILD_ALLOWLIST: {sorted(not_declared)}"


def compose_file() -> dict:
    import yaml  # installed with the app's dependencies (uvicorn[standard])

    return yaml.safe_load((DEMO / "compose.yml").read_text())


def test_compose_keeps_the_worker_and_the_database_off_the_routed_network():
    compose = compose_file()
    services = compose["services"]
    assert services["worker"]["networks"] == ["internal"]
    assert services["postgres"]["networks"] == ["internal"]
    assert compose["networks"]["internal"] == {"internal": True}
    assert [name for name, svc in services.items() if "published" in svc["networks"]] == ["app"]


def test_compose_python_services_drop_privileges_and_report_zero_spend_caps():
    services = compose_file()["services"]
    for name in ("app", "worker"):
        assert services[name]["cap_drop"] == ["ALL"], name
        assert services[name]["security_opt"] == ["no-new-privileges:true"], name
        assert services[name]["environment"]["MODEL_DAILY_CAP_USD"] == "0", name
        assert services[name]["environment"]["MODEL_TOTAL_CAP_USD"] == "0", name


def test_compose_publishes_only_the_app_and_only_on_loopback():
    text = (DEMO / "compose.yml").read_text()
    assert re.search(r"^name: keviq-demo$", text, re.M)
    assert "keviq-live" not in text
    ports = re.findall(r"^\s+- \"([^\"]+)\"$", text.split("ports:", 1)[1].split("\n\n", 1)[0], re.M)
    assert ports == ["127.0.0.1:${DEMO_APP_PORT:?set DEMO_APP_PORT in deploy/demo/.env}:8080"]
    assert text.count("ports:") == 1
    for taken in ("8000", "8001", "55433", ":80:", ":443:"):
        assert taken not in text
    # the credentials reach the containers, so the guard can see and refuse them
    assert "MODEL_API_KEY: ${MODEL_API_KEY:-}" in text
    assert "FACEBOOK_PAGE_TOKEN: ${FACEBOOK_PAGE_TOKEN:-}" in text


def test_nginx_template_streams_sse_and_strips_authorization():
    text = (DEMO / "nginx-demo.conf.template").read_text()
    for needle in (
        "proxy_http_version 1.1;",
        "proxy_buffering off;",
        'proxy_set_header Connection "";',
        # the app refuses seller actions when an Authorization header arrives (app/cards_api.py)
        'proxy_set_header Authorization "";',
        "auth_basic_user_file",
        "/etc/letsencrypt/live/DEMO_HOST/fullchain.pem",
        "proxy_pass http://127.0.0.1:DEMO_APP_PORT;",
        "root /var/www/certbot;",
        "Strict-Transport-Security",
    ):
        assert needle in text, needle
    assert "preload" not in text
    assert re.search(r"proxy_read_timeout\s+\d+[hm];", text)


# --- nginx vhost, parsed into blocks (comments dropped) -----------------------------------------------------


def nginx_blocks(text: str) -> list:
    """[(args, children)] for each directive; args[0] is the directive name, quotes removed."""
    tokens = re.findall(r'"[^"]*"|[{};]|[^\s{};"]+', re.sub(r"#[^\n]*", "", text))
    stack: list = [[]]
    args: list = []
    for token in tokens:
        if token == ";":
            stack[-1].append((args, []))
            args = []
        elif token == "{":
            block: list = []
            stack[-1].append((args, block))
            stack.append(block)
            args = []
        elif token == "}":
            stack.pop()
        else:
            args.append(token.strip('"'))
    assert len(stack) == 1 and not args, "unbalanced template"
    return stack[0]


def nginx_template() -> list:
    return nginx_blocks((DEMO / "nginx-demo.conf.template").read_text())


def server_on(port: str) -> list:
    servers = [children for args, children in nginx_template() if args == ["server"]]
    found = [s for s in servers if any(args[:2] == ["listen", port] for args, _ in s)]
    assert len(found) == 1, port
    return found[0]


def walk(block: list, path: tuple = ()):
    for args, children in block:
        yield path, args
        yield from walk(children, path + (tuple(args),))


def test_nginx_443_server_asks_for_the_login_everywhere_but_healthz():
    server = server_on("443")
    assert ["auth_basic", "Keviq demo"] in [args for args, _ in server]
    assert ["auth_basic_user_file", "/etc/nginx/keviq-demo.htpasswd"] in [args for args, _ in server]
    nested = [(path, args) for path, args in walk(server) if path and args[0] == "auth_basic"]
    assert nested == [((("location", "=", "/healthz"),), ["auth_basic", "off"])]


def test_nginx_event_intake_is_read_only_through_the_public_url():
    server = server_on("443")
    pattern = "^/api/sessions/[^/]+/events$"
    intake = [children for args, children in server if args == ["location", "~", pattern]]
    assert len(intake) == 1
    assert (["limit_except", "GET"], [(["deny", "all"], [])]) in intake[0]
    assert re.match(pattern, "/api/sessions/demo1-judge1/events")


def test_nginx_limits_the_rate_hides_its_version_and_pins_tls():
    top = nginx_template()
    # The file is included inside the host's http {} block: nothing at the top level may change other vhosts.
    assert sorted({args[0] for args, _ in top}) == ["limit_req_zone", "server"]
    assert ["limit_req_zone", "$binary_remote_addr", "zone=keviq_demo:1m", "rate=10r/s"] in [
        a for a, _ in top
    ]
    server = [args for args, _ in server_on("443")]
    for directive in (
        ["limit_req", "zone=keviq_demo", "burst=40", "nodelay"],
        ["limit_req_status", "429"],
        ["server_tokens", "off"],
        ["ssl_protocols", "TLSv1.2", "TLSv1.3"],
    ):
        assert directive in server, directive
    assert ["server_tokens", "off"] in [args for args, _ in server_on("80")]
    everything = [args for _, args in walk(top)]
    assert not [args for args in everything if "default_server" in args]
    assert not [args for path, args in walk(server_on("443")) if path and args[0] == "limit_req"]


def test_nginx_stage_one_is_port_80_only():
    # README step 3 installs the part above the marker before the certificate exists.
    text = (DEMO / "nginx-demo.conf.template").read_text()
    stage_one = text.split("# ----- BEGIN 443", 1)[0]
    listens = [args for _, args in walk(nginx_blocks(stage_one)) if args[0] == "listen"]
    assert listens == [["listen", "80"]]
