# map_data.json — build pipeline

**Update cadence: manual, as needed. No scheduled job. Regular refreshes are welcome
but not required** — the squad data and birthplace enrichment are stable for the
duration of WC 2026.

`pipeline/map_data.json` is a pipeline-internal intermediate as of the July
2026 relational-model build (`pipeline/schema.sql` → `pipeline/load.py` →
`pipeline/export.py`) — only `load.py` reads it now. The frontend-facing
successor is `data/v2/map.json`. See `pipeline/README.md`'s "Relational
model" section for the full picture; this diagram covers only how
`map_data.json` itself gets built.

```mermaid
flowchart TD
    WP["Wikipedia\n2026 FIFA World Cup squads"]
    WD["Wikidata\nSPARQL API (P19)"]
    WPP["Wikipedia\nindividual player pages"]
    WPA["Wikipedia API\nlanglinks (FR/DE/IT/ES)"]
    EJ["pipeline/map_data.json\n(existing — preserves wikiTitle & IDs)"]

    subgraph countries ["pipeline/countries.json provenance"]
        ML["mledoze/countries\n(npm package — population, ISO codes)"]
        WDC["Wikidata\nSPARQL (capital city names)"]
        FC["pipeline/fetch_countries.py"]
        PUK["pipeline/patch_uk_nations.py\n(ids 8260–8263, gb-eng/sct/wls/nir)"]
        PKC["pipeline/patch_kosovo.py\n(id 383, xk)"]
        CJ["pipeline/countries.json"]

        ML --> FC
        WDC --> FC
        FC --> PUK
        PUK --> PKC
        PKC --> CJ
    end

    BP["pipeline/wc2026_birthplaces.py"]
    CP["pipeline/wc2026_coaches.py"]
    BJ["pipeline/build_json.py"]
    WU["pipeline/add_wiki_urls.py"]

    PC["pipeline/wc2026_players.csv"]
    CC["pipeline/wc2026_coaches.csv"]

    OUT["pipeline/map_data.json"]
    V2["data/v2/map.json\n(via load.py + export.py —\nthe frontend-facing output)"]

    WP --> BP
    WD --> BP
    WPP --> BP
    BP --> PC

    WP --> CP
    WD --> CP
    WPP --> CP
    CP --> CC

    PC --> BJ
    CC --> BJ
    CJ --> BJ
    EJ -.->|id & wikiTitle seed| BJ
    BJ --> OUT

    WP --> WU
    WPA --> WU
    OUT -.->|read + overwrite| WU
    WU --> OUT
    OUT --> V2
```
