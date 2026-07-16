Context for a `mundial` (frontend) session: the full player/coach data model shipped
by `mundial-build`'s pipeline, entity by entity, from the player outward through every
entity it's connected to. Canonical source is `mundial-build/pipeline/schema.sql`
(the `person`/`country`/`city`/... tables + `view_*` exports) — this is a snapshot of
it, current as of the `position` field being added. If a field described here doesn't
match what you actually see in a fetched file, trust the file — the pipeline may have
moved on since this was written.

All files below live under the `data/` submodule (`mundial-data`), which `mundial`
fetches directly. `pid` is the cross-file join key for a player/coach — same integer
in every file, assigned once in `mundial-build/pipeline/person_registry.csv` and never
reused.

## person (the player or coach) — join key: `pid`

Own attributes:
- `name` — display name (EN-Wikipedia-derived)
- `role` — `player` | `coach`
- `caps` — international caps (always 0 for coaches)
- `surname` — sortable surname, derived from `name`
- `shirtNumber` — tournament squad number (players only; absent for coaches)
- `position` — `GK` | `DF` | `MF` | `FW` (players only; absent for coaches or an
  unresolved player)
- `en_title` — EN Wikipedia title, only stored when it differs from `name`; not
  shipped as its own field — it's what `wiki_title` (below) is built from

Where in `data/`: **`v2/map.json`**, in every player/coach object —
`data[].players[]` (players/coaches grouped by *birth* country, for players born
outside the country they play for), `data[].top[]` (same as `.players[]` but
top-5-by-caps and without `pid` — display-only, not a join target), and
`natives[nation][]` (players/coaches born in the country they play for). Object
shape: `{name, nation, caps, surname, shirtNumber?, position?, role?, pid}` — `role`
key is present only for coaches (its absence means "player"); `nation` here is the
country the person *plays for*, always present; `shirtNumber`/`position` are absent
for coaches.

## af_person — n:1 into person — join key: `(role, af_id)`

`af_id` is api-football's numeric person id (external, and a **separate id space**
per role — a player and a coach can share the same numeric `af_id`). One person can
have more than one `af_id` (api-football has issued duplicate ids for the same
human), hence n:1.

Own attribute beyond the key: none besides the FK to `pid`, plus a denormalized
birth country for convenience.

Where in `data/`: **`v2/live.json`** — `{iso2: {af_id: {pid, birthCountry?}}}`,
keyed by the team's iso2 then by `af_id` (string). `birthCountry` is present only
when resolved. The same file's top-level `teams` key (`{af_team_id: iso2}`) is
api-football's *team* id map, not person-level — see `af_team` below.

## wiki_title — n:1 into person (up to 5 rows/person) — join key: `(pid, lang)`

Own attribute: `title` — URL-ready Wikipedia article title in that language.

Where in `data/`: one file per language — **`v2/wiki_en.json`**,
**`wiki_fr.json`**, **`wiki_de.json`**, **`wiki_it.json`**, **`wiki_es.json`**.
Each is `{urlTemplate, titles}` where `titles` is an array indexed by `pid`
(`titles[pid]` = that language's title, or `null` if no article exists in that
language — fall back to EN). `urlTemplate` is `https://{lang}.wikipedia.org/wiki/{title}`.

## city — n:1 into person (shared across persons born in the same place) — join key: internal id, not exposed; reachable per-person via `pid`

