# CLAUDE.md — pipeline/

Pipeline-specific rules and the canonical build sequence. Loaded alongside
the root [`../CLAUDE.md`](../CLAUDE.md) — that one has the scope rule,
repo layout, and related-repos map; this one has everything that only
matters when actually working in `pipeline/`.

## Core pipeline (squad + country data)

```bash
# Countries (run when rebuilding from scratch — patches run automatically at end)
python3 pipeline/fetch_countries.py      # → pipeline/countries.json (includes patch_uk_nations + patch_kosovo)

# Squad data
python3 pipeline/wc2026_birthplaces.py  # → pipeline/wc2026_players.csv
python3 pipeline/wc2026_coaches.py      # → pipeline/wc2026_coaches.csv
python3 pipeline/build_json.py          # → pipeline/map_data.json

# Enrich Wikipedia identity (slow, ~5 min)
python3 pipeline/add_wiki_urls.py       # → pipeline/map_data.json (in-place) + pipeline/wiki_<lang>.json ×5

# Coverage gate — run after the pipeline, before committing.
# Fails loudly if any upstream country name/spelling variant doesn't resolve
# through pipeline/country_aliases.json, or a current WC2026 nation is missing
# from a CSV.
python3 pipeline/validate_country_coverage.py

# Elo ratings (no key needed; re-patches Kosovo automatically)
python3 pipeline/update_elo_rankings.py  # → data/elo_rank.json

# Round of 32 teams + player/coach identity for the live-match page (need API_FOOTBALL_KEY)
python3 pipeline/fetch_r32_teams.py     # → pipeline/r32_teams.json
python3 pipeline/build_player_wiki.py   # → pipeline/player_wiki.json

# Every WC2026 fixture, past and planned (needs API_FOOTBALL_KEY) — re-run whenever
# fixtures are added or results come in. Writes straight to data/, no load.py/export.py step.
# load.py also derives tournament elimination status (data/v2/status.json) from this
# file directly, so there's no separate team-status fetch.
python3 pipeline/fetch_fixtures.py      # → data/fixtures.json

# Relational model (runs AFTER the above; see pipeline/README.md "Relational model")
# map_data.json / player_wiki.json / wiki_<lang>.json / data/fixtures.json above are this
# step's inputs — pipeline-internal (except data/fixtures.json, which is also
# frontend-facing on its own), not what the frontend fetches from the DB side.
python3 pipeline/load.py    # inputs → pipeline/mundial.db (gitignored) + person_registry.csv
python3 pipeline/export.py  # mundial.db → data/v2/ pid-keyed view files, atomically — THE
                             # frontend-facing output of this whole pipeline

# Extras (only needed for pages/ standalone charts)
python3 extras/build_elo_history.py  # → extras/elo_history.json  (for pages/wc2026_elo_history.html)
python3 extras/add_gdp.py            # → extras/gdp.json                   (for pages/wc2026_correlation.html)
python3 extras/add_gdp_pc_ppp.py     # → extras/gdp_pc_ppp.json            (for pages/wc2026_correlation.html)
python3 extras/add_hdi.py            # → extras/hdi.json                   (for pages/wc2026_correlation.html)
```

`fetch_r32_teams.py`, `build_player_wiki.py`, and `fetch_fixtures.py` all
need an api-football key — set `API_FOOTBALL_KEY` in `.env` (auto-loaded) or
pass `--key` to `fetch_r32_teams.py`.

This is the canonical command sequence — `pipeline/README.md`'s "Core
pipeline" section points back here rather than repeating it, so keep this
one copy current when a script's invocation or output path changes.

### Fixtures refresh only (also picks up new eliminations)

Re-running the full sequence above isn't necessary just to pick up newly
finished fixtures — squads, wiki identity, and Elo don't change between
matches. This subset is enough (needs `API_FOOTBALL_KEY`); `update_fixtures.sh`
automates it end to end, including the commit workflow below:

```bash
python3 pipeline/fetch_fixtures.py   # → data/fixtures.json (re-fetches every fixture, scores + status)
python3 pipeline/load.py             # → pipeline/mundial.db (also derives eliminations from fixtures.json)
python3 pipeline/export.py           # → data/v2/*.json, incl. status.json — the frontend-facing output
```

Then follow "Commit workflow" below — commit `data/fixtures.json` and
`data/v2/status.json` together, since both come from this one fetch.

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

## Birthplace overrides (`wc2026_birthplaces.py`)

`wc2026_birthplaces.py` regenerates `wc2026_players.csv` from scratch every
run (Wikipedia squads table → Wikidata P19 → per-player Wikipedia infobox
fallback), so any hand-edit made directly on the CSV gets clobbered on the
next rebuild. For players none of those three sources have a birthplace for,
add an entry to `pipeline/birthplace_overrides.json`, keyed by nation then
exact player name as it appears on the WC2026 squads page:

```json
{ "Egypt": { "Tarek Alaa": { "birth_city": "Cairo", "birth_country": "Egypt", "_note": "..." } } }
```

`apply_manual_overrides()` applies it as the last step before the CSV is
written, and only *fills blanks* — the automated scrape always wins if it
already found a value. If a later rerun's scrape disagrees with an override
(rather than just being empty), that's a signal the override may be stale
(or genuinely wrong), so it's left as a printed warning to resolve by hand
rather than silently overwritten either direction. Same pattern as
`player_aliases_manual.json` below. A name in the overrides file with no
matching row in that run's scrape also prints a warning instead of failing
silently.

## Player/coach identity (api-football id is the join key)

Same problem one level down: a player's Wikipedia name doesn't always match
what api-football renders for the same person in live lineup data (and
api-football itself isn't even consistent about it across fixtures).
`pipeline/build_player_wiki.py` resolves this once, at build time, via a
7-tier matcher plus `pipeline/player_aliases_confirmed.json` (hand-verified
pairs, keyed by api-football's numeric id so a future name-string change
doesn't break it) — exporting `pipeline/player_wiki.json`, keyed by iso2 then
by that numeric id. `pipeline/load.py` re-keys this by `pid` (see
`pipeline/README.md`'s "Relational model" section) into `data/v2/live.json`,
which `mundial/wc2026_live.html` looks players/coaches up in directly by
`player.id`/`coach.id` — no name matching client-side. Residual unresolved
names land in `pipeline/player_aliases_manual.json`; check its `_note` field
before assuming an entry is a bug (some are genuine non-issues — an injured
player who hasn't played, a coaching change mid-tournament). Full details,
including the duplicate-name safety net, in `pipeline/README.md`.

Wikipedia links themselves are resolved via a shared `wikiTitle` (EN title)
field on every player/coach in `pipeline/map_data.json` and
`pipeline/player_wiki.json`, joined against 5 per-language files
(`pipeline/wiki_en.json` etc., each `{urlTemplate, titles}`, keyed by
`wikiTitle`). `pipeline/load.py` re-keys this by `pid` into
`data/v2/wiki_<lang>.json` (`titles` as a pid-indexed array) — a client
fetches only the one language it needs, not all 5. See `pipeline/README.md`'s
"Wiki data" and "Relational model" sections for the exact shapes.

## Commit workflow

After pipeline changes, commit in the submodule first, then update the pointer here:

```bash
# 1. Commit in the data submodule
git -C data add <files> && git -C data commit -m "..." && git -C data push

# 2. Bump the submodule pointer in this repo
git add data && git commit -m "chore: bump mundial-data submodule — ..." && git push
```

Then hand off a prompt to a `mundial` session to pull the updated submodule.
