#!/usr/bin/env python3
"""
fetch_discipline_stats.py — per-team foul/card totals for the 48 WC2026
qualified countries, from api-football's /fixtures/statistics.

Pipeline-internal intermediate (like r32_teams.json/player_wiki.json):
load.py reads pipeline/discipline_stats.json into the team_discipline table;
schema.sql's view_discipline derives per-match averages, fouls-per-card, and
each team's current stage (joining team_status/view_current_round) — nothing
below computes those itself, same division of labour as squad_size.

Reuses data/fixtures.json (already fetched by fetch_fixtures.py) for the
list of fixture ids + status instead of re-hitting /fixtures — only
/fixtures/statistics is called here, once per *finished* fixture (FT/AET/
PEN), and only for fixtures not already in the local cache. That cache
(pipeline/discipline_stats_cache.json, one entry per fixture id, committed —
same "hits a live API, not cheap to redo" reasoning as r32_teams.json) makes
reruns free after the first pass over a given fixture, and lets a run
interrupted by a rate limit resume where it left off.

"Fouls suffered" isn't a field api-football exposes directly — it's derived
per fixture as the opponent's "Fouls" (committed) value in that same match.

Usage:
    export API_FOOTBALL_KEY=your_key_here
    python3 pipeline/fetch_discipline_stats.py
    python3 pipeline/fetch_discipline_stats.py --refresh-cache
"""
import argparse
import json
import os
import sys
import time
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

API_BASE = "https://v3.football.api-sports.io"
FINISHED_STATUSES = ("FT", "AET", "PEN")

ROOT = Path(__file__).parent
FIXTURES_PATH = ROOT.parent / "data" / "fixtures.json"
CACHE_PATH = ROOT / "discipline_stats_cache.json"
OUT_PATH = ROOT / "discipline_stats.json"


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


def stat_value(statistics, stat_type):
    for s in statistics:
        if s["type"] == stat_type:
            return int(s["value"] or 0)
    return 0


def fetch_fixture_statistics(fixture_id, headers, cache, sleep_s):
    key = str(fixture_id)
    if key in cache:
        return cache[key]
    data = fetch_json(f"{API_BASE}/fixtures/statistics", {"fixture": fixture_id}, headers)
    response = data["response"]
    cache[key] = response
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    time.sleep(sleep_s)
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", default=os.environ.get("API_FOOTBALL_KEY"), help="api-football key")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between uncached API calls (raise this on the free tier's 10 req/min limit)")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore discipline_stats_cache.json and refetch every finished fixture")
    parser.add_argument("--refresh-countries", action="store_true", help="Refetch api-football's /countries list instead of using the cache")
    args = parser.parse_args()

    if not args.key:
        print("Error: API key required. Set API_FOOTBALL_KEY env var or use --key.", file=sys.stderr)
        sys.exit(1)
    headers = {"x-apisports-key": args.key}

    if not FIXTURES_PATH.exists():
        print(f"Error: {FIXTURES_PATH} not found — run pipeline/fetch_fixtures.py first.", file=sys.stderr)
        sys.exit(1)
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["fixtures"]
    finished = [f for f in fixtures if f["status"] in FINISHED_STATUSES]
    print(f"{len(finished)}/{len(fixtures)} fixtures finished — fetching statistics for those…", flush=True)

    iso_map = fetch_country_codes(fetch_json, API_BASE, headers, refresh=args.refresh_countries)

    cache = {} if args.refresh_cache else (
        json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    )

    # Seed every WC2026 team with zeros so a team with no finished fixtures
    # yet still gets a row (load.py's team_discipline is NOT NULL throughout).
    wc2026_iso2 = json.loads((ROOT / "country_aliases.json").read_text(encoding="utf-8"))["wc2026_nations"]
    totals = {
        iso2: {"matches": 0, "foulsCommitted": 0, "foulsSuffered": 0, "yellowCards": 0, "redCards": 0}
        for iso2 in wc2026_iso2
    }

    unresolved = set()
    n_missing_stats = 0
    for i, f in enumerate(finished, 1):
        print(f"  [{i}/{len(finished)}] fixture {f['id']} ({f['home']} vs {f['away']})", end="\r", flush=True)
        blocks = fetch_fixture_statistics(f["id"], headers, cache, args.sleep)
        if len(blocks) != 2:
            n_missing_stats += 1
            continue
        for block in blocks:
            iso2 = team_iso2(block["team"]["name"], iso_map)
            if not iso2 or iso2 not in totals:
                unresolved.add(block["team"]["name"])
                continue
            opponent = blocks[1] if block is blocks[0] else blocks[0]
            totals[iso2]["matches"] += 1
            totals[iso2]["foulsCommitted"] += stat_value(block["statistics"], "Fouls")
            totals[iso2]["foulsSuffered"] += stat_value(opponent["statistics"], "Fouls")
            totals[iso2]["yellowCards"] += stat_value(block["statistics"], "Yellow Cards")
            totals[iso2]["redCards"] += stat_value(block["statistics"], "Red Cards")
    print()

    if unresolved:
        print(f"  Warning: could not resolve country for: {sorted(unresolved)}", file=sys.stderr)
    if n_missing_stats:
        print(f"  Warning: {n_missing_stats} finished fixtures had no statistics available yet", file=sys.stderr)

    header = f"{'Country':<24}{'MP':>4}{'Fouls Cmt':>11}{'Fouls Suf':>11}{'YC':>5}{'RC':>5}"
    print(header)
    print("-" * len(header))
    for iso2 in sorted(totals, key=lambda i: reg.display_name(i)):
        t = totals[iso2]
        print(f"{reg.display_name(iso2):<24}{t['matches']:>4}{t['foulsCommitted']:>11}{t['foulsSuffered']:>11}"
              f"{t['yellowCards']:>5}{t['redCards']:>5}")

    payload = {
        "source": "api-football.com",
        "updated": date.today().isoformat(),
        "teams": totals,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
