#!/bin/sh
# Hosted demo entrypoint: run the start guard, then the service command. Refuses (exit 1) when the guard does.
set -eu
"${PYTHON:-python}" "$(dirname "$0")/guard.py" || exit 1
exec "$@"
