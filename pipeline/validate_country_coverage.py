#!/usr/bin/env python3
"""
validate_country_coverage.py — build-time gate for country identity.

Scans every raw country-name string currently present in this project's CSV
inputs and JSON outputs and resolves each through country_registry. A new
upstream spelling variant (the DR-Congo/Cabo-Verde/Congo-DR class of bug)
shows up here as a failed build, not a silent wrong-flag screenshot weeks
later.

Also checks that every nation in the current WC2026 field actually has rows
in both CSVs — catches a whole team silently missing from a scrape.

Usage:
    python3 pipeline/validate_country_coverage.py
"""
import csv
import json
import sys
from pathlib import Path

import country_registry as reg

ROOT         = Path(__file__).parent
DATA_DIR     = ROOT.parent / "data"
PLAYERS_CSV  = ROOT / "wc2026_players.csv"
COACHES_CSV  = ROOT / "wc2026_coaches.csv"
MAP_DATA     = ROOT / "map_data.json"
ELO_RANK     = DATA_DIR / "elo_rank.json"
R32_TEAMS    = DATA_DIR / "r32_teams.json"


def check_names(source: str, names, failures: list):
    for name in sorted(set(n for n in names if n)):
        try:
            reg.resolve_iso2(name)
        except reg.UnknownCountryError as e:
            failures.append(f"[{source}] {e}")


def check_iso2(source: str, entries, failures: list):
    """Verify a stored iso2 field actually matches what the shared resolver
    would independently compute from the name it's paired with. Catches a
    file's own iso2 silently disagreeing with the canonical resolver (e.g. an
    upstream API's own country-code table being wrong) — resolvability alone
    (check_names) doesn't catch this, since the name can resolve fine while
    the stored iso2 next to it is simply a different, also-valid-looking
    code."""
    for name, stored_iso2 in entries:
        if not name or not stored_iso2:
            continue
        try:
            expected = reg.resolve_iso2(name)
        except reg.UnknownCountryError:
            continue  # already reported by check_names on the same field
        if expected != stored_iso2.lower():
            failures.append(
                f"[{source}] {name!r} has iso2 {stored_iso2!r}, "
                f"resolver says {expected!r}"
            )


def check_birth_countries(source: str, names, failures: list):
    """Like check_names, but applies the same historical-entity pre-pass
    build_json.py applies to birth_country values before resolving."""
    resolved = (
        reg.HISTORICAL_BIRTH_COUNTRY_ALIASES.get(n, n)
        for n in names
        if n and n not in reg.INVALID_BIRTH_COUNTRY_VALUES
    )
    check_names(source, resolved, failures)


def main():
    failures = []

    if PLAYERS_CSV.exists():
        with open(PLAYERS_CSV, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        check_names("wc2026_players.csv:nation", (r["nation"] for r in rows), failures)
        check_birth_countries("wc2026_players.csv:birth_country", (r["birth_country"] for r in rows), failures)
        player_nations = {r["nation"] for r in rows if r["nation"]}
    else:
        print(f"  (skipping {PLAYERS_CSV.name} — not found)", file=sys.stderr)
        player_nations = set()

    if COACHES_CSV.exists():
        with open(COACHES_CSV, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        check_names("wc2026_coaches.csv:nation", (r["nation"] for r in rows), failures)
        check_birth_countries("wc2026_coaches.csv:birth_country", (r["birth_country"] for r in rows), failures)
        check_birth_countries("wc2026_coaches.csv:nationality", (r["nationality"] for r in rows), failures)
        coach_nations = {r["nation"] for r in rows if r["nation"]}
    else:
        print(f"  (skipping {COACHES_CSV.name} — not found)", file=sys.stderr)
        coach_nations = set()

    if MAP_DATA.exists():
        map_data = json.loads(MAP_DATA.read_text(encoding="utf-8"))
        check_names("map_data.json:data[].country", (r["country"] for r in map_data.get("data", [])), failures)
        check_iso2("map_data.json:data[]", ((r["country"], r.get("iso2")) for r in map_data.get("data", [])), failures)
    else:
        print(f"  (skipping {MAP_DATA.name} — not found)", file=sys.stderr)

    if ELO_RANK.exists():
        elo = json.loads(ELO_RANK.read_text(encoding="utf-8"))
        rankings = [r for r in elo.get("rankings", []) if not r.get("weirdo")]
        check_names("elo_rank.json:rankings[].name", (r["name"] for r in rankings), failures)
        check_iso2("elo_rank.json:rankings[]", ((r["name"], r.get("iso2")) for r in rankings), failures)
    else:
        print(f"  (skipping {ELO_RANK.name} — not found)", file=sys.stderr)

    if R32_TEAMS.exists():
        r32 = json.loads(R32_TEAMS.read_text(encoding="utf-8"))
        teams = r32.get("teams", [])
        check_names("r32_teams.json:teams[].name", (t["name"] for t in teams), failures)
        check_iso2("r32_teams.json:teams[]", ((t["name"], t.get("iso2")) for t in teams), failures)
    else:
        print(f"  (skipping {R32_TEAMS.name} — not found)", file=sys.stderr)

    # ── WC2026 field coverage — every current nation must have squad + coach rows ──
    wc2026 = reg.wc2026_nations()
    if player_nations:
        missing = sorted(wc2026 - player_nations)
        for name in missing:
            failures.append(f"[wc2026 coverage] {name!r} missing from wc2026_players.csv")
    if coach_nations:
        missing = sorted(wc2026 - coach_nations)
        for name in missing:
            failures.append(f"[wc2026 coverage] {name!r} missing from wc2026_coaches.csv")

    if failures:
        print(f"FAIL: {len(failures)} unresolved/missing entries:\n", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: all country names resolve, all {len(wc2026)} WC2026 nations covered.")


if __name__ == "__main__":
    main()
