#!/usr/bin/env bash
# update_fixtures.sh — refresh fixture results + tournament elimination
# status, and publish both.
#
# Runs the "Fixtures refresh only" recipe from pipeline/CLAUDE.md
# (fetch_fixtures.py -> load.py -> export.py — one api-football fetch feeds
# both data/fixtures.json and the derived data/v2/status.json), then
# commits/pushes only if either file actually changed — submodule first,
# then the pointer bump here, per pipeline/CLAUDE.md's "Commit workflow".
# Finally pins the sibling `mundial` checkout's data submodule to that exact
# commit (not the branch tip — a floating `--remote` update could pick up a
# later, untested mundial-data commit) and pushes it.
set -euo pipefail
trap 'echo "update_fixtures.sh: FAILED at line $LINENO" >&2' ERR

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MUNDIAL_DIR="$ROOT/../mundial"

if [[ ! -d data/.git ]] && [[ ! -f data/.git ]]; then
    echo "update_fixtures.sh: data/ isn't a checked-out submodule (run 'git submodule update --init')." >&2
    exit 1
fi

if [[ ! -d "$MUNDIAL_DIR/.git" ]]; then
    echo "update_fixtures.sh: sibling mundial checkout not found at $MUNDIAL_DIR." >&2
    exit 1
fi

echo "==> Fetching fixture results..."
python3 pipeline/fetch_fixtures.py

echo "==> Rebuilding the relational database..."
python3 pipeline/load.py

echo "==> Exporting frontend-facing data..."
python3 pipeline/export.py

if git -C data diff --quiet -- fixtures.json v2/status.json; then
    echo "==> No change in data/fixtures.json or data/v2/status.json — nothing to commit."
else
    today="$(date +%Y-%m-%d)"

    echo "==> Committing in the data submodule..."
    git -C data add fixtures.json v2/status.json
    git -C data commit -m "Update fixtures — ${today}"

    echo "==> Pushing data submodule..."
    git -C data push

    echo "==> Bumping submodule pointer..."
    git add data
    git commit -m "chore: bump mundial-data submodule — fixtures update ${today}"

    echo "==> Pushing mundial-build..."
    git push --no-recurse-submodules

    data_commit="$(git -C data rev-parse HEAD)"

    echo "==> Pinning mundial's data submodule to ${data_commit}..."
    git -C "$MUNDIAL_DIR/data" fetch --depth 1 origin "$data_commit"
    git -C "$MUNDIAL_DIR/data" checkout --detach "$data_commit"
    if git -C "$MUNDIAL_DIR" diff --quiet -- data; then
        echo "==> mundial's data pointer already at ${data_commit} — nothing to commit."
    else
        git -C "$MUNDIAL_DIR" add data
        git -C "$MUNDIAL_DIR" commit -m "chore: bump mundial-data submodule — fixtures update ${today}"

        echo "==> Pushing mundial..."
        git -C "$MUNDIAL_DIR" push --no-recurse-submodules
    fi
fi

# ── Sanity check: this repo's data submodule vs what mundial is pinned to.
# The whole point of this script is that these two never silently drift
# apart — so prove it on every run, not just the ones that pushed something.
build_head="$(git -C data rev-parse HEAD)"
mundial_pin="$(git -C "$MUNDIAL_DIR/data" rev-parse HEAD)"

if [[ "$build_head" == "$mundial_pin" ]]; then
cat <<'EOF'

           _____________________
          /                     \
         |    ALL REPOS IN SYNC   |
          \_____________________/
                  \   ^__^
                   \  (oo)\_______
                      (__)\       )\/\
                          ||----w |
                          ||     ||

   ______     ______     ______     ______     ______
  /\  __ \   /\  ___\   /\  == \   /\  ___\   /\  ___\
  \ \  __ \  \ \___  \  \ \  __<  \ \  __\   \ \___  \
   \ \_\ \_\  \/\_____\  \ \_\ \_\ \ \_____\  \/\_____\
    \/_/\/_/   \/_____/   \/_/ /_/  \/_____/   \/_____/

EOF
    echo "   mundial-build/data : ${build_head}"
    echo "   mundial/data        : ${mundial_pin}"
else
cat <<'EOF'

   #     # ###  #####  #     #    #    ####### #### #  #  #
   ##   ## #  # #      ##   ##   # #      #      #   #  #  #
   # # # # #    ##     # # # #  #####     #      #   #  #  #
   #  #  # #  # #      #  #  # #     #    #      #   #  #  #
   #     # ###  ##### #     # #     #    #     ####  #  #  #

   MISMATCH — mundial's data pointer does NOT match mundial-build/data.
EOF
    echo "   mundial-build/data : ${build_head}"
    echo "   mundial/data        : ${mundial_pin}"
    echo
    echo "   Re-run this script, or check for a sibling mundial checkout issue."
    exit 1
fi

echo "==> Done."
