"""Hosted demo deploy files (deploy/demo/): the start guard refuses model and Page credentials, the compose stack
publishes the app on loopback only, and the nginx template keeps SSE streaming and strips the browser's
Authorization header before the app sees it."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "deploy" / "demo"
GUARD = DEMO / "guard.py"
ENTRYPOINT = DEMO / "entrypoint.sh"


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
@pytest.mark.parametrize("value", ["x", "replace-me", " "])
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