Own attributes: `name`, `lat`, `lon` (plus a `country` FK — the birth country,
same as the person's own birth country).

Where in `data/`: **`v2/birthplace.json`** — `{pid: {city, lat, lon}}`. Best-effort:
a person with no scraped birth city, or a city OpenStreetMap Nominatim couldn't
geocode, is simply **absent** from this file rather than carrying a null lat/lon —
don't treat a missing `pid` key here as an error.

---

Everything below is reached via `person.nation` ("plays for") or `person.birth`
("born in") — both are countries, so every one of these can apply twice per person
(once for the team they play for, once for where they were born), except where noted.

## country — join key: `iso2` (lowercase; the pipeline-wide join key)

Own attributes: `id` (ISO 3166-1 numeric + pipeline patch ids — Kosovo 383, UK home
nations 8260–8263), `iso3` (NULL for UK home nations), `name` (canonical EN display
name), `population`, `pop_year`, `is_wc2026` (in the 48-team field),
`squad_size_override` (injury-shrunk squads — NULL means 26).

Where in `data/`:
- `name`/`iso2`/`id`: **`v2/map.json`** `data[].country` / `.iso2` / `.id` (one
  record per *birth* country that has exports); a player's *playing-for* country
  only appears by display name on the player object itself (`.nation`), not iso2/id.
- `population`: **`v2/map.json`** `pop` — `{iso2: population_in_millions}`.
- `is_wc2026` / `squad_size_override`: **not exported** — pipeline-internal only
  (the latter feeds `view_squad_size`, a derived squad-size count, which nothing in
  `export.py` currently ships either).

## capital_name — n:1 into country (up to 5 rows, one per language) — join key: `(country, lang)`

Own attribute: `name` — localized capital city name.

Where in `data/`: **`v2/map.json`** `capital` — `{iso2: {lang: name}}` (lang ∈
`en fr de it es`; not every country has all 5).

## elo_ranking — ~1:1 with country (some non-ISO entrants use a name instead) — join key: `country` (or `name` for non-ISO entrants like Zanzibar, Tibet)

Own attributes: `rank` (ties exist), `pts`, `fifa_member`, `weirdo`.

Where in `data/`: **`elo_rank.json`** (top-level of `data/`, not under `v2/`) —
a `rankings` array of `{iso2 or name, rank, pts, fifaMember, weirdo}`.

## af_team — 1:1 with country — join key: `country`

Own attribute: `af_team_id` — api-football's numeric team id (external; distinct
id space from `af_person`'s `af_id`).

Where in `data/`: **`v2/live.json`** `teams` — `{af_team_id: iso2}` (inverse-keyed:
lookup is by the numeric id, not by iso2).

## team_status — 1:1 with country — join key: `country`

Own attributes: `status` (`alive` | `eliminated`), `eliminated_round`,
`eliminated_date`, `eliminated_by` (which country beat them).

Where in `data/`: **`v2/status.json`** — `{iso2: {round, date?, lostTo?}}`, but
**ELIMINATED TEAMS ONLY**. A team's `iso2` being absent from this file *is* the
"still alive" signal — there's no separate positive "alive" list. `date` is absent
for a Group Stage exit (round-robin, no single deciding fixture).

## team_discipline — n:1 with country, one row per stage reached — join key: `(country, stage)`

Own attributes (per stage, cumulative through that stage — not that stage's own
total): `matches_played`, `fouls_committed`, `fouls_suffered`, `yellow_cards`,
`red_cards`, plus view-derived `avg_fouls_committed`, `avg_fouls_suffered`,
`fouls_per_card`.

Where in `data/`: **`v2/discipline.json`** — `{iso2: {matchesPlayed,
foulsCommitted, foulsSuffered, avgFoulsCommitted, avgFoulsSuffered, yellowCards,
redCards, foulsPerCard, stage, eliminated, byStage}}`. Top-level fields are that
team's latest cumulative totals; `byStage` (`{stage: {same fields minus stage/
eliminated}}`) freezes those same cumulative totals as of each earlier stage too,
so a client can show "figures as of round X" without doing its own running-total
math (e.g. a Quarter-finals red card is in `byStage["Quarter-finals"]` but not
`byStage["Round of 16"]`).

## fixtures / groups / standings — country-level, NOT part of the relational model above (written straight from api-football, no `pid`/person join)

Where in `data/`: **`fixtures.json`** (top-level of `data/`) —
- `fixtures[]`: every WC2026 fixture (kickoff date, round, teams, score, status)
- `groups`: `{"A": [iso2, ...], ..., "L": [...]}` — fixed at the draw
- `group`: a field on every `"Group Stage - N"` fixture, the group letter
- `standings`: `{"A": [{iso2, rank, points, played, win, draw, lose, goalsFor,
  goalsAgainst, goalsDiff}, ...], ...}` — api-football's own rank order, so FIFA's
  tie-break rules aren't reimplemented client-side; re-fetched fresh every run
  (unlike `groups`, which doesn't change once the draw is set)

Mentioned for completeness since it's still team/country-level data adjacent to
everything above, but there's no FK from `person` into it — join on `iso2` the same
way you'd join `nation`/`birth` into `country`.
