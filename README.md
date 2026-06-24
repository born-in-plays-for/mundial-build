# mundial-build

Data pipeline, scripts, and dev tooling for [mundial.cthiebaud.com](https://mundial.cthiebaud.com/).

This repo contains everything needed to **build** the data and assets for the [cthiebaud/mundial](https://github.com/cthiebaud/mundial) runtime repo. It is not deployed — the output files (JSON, CSV) are committed to the `mundial` repo.

## Repositories

| Repo | Content | Deploys to |
|---|---|---|
| [cthiebaud/mundial](https://github.com/cthiebaud/mundial) | Static frontend (HTML, JS, CSS, JSON) | GitHub Pages |
| [cthiebaud/mundial-server](https://github.com/cthiebaud/mundial-server) | Backend (Flask, admin, WebSocket) | Runs locally (+ ngrok) |
| **cthiebaud/mundial-build** (this repo) | Data pipeline, scripts, dev tooling | Not deployed |

## Structure

| Directory | Purpose |
|---|---|
| `pipeline/` | Data acquisition scripts and source CSVs — see `pipeline/README.md` |
| `screenshots/` | App screenshots for documentation |
| `quotes_proposals.yaml` | Candidate quotes for the rotating header |

## Usage

Pipeline scripts read from external sources and write output files to a local clone of `mundial`:

```bash
# Assuming mundial is cloned as a sibling directory
pip install requests beautifulsoup4 pandas lxml matplotlib pycountry
python3 pipeline/wc2026_birthplaces.py   # -> ../mundial/pipeline/wc2026_players.csv ... wait
```

See `pipeline/README.md` for full documentation.
