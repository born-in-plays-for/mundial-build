# CLAUDE.md — mundial-build

## Scope rule

**Stay in this repo.** Pipeline work, data commits, and submodule pointer updates for `mundial-data` all live here. When changes in another repo are needed (e.g., `mundial` or `mundial-server`), write a clear prompt for a separate Claude session scoped to that repo — do not edit those repos directly from here.

## Repo layout

| Path | Purpose |
|---|---|
| `pipeline/` | Data pipeline scripts, source CSVs, `countries.json` (build input), and `country_aliases.json`/`country_registry.py` (canonical country-identity resolution). See [`pipeline/CLAUDE.md`](pipeline/CLAUDE.md) for the build sequence and pipeline-specific rules. |
| `data/` | Git submodule → [mundial-data](https://github.com/born-in-plays-for/mundial-data) — core frontend assets only |
| `extras/` | Supplementary data not consumed by the main map (`wc2026_gdp.json`, `wc2026_gdp_pc_ppp.json`, `wc2026_hdi.json`) |
| `pages/` | Self-contained HTML pages hosted from this repo (`wc2026_correlation.html`) |
| `infographics/` | Infographic HTML sources |

### `data/` submodule — what belongs there

Only files consumed directly by the `mundial` frontend belong in the submodule:
`elo_rank.json`, `elo_history.json`, `uk-nations.geojson`, `fixtures.json`, and the
pid-keyed `v2/` files (`v2/map.json`, `v2/live.json`, `v2/status.json`,
`v2/wiki_en.json`/`wiki_fr.json`/`wiki_de.json`/`wiki_it.json`/`wiki_es.json`) — see
`pipeline/README.md`'s "Relational model" section for how these are built.
`v2/status.json` carries **eliminated teams only** (`{iso2: {round, date?}}`)
— a team absent from it is still alive. `v2/live.json` also carries a `teams`
key (`{af_team_id: iso2}`, api-football's numeric team id) — this replaced
the standalone `r32_teams.json` frontend fetch in July 2026.

`countries.json` is a pipeline build input — it lives in `pipeline/`, not in the submodule.
GDP/HDI extras live in `extras/` and are fetched only by `pages/wc2026_correlation.html`.

`fixtures.json` (`pipeline/fetch_fixtures.py`) is every WC2026 fixture, past and
planned — raw match-level data (kickoff date, round, teams, score, status), written
straight to the submodule like `elo_rank.json`, not routed through the `v2/`
relational build: nothing in it needs a pid or a person/wiki join.

The older `map_data.json`, `player_wiki.json`, non-`v2` `wiki_<lang>.json`, and
`r32_teams.json` files are **pipeline-internal intermediates now, not
frontend-facing** — the frontend migrated to `v2/` in July 2026. They live in
`pipeline/`, not the submodule, and are committed there (not gitignored)
because producing them hits live external APIs (Wikipedia, api-football)
and isn't cheap to redo casually — same reasoning as the committed `wc2026_players.csv`.

## Related repos

| Repo | Role |
|---|---|
| [mundial](https://github.com/born-in-plays-for/mundial) | Frontend (HTML/JS/CSS) — has its own submodule pointer to `mundial-data` |
| [mundial-data](https://github.com/born-in-plays-for/mundial-data) | Shared JSON output; `data/` here is a submodule of it |
| [mundial-server](https://github.com/born-in-plays-for/mundial-server) | Backend |
