#!/bin/sh
# Keep unfinished narration takes between scheduled runs, in this repository's takes/ folder on main.
# Run from inside a clone of this repository:
#   sh tools/take_cache.sh pull DIR   -> DIR/cache holds the saved takes (empty if none yet)
#   sh tools/take_cache.sh push DIR   -> saves DIR/cache into takes/, commits and pushes to main
set -e
cmd=$1; mkdir -p "$2"; dir=$(cd "$2" && pwd); repo=$(git rev-parse --show-toplevel)
G="git -c user.name=Claude -c user.email=noreply@anthropic.com"
if [ "$cmd" = pull ]; then
  git -C "$repo" pull -q --rebase origin main || true
  rm -rf "$dir/cache"; mkdir -p "$dir/cache"
  [ -d "$repo/takes" ] && cp -r "$repo/takes/." "$dir/cache/"
  echo "takes in cache: $(ls "$dir/cache" | grep -c flac || true)"
elif [ "$cmd" = push ]; then
  rm -rf "$repo/takes"; mkdir -p "$repo/takes"; cp -r "$dir/cache/." "$repo/takes/"; touch "$repo/takes/.keep"
  cd "$repo"; git add -A takes
  if git diff --cached --quiet; then echo "takes unchanged"; exit 0; fi
  $G commit -qm "Keep unfinished narration takes between runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M3Z9EXxWEE7teD9H1GGRvN"
  for i in 1 2 3; do git pull -q --rebase --autostash origin main && git push -q origin HEAD:main && break; sleep 5; done
  echo "saved $(ls takes | grep -c flac || true) takes"
fi
