"""The paid wrapper must fail closed when its required image monitor fails. No API or DB is used."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "guard", ["error", "missing", "silent", "hang", "false_marker", "ok_then_error", "ok"]
)
def test_paid_wrapper_requires_a_successful_image_guard(tmp_path, guard):
    for name in ("measure", "data/local/test1", "docs/evidence/2026-10-29", "scripts", "bin", "out"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "measure/c5_bind.py").write_text("")
    (tmp_path / "data/local/test1/labels.json").write_text("{}")
    (tmp_path / "docs/evidence/2026-10-29/rescore-paid-log-config.json").write_text("{}")
    shutil.copy2(ROOT / "scripts/c5_paid_run.sh", tmp_path / "scripts/c5_paid_run.sh")
    (tmp_path / "fake.env").write_text(
        "DATABASE_URL=postgresql://example:example@127.0.0.1:1/example_test\n"
        "MODEL_API_KEY=NONSECRET-TEST-ONLY\n"
    )
    child = tmp_path / "child.py"
    child.write_text("""import os, signal, subprocess, sys, time
from pathlib import Path
role = sys.argv[1]
def term(*args):
    Path("out/" + role + ".stopped").write_text("yes")
    sys.exit(143)
signal.signal(signal.SIGTERM, term)
Path("out/" + role + ".pid").write_text(str(os.getpid()))
if role == "app":
    Path("out/started").touch()
    time.sleep(30)
elif role == "runner":
    leaf = subprocess.Popen([sys.executable, __file__, "leaf"])
    while not Path("out/guard.started").exists():
        time.sleep(0.01)
    if os.environ["TEST_GUARD"] not in ("ok", "hang"):
        # Only the wrapper can stop this run after a failed guard, independent of machine speed.
        time.sleep(30)
    elif os.environ["TEST_GUARD"] == "ok":
        guard_pid = int(Path("out/guard.pid").read_text())
        while True:
            try:
                os.kill(guard_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
    Path("out/runner.finished").touch()
    leaf.wait()
elif role == "leaf":
    while not Path("out/runner.finished").exists():
        time.sleep(0.01)
""")
    guard_script = tmp_path / "guard.py"
    guard_script.write_text("""import os, sys, time
from pathlib import Path
Path("out/guard.pid").write_text(str(os.getpid()))
deadline = time.monotonic() + 3
while not Path("out/leaf.pid").exists() and time.monotonic() < deadline:
    time.sleep(0.01)
Path("out/guard.started").touch()
kind = os.environ["TEST_GUARD"]
if kind == "hang":
    time.sleep(30)
elif kind in ("ok", "ok_then_error"):
    print('{"kind": "IMAGE_OK", "card_id": "synthetic", "version": 2}', flush=True)
    sys.exit(0 if kind == "ok" else 3)
elif kind == "missing":
    print('{"kind": "IMAGE_MISSING"}', flush=True)
    sys.exit(1)
elif kind == "false_marker":
    print('transport failed before IMAGE_OK', flush=True)
elif kind == "error":
    sys.exit(3)
""")
    commands = {
        "git": """#!/bin/bash
case "$1" in
 status) exit 0;;
 log) echo 'Merge pull request #81: Synthetic fixture';;
 rev-parse) echo 'synthetic-head';;
esac
""",
        "curl": """#!/bin/bash
case "$*" in
 *api/cost*) echo '{"session_usd":0.0,"session_cap_usd":8.0,"session_warn_usd":6.0,"daily_cap_usd":30.0,"total_cap_usd":300.0}';;
 *healthz*) [ -f out/started ] || exit 7; echo '{"ok":true}';;
 *) exit 7;;
esac
""",
        "uv": """#!/bin/bash
case "$*" in
 *app.migrate*) exit 0;;
 *uvicorn*) exec python3 child.py app;;
 *paid-image-5min.py*)
   case " $* " in *" --passive "*) exec python3 guard.py;; *) exit 98;; esac;;
 *) exit 99;;
esac
""",
        "caffeinate": "#!/bin/bash\nexec python3 child.py runner\n",
        # The old wrapper's global process lookup must never affect anything outside this fixture.
        "pkill": "#!/bin/bash\necho forbidden-global-lookup >> out/global-lookup\nexit 1\n",
        "sleep": "#!/bin/bash\n/bin/sleep 0.05\n",
    }
    for name, text in commands.items():
        path = tmp_path / "bin" / name
        path.write_text(text)
        path.chmod(0o700)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "MAIN_ENV": str(tmp_path / "fake.env"),
        "OTHER_TODAY_USD": "0",
        "OTHER_TOTAL_USD": "0",
        "TEST_GUARD": guard,
    }
    r = subprocess.run(
        ["bash", "scripts/c5_paid_run.sh", "c5-paid-probe", "out", "probe_test", "18049"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (r.returncode == 0) == (guard == "ok"), r.stdout + r.stderr
    if guard in ("error", "missing", "silent", "false_marker", "ok_then_error"):
        assert not (tmp_path / "out/runner.finished").exists(), "runner continued after guard failed"
        assert (tmp_path / "out/leaf.stopped").exists(), "runner descendant was left active"
    if guard == "ok":
        assert (tmp_path / "out/runner.finished").exists()
    assert not (tmp_path / "out/global-lookup").exists(), "cleanup must target owned process groups only"
    assert (tmp_path / "out/app.stopped").exists()
