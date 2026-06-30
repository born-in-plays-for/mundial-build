# r32_teams.json — build pipeline

```mermaid
flowchart TD
    API["api-football.com\n(v3 — WC 2026 fixtures/standings)"]
    KEY["API key\n(env var or .env file)"]

    FR["pipeline/fetch_r32_teams.py"]
    OUT["data/r32_teams.json"]

    KEY --> FR
    API --> FR
    FR --> OUT
```

Requires a paid API key from [dashboard.api-football.com](https://dashboard.api-football.com)
(or via RapidAPI). The script is only re-run when the Round of 32 qualification is
decided — it is not part of the regular data refresh cycle.
