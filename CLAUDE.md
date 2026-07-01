# CLAUDE.md — mundial-build

## Scope rule

**Stay in this repo.** Pipeline work, data commits, and submodule pointer updates for `mundial-data` all live here. When changes in another repo are needed (e.g., `mundial` or `mundial-server`), write a clear prompt for a separate Claude session scoped to that repo — do not edit those repos directly from here.

## Repo layout

| Path | Purpose |
|---|---|
| `pipeline/` | Data pipeline scripts, source CSVs, `countries.json` (build input), and `country_aliases.json`/`country_registry.py` (canonical country-identity resolution — see below) |
| `data/` | Git submodule → [mundial-data](https://github.com/born-in-plays-for/mundial-data) — core frontend assets only |
| `extras/` | Supplementary data not consumed by the main map (`wc2026_gdp.json`, `wc2026_gdp_pc_ppp.json`, `wc2026_hdi.json`) |
| `pages/` | Self-contained HTML pages hosted from this repo (`wc2026_correlation.html`) |
| `infographics/` | Infographic HTML sources |

### `data/` submodule — what belongs there

Only files consumed directly by the `mundial` frontend map belong in the submodule:
`map_data.json`, `elo_rank.json`, `elo_history.json`, `r32_teams.json`, `uk-nations.geojson`.

`countries.json` is a pipeline build input — it lives in `pipeline/`, not in the submodule.
GDP/HDI extras live in `extras/` and are fetched only by `pages/wc2026_correlation.html`.

## Related repos

| Repo | Role |
|---|---|
| [mundial](https://github.com/born-in-plays-for/mundial) | Frontend (HTML/JS/CSS) — has its own submodule pointer to `mundial-data` |
| [mundial-data](https://github.com/born-in-plays-for/mundial-data) | Shared JSON output; `data/` here is a submodule of it |
| [mundial-server](https://github.com/born-in-plays-for/mundial-server) | Backend |

## Core pipeline (squad + country data)

```bash
# Countries (run when rebuilding from scratch — patches run automatically at end)
python3 pipeline/fetch_countries.py      # → pipeline/countries.json (includes patch_uk_nations + patch_kosovo)

# Squad data
python3 pipeline/wc2026_birthplaces.py  # → pipeline/wc2026_players.csv
python3 pipeline/wc2026_coaches.py      # → pipeline/wc2026_coaches.csv
python3 pipeline/build_json.py          # → data/map_data.json

# Enrich Wikipedia URLs (slow, ~5 min)
python3 pipeline/add_wiki_urls.py       # → data/map_data.json (in-place)

# Coverage gate — run after the pipeline, before committing.
# Fails loudly if any upstream country name/spelling variant doesn't resolve
# through pipeline/country_aliases.json, or a current WC2026 nation is missing
# from a CSV.
python3 pipeline/validate_country_coverage.py

# Extras (only needed for pages/ standalone charts)
python3 extras/build_elo_history.py  # → extras/elo_history.json  (for pages/wc2026_elo_history.html)
python3 extras/add_gdp.py            # → extras/gdp.json                   (for pages/wc2026_correlation.html)
python3 extras/add_gdp_pc_ppp.py     # → extras/gdp_pc_ppp.json            (for pages/wc2026_correlation.html)
python3 extras/add_hdi.py            # → extras/hdi.json                   (for pages/wc2026_correlation.html)
```

## UK home nations & Kosovo

Standard ISO tables don't include UK home nations (ids 8260–8263, alpha2 `gb-eng/gb-sct/gb-wls/gb-nir`) or Kosovo (id 383, `xk`). They are injected by patch scripts:

- `pipeline/patch_uk_nations.py` — patches `pipeline/countries.json` in-place
- `pipeline/patch_kosovo.py` — patches `pipeline/countries.json` and `data/elo_rank.json`

Both patches are **automatically called** at the end of `fetch_countries.py`. They can also be run standalone.

## Country identity (iso2 is the join key)

The same country shows up under different free-text spellings across upstream
sources (Wikipedia, Wikidata, eloratings.net, api-football, World Bank, …) —
e.g. DR Congo alone appears as `"DR Congo"`, `"Congo, The Democratic Republic
of the"`, and `"Congo DR"` depending on the source. `pipeline/country_registry.py`
is the single place that resolves a raw name to a canonical lowercase iso2
(`resolve_iso2()`), and the single place output scripts get a display name
from (`canonical_name()` / `display_name()`). Its data lives in
`pipeline/country_aliases.json` (known spelling variants, keyed by iso2, plus
the current 48-team WC2026 field).

An unrecognized name raises `UnknownCountryError` instead of silently falling
through to a heuristic — add the missing spelling to `country_aliases.json`
rather than adding another local override dict. `pipeline/build_json.py`,
`pipeline/update_elo_rankings.py`, `pipeline/fetch_r32_teams.py`,
`pipeline/wc2026_birthplaces.py`, and `pipeline/wc2026_coaches.py` all resolve
through this module. `extras/` scripts (GDP/HDI/elo_history, which only feed
`pages/` charts) still use their own independent name maps — not yet migrated.

## Commit workflow

After pipeline changes, commit in the submodule first, then update the pointer here:

```bash
# 1. Commit in the data submodule
git -C data add <files> && git -C data commit -m "..." && git -C data push

# 2. Bump the submodule pointer in this repo
git add data && git commit -m "chore: bump mundial-data submodule — ..." && git push
```

Then hand off a prompt to a `mundial` session to pull the updated submodule.
