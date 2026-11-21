#!/bin/sh
# Keep unfinished narration takes between scheduled runs, on this repository's "wip" branch (never on main).
# Run from inside a clone of this repository:
#   sh tools/take_cache.sh pull DIR   -> DIR/cache holds the saved takes (empty if none yet)
#   sh tools/take_cache.sh push DIR   -> saves DIR/cache to the wip branch as one fresh commit, so it stays small
set -e
cmd=$1; dir=$(mkdir -p "$2" && cd "$2" && pwd); repo=$(git rev-parse --show-toplevel)
G="git -c user.name=Claude -c user.email=noreply@anthropic.com"
if [ "$cmd" = pull ]; then
  rm -rf "$dir/cache"; mkdir -p "$dir/cache"
  if git -C "$repo" fetch -q origin wip 2>/dev/null; then
    git -C "$repo" archive FETCH_HEAD cache | tar -x -C "$dir" || true
  fi
  echo "takes in cache: $(ls "$dir/cache" | grep -c flac || true)"
elif [ "$cmd" = push ]; then
  wt="$dir/.wip_worktree"; rm -rf "$wt"
  git -C "$repo" worktree add -q --detach "$wt"
  cd "$wt"; git checkout -q --orphan wip_tmp; git rm -rqf --cached . >/dev/null 2>&1 || true
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -r "$dir/cache" cache; touch cache/.keep; git add cache
  $G commit -qm "Unfinished narration takes (kept between runs)"
  git push -q -f origin HEAD:wip
  n=$(ls cache | grep -c flac || true)
  cd "$repo"; git worktree remove --force "$wt"; git branch -D -q wip_tmp 2>/dev/null || true
  echo "saved $n takes to the wip branch"
fi
