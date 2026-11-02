import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_module_has_no_import_path_to_commerce_or_network():
    allowed = {
        "asyncio",
        "os",
        "re",
        "threading",
        "time",
        "collections",
        "dataclasses",
        "typing",
        "json",
        "sys",
        "argparse",
    }
    for path in (ROOT / "worker/cohost").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(n.name.split(".")[0] in allowed for n in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.level == 1 and node.module in {"controller", "playback"}
                else:
                    assert node.module.split(".")[0] in allowed, path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "__import__"}, path.name


def test_silent_cli_replay_has_only_disclosure_and_approved_card():
    events = [
        {"type": "microphone_ready"},
        {"type": "enable"},
        {"type": "wait", "seconds": 1.01},
        {
            "type": "card",
            "sequence": 1,
            "card": {
                "card_id": "sample",
                "version": 1,
                "name": "Mẫu thử",
                "price_vnd": 100000,
                "approved": True,
                "available_sizes": ["M"],
            },
        },
        {"type": "turn"},
    ]
    result = subprocess.run(
        [sys.executable, "-m", "worker.cohost", "--mode", "replay"],
        input="\n".join(json.dumps(e) for e in events) + "\n",
        text=True,
        capture_output=True,
        env=dict(os.environ, COHOST_ENABLED="1", MODEL_API_KEY=""),
        cwd=ROOT,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(lines) == 2
    assert lines[0]["text"] == "Tôi là trợ lý AI hỗ trợ giới thiệu sản phẩm."
    assert lines[1]["text"] == "Mẫu thử, giá 100,000 đồng. Size còn: M."


def test_unknown_input_stops_without_echoing_payload():
    result = subprocess.run(
        [sys.executable, "-m", "worker.cohost", "--mode", "demo"],
        input=json.dumps({"type": "unknown-private-input"}) + "\n",
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=5,
    )
    assert result.returncode == 2
    assert "unknown-private-input" not in result.stderr
    assert not result.stdout
