# Data pipeline

Scripts and source data for the Mundial 2026 choropleth map.

All scripts resolve paths relative to `__file__`, so `python3 pipeline/foo.py`
and `cd pipeline && python3 foo.py` are equivalent.

---

## Prerequisites

```bash
pip install requests beautifulsoup4 pandas lxml matplotlib pycountry
```

Some scripts (`orchestrator.py`, `add_hdi.py`) also need:

```bash
pip install openpyxl
```

---

## Script index

| Script | Output | Notes |
|--------|--------|-------|
| `wc2026_birthplaces.py` | `pipeline/wc2026_players.csv` | Scraper: Wikipedia squad page + Wikidata birth lookup |
| `wc2026_coaches.py` | `pipeline/wc2026_coaches.csv` | Scraper: coaches from Wikipedia squad page + Wikidata birth lookup |
| `build_json.py` | `wc2026_map_data.json` | Rebuilds the main data file from CSV + countries.json + coaches CSV |
| `add_wiki_urls.py` | `wc2026_map_data.json` (in-place) | Enriches with per-language Wikipedia links (players + coaches) |
| `fetch_countries.py` | `countries.json` | Population + multilingual capital from mledoze + World Bank + Wikidata |
| `patch_uk_nations.py` | `countries.json` (in-place) | Adds UK home nations (ids 8260–8263) |
| `patch_kosovo.py` | `countries.json`, `wc2026_elo_rank.json` (in-place) | Adds Kosovo (id 383) |
| `update_elo_rankings.py` | `wc2026_elo_rank.json` | Fetches current Elo ratings from eloratings.net |
| `elo_diff_summary.py` | _(stdout)_ | Compares old vs new Elo JSON, prints a git commit message with ranking/points changes |
| `build_elo_history.py` | `wc2026_elo_history.json` | Parses eloratings.net graph.tsv for animated bar chart race |
| `add_gdp.py` | `wc2026_gdp.json` | World Bank GDP (current USD billions) |
| `add_gdp_pc_ppp.py` | `wc2026_gdp_pc_ppp.json` | World Bank GDP per capita PPP |
| `add_hdi.py` | `wc2026_hdi.json` | UNDP Human Development Index |
| `add_uk_regional_gdp.py` | enriches GDP/GDHI for UK home nations | ONS regional data |
| `wc2026_make_ratio_chart.py` | `wc2026_export_ratio.png` | Bar chart: players exported per million population |
| `orchestrator.py` | `triadic_profile_world.csv`, `triadic_profile_latest.csv` | Merges GDP/HDI data for correlation page |

---

## Core pipeline (squad data)

### Step 1 — Scrape Wikipedia squads

```bash
python3 pipeline/wc2026_birthplaces.py
```

Fetches player rosters from the Wikipedia page
[2026 FIFA World Cup squads](https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads)
plus Wikidata for birth city/country lookup.

Outputs:
- `pipeline/wc2026_players.csv` — full squad roster (1 row per player)
- `pipeline/wc2026_by_birthcountry.csv` — aggregated ranking by birth country

**Known issue:** a small number of players (~20) may have `birth_country` set to
`]`, `[a]` (Wikipedia footnote markers) or `NaN` (missing data). See
*Fixing missing birth countries* below.

---

### Step 1b — Scrape coaches

```bash
python3 pipeline/wc2026_coaches.py
```

Extracts all 48 head coaches from the same Wikipedia squad page, with nationality
(flag detection for foreign coaches) and birthplace via Wikidata SPARQL.

Outputs `pipeline/wc2026_coaches.csv` — 1 row per coach with birth city/country.

Coaches born in the UK get their `birth_country` resolved to the specific home
nation (England, Scotland, Wales, Northern Ireland) via the `UK_CITY_TO_NATION`
map in the script. If a new UK-born coach appears and their city is not in the
map, the script prints a warning — add the city manually.

