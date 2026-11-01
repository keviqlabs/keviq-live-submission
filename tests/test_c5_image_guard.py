"""Passive image checks preserve the existing image criterion without signaling any process."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("passive,found", [(True, False), (True, True), (False, False)])
def test_image_guard_passive_reports_without_stopping(monkeypatch, capsys, passive, found):
    spec = importlib.util.spec_from_file_location(
        "paid_image_guard", ROOT / "docs/evidence/2026-10-28/scripts/paid-image-5min.py"
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    argv = ["image-guard", "--base-url", "http://127.0.0.1:1", "--run", "synthetic", "--window-s", "0"]
    if passive:
        argv.append("--passive")
    monkeypatch.setattr(sys, "argv", argv)
    events = [{"type": "frame", "event_id": "frame", "seq": 1, "ts_ms": 0}]
    if found:
        events += [
            {"type": "model_call", "event_id": "cut", "seq": 2, "purpose": "card_cutout", "ok": True},
            {
                "type": "card",
                "event_id": "card",
                "seq": 3,
                "card_id": "one",
                "version": 1,
                "image_url": "data:image/png;base64,AAAA",
            },
        ]
    monkeypatch.setattr(guard, "page", lambda *args: events)

    class Client:
        def get(self, *args, **kwargs):
            return self

        def json(self):
            return {}

    monkeypatch.setattr(guard.httpx, "Client", lambda **kwargs: Client())
    stops = []
    monkeypatch.setattr(guard, "stop", lambda run: (stops.append(run) or [], []))
    monkeypatch.setattr(guard.os, "kill", lambda *args: pytest.fail("must not send real signals"))
    assert guard.main() == (0 if found else 1)
    assert stops == (["synthetic"] if not passive and not found else [])
    assert ('"kind": "IMAGE_OK"' if found else '"kind": "IMAGE_MISSING"') in capsys.readouterr().out
