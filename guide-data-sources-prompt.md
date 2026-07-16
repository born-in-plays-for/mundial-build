Replace the "Data Sources" section of `mundial`'s user guide (`guide/guide-map.md`) with the
content below. It's been rewritten from `mundial-build` (the pipeline repo) to reflect the
pipeline's current state — the old version predates several data sources that have since been
added (country populations/capitals, birth-city geocoding, the talent-production map layer,
live fixtures/standings/discipline stats).

## What to do

1. In `guide/guide-map.md`, find the block wrapped in `<!-- i18n:data_sources -->` /
   `<!-- /i18n:data_sources -->` (the file's final section) and replace its **entire contents**
   (including both marker comments) with the "New content" block below.
2. Immediately after that block sits a ` ```mermaid ` flowchart — replace it too, with the
   "New Mermaid diagram" block below.
3. Don't touch anything else in the file.
4. Run `python3 guide/build_guide.py --no-screenshots` to regenerate `guide/built/*.md`.
5. The 4 translated versions (`guide/i18n/{fr,de,it,es}.json`, key `data_sources`) will now be
   stale — that's expected, `build_guide.py` falls back to English for any block a translation
   hasn't caught up to yet. Don't attempt to translate in this pass.
6. Review the diff, then commit if it looks right.

## New content

```markdown
<!-- i18n:data_sources -->
# Data Sources

| Source | Used for |
|---|---|
| [eloratings.net](https://www.eloratings.net/) | World Football Elo rankings |
| [Wikipedia — 2026 World Cup squads](https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads) | Player names, cap counts, shirt numbers |
| [Wikipedia API](https://en.wikipedia.org/w/api.php) | Each player's and coach's Wikipedia page resolved in 5 languages (en, fr, de, it, es) |
| [Wikipedia — List of FIFA country codes](https://en.wikipedia.org/wiki/List_of_FIFA_country_codes) | FIFA membership |
| [Wikidata](https://www.wikidata.org/) | Birth countries; multilingual capital-city names |
| [mledoze/countries](https://github.com/mledoze/countries) + [World Bank](https://data.worldbank.org/) | Country populations and capitals |
| [OpenStreetMap Nominatim](https://nominatim.org/) | Birth-city geocoding, for the birthplace map view |
| [GeoNames](https://www.geonames.org/) | Reference population points for the talent-production map layer |
| [api-football](https://www.api-football.com/) | Live fixtures, group standings, match results, discipline (fouls/cards) stats |

**Elo ratings** work like the chess rating system they're named after: every match moves both teams'
scores up or down depending on the result, the goal margin, and how strong the opponent was rated
going in — beating a highly-rated team gains far more than beating a weak one. Unlike the official
FIFA World Ranking, which only updates a handful of times a year, Elo recalculates after each match
and reacts immediately to results, which is why [eloratings.net](https://www.eloratings.net/) is used
as this site's country reference instead of FIFA's own list.

**Birth country resolution** is the most delicate step in the pipeline.
The Wikipedia squad page does not list where players were born — it only provides their names
and links to their individual Wikipedia pages.
The pipeline uses those links as keys to query [Wikidata](https://www.wikidata.org/)
via SPARQL, retrieving each player's recorded place of birth and the country that place belongs to.
This two-step lookup (Wikipedia → Wikidata) is what makes it possible to draw the born-in / plays-for connections on the map.

**The talent-production map layer** answers a different question than "where were the most players
born" — a raw density map like that would just track megacity population. Instead it asks "does this
place produce more WC2026 talent than its population would predict?" Two Gaussian surfaces are built
on the same grid: one from geocoded player/coach birth cities, one from a reference population
dataset ([GeoNames](https://www.geonames.org/)), using the same kernel and bandwidth so the two are
directly comparable cell by cell. Dividing one by the other, then normalizing against the tournament's
own global rate, gives a *relative* risk — a value of 1 means "producing talent exactly proportional
to the people who live here," not "producing a lot of talent in absolute terms." That's why a
megacity can register as unremarkable on this map while a small, well-known footballing town lights
up: the layer is deliberately measuring over- and under-performance relative to population, not raw
output.

**Live standings** use api-football's own group-table ranking rather than one computed from scores
here, so head-to-head record, discipline points, and the rest of FIFA's official tie-break rules are
never at risk of disagreeing with the real classement over an edge case those rules exist for in the
first place.

These sources feed an automated pipeline that merges, cross-references,
and enriches the raw data before publishing it to this page.
Elo ratings and live match data (fixtures, standings, discipline stats) are refreshed as results come
in; squad, birthplace, and talent-production data are updated manually when squads change.
<!-- /i18n:data_sources -->
```

## New Mermaid diagram

```mermaid
flowchart LR
  ELO["eloratings.net\nElo rankings"] --> P
  WP["Wikipedia\nsquad pages · FIFA codes\nplayer/coach pages × 5 languages"] --> P
  WD["Wikidata\nbirth countries · capitals"] --> P
  CTY["mledoze/countries + World Bank\npopulations & capitals"] --> P
  AF["api-football\nfixtures · standings · discipline"] --> P
  OSM["OpenStreetMap Nominatim\nbirth-city geocoding"] --> KDE
  GEO["GeoNames\npopulation points"] --> KDE
  KDE(["talent-production\nKDE surface"]) --> P
  P(["data pipeline"]) --> M["this page"]
```

## Notes on what changed vs. the old version, for context

- Added rows: country populations/capitals (mledoze/countries + World Bank + Wikidata), birth-city
  geocoding (OpenStreetMap Nominatim), the talent-production layer's population reference (GeoNames),
  and live match data (api-football: fixtures, standings, discipline).
- Added prose paragraphs for the talent-production layer (explicitly asked for — it's the most
  methodologically interesting addition, in the same style as the existing Elo/birth-country
  paragraphs) and a short one on live standings' tie-break handling.
- Population/capital sourcing and birth-city geocoding got table rows only, not their own prose —
  they're real additions but not as conceptually surprising as the talent-production layer.
- Deliberately left out: the FIFA official squad-list PDF (`fifa_squad_lists_2026.pdf`) — it's a
  manual cross-check diagnostic in the pipeline repo, not a live input that feeds this map, so
  listing it here would be misleading about what actually populates the page. Also left out
  `api_football_countries.py`'s country-code fallback map — purely an internal alias-resolution
  detail, not something a reader benefits from knowing about.
- Closing cadence sentence updated: old text said "Elo rankings are refreshed daily; squad data is
  updated manually" — Elo is no longer the only thing on a live cadence (fixtures/standings/discipline
  now are too), so it's rephrased as "Elo ratings and live match data ... refreshed as results come
  in; squad, birthplace, and talent-production data are updated manually."
