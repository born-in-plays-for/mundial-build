#!/usr/bin/env bash
# update_status.sh — refresh tournament elimination status and publish it.
#
# Runs the "team status refresh only" recipe from pipeline/CLAUDE.md
# (fetch_team_status.py -> load.py -> export.py), then commits/pushes only
# if data/v2/status.json actually changed — submodule first, then the
# pointer bump here, per pipeline/CLAUDE.md's "Commit workflow".
set -euo pipefail
trap 'echo "update_status.sh: FAILED at line $LINENO" >&2' ERR

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d data/.git ]] && [[ ! -f data/.git ]]; then
    echo "update_status.sh: data/ isn't a checked-out submodule (run 'git submodule update --init')." >&2
    exit 1
fi

echo "==> Fetching fixture results and recomputing eliminations..."
python3 pipeline/fetch_team_status.py

echo "==> Rebuilding the relational database..."
python3 pipeline/load.py

echo "==> Exporting frontend-facing data..."
python3 pipeline/export.py

if git -C data diff --quiet -- v2/status.json; then
    echo "==> No change in data/v2/status.json — nothing to commit."
    exit 0
fi

today="$(date +%Y-%m-%d)"

echo "==> Committing in the data submodule..."
git -C data add v2/status.json
git -C data commit -m "Update team status — ${today}"

echo "==> Pushing data submodule..."
git -C data push

echo "==> Bumping submodule pointer + pipeline/team_status.json..."
git add data pipeline/team_status.json
git commit -m "chore: bump mundial-data submodule — team status update ${today}"

echo "==> Pushing mundial-build..."
git push

echo "==> Done."
