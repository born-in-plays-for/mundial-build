#!/usr/bin/env python3
"""
geocode_birthplaces.py — resolve every player/coach birth city in
pipeline/map_data.json to lat/lon coordinates, via OpenStreetMap's Nominatim
search API (no key needed).

map_data.json already carries a "birthCity" string per player/coach
(build_json.py, sourced from wc2026_players.csv/wc2026_coaches.csv's own
birth_city column) — this script only geocodes it, keyed by the SAME
(city, birth-country display name) pair load.py resolves for each person, so
a plain dict lookup at load time finds it without re-deriving anything.

City+country pairs are deduplicated first (many players share a birth city —
e.g. several Brazilians born in São Paulo), then geocoded one at a time,
respecting Nominatim's usage policy (max 1 request/sec, identifying
User-Agent). Results are cached in pipeline/geocode_cache.json, keyed by the
exact "City, Country" query string, so a rerun only fetches pairs it hasn't
seen before — same "hits a live external API, not cheap to redo casually"
reasoning as pipeline/discipline_stats_cache.json, and likewise committed
(not gitignored). A pair Nominatim couldn't resolve is cached as null so
it isn't retried every run either; pass --refresh-cache to force a full
re-geocode (e.g. if Nominatim's coverage improves for a previously-missed
city).

Usage:
    python3 pipeline/geocode_birthplaces.py
    python3 pipeline/geocode_birthplaces.py --refresh-cache
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

PIPELINE   = Path(__file__).parent
MAP_DATA   = PIPELINE / "map_data.json"
CACHE_PATH = PIPELINE / "geocode_cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy requires an identifying User-Agent and caps public
# API use at 1 request/second — https://operations.osmfoundation.org/policies/nominatim/
USER_AGENT    = "mundial-build-geocoder/1.0 (https://github.com/born-in-plays-for/mundial-build)"
RATE_LIMIT_S  = 1.1


def collect_city_country_pairs(map_data):
    """-> sorted set of (city, birth-country display name) pairs, matching
    exactly the pairing load.py will look each person up by: exports are
    grouped by rec["country"] (the birth country), natives by the nation
    dict key (birth country == nation for a native by definition)."""
    pairs = set()
    for rec in map_data["data"]:
        for p in rec["players"]:
            if p.get("birthCity"):
                pairs.add((p["birthCity"], rec["country"]))
    for nation, players in map_data["natives"].items():
        for p in players:
            if p.get("birthCity"):
                pairs.add((p["birthCity"], nation))
    return sorted(pairs)


def geocode(city, country):
    """-> {"city", "lat", "lon"} or None if Nominatim has no match."""
    query = f"{city}, {country}"
    url = f"{NOMINATIM_URL}?{urlencode({'q': query, 'format': 'json', 'limit': 1})}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            results = json.load(resp)
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  Warning: geocode request failed for {query!r}: {e}", file=sys.stderr)
        return None
    if not results:
        return None
    r = results[0]
    return {"city": city, "lat": float(r["lat"]), "lon": float(r["lon"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-cache", action="store_true",
                        help="re-geocode every pair, ignoring cached results (incl. cached misses)")
    args = parser.parse_args()

    with open(MAP_DATA, encoding="utf-8") as f:
        map_data = json.load(f)
    pairs = collect_city_country_pairs(map_data)

    cache = {}
    if CACHE_PATH.exists() and not args.refresh_cache:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f).get("cities", {})

    todo = [(city, country) for city, country in pairs
            if f"{city}, {country}" not in cache]
    print(f"{len(pairs)} unique (city, country) pairs, {len(todo)} to geocode "
          f"({len(pairs) - len(todo)} already cached)")

    resolved = sum(1 for v in cache.values() if v is not None)
    for i, (city, country) in enumerate(todo, 1):
        query = f"{city}, {country}"
        result = geocode(city, country)
        cache[query] = result
        if result is not None:
            resolved += 1
        else:
            print(f"  No match: {query!r}", file=sys.stderr)
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} geocoded ({resolved} resolved so far)")
        if i < len(todo):
            time.sleep(RATE_LIMIT_S)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "source":  "nominatim.openstreetmap.org",
            "updated": time.strftime("%Y-%m-%d"),
            "cities":  dict(sorted(cache.items())),
        }, f, ensure_ascii=False, indent=2)

    misses = len(cache) - resolved
    print(f"Wrote {CACHE_PATH}")
    print(f"  {len(cache)} pairs cached, {resolved} resolved, {misses} unresolved")


if __name__ == "__main__":
    main()
