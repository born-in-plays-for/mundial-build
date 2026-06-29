# CLAUDE.md — mundial-build

## Scope rule

**Stay in this repo.** Pipeline work, data commits, and submodule pointer updates for `mundial-data` all live here. When changes in another repo are needed (e.g., `mundial` or `mundial-server`), write a clear prompt for a separate Claude session scoped to that repo — do not edit those repos directly from here.

## Repo layout

| Path | Purpose |
|---|---|
| `pipeline/` | Data pipeline scripts and source CSVs (see `pipeline/README.md`) |
| `data/` | Git submodule → [mundial-data](https://github.com/born-in-plays-for/mundial-data) |
| `infographics/` | Infographic HTML sources |

## Related repos

| Repo | Role |
|---|---|
| [mundial](https://github.com/born-in-plays-for/mundial) | Frontend (HTML/JS/CSS) — has its own submodule pointer to `mundial-data` |
| [mundial-data](https://github.com/born-in-plays-for/mundial-data) | Shared JSON output; `data/` here is a submodule of it |
| [mundial-server](https://github.com/born-in-plays-for/mundial-server) | Backend |

## Core pipeline (squad + country data)

```bash
# Countries (run when rebuilding from scratch — patches run automatically at end)
python3 pipeline/fetch_countries.py      # → data/countries.json (includes patch_uk_nations + patch_kosovo)

# Squad data
python3 pipeline/wc2026_birthplaces.py  # → pipeline/wc2026_players.csv
python3 pipeline/wc2026_coaches.py      # → pipeline/wc2026_coaches.csv
python3 pipeline/build_json.py          # → data/wc2026_map_data.json

# Enrich Wikipedia URLs (slow, ~5 min)
python3 pipeline/add_wiki_urls.py       # → data/wc2026_map_data.json (in-place)
```

## UK home nations & Kosovo

Standard ISO tables don't include UK home nations (ids 8260–8263, alpha2 `gb-eng/gb-sct/gb-wls/gb-nir`) or Kosovo (id 383, `xk`). They are injected by patch scripts:

- `pipeline/patch_uk_nations.py` — patches `data/countries.json` in-place
- `pipeline/patch_kosovo.py` — patches `data/countries.json` and `data/wc2026_elo_rank.json`

Both patches are **automatically called** at the end of `fetch_countries.py`. They can also be run standalone.

## Commit workflow

After pipeline changes, commit in the submodule first, then update the pointer here:

```bash
# 1. Commit in the data submodule
git -C data add <files> && git -C data commit -m "..." && git -C data push

# 2. Bump the submodule pointer in this repo
git add data && git commit -m "chore: bump mundial-data submodule — ..." && git push
```

Then hand off a prompt to a `mundial` session to pull the updated submodule.
