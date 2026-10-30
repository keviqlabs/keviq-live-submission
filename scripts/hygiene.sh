#!/usr/bin/env bash
# Repository hygiene guard: commit messages, commit identities, tracked text and file names stay
# free of tool names, internal labels and personal addresses. Used by `make hygiene`, the
# commit-msg hook and the GitHub workflow. Exit 0 clean, 1 findings, 2 usage or no base to compare.
#
#   scripts/hygiene.sh                    # commits origin/main..HEAD + tracked files at HEAD
#   scripts/hygiene.sh <base> <head>      # commits base..head + tracked files at head
#   scripts/hygiene.sh --message <file>   # one commit message (git commit-msg hook)
#
# Binary file contents are not scanned (git grep -I); file names and text contents are.
set -u

# Words that never belong in a commit message, a tracked file or a file name.
FORBIDDEN='[Cc]laude|[Cc]odex|[Aa]nthropic|[Oo]pen[Aa][Ii]|[Cc]opilot|ChatGPT|Co-[Aa]uthored-[Bb]y|Generated with|\bHQ[0-9]*\b|\bDEC-[0-9]+|\bAgent [0-9]+\b|\bAstra\b'
# Commit identities: GitHub's private addresses only, never a personal mailbox.
IDENTITY='^[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com$|^noreply@github\.com$'
# Excluded from the content scan: the guard itself, its tests and README.md (which carries the
# one sanctioned disclosure of tool-prefixed branch names). File names are still checked.
EXCLUDE=(':(exclude)README.md' ':(exclude)scripts/hygiene.sh' ':(exclude)tests/test_hygiene_script.py')

fail=0
note() { echo "hygiene: $*"; }
usage() { echo "usage: $0 [<base> <head>] | --message <file>" >&2; exit 2; }

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
  [ $# -eq 2 ] && [ -f "$2" ] || usage
  # Only git's own editor template (from "# Please enter the commit message" or the scissors
  # line onward) is skipped; a "#" line the author typed with -m or -F is part of the message.
  check_message_text "(staged)" < <(awk '/^# Please enter the commit message|^# -+ >8 -+$/ { exit } { print }' "$2")
  exit $fail
fi

case "${1:-}" in -*) usage ;; esac
[ $# -le 2 ] || usage

resolve() { git rev-parse --verify -q "$1^{commit}" 2>/dev/null; }  # full sha or non-zero
not_a_commit() { echo "hygiene: '$1' is not a commit" >&2; exit 2; }

if [ $# -eq 0 ]; then
  base=$(resolve origin/main || resolve main) \
    || { echo "hygiene: no origin/main or main to compare with; fetch main first" >&2; exit 2; }
  head=$(resolve HEAD) || not_a_commit HEAD
else
  [ $# -eq 2 ] || usage
  base=$(resolve "$1") || not_a_commit "$1"
  head=$(resolve "$2") || not_a_commit "$2"
fi

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
note "checked $(git rev-list --count "$range") commit(s) in $(git rev-parse --short "$base")..$(git rev-parse --short "$head")"

if [ -n "${GITHUB_HEAD_REF:-}" ] && printf '%s' "$GITHUB_HEAD_REF" | grep -q -E '^(claude|codex|copilot|cursor)/'; then
  echo "::warning::branch '$GITHUB_HEAD_REF' carries a tool prefix; merge with the written PR title so it never enters history"
fi

# File names at head (every path, EXCLUDE included).
hits=$(git ls-tree -r --name-only -z "$head" | grep -z -E "$FORBIDDEN" | tr '\0' '\n' || true)
if [ -n "$hits" ]; then
  fail=1
  note "forbidden word in file name:"
  echo "$hits" | sed 's/^/    /'
fi

# Text contents at head. git grep reads the tree, so a path git would quote in `ls-files` is read
# like any other; quotePath is off so the hit names the file as it is. The leading "<sha>:" is dropped.
hits=$(git -c core.quotePath=false grep -I -n -E "$FORBIDDEN" "$head" -- . "${EXCLUDE[@]}" | sed "s/^[0-9a-f]*://" || true)
if [ -n "$hits" ]; then
  fail=1
  note "forbidden word in tracked files:"
  echo "$hits" | sed 's/^/    /'
fi

if [ $fail -eq 0 ]; then note "clean"; fi
exit $fail
