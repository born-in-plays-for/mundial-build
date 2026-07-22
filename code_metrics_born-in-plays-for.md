# Code metrics — born-in-plays-for/ (all repos)

Generated 2026-07-22, from local clones of the four repos in `~/github.com/born-in-plays-for/`.
Tools: `git`, [lizard](https://github.com/terryyin/lizard) 1.23 (McCabe cyclomatic complexity), Python line-counter (cloc is broken on this machine — Perl module conflict — so LOC-by-extension was hand-computed from `git ls-files`).

Standards referenced: McCabe cyclomatic complexity thresholds (CCN ≤10 simple, 11–20 moderate risk, >20 high risk, >50 “untestable” — per NIST 500-235 / common static-analysis defaults such as SonarQube's), and ISO/IEC 25010 maintainability characteristics (testability, modifiability) for the qualitative checks (tests, docs, CI, dependency declarations).

`mundial-data` is excluded from the code-quality tables below — it's pure generated JSON/GeoJSON output, not source code (see its own section at the end for a data-volume snapshot instead).

## 1. Size

| Repo | Tracked files | Total lines | Source LOC (py+js+sh+sql) | Human contributors | First commit | Commits (30d) |
|---|---:|---:|---:|---:|---|---:|
| mundial (frontend) | 172 | 29,471 | 9,920 | 1 | 2026-06-05 | 595 |
| mundial-build (pipeline) | 100 | 215,591¹ | 9,669 | 1 | 2026-06-24 | 179 |
| mundial-server (backend) | 13 | 2,602 | 1,219 | 1 | 2026-06-21 | 30 |

¹ Dominated by one 158k-line data file (`pipeline/population_points.csv`) committed as a build input — see §4.

**By language, mundial (frontend):**
| Ext | Files | Lines |
|---|---:|---:|
| .js | 21 | 9,379 |
| .html | 13 | 3,901 |
| .css/.scss | 12 | 1,613 |
| .py | 3 | 541 |
| .svg | 51 | 883 |

**By language, mundial-build (pipeline):**
| Ext | Files | Lines |
|---|---:|---:|
| .py | 40 | 9,524 |
| .json | 24 | 29,155 |
| .csv/.tsv | 6 | 161,296 |
| .sql | 1 | 495 |
| .sh | 1 | 145 |

**By language, mundial-server (backend):**
| Ext | Files | Lines |
|---|---:|---:|
| .py | 3 | 1,113 |
| .html | 3 | 695 |

## 2. Complexity (McCabe cyclomatic complexity, via lizard)

| Repo | Functions | Avg CCN | Avg NLOC/fn | Functions over threshold (CCN>15 or len>1000) | % flagged |
|---|---:|---:|---:|---:|---:|
| mundial | 801 | 2.4 | 5.6 | 7 | 0.9% |
| mundial-build | 201 | **8.4** | 22.5 | **32** | **15.9%** |
| mundial-server | 61 | 3.4 | 9.5 | 2 | 3.3% |

**mundial-build is the clear complexity hotspot** — nearly 1 in 6 functions exceeds the risk threshold, vs. under 1% in the frontend. This tracks with what the pipeline does: each script is a bespoke scraper/parser for a different messy external source (Wikipedia tables, PDFs, IMF/ONS/UNDP spreadsheets), so branchy, hard-to-unit-test parsing logic is somewhat inherent to the domain — but it's still worth knowing where the risk concentrates.

Top 5 most complex functions across all repos:

| CCN | NLOC | Function | Location |
|---:|---:|---|---|
| 53 | 152 | `main()` | `mundial-build/pipeline/load.py:307` |
| 44 | 96 | `main()` | `mundial-build/pipeline/check_fifa_pdf_consistency.py:223` |
| 40 | 31 | `(anonymous)` | `mundial/js/qualified.js:158` |
| 40 | 95 | `find_longest_path()` | `mundial/chains/subgraphs/compute_longest_paths.py:84` |
| 39 | 88 | `parse_wikipedia()` | `mundial-build/pipeline/wc2026_birthplaces.py:129` |

`pipeline/load.py:main` (CCN 53, 152 lines) is the single riskiest function in the whole project — well past the "untestable without heavy branching-path effort" line.

## 3. Maintainability / process signals

| Signal | mundial | mundial-build | mundial-server |
|---|---|---|---|
| Automated test files | **0** | **0** | **0** |
| CI workflows (`.github/workflows`) | 2 | 1 | 0 |
| Dependency manifest (`package.json`/`requirements.txt`) | package.json ✓ | **none** | **none** |
| TODO/FIXME/HACK/XXX markers | 8 | 14 | 0 |
| Largest single file | `js/wc2026_map.js` (2,719 lines) | `pipeline/load.py` (part of the CCN-53 hotspot) | `backend.py` (547 lines) |

**No automated tests exist anywhere across all four repos.** That's the single biggest maintainability gap by any standard (ISO 25010 testability, or just practically: the 53-CCN `load.py:main` has zero regression coverage).

`mundial-build` runs real pipelines (fetches from Wikipedia, IMF, ONS, UNDP, api-football, eloratings.net) with **no `requirements.txt`/`pyproject.toml`** — dependencies (`requests`, `pandas`, `beautifulsoup4`, `pycountry`, …) are only declared ad hoc inside CI workflow `pip install` steps, so reproducing the environment locally means reverse-engineering it from `.github/workflows/*.yml`.

**Bus factor: 1 human, across all four repos.** `git shortlog -sn -e` shows a second committer on mundial/mundial-build/mundial-data, but it's `github-actions[bot]` (the scheduled CI workflows auto-committing generated data — Elo rankings, fixtures, etc.), not a person:

| Repo | Christophe Thiebaud | github-actions[bot] |
|---|---:|---:|
| mundial | 887 | 47 |
| mundial-build | 146 | 33 |
| mundial-server | 36 | — |
| mundial-data | 71 | 25 |

So there is zero redundancy — one person holds all the context for every repo. Not unusual for a solo project, but worth being explicit about since it means there's no fallback if that context is ever unavailable.

## 4. Data volume (context, not code quality)

| Repo | Working-tree size (excl. `.git`) | Notable |
|---|---:|---|
| mundial-data (submodule) | 7.7 MB | 16 tracked files, pure JSON/GeoJSON output |
| mundial-build/pipeline | includes `population_points.csv` (158,456 lines), `fifa_squad_lists_2026.pdf` (8,370 lines), `discipline_stats_cache.json` (16,894 lines), `geocode_cache.json` (6,023 lines) — all deliberately committed as expensive-to-regenerate build inputs/caches (see `mundial-build/CLAUDE.md`) | — |

These inflate the raw "total lines" count for `mundial-build` in §1 but aren't code — they're the reason the LOC table there separates "Total lines" from "Source LOC".

## Summary

- **~20.8k lines of actual source code** across the three code repos (mundial 9.9k, mundial-build 9.7k, mundial-server 1.2k).
- **Complexity is concentrated in `mundial-build`'s pipeline scripts** — 16% of its functions exceed standard risk thresholds, vs. <1–3% elsewhere. `pipeline/load.py:main` (CCN 53) is the top refactor candidate project-wide.
- **Zero automated tests** in any of the four repos — the biggest structural risk given the complexity found above.
- `mundial-build` has no declared Python dependencies outside of CI YAML.
- Frontend (`mundial`) is comparatively very clean: low complexity, CI present, dependencies declared via `package.json`.