**⚠ Mid-tournament coaching changes:** The scraper pulls coaches from the Wikipedia
squad page at scrape time. If a coach is replaced mid-tournament (as happened with
Tunisia: Sabri Lamouchi → Hervé Renard after matchday 1), the CSV may need a
manual edit if Wikipedia hasn't been updated yet. Always verify
`pipeline/wc2026_coaches.csv` after a re-scrape during the tournament.

---

### Step 2 — Rebuild the main JSON

```bash
python3 pipeline/build_json.py
```

Reads `pipeline/wc2026_players.csv` + `pipeline/wc2026_coaches.csv` (optional) +
`countries.json`, writes `wc2026_map_data.json`.

- `data` array: players/coaches born in one country who play for/coach another
- `natives`: players/coaches born in the country they play for/coach
- Coaches carry `"role":"coach"` in the JSON — the frontend badges them distinctly
- Preserves existing `wiki_langs` / `wiki` fields (safe to re-run after `add_wiki_urls.py`)
- Reads population + multilingual capital from `countries.json` (keyed by lowercase alpha2)

---

### Step 3 — Enrich with Wikipedia URLs

```bash
python3 pipeline/add_wiki_urls.py
```

Fetches langlinks (FR / DE / IT / ES) from the Wikipedia API for every player
in `wc2026_map_data.json` (both `data` and `natives` sections).

Writes `wiki_langs: {en, fr?, de?, it?, es?}` onto every player object.

Typical run time: ~5 minutes (one API call per language per batch of 50 titles,
with 1-second sleep between batches).

---

## Population and capital data (`countries.json`)

`countries.json` (project root) is the canonical source for population and
multilingual capital city names, keyed by ISO numeric id (string):

```json
{
  "250": {
    "id": 250, "alpha2": "fr", "alpha3": "fra", "name": "France",
    "capital": {"en":"Paris","fr":"Paris","de":"Paris","it":"Parigi","es":"París"},
    "population": 68374591
  }
}
```

`build_json.py` reads from this file (indexed by lowercase alpha2). Never edit it
manually — regenerate with the scripts below.

### Rebuilding `countries.json` from scratch

```bash
python3 pipeline/fetch_countries.py   # ~5 min — mledoze + World Bank + Wikidata
python3 pipeline/patch_uk_nations.py  # adds England/Scotland/Wales/NI (ids 8260–8263)
python3 pipeline/patch_kosovo.py      # adds Kosovo (id 383) + updates elo rank
```

**UK home nations** (synthetic ids 8260–8263, alpha2 codes `gb-eng` / `gb-sct` /
`gb-wls` / `gb-nir`) are not in any standard ISO table — they must be patched in
after `fetch_countries.py`. Population from 2021/22 census; capitals from Wikidata.

**Kosovo** (user-assigned id 383, alpha2 `xk`) is not in the `iso-3166-1` package's
numeric table and may be absent from `Intl.DisplayNames`. The patch script also moves
Kosovo from `fifaAbsences` to `rankings` in `wc2026_elo_rank.json` with
`rank: null, pts: null`.

---

## Elo rankings

### Update current Elo ratings

```bash
pip install pycountry
python3 pipeline/update_elo_rankings.py
# Then immediately re-patch Kosovo (update_elo_rankings may overwrite it):
python3 pipeline/patch_kosovo.py
```

Fetches `eloratings.net/World.tsv`, rewrites `wc2026_elo_rank.json`. Adds
`weirdo` (non-sovereign / no ISO alpha-2) and `fifaMember` flags per entry.

### Rebuild Elo rating history (for animated bar chart race)

```bash
python3 pipeline/build_elo_history.py
```

Parses `eloratings.net/graph.tsv` → `wc2026_elo_history.json`. Used by
`wc2026_elo_history.html`.

---

## Economic / development data (correlation page)

These feeds power `wc2026_correlation.html` (scatter plot of economy vs. player
migration). Each writes a standalone JSON to the project root.

```bash
python3 pipeline/add_gdp.py          # World Bank GDP → wc2026_gdp.json
python3 pipeline/add_gdp_pc_ppp.py   # World Bank GDP/cap PPP → wc2026_gdp_pc_ppp.json
python3 pipeline/add_hdi.py          # UNDP HDI → wc2026_hdi.json
python3 pipeline/add_uk_regional_gdp.py  # ONS GDHI for UK home nations
```

