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
it isn't retried every run either; pass --retry-misses to re-attempt just
those (e.g. after a FALLBACK_PATTERNS improvement) without re-fetching
everything already resolved, or --refresh-cache to force a full re-geocode
(e.g. if a ranking-logic change like BLOCKED_ADDRESS_TYPES could also affect
previously "resolved" pairs, not just misses).

A scraped birth city that's actually a sub-city administrative unit ("12th
arrondissement of Paris", "Bodø Municipality") often has no direct Nominatim
match — see FALLBACK_PATTERNS, which strips the qualifier and retries with
just the city name, keeping the original string as the label. The remaining
unresolvable minority (corrupted source strings, small villages/parishes
Nominatim doesn't have under that name) isn't handled here — same "add a
hand-verified entry to an overrides file" pattern as
pipeline/birthplace_overrides.json, not yet built for geocoding.

Usage:
    python3 pipeline/geocode_birthplaces.py
    python3 pipeline/geocode_birthplaces.py --retry-misses
    python3 pipeline/geocode_birthplaces.py --refresh-cache
"""
import argparse
import json
import re
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
RESULT_LIMIT  = 5

# Many administrative regions share their capital city's exact name (Italian
# provinces are usually named after their capital — "Parma" the province vs
# "Parma" the city; same for Swiss cantons — "Fribourg"/"Solothurn" the
# canton vs the town). Nominatim's plain-text search ranks matches by
# "importance", which favors the larger administrative region often enough
# that a naive limit=1 silently returns the region's centroid instead of the
# city — off by tens of km, not a missing-data problem but a WRONG one.
#
# addresstype alone can't tell a genuine "the city nested inside this region"
# candidate apart from an unrelated same-named place: Bavaria's independent
# cities (kreisfreie Städte, e.g. Amberg) share admin_level 6 with real
# counties, so Nominatim tags them "county" too despite BEING the city — an
# early version of this fix that blanket-preferred any non-region addresstype
# threw away a correct Amberg-the-city match for an unrelated same-named
# village 150km away. A later version narrowed the swap to addresstype
# exactly "city", which avoided that regression but then missed Fribourg/
# Solothurn (Nominatim tags smaller European towns "town", not "city" — so
# "must literally be typed city" is too narrow a net; a mid-size Swiss town
# just silently fell back to its canton's centroid instead).
#
# The actual invariant that distinguishes both failure modes: a genuine
# nested settlement's coordinates fall INSIDE the region candidate's own
# bounding box (Parma-city sits inside Parma-province's bbox; Fribourg-town
# sits inside Fribourg-canton's bbox) — an unrelated same-named place doesn't
# (Amberg-village's bbox is ~150km from Amberg-county's, disjoint). So: swap
# toward the first non-region candidate that's geographically contained
# within the region candidate's bbox, regardless of its specific addresstype
# label, and only require the region check at all when the top result is
# itself a broad administrative area to begin with.
REGION_ADDRESS_TYPES = {"continent", "country", "state", "region",
                        "county", "province", "state_district"}


def _contains(region, candidate):
    """-> True if candidate's point falls inside region's own bounding box
    (both are raw Nominatim result dicts)."""
    try:
        lat, lon = float(candidate["lat"]), float(candidate["lon"])
        south, north, west, east = (float(x) for x in region["boundingbox"])
    except (KeyError, ValueError):
        return False
    return south <= lat <= north and west <= lon <= east

# Scraped birth cities are sometimes a sub-city administrative unit rather
# than a plain city name Nominatim's free-text search can match directly —
# "12th arrondissement of Paris", "Bodø Municipality", "Karamürsel district".
# The real city name is right there in the string; these patterns strip the
# qualifier and retry with just that, rather than giving up. Order matters —
# more specific suffixes (checked first) must not be swallowed by a broader
# one below them (e.g. "city district" ends in "district").
FALLBACK_PATTERNS = [
    re.compile(r'^\d+(?:st|nd|rd|th)\s+arrondissement of\s+(.+)$', re.IGNORECASE),
    re.compile(r'^(.+?)\s+city district$', re.IGNORECASE),
    re.compile(r'^(.+?)\s+Municipality$'),
    re.compile(r'^(.+?)\s+district$', re.IGNORECASE),
]


def strip_admin_qualifier(city):
    """-> a plainer city name to retry with, or None if no pattern matches."""
    for pattern in FALLBACK_PATTERNS:
        m = pattern.match(city)
        if m:
            return m.group(1)
    return None


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


def _query_nominatim(query):
    """-> best-candidate result dict, or None (no results / request failed).
    Only overrides Nominatim's own top-ranked candidate when that top result
    is region-level (see REGION_ADDRESS_TYPES) AND a lower-ranked candidate's
    coordinates fall inside the region's own bounding box (see _contains) —
    that geographic-nesting check, not the candidate's addresstype label, is
    what tells a genuine "the city within this region" match apart from an
    unrelated same-named place elsewhere (REGION_ADDRESS_TYPES's comment has
    the two real cases — Amberg, Fribourg/Solothurn — that ruled out simpler
    addresstype-only heuristics).

    featureType=settlement restricts results to actual populated places
    (city/town/village/hamlet/municipality boundaries) server-side — without
    it, a short ambiguous query can rank a same-named non-place feature
    (a railway station, a landmark) top by "importance", which no amount of
    addresstype post-filtering catches since it was never a region/city
    candidate to begin with (caught live: "Skalka, Czech Republic" top-
    ranked a Prague railway station over any of the five real places named
    Skalka)."""
    url = (f"{NOMINATIM_URL}?{urlencode({'q': query, 'format': 'json', 'limit': RESULT_LIMIT})}"
           f"&featureType=settlement")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            results = json.load(resp)
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  Warning: geocode request failed for {query!r}: {e}", file=sys.stderr)
        return None
    if not results:
        return None
    top = results[0]
    if top.get("addresstype") in REGION_ADDRESS_TYPES:
        nested = next((r for r in results[1:]
                       if r.get("addresstype") not in REGION_ADDRESS_TYPES
                       and _contains(top, r)), None)
        if nested is not None:
            return nested
    return top


def geocode(city, country):
    """-> {"city", "lat", "lon"} or None if Nominatim has no match, even
    after retrying with an admin-qualifier stripped from `city` (see
    strip_admin_qualifier). `city` in the result is always the ORIGINAL
    scraped name, even when a stripped fallback query is what resolved it —
    the fallback only changes what's sent to Nominatim, not the label a
    client displays."""
    r = _query_nominatim(f"{city}, {country}")
    if r is None:
        plain = strip_admin_qualifier(city)
        if plain is not None:
            r = _query_nominatim(f"{plain}, {country}")
    if r is None:
        return None
    # addresstype is diagnostic only (not consumed by load.py) — kept so a
    # future audit can spot-check without re-querying Nominatim from scratch.
    return {"city": city, "lat": float(r["lat"]), "lon": float(r["lon"]),
            "addresstype": r.get("addresstype")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-cache", action="store_true",
                        help="re-geocode every pair, ignoring cached results (incl. cached misses)")
    parser.add_argument("--retry-misses", action="store_true",
                        help="re-geocode only pairs cached as unresolved (null); leaves "
                             "already-resolved pairs untouched, unlike --refresh-cache")
    args = parser.parse_args()

    with open(MAP_DATA, encoding="utf-8") as f:
        map_data = json.load(f)
    pairs = collect_city_country_pairs(map_data)

    cache = {}
    if CACHE_PATH.exists() and not args.refresh_cache:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f).get("cities", {})
    if args.retry_misses:
        cache = {k: v for k, v in cache.items() if v is not None}

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
