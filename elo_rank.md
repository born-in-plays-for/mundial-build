# elo_rank.json — build pipeline

```mermaid
flowchart TD
    ELO["eloratings.net/World.tsv\n(Elo ratings, all nations)"]
    FIFA["Wikipedia\nList of FIFA country codes"]
    FC["pipeline/fifa_members_cache.json\n(30-day TTL cache)"]
    EJ["data/elo_rank.json\n(existing — preserves structure)"]

    UR["pipeline/update_elo_rankings.py"]
    PK["pipeline/patch_kosovo.py"]

    TSV["pipeline/elo_rank.tsv\n(debug snapshot)"]
    OUT["data/elo_rank.json"]

    ELO --> UR
    FIFA -.->|cached| FC
    FC --> UR
    EJ -.->|read for continuity| UR
    UR --> TSV
    UR --> OUT
    OUT -.->|read + patch in-place| PK
    PK --> OUT
```

`patch_kosovo.py` is called automatically at the end of `update_elo_rankings.py`.
Kosovo (XK) has no standard ISO entry and is injected manually with `rank=null, pts=null`.