The `orchestrator.py` script merges all economic sources with source-priority
rules (UNDP > World Bank > IMF; ONS for UK sub-nations) and outputs CSV files
for further analysis — not required for the web app.

---

## Partial updates

### Re-scrape only (after a squad change)

```bash
python3 pipeline/wc2026_birthplaces.py
python3 pipeline/build_json.py
python3 pipeline/add_wiki_urls.py    # only new players need new API calls
```

### Fixing country name mismatches

Some scraped `birth_country` values use the full formal country name while
`nation` uses a short form — breaking the `birth_country == nation` check.
`build_json.py` applies normalizations via `BIRTH_COUNTRY_ALIASES`.

Known case:

| `birth_country` (scraped) | `nation` | Fix |
|---|---|---|
| `Democratic Republic of the Congo` | `DR Congo` | → `DR Congo` |

Add new mismatches to `BIRTH_COUNTRY_ALIASES` in `build_json.py`.

To verify manually after step 1:

```bash
python3 - <<'EOF'
import pandas as pd
df = pd.read_csv('pipeline/wc2026_players.csv')
print(df[df['birth_country'].isna() | df['birth_country'].isin([']', '[a]'])])
EOF
```

Every squad should have exactly 26 players (Austria and Canada are known
exceptions at 25 due to injuries/late withdrawals).

### Fixing missing birth countries

For each player with a bad `birth_country`:
1. Look up their birth city/country on Wikipedia or Wikidata.
2. Edit `pipeline/wc2026_players.csv` directly.
3. Re-run steps 2–3.

### Birth-city quality: `birth_city == birth_country`

Wikidata sometimes returns only the country (or a vague region name) instead of
a proper city for `P19` (place of birth), resulting in `birth_city` equal to
`birth_country` (e.g. `birth_city=France, birth_country=France`).

`wc2026_birthplaces.py` now treats `birth_city == birth_country` as "missing" in
both enrichment passes (Wikidata SPARQL and Wikipedia page fallback), so
**re-running the scraper** should resolve most of these automatically.

If some still remain after a scrape (e.g. when Wikipedia's infobox also lacks the
city), edit `pipeline/wc2026_players.csv` directly and re-run steps 2–3.

Previously patched manually:

| Player | Scraped `birth_city` | Correct `birth_city` | Source |
|--------|---------------------|----------------------|--------|
| Callan Elliot | `Scotland` | `Dumfries` | [BBC](https://www.bbc.com/news/articles/czx8pllvpv7o) |
| Lenny Joseph | `France` | `Paris` | [Wikipedia](https://en.wikipedia.org/wiki/Lenny_Joseph) |
| Ahmed Qasem | `Sweden` | `Motala` | [MLS](https://www.mlssoccer.com/players/ahmed-qasem/) |
| Simon Banza | `France` | `Creil` | [Wikipedia](https://en.wikipedia.org/wiki/Simon_Banza) |
| Igor Matanović | `Germany` | `Hamburg` | [Wikipedia](https://en.wikipedia.org/wiki/Igor_Matanovi%C4%87) |

---

## Generating the birth country ratio chart

```bash
python3 pipeline/wc2026_make_ratio_chart.py              # Curaçao excluded (default)
python3 pipeline/wc2026_make_ratio_chart.py --with-curacao
```

Outputs `wc2026_export_ratio.png` in the project root. Requires `matplotlib`.

---

## Source files

| File | Description |
|------|-------------|
| `wc2026_players.csv` | Full squad roster — **source of truth** for squad data |
| `wc2026_by_birthcountry.csv` | Aggregated ranking by birth country |
| `uk_regional_gdp.csv` | ONS regional GDP/GDHI data for UK home nations (used by `add_uk_regional_gdp.py`) |

The generated files (`wc2026_map_data.json`, `countries.json`, `wc2026_elo_rank.json`,
`wc2026_gdp.json`, etc.) live in the project root because they are served directly
by the web app.
