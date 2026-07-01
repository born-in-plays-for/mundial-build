#!/usr/bin/env python3
"""
Fetch the 32 teams that qualified for the Round of 32 (seizièmes de finale)
of the 2026 FIFA World Cup using the api-football API.

WC 2026 format: 48 teams → top 2 per group + 8 best 3rd-place → 32 advance.

Usage:
    export API_FOOTBALL_KEY=your_key_here
    python3 pipeline/fetch_r32_teams.py

    # or pass key directly:
    python3 pipeline/fetch_r32_teams.py --key YOUR_KEY

    # RapidAPI host (default is direct api-sports.io):
    python3 pipeline/fetch_r32_teams.py --rapidapi

API key: https://dashboard.api-football.com/  (or via RapidAPI)
"""
import argparse
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dep. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

import country_registry as reg

# Auto-load .env from the project root (two levels up from this script)
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

WC2026_LEAGUE_ID = 1
WC2026_SEASON    = 2026

ROOT = Path(__file__).parent.parent / "data"
OUT  = ROOT / "r32_teams.json"

DIRECT_BASE  = "https://v3.football.api-sports.io"
RAPID_BASE   = "https://api-football-v1.p.rapidapi.com/v3"
RAPID_HOST   = "api-football-v1.p.rapidapi.com"


def make_headers(key: str, rapidapi: bool) -> dict:
    if rapidapi:
        return {
            "X-RapidAPI-Key":  key,
            "X-RapidAPI-Host": RAPID_HOST,
        }
    return {"x-apisports-key": key}


def get(url: str, params: dict, headers: dict) -> dict:
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors", {})
    if errors:
        print(f"API error: {errors}", file=sys.stderr)
        sys.exit(1)
    return data


def fetch_rounds(base: str, headers: dict) -> list[str]:
    data = get(
        f"{base}/fixtures/rounds",
        {"league": WC2026_LEAGUE_ID, "season": WC2026_SEASON},
        headers,
    )
    return data.get("response", [])


def fetch_fixtures_for_round(base: str, headers: dict, round_name: str) -> list[dict]:
    data = get(
        f"{base}/fixtures",
        {
            "league": WC2026_LEAGUE_ID,
            "season": WC2026_SEASON,
            "round":  round_name,
        },
        headers,
    )
    return data.get("response", [])


def find_r32_round(rounds: list[str]) -> str | None:
    """
    api-football may name the first knockout round differently depending on
    the tournament edition. Try the most likely candidates.
    """
    candidates = [
        "Round of 32",
        "1/16-Finals",
        "Sixteenth-finals",
        "Round of 16",   # fallback: some editions collapse naming
    ]
    rounds_lower = {r.lower(): r for r in rounds}
    for c in candidates:
        if c.lower() in rounds_lower:
            return rounds_lower[c.lower()]
    return None


def fetch_country_codes(base: str, headers: dict) -> dict[str, str]:
    """Return a normalised-name→ISO-alpha-2 map from api-football /countries.

    api-football uses hyphenated names in /countries ("South-Africa") but
    spaced names in /fixtures ("South Africa"). We index both forms.
    """
    data = get(f"{base}/countries", {}, headers)
    result = {}
    for c in data.get("response", []):
        if not c.get("code"):
            continue
        code = c["code"].lower()
        name = c["name"]
        result[name.lower()] = code
        result[name.lower().replace("-", " ")] = code
    return result


def extract_teams(fixtures: list[dict], iso_map: dict[str, str]) -> list[dict]:
    seen = {}
    for fix in fixtures:
        for side in ("home", "away"):
            team = fix["teams"][side]
            tid  = team["id"]
            if tid not in seen:
                name = team["name"]
                key  = name.lower()
                iso2 = iso_map.get(key)
                if not iso2:
                    # Fixture names that don't match any /countries entry even
                    # after normalisation — fall back to the shared alias
                    # table (pipeline/country_aliases.json) instead of a
                    # local override dict.
                    try:
                        iso2 = reg.resolve_iso2(name)
                    except reg.UnknownCountryError:
                        iso2 = None
                seen[tid] = {"id": tid, "name": name, "iso2": iso2}
    return sorted(seen.values(), key=lambda t: t["name"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key",     default=os.environ.get("API_FOOTBALL_KEY"), help="api-football key")
    parser.add_argument("--rapidapi", action="store_true", help="Use RapidAPI host instead of direct api-sports.io")
    args = parser.parse_args()

    if not args.key:
        print("Error: API key required. Set API_FOOTBALL_KEY env var or use --key.", file=sys.stderr)
        sys.exit(1)

    base    = RAPID_BASE if args.rapidapi else DIRECT_BASE
    headers = make_headers(args.key, args.rapidapi)

    print(f"Fetching rounds for WC {WC2026_SEASON} (league {WC2026_LEAGUE_ID})…", flush=True)
    rounds = fetch_rounds(base, headers)
    if not rounds:
        print("No rounds found. The tournament may not be in the API yet.", file=sys.stderr)
        sys.exit(1)

    print(f"  Available rounds: {rounds}", flush=True)

    round_name = find_r32_round(rounds)
    if not round_name:
        print(
            "Could not identify the Round of 32 automatically.\n"
            f"Available rounds: {rounds}\n"
            "Re-run with a specific round name.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Fetching country ISO codes…", flush=True)
    iso_map = fetch_country_codes(base, headers)

    print(f"\nFetching fixtures for round: '{round_name}'…", flush=True)
    fixtures = fetch_fixtures_for_round(base, headers, round_name)

    if not fixtures:
        print(f"No fixtures found for '{round_name}'. Round may not be scheduled yet.", file=sys.stderr)
        sys.exit(1)

    teams = extract_teams(fixtures, iso_map)

    print(f"\n{'─'*40}")
    print(f"  {len(teams)} teams in the {round_name}")
    print(f"{'─'*40}")
    no_iso = [t["name"] for t in teams if not t["iso2"]]
    if no_iso:
        print(f"  Warning: no ISO code matched for: {no_iso}", file=sys.stderr)

    for i, t in enumerate(teams, 1):
        print(f"  {i:2}. {t['name']} ({t['iso2'] or '?'})")
    print(f"{'─'*40}\n")

    if len(teams) != 32:
        print(f"Warning: expected 32 teams, got {len(teams)}. Fixtures may be incomplete.", file=sys.stderr)

    from datetime import date
    import json

    payload = {
        "source":  "api-football.com",
        "updated": date.today().isoformat(),
        "round":   round_name,
        "teams":   teams,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written {OUT.relative_to(ROOT.parent)}", flush=True)


if __name__ == "__main__":
    main()
