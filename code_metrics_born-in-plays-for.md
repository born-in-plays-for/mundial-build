# Code metrics — born-in-plays-for/ (all repos)

Generated 2026-07-22, from local clones of the four repos in `~/github.com/born-in-plays-for/`.
Tools: `git`, [cloc](https://github.com/AlDanial/cloc) 2.10 (LOC by language, comment/blank/code split), [lizard](https://github.com/terryyin/lizard) 1.23 (McCabe cyclomatic complexity).

Reproduce with: `~/scripts/born_in_plays_for_metrics.sh` (self-contained, no need to ask an assistant to regenerate this — see script header for requirements).

Standards referenced: McCabe cyclomatic complexity thresholds (CCN ≤10 simple, 11–20 moderate risk, >20 high risk, >50 “untestable” — per NIST 500-235 / common static-analysis defaults such as SonarQube's), and ISO/IEC 25010 maintainability characteristics (testability, modifiability) for the qualitative checks (tests, docs, CI, dependency declarations).

`cloc` and `git ls-files` both naturally skip the `data/` git submodule (a submodule is one gitlink entry, not its file contents) and any untracked directories (build artifacts, `node_modules`, etc.) — so all counts below are exactly the maintained source, nothing more.

`mundial-data` is excluded from the code-quality tables below — it's pure generated JSON/GeoJSON output, not source code (see its own section at the end for a data-volume snapshot instead).

## 1. Size (cloc, git-tracked files only)

| Repo | Files | Blank | Comment | Code | Human contributors | First commit | Commits (30d) |
|---|---:|---:|---:|---:|---:|---|---:|
| mundial (frontend) | 150 | 2,087 | 3,226 | 20,593 | 1 | 2026-06-05 | 598 |
| mundial-build (pipeline) | 105 | 1,875 | 2,259 | 204,063¹ | 1 | 2026-06-24 | 181 |
| mundial-server (backend) | 12 | 401 | 97 | 2,096 | 1 | 2026-06-21 | 30 |

¹ Dominated by one 158k-line data file (`pipeline/population_points.csv`) and other build-input JSON — see §4 for the real "source code" figure, which excludes those.

**By language, mundial (frontend):**
| Language | Files | Blank | Comment | Code |
|---|---:|---:|---:|---:|
| JavaScript | 19 | 554 | 2,117 | 6,019 |
| JSON | 30 | 0 | 0 | 7,762 |
| Markdown | 28 | 1,191 | 196 | 3,392 |
| HTML | 5 | 102 | 38 | 1,356 |
| SVG | 50 | 2 | 5 | 914 |
| CSS | 11 | 105 | 751 | 690 |
| Python | 3 | 106 | 93 | 342 |
| YAML | 2 | 18 | 0 | 83 |
| SCSS | 1 | 9 | 26 | 34 |

**By language, mundial-build (pipeline):**
| Language | Files | Blank | Comment | Code |
|---|---:|---:|---:|---:|
| CSV | 5 | 0 | 0 | 161,053 |
| JSON | 39 | 0 | 0 | 33,533 |
| Python | 40 | 1,369 | 1,980 | 6,175 |
| Markdown | 14 | 355 | 2 | 1,825 |
| HTML | 4 | 91 | 1 | 1,101 |
| SQL | 1 | 25 | 236 | 234 |
| Bourne Shell | 1 | 24 | 37 | 84 |
| YAML | 1 | 11 | 3 | 58 |

**By language, mundial-server (backend):**
| Language | Files | Blank | Comment | Code |
|---|---:|---:|---:|---:|
| Python | 3 | 110 | 75 | 928 |
| HTML | 3 | 66 | 8 | 621 |
| Markdown | 4 | 212 | 0 | 464 |
| Bourne Shell | 1 | 13 | 14 | 79 |
| JSON | 1 | 0 | 0 | 4 |

**Comment density** (comment lines ÷ (comment + code), source languages only): mundial JS+Python ≈ 27%, mundial-build Python ≈ 24% (+ a notably dense 50% in the single `.sql` file), mundial-server Python ≈ 7% — the backend's Python is the least documented of the three.

## 2. Complexity (McCabe cyclomatic complexity, via lizard)

| Repo | Functions | Avg CCN | Avg NLOC/fn | Functions over threshold (CCN>15 or len>1000) | % flagged |
|---|---:|---:|---:|---:|---:|
| mundial | 787 | 2.4 | 5.6 | 7 | 0.9% |
| mundial-build | 201 | **8.4** | 22.5 | **32** | **15.9%** |
| mundial-server | 61 | 3.4 | 9.5 | 2 | 3.3% |

**mundial-build is the clear complexity hotspot** — nearly 1 in 6 functions exceeds the risk threshold, vs. under 1% in the frontend. This tracks with what the pipeline does: each script is a bespoke scraper/parser for a different messy external source (Wikipedia tables, PDFs, IMF/ONS/UNDP spreadsheets), so branchy, hard-to-unit-test parsing logic is somewhat inherent to the domain — but it's still worth knowing where the risk concentrates.

Top complex functions, mundial-build (full flagged list — this repo accounts for 32 of the 41 flagged functions project-wide):

| CCN | NLOC | Function | File |
|---:|---:|---|---|
| **53** | 152 | `main()` | `pipeline/load.py` |
| 44 | 96 | `main()` | `pipeline/check_fifa_pdf_consistency.py` |
| 39 | 88 | `parse_wikipedia()` | `pipeline/wc2026_birthplaces.py` |
| 33 | 60 | `_parse_csv()` | `extras/fetchers/undp_hdi.py` |
| 31 | 43 | `parse_wikipedia_pandas()` | `pipeline/wc2026_birthplaces.py` |
| 31 | 58 | `main()` | `pipeline/validate_country_coverage.py` |
| 30 | 96 | `main()` | `pipeline/build_player_wiki.py` |
| 28 | 44 | `match_tier()` | `pipeline/build_player_wiki.py` |
| 28 | 46 | `_parse_wide_df()` | `extras/fetchers/undp_hdi.py` |
| 28 | 49 | `main()` | `extras/elo_diff_summary.py` |
| 27 | 77 | `main()` | `pipeline/geocode_birthplaces.py` |
| 25 | 65 | `enrich_birthplaces()` | `pipeline/wc2026_coaches.py` |
| 25 | 60 | `fetch_fifa_members_iso2()` | `pipeline/update_elo_rankings.py` |
| 24 | 46 | `compute_eliminated()` | `pipeline/load.py` |
| … | | +18 more, CCN 16–22 | (see full list via the script's `lizard` invocation) |

`pipeline/load.py:main` (CCN 53, 152 lines) is the single riskiest function project-wide — well past the "untestable without heavy branching-path effort" line. Notably `load.py` contributes **two** of the top offenders (`main` and `compute_eliminated`), making it the single riskiest file.

Elsewhere: mundial's worst is `(anonymous)@js/qualified.js:158` (CCN 40) and `find_longest_path()` in `chains/subgraphs/compute_longest_paths.py` (CCN 40); mundial-server's worst is `_track_loop()` in `backend.py` (CCN 18).

## 3. Maintainability / process signals

| Signal | mundial | mundial-build | mundial-server |
|---|---|---|---|
| Automated test files | **0** | **0** | **0** |
| CI workflows (`.github/workflows`) | 2 | 1 | 0 |
| Dependency manifest (`package.json`/`requirements.txt`) | package.json ✓ | **none** | **none** |
| TODO/FIXME/HACK/XXX markers | 7 | 14 | 0 |

**No automated tests exist anywhere across all four repos.** That's the single biggest maintainability gap by any standard (ISO 25010 testability, or just practically: the 53-CCN `load.py:main` has zero regression coverage).

`mundial-build` runs real pipelines (fetches from Wikipedia, IMF, ONS, UNDP, api-football, eloratings.net) with **no `requirements.txt`/`pyproject.toml`** — dependencies (`requests`, `pandas`, `beautifulsoup4`, `pycountry`, …) are only declared ad hoc inside CI workflow `pip install` steps, so reproducing the environment locally means reverse-engineering it from `.github/workflows/*.yml`.

**Bus factor: 1 human, across all four repos.** `git shortlog -sn -e` shows a second committer on mundial/mundial-build/mundial-data, but it's `github-actions[bot]` (the scheduled CI workflows auto-committing generated data — Elo rankings, fixtures, etc.), not a person:

| Repo | Christophe Thiebaud | github-actions[bot] |
|---|---:|---:|
| mundial | 890 | 47 |
| mundial-build | 147 | 34 |
| mundial-server | 36 | — |
| mundial-data | 71 | 25 |

So there is zero redundancy — one person holds all the context for every repo. Not unusual for a solo project, but worth being explicit about since it means there's no fallback if that context is ever unavailable.

## 4. Data volume (context, not code quality)

| Repo | Data files (JSON/CSV/SQL code lines, cloc) | Notable |
|---|---:|---|
| mundial-data (submodule) | 4,333 (15 JSON files) | pure generated output, no logic |
| mundial-build | CSV 161,053 + JSON 33,533 + SQL 234 = 194,820 | `pipeline/population_points.csv` alone is 158k lines; all committed as expensive-to-regenerate build inputs/caches (see `mundial-build/CLAUDE.md`) rather than checked-in "code" |
| mundial (frontend) | JSON 7,762 (30 files) | pre-built map/flag/geo assets shipped to the browser |

Subtracting this data volume, **actual source code across the three code repos is ~13.4k lines**: mundial 6,361 (JS+Python), mundial-build 6,493 (Python+SQL+shell), mundial-server 1,007 (Python+shell) — a much smaller and more honest figure than the raw file-line totals in §1.

## Summary

- **~13.4k lines of actual source code** across the three code repos, once JSON/CSV/Markdown/HTML data and docs are excluded: mundial 6.4k, mundial-build 6.5k, mundial-server 1.0k.
- **Complexity is concentrated in `mundial-build`'s pipeline scripts** — 16% of its functions exceed standard risk thresholds (7 in mundial, 2 in mundial-server, 32 in mundial-build). `pipeline/load.py` is the single riskiest file, containing both the top offender (`main`, CCN 53) and another flagged function (`compute_eliminated`, CCN 24).
- **Zero automated tests** in any of the four repos — the biggest structural risk given the complexity found above.
- `mundial-build` has no declared Python dependencies outside of CI YAML.
- Frontend (`mundial`) is comparatively clean: low complexity, CI present, dependencies declared via `package.json` — though its Python/JS comment density (~27%) and mundial-build's (~24%) both comfortably beat mundial-server's (~7%).
- Bus factor is 1 (human) everywhere — the apparent second contributor on three of the four repos is `github-actions[bot]`, not a person.
