# mundial-build

Data pipeline, scripts, and dev tooling for the [Born In, Plays For](https://github.com/born-in-plays-for) project.

This repo scrapes player data, Elo ratings, and economic indicators, then writes JSON output to the `data/` submodule ([mundial-data](https://github.com/born-in-plays-for/mundial-data)).

## Structure

| Directory | Purpose |
|---|---|
| `pipeline/` | Data acquisition scripts and source CSVs — see `pipeline/README.md` |
| `data/` | Git submodule → [mundial-data](https://github.com/born-in-plays-for/mundial-data) (pipeline output) |
| `infographics/` | Infographic HTML sources (social cards) |
| `screenshots/` | App screenshots for documentation |
| `quotes_proposals.yaml` | Candidate quotes for the rotating header |

## Setup

```bash
pip install requests beautifulsoup4 pandas lxml matplotlib pycountry
git submodule update --init
```

## Usage

Pipeline scripts read from external sources and write to the `data/` submodule:

```bash
python3 pipeline/orchestrator.py
```

After running the pipeline, commit and push the data:

```bash
cd data
git add -A && git commit -m "update data" && git push
```

Then update the submodule pointer in [mundial](https://github.com/born-in-plays-for/mundial):

```bash
cd ../mundial/data
git pull origin main
cd ..
git add data && git commit -m "update data submodule" && git push
```

See `pipeline/README.md` for full documentation of individual scripts.

## See also

- [born-in-plays-for](https://github.com/born-in-plays-for) — org overview + architecture diagram
- [mundial](https://github.com/born-in-plays-for/mundial) — frontend
- [mundial-data](https://github.com/born-in-plays-for/mundial-data) — shared data files (submodule)
- [mundial-server](https://github.com/born-in-plays-for/mundial-server) — backend
