#!/usr/bin/env bash
# Repository hygiene guard: commit messages, commit identities and tracked text stay free of
# tool names, internal labels and personal addresses. Used by `make hygiene`, the commit-msg hook
# and the GitHub workflow. Exit 0 clean, 1 findings, 2 usage.
#
#   scripts/hygiene.sh                    # commits origin/main..HEAD + tracked files
#   scripts/hygiene.sh <base> <head>      # commits base..head + tracked files at head
#   scripts/hygiene.sh --message <file>   # one commit message (git commit-msg hook)
set -u

# Words that never belong in a commit message or a tracked file. README.md carries the one
# sanctioned disclosure of tool-prefixed branch names and is excluded from the file scan.
FORBIDDEN='[Cc]laude|[Cc]odex|[Aa]nthropic|[Oo]pen[Aa][Ii]|[Cc]opilot|ChatGPT|Co-[Aa]uthored-[Bb]y|Generated with|\bHQ[0-9]*\b|\bDEC-[0-9]+|\bAgent [0-9]+\b|\bAstra\b'
# Commit identities: GitHub's private addresses only, never a personal mailbox.
IDENTITY='^[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com$|^noreply@github\.com$'
SKIP_FILES='^(README\.md|scripts/hygiene\.sh)$'

fail=0
note() { echo "hygiene: $*"; }

check_message_text() {  # $1 label, stdin message
  local hits
  hits=$(grep -n -E "$FORBIDDEN" || true)
  if [ -n "$hits" ]; then
    fail=1
    note "forbidden word in commit message $1:"
    echo "$hits" | sed 's/^/    /'
  fi
}

if [ "${1:-}" = "--message" ]; then
  [ -n "${2:-}" ] && [ -f "$2" ] || { echo "usage: $0 --message <file>" >&2; exit 2; }
  check_message_text "(staged)" < <(grep -v '^#' "$2")  # no pipe: the fail flag must survive
  exit $fail
fi

base="${1:-}"
head="${2:-HEAD}"
if [ -z "$base" ]; then
  base=$(git rev-parse --verify -q origin/main || git rev-parse --verify -q main) || base=""
fi
[ $# -le 2 ] || { echo "usage: $0 [<base> <head>] | --message <file>" >&2; exit 2; }

if [ -n "$base" ] && git rev-parse --verify -q "$base" >/dev/null; then
  range="$base..$head"
  for sha in $(git rev-list "$range"); do
    check_message_text "$(git rev-parse --short "$sha")" < <(git log -1 --format='%B' "$sha")
    while IFS='|' read -r author committer; do
      for addr in "$author" "$committer"; do
        if ! printf '%s' "$addr" | grep -q -E "$IDENTITY"; then
          fail=1
          note "commit $(git rev-parse --short "$sha") uses a personal address: $addr"
        fi
      done
    done < <(git log -1 --format='%ae|%ce' "$sha")
  done
  note "checked $(git rev-list --count "$range") commit(s) in $range"
else
  note "no base ref found; commit range skipped"
fi

if [ -n "${GITHUB_HEAD_REF:-}" ] && printf '%s' "$GITHUB_HEAD_REF" | grep -q -E '^(claude|codex|copilot|cursor)/'; then
  echo "::warning::branch '$GITHUB_HEAD_REF' carries a tool prefix; merge with the written PR title so it never enters history"
fi

hits=$(git ls-files | grep -v -E "$SKIP_FILES" | tr '\n' '\0' | xargs -0 grep -I -n -E "$FORBIDDEN" 2>/dev/null || true)
if [ -n "$hits" ]; then
  fail=1
  note "forbidden word in tracked files:"
  echo "$hits" | sed 's/^/    /'
fi

if [ $fail -eq 0 ]; then note "clean"; fi
exit $fail
