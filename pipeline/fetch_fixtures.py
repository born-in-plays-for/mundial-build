#!/usr/bin/env python3
"""
fetch_fixtures.py — fetch every WC2026 fixture (played and scheduled) from
api-football and write data/fixtures.json.

Frontend-facing, same as data/elo_rank.json: raw match-level data (kickoff
date, round, teams, score, status) written straight to the submodule, not
routed through the load.py/export.py relational build — nothing here needs
a pid or a person/wiki join, so the DB gains nothing over a passthrough
list. Same living-dataset cadence as fetch_team_status.py — re-run whenever
fixtures are added or results come in.

Usage:
    export API_FOOTBALL_KEY=your_key_here
    python3 pipeline/fetch_fixtures.py
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

ROOT = Path(__file__).parent
OUT = ROOT.parent / "data" / "fixtures.json"


def fetch_json(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        print(f"API error: {data['errors']}", file=sys.stderr)
        sys.exit(1)
    return data


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
    raw = data["response"]
    print(f"  {len(raw)} fixtures", flush=True)

    iso_map = fetch_country_codes(fetch_json, API_BASE, headers, refresh=args.refresh_countries)

    fixtures = []
    unresolved = set()
    for f in raw:
        home_name = f["teams"]["home"]["name"]
        away_name = f["teams"]["away"]["name"]
        home_iso2 = team_iso2(home_name, iso_map)
        away_iso2 = team_iso2(away_name, iso_map)
        if not home_iso2:
            unresolved.add(home_name)
        if not away_iso2:
            unresolved.add(away_name)
        entry = {
            "id": f["fixture"]["id"],
            "date": f["fixture"]["date"],
            "round": f["league"]["round"],
            "status": f["fixture"]["status"]["short"],
            "home": home_iso2,
            "away": away_iso2,
            "goals": {"home": f["goals"]["home"], "away": f["goals"]["away"]},
        }
        penalty = f["score"]["penalty"]
        if penalty["home"] is not None:
            entry["score"] = {"penalty": penalty}
        fixtures.append(entry)

    if unresolved:
        print(f"  Warning: could not resolve country for: {sorted(unresolved)}", file=sys.stderr)

    fixtures.sort(key=lambda x: x["date"])

    payload = {
        "source": "api-football.com",
        "updated": date.today().isoformat(),
        "fixtures": fixtures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    n_played = sum(1 for x in fixtures if x["status"] in ("FT", "AET", "PEN"))
    print(f"\n{len(fixtures)} fixtures ({n_played} played, {len(fixtures) - n_played} scheduled/in progress)")
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
