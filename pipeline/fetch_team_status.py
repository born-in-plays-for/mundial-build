#!/usr/bin/env python3
"""
fetch_team_status.py — determine each WC2026 team's tournament status
(alive, or eliminated + at which round + when) from api-football fixture
results, and write pipeline/team_status.json for pipeline/load.py.

Elimination logic:
  - Knockout rounds (Round of 32 onward): a finished fixture (status FT/
    AET/PEN) carries a boolean "winner" on each side; the loser is
    eliminated at that round, dated to the fixture's kickoff date, with
    "lostTo" recording who beat them. lostTo isn't just record-keeping: a
    team that appears as someone's lostTo has thereby proven it WON that
    round and is now playing the next one — which derives every ALIVE
    team's current round too, from this same eliminated-only data, with no
    separate field or concept needed (see schema.sql's view_current_round).
  - Group stage: WC2026's format (12 groups of 4, top 2 + 8 best thirds
    advance) has real tie-break rules this script does not replicate.
    Instead, once every group-stage fixture is finished, any WC2026 team
    NOT among the round-of-32 fixtures' participants is eliminated —
    absence from the round of 32 bracket IS the tie-break outcome, already
    computed by whoever seeds that round. Tagged "Group Stage", undated,
    no lostTo (round-robin, no single deciding opponent).

This is a living dataset, same cadence as build_player_wiki.py /
update_elo_rankings.py — re-run whenever fixtures finish.

Usage:
    export API_FOOTBALL_KEY=your_key_here
    python3 pipeline/fetch_team_status.py
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dep. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

import country_registry as reg
from api_football_countries import fetch_country_codes

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

WC2026_LEAGUE_ID = 1
WC2026_SEASON = 2026
API_BASE = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}

# Knockout stage names as api-football actually returns them for this
# league/season (verified live — see fetch_r32_teams.py's find_r32_round
# for the naming-varies-by-edition caveat; add fallbacks here if a future
# re-run reports an unrecognized round name for a knockout fixture).
KNOCKOUT_STAGES = ["Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final"]

ROOT = Path(__file__).parent
OUT = ROOT / "team_status.json"


def fetch_json(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        print(f"API error: {data['errors']}", file=sys.stderr)
        sys.exit(1)
    return data


def classify_round(round_name):
    """-> 'group' | one of KNOCKOUT_STAGES | None (unrecognized)."""
    if round_name.lower().startswith("group stage"):
        return "group"
    for stage in KNOCKOUT_STAGES:
        if stage.lower() == round_name.lower():
            return stage
    return None


def team_iso2(name, iso_map):
    try:
        return reg.resolve_iso2(name)
    except reg.UnknownCountryError:
        return iso_map.get(name.lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh-countries", action="store_true",
                        help="Refetch api-football's /countries list instead of using the cache")
    args = parser.parse_args()

    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        print("Error: API_FOOTBALL_KEY required (.env or env var).", file=sys.stderr)
        sys.exit(1)
    headers = {"x-apisports-key": key}

    print(f"Fetching all WC{WC2026_SEASON} fixtures…", flush=True)
    data = fetch_json(f"{API_BASE}/fixtures",
                      {"league": WC2026_LEAGUE_ID, "season": WC2026_SEASON}, headers)
    fixtures = data["response"]
    print(f"  {len(fixtures)} fixtures", flush=True)

    iso_map = fetch_country_codes(fetch_json, API_BASE, headers, refresh=args.refresh_countries)

    by_stage = {}
    for f in fixtures:
        stage = classify_round(f["league"]["round"])
        by_stage.setdefault(stage, []).append(f)

    # An unrecognized round with a FINISHED fixture in it means real
    # elimination data would be silently dropped — fail loudly instead
    # (same "fail loudly, don't fall through to a heuristic" philosophy as
    # country_registry.UnknownCountryError). An unrecognized round with
    # nothing decided yet is harmless forward-looking noise — warn only.
    unrecognized = by_stage.get(None, [])
    decided_unrecognized = [f for f in unrecognized if f["fixture"]["status"]["short"] in FINISHED]
    if decided_unrecognized:
        names = sorted({f["league"]["round"] for f in decided_unrecognized})
        print(f"FATAL: {len(decided_unrecognized)} finished fixture(s) in unrecognized "
              f"round(s) {names} — their eliminations would be silently dropped. Add the "
              f"round name to KNOCKOUT_STAGES (or the group-stage prefix check) and re-run.",
              file=sys.stderr)
        sys.exit(1)
    if unrecognized:
        names = sorted({f["league"]["round"] for f in unrecognized})
        print(f"  Warning: unrecognized round name(s) with nothing decided yet, ignored: "
              f"{names}", file=sys.stderr)

    eliminated = {}  # iso2 -> {"round": ..., "date": <ISO date> | None, "lostTo": <iso2> | None}

    # ── Group stage: decide only once every group fixture is finished ────
    group_fixtures = by_stage.get("group", [])
    group_done = bool(group_fixtures) and all(
        f["fixture"]["status"]["short"] in FINISHED for f in group_fixtures)
    r32_fixtures = by_stage.get("Round of 32", [])
    if group_done:
        if r32_fixtures:
            r32_iso2 = set()
            for f in r32_fixtures:
                for side in ("home", "away"):
                    iso2 = team_iso2(f["teams"][side]["name"], iso_map)
                    if iso2:
                        r32_iso2.add(iso2)
            wc2026_iso2 = {reg.resolve_iso2(n) for n in reg.wc2026_nations()}
            for iso2 in sorted(wc2026_iso2 - r32_iso2):
                eliminated[iso2] = {"round": "Group Stage", "date": None, "lostTo": None}
        else:
            print("  Group stage finished but Round of 32 isn't scheduled/known "
                  "yet — skipping group-stage elimination for now", file=sys.stderr)

    # ── Knockout rounds: each finished fixture's loser is eliminated ─────
    for stage in KNOCKOUT_STAGES:
        for f in by_stage.get(stage, []):
            if f["fixture"]["status"]["short"] not in FINISHED:
                continue
            home, away = f["teams"]["home"], f["teams"]["away"]
            if home["winner"] is None:
                continue  # finished but no winner recorded — shouldn't happen, guard anyway
            loser, winner = (away, home) if home["winner"] else (home, away)
            iso2 = team_iso2(loser["name"], iso_map)
            if not iso2:
                print(f"  Warning: could not resolve country for eliminated "
                      f"team {loser['name']!r}", file=sys.stderr)
                continue
            winner_iso2 = team_iso2(winner["name"], iso_map)
            if not winner_iso2:
                print(f"  Warning: could not resolve country for winning "
                      f"team {winner['name']!r} — {loser['name']}'s lostTo omitted",
                      file=sys.stderr)
            eliminated[iso2] = {"round": stage, "date": f["fixture"]["date"][:10],
                                "lostTo": winner_iso2}

    payload = {
        "source":     "api-football.com",
        "updated":    date.today().isoformat(),
        "eliminated": dict(sorted(eliminated.items())),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n{len(eliminated)} team(s) eliminated so far:")
    for iso2, info in sorted(eliminated.items(), key=lambda kv: (kv[1]["round"], kv[0])):
        suffix = f" ({info['date']}, lost to {info['lostTo']})" if info["date"] else ""
        print(f"  {iso2}: {info['round']}{suffix}")
    print(f"\nWrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
