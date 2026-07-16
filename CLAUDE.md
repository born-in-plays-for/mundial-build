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
`elo_rank.json`, `elo_history.json`, `uk-nations.geojson`, `fixtures.json`,
`kde_risk.json`, `hotspots.json`, and the
pid-keyed `v2/` files (`v2/map.json`, `v2/live.json`, `v2/status.json`,
`v2/discipline.json`, `v2/birthplace.json`,
`v2/wiki_en.json`/`wiki_fr.json`/`wiki_de.json`/`wiki_it.json`/`wiki_es.json`) — see
`pipeline/README.md`'s "Relational model" section for how these are built.
`v2/status.json` carries **eliminated teams only** (`{iso2: {round, date?}}`)
— a team absent from it is still alive. `v2/live.json` also carries a `teams`
key (`{af_team_id: iso2}`, api-football's numeric team id) — this replaced
the standalone `r32_teams.json` frontend fetch in July 2026. `v2/discipline.json`
(`{iso2: {matchesPlayed, foulsCommitted, foulsSuffered, avgFoulsCommitted,
avgFoulsSuffered, yellowCards, redCards, foulsPerCard, stage, eliminated,
byStage}}`, one entry per WC2026 team) is per-team foul/card totals from
api-football's fixture statistics — see `pipeline/README.md`'s "Discipline
stats" section. Top-level fields are the team's latest cumulative totals;
`byStage` gives the SAME cumulative totals frozen at each earlier stage too
(`{stage: {...}}`) — so e.g. a Quarter-finals red card is in
`byStage["Quarter-finals"]` but not `byStage["Round of 16"]`, letting a
client show "figures as of round X" without doing its own running-total math
or leaking a later round's cards into an earlier one.
**Not yet wired into `update_fixtures.sh`** — stays stale between manual
`pipeline/fetch_discipline_stats.py` runs even as fixtures/status auto-refresh.

`kde_risk.json` + `hotspots.json` (`pipeline/kde_risk.py`) are a "talent
production" relative-risk map layer — a population-normalized surface
answering "does this place produce more WC2026 talent than its population
would predict," not raw player density (which would just track megacity
population). `kde_risk.json` is `{bandwidthKm, resolutionDeg, bbox, nx, ny,
source, values}`, a row-major `log2(relative risk)` grid (`null` = masked,
population too sparse to be meaningful); `hotspots.json` is
`[{name, country, lon, lat, players, log2Risk}, ...]`, local maxima of that
grid snapped to real WC2026 birth cities. See `pipeline/README.md`'s "KDE
talent-production surface" section for the full methodology — notably why
São Paulo comes out near/below the global rate despite having several
notable players (its population is so large that isn't a statistical
outlier — a deliberate, verified property of population normalization, not
a bug). Written straight to the submodule like `fixtures.json`/
`elo_rank.json`, no `v2/` relational routing. **Not wired into
`update_fixtures.sh`** — only needs re-running after a squad re-scrape.

`v2/birthplace.json` (`{pid: {city, lat, lon, population?, actualCityName?}}`)
is a geocoded birth city per player/coach, for the frontend's "all players"
table to plot birthplaces on the map — `pipeline/wc2026_birthplaces.py`/
`wc2026_coaches.py` already scrape a `birth_city` string from
Wikipedia/Wikidata (it just never used to leave the CSVs);
`pipeline/geocode_birthplaces.py` resolves that string to lat/lon via
OpenStreetMap's Nominatim, caching results in the committed
`pipeline/geocode_cache.json` (rate-limited to 1 req/s, so not cheap to
redo). Best-effort — a person with no scraped city, or a city Nominatim
couldn't resolve, is simply absent from the file rather than carrying a null
lat/lon. `population`, when present, is a STRING — Nominatim's own OSM
`population` extratag for that same resolved place, verbatim, not coerced
to a number (the tag isn't reliably numeric and nothing consumes it
arithmetically) — omitted, not null, for the majority of places that don't
carry the tag; see `pipeline/README.md`'s "Birthplace geocoding" section
for why coordinate-matching against GeoNames was deliberately rejected in
favor of this. `city` has always been the ORIGINAL scraped string, even
when it's actually a sub-city administrative unit rather than a plain city
name ("12th arrondissement of Paris", "Bodø Municipality") — `actualCityName`
now carries the plain form ("Paris", "Bodø") separately, present only when
`city` actually has that kind of qualifier. **Not wired into any auto-refresh
script** — squads don't change mid-tournament, so re-run
`pipeline/geocode_birthplaces.py` only after a squad re-scrape
(`wc2026_birthplaces.py`/`wc2026_coaches.py` + `build_json.py`).

`countries.json` is a pipeline build input — it lives in `pipeline/`, not in the submodule.
GDP/HDI extras live in `extras/` and are fetched only by `pages/wc2026_correlation.html`.

`fixtures.json` (`pipeline/fetch_fixtures.py`) is every WC2026 fixture, past and
planned — raw match-level data (kickoff date, round, teams, score, status), written
straight to the submodule like `elo_rank.json`, not routed through the `v2/`
relational build: nothing in it needs a pid or a person/wiki join. It also carries
a top-level `groups` map (`{"A": [iso2, ...], ..., "L": [...]}`, 12 groups of 4,
alphabetical) and a `group` field (the letter) on every `"Group Stage - N"`
fixture, both sourced from api-football's `/standings` endpoint (the fixtures
endpoint's own `round` field only carries the matchday, never the letter) —
fixed at the draw, not standings order, so this doesn't need re-fetching once
the group stage ends. The same `/standings` call also fills a top-level
`standings` map (`{"A": [{iso2, rank, points, played, win, draw, lose,
goalsFor, goalsAgainst, goalsDiff}, ...], ...}`, rank order) — a full
classement table per group, sourced from api-football's own `rank` so FIFA's
tie-break rules (head-to-head, discipline, ...) aren't reimplemented
client-side. Unlike `groups`, `standings` **does** change match to match, so
it's re-fetched fresh on every `fetch_fixtures.py` run.

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
