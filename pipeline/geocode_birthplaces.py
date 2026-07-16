#!/usr/bin/env python3
"""
geocode_birthplaces.py — resolve player/coach birth cities in
pipeline/map_data.json to lat/lon coordinates, via OpenStreetMap's Nominatim
search API (no key needed).

This is the FALLBACK path only: map_data.json's "birthCity" carries an
optional "birthLat"/"birthLon" too, when wc2026_birthplaces.py's
enrich_birth_coordinates already resolved the person's own Wikidata P19
claim to a coordinate — that's disambiguated by construction (P19 points at
one specific place entity, never a bare name) and takes priority in
load.py, so this script only ever runs for people without one. Free-text
search over a bare city NAME can't tell two different real places with the
same name apart — see the module-level "Montreuil, France" note below —
which per-person Wikidata coordinates avoid entirely; this script remains
necessary only for the residual minority with no usable P19 claim.

map_data.json already carries a "birthCity" string per player/coach
(build_json.py, sourced from wc2026_players.csv/wc2026_coaches.csv's own
birth_city column) — this script geocodes it, keyed by the SAME (city,
birth-country display name) pair load.py resolves for each such person, so
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
(e.g. if a ranking-logic change like REGION_ADDRESS_TYPES/_contains could
also affect previously "resolved" pairs, not just misses).

A scraped birth city that's actually a sub-city administrative unit ("12th
arrondissement of Paris", "Bodø Municipality") often has no direct Nominatim
match — see FALLBACK_PATTERNS, which strips the qualifier and retries with
just the city name. `city` in a resolved entry stays that ORIGINAL scraped
string (the label a client displays) even when the stripped form is what
actually got the match; the stripped form itself is kept alongside it as
`actualCityName` (e.g. "Paris" for a `city` of "12th arrondissement of
Paris") — present only when FALLBACK_PATTERNS actually matched, same
"only when it differs" convention as e.g. schema.sql's `person.en_title`.
This is a pure string transformation (no Nominatim call needed), so it's
backfilled unconditionally on every run, not gated behind a flag the way
`population` is.

pipeline/geocode_overrides.json is a hand-verified, cited-source correction
list, checked FIRST — before any Nominatim query at all — so a present
entry always wins outright (same precedent as
pipeline/surname_overrides.json, NOT pipeline/birthplace_overrides.json's
"only fills gaps" one). This matters because Nominatim finding *a* result
doesn't mean it found the *right* one: "Montreuil, France" confidently
resolves to Montreuil-sur-Mer (a ~1,900-person village, apparently ranked
above real candidates by Nominatim's "importance" score — likely its
*Les Misérables* fame) instead of Montreuil, Seine-Saint-Denis (the actual
110,000-person Paris suburb every currently-known WC2026 player named
"Montreuil" was verified born in via Wikidata P19) — a wrong RESULT, not a
missing one, so an override that only kicked in when Nominatim found
nothing could never have fixed it. Overrides also cover the traditional
gap-filling case (corrupted source strings, small villages/parishes
Nominatim doesn't index under that name, or a case needing real research to
identify at all) — those simply have no live Nominatim result to lose to
in the first place.

Each resolved result also carries `population`, when Nominatim's matched
place happens to carry an OSM `population` tag (requested via
`extratags=1`) — deliberately NOT a second lookup against a different
dataset (e.g. GeoNames' cities1000 dump, already used elsewhere for KDE
population weighting): joining that in by nearest coordinate would need its
own new fuzzy-matching logic, on top of the city-identity resolution this
script already does. `population` is `None` when the tag is simply absent
(most small places) or for a `geocode_overrides.json` override (which never
has a live Nominatim result to read it from) — a coverage gap, not a
failure, same as an unresolved geocode.

Usage:
    python3 pipeline/geocode_birthplaces.py
    python3 pipeline/geocode_birthplaces.py --retry-misses
    python3 pipeline/geocode_birthplaces.py --refresh-cache
    python3 pipeline/geocode_birthplaces.py --add-population
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

PIPELINE     = Path(__file__).parent
MAP_DATA     = PIPELINE / "map_data.json"
CACHE_PATH   = PIPELINE / "geocode_cache.json"
OVERRIDES_PATH = PIPELINE / "geocode_overrides.json"


def load_overrides():
    """-> {"City, Country": {"lat", "lon"}}, the hand-verified fallback for
    pairs Nominatim can't resolve on its own — see geocode_overrides.json's
    _comment and this module's docstring."""
    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        return json.load(f)["overrides"]

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
    dict key (birth country == nation for a native by definition).

    Skips anyone who already carries birthLat/birthLon — a person's own
    Wikidata P19 coordinate (see wc2026_birthplaces.py's
    enrich_birth_coordinates) is disambiguated by construction and takes
    priority over this script's free-text Nominatim search in load.py, so
    there's nothing for Nominatim to usefully resolve for them (also
    shrinks how many live queries this script makes)."""
    pairs = set()
    for rec in map_data["data"]:
        for p in rec["players"]:
            if p.get("birthCity") and p.get("birthLat") is None:
                pairs.add((p["birthCity"], rec["country"]))
    for nation, players in map_data["natives"].items():
        for p in players:
            if p.get("birthCity") and p.get("birthLat") is None:
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
           f"&featureType=settlement&extratags=1")
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


def _resolve(city, country):
    """-> raw Nominatim result dict for (city, country), retrying with an
    admin-qualifier stripped from `city` (see strip_admin_qualifier) if the
    direct query finds nothing, or None if neither does. Shared by geocode()
    and --add-population's backfill pass so both use the exact same
    resolution the cached lat/lon already came from."""
    r = _query_nominatim(f"{city}, {country}")
    if r is None:
        plain = strip_admin_qualifier(city)
        if plain is not None:
            r = _query_nominatim(f"{plain}, {country}")
    return r


def _population_of(r):
    """-> population from a raw Nominatim result's extratags, as the exact
    raw string OSM carries it (not coerced to a number — no downstream
    consumer does arithmetic on it, and OSM's own tag isn't reliably
    numeric anyway, e.g. a value like "2.618" seen live), or None if the
    tag is absent (most small places don't carry one). Nominatim sometimes
    returns "extratags": null rather than omitting the key entirely, so
    `.get("extratags", {})` alone isn't enough — that default only kicks in
    when the key is missing, not when it's present but null."""
    pop = (r.get("extratags") or {}).get("population") if r else None
    return pop or None


def geocode(city, country, overrides):
    """-> {"city", "lat", "lon", "population", "actualCityName"?} or None if
    nothing resolves it. geocode_overrides.json is checked FIRST,
    unconditionally — a present override always wins over a live query
    (same "always wins outright" precedent as surname_overrides.json, not
    birthplace_overrides.json's "fills gaps only" one), because Nominatim
    finding *a* result doesn't mean it found the *right* one — this
    ordering is what lets an override CORRECT a wrong result, not just fill
    a gap when Nominatim finds nothing at all (the bug it was added for:
    "Montreuil, France" confidently resolved to tiny Montreuil-sur-Mer
    instead of the actual Montreuil, Seine-Saint-Denis — a real result,
    just the wrong homonym; see pipeline/README.md). `city` in the result
    is always the ORIGINAL scraped name, even when a stripped fallback
    query is what resolved it — the fallback only changes what's sent to
    Nominatim, not the label a client displays; `actualCityName` carries
    that stripped form separately, only when FALLBACK_PATTERNS actually
    matched `city`."""
    actual = strip_admin_qualifier(city)
    override = overrides.get(f"{city}, {country}")
    if override is not None:
        # Never queries Nominatim at all when an override is present — no
        # population tag to read either.
        result = {"city": city, "lat": override["lat"], "lon": override["lon"],
                  "addresstype": "override", "population": None}
    else:
        r = _resolve(city, country)
        if r is None:
            return None
        # addresstype is diagnostic only (not consumed by load.py) — kept so
        # a future audit can spot-check without re-querying Nominatim.
        result = {"city": city, "lat": float(r["lat"]), "lon": float(r["lon"]),
                  "addresstype": r.get("addresstype"), "population": _population_of(r)}
    if actual is not None:
        result["actualCityName"] = actual
    return result


def _backfill_actual_city_names(cache):
    """Adds 'actualCityName' to every resolved cache entry whose 'city'
    matches a FALLBACK_PATTERNS admin-qualifier and doesn't have it yet —
    pure string derivation from data already in the cache, no Nominatim
    call needed, so safe (and cheap) to run unconditionally on every
    invocation rather than gating it behind its own --add-* flag."""
    for entry in cache.values():
        if entry is None or "actualCityName" in entry:
            continue
        actual = strip_admin_qualifier(entry["city"])
        if actual is not None:
            entry["actualCityName"] = actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-cache", action="store_true",
                        help="re-geocode every pair, ignoring cached results (incl. cached misses)")
    parser.add_argument("--retry-misses", action="store_true",
                        help="re-geocode only pairs cached as unresolved (null); leaves "
                             "already-resolved pairs untouched, unlike --refresh-cache")
    parser.add_argument("--add-population", action="store_true",
                        help="backfill 'population' onto already-cached, already-resolved "
                             "entries that predate this field, without re-resolving lat/lon; "
                             "skips override entries (never have a live Nominatim result)")
    args = parser.parse_args()

    with open(MAP_DATA, encoding="utf-8") as f:
        map_data = json.load(f)
    pairs = collect_city_country_pairs(map_data)
    overrides = load_overrides()

    cache = {}
    if CACHE_PATH.exists() and not args.refresh_cache:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f).get("cities", {})
    if args.retry_misses:
        cache = {k: v for k, v in cache.items() if v is not None}

    if args.add_population:
        todo_pop = [(city, country) for city, country in pairs
                    if (entry := cache.get(f"{city}, {country}")) is not None
                    and entry.get("addresstype") != "override"
                    and "population" not in entry]
        print(f"{len(todo_pop)} cached cities missing population data")
        checked = 0
        for i, (city, country) in enumerate(todo_pop, 1):
            query = f"{city}, {country}"
            population = _population_of(_resolve(city, country))
            cache[query]["population"] = population
            checked += 1
            if population is not None:
                print(f"  {query}: population {population}")
            if i % 25 == 0 or i == len(todo_pop):
                print(f"  {i}/{len(todo_pop)} checked")
            if i < len(todo_pop):
                time.sleep(RATE_LIMIT_S)
        _backfill_actual_city_names(cache)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "source":  "nominatim.openstreetmap.org",
                "updated": time.strftime("%Y-%m-%d"),
                "cities":  dict(sorted(cache.items())),
            }, f, ensure_ascii=False, indent=2)
        found = sum(1 for city, country in todo_pop
                    if cache[f"{city}, {country}"]["population"] is not None)
        print(f"Wrote {CACHE_PATH}")
        print(f"  {checked} checked, {found} had a population tag")
        return

    todo = [(city, country) for city, country in pairs
            if f"{city}, {country}" not in cache]
    print(f"{len(pairs)} unique (city, country) pairs, {len(todo)} to geocode "
          f"({len(pairs) - len(todo)} already cached)")

    resolved = sum(1 for v in cache.values() if v is not None)
    for i, (city, country) in enumerate(todo, 1):
        query = f"{city}, {country}"
        result = geocode(city, country, overrides)
        cache[query] = result
        if result is not None:
            resolved += 1
        else:
            print(f"  No match: {query!r}", file=sys.stderr)
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} geocoded ({resolved} resolved so far)")
        if i < len(todo):
            time.sleep(RATE_LIMIT_S)

    _backfill_actual_city_names(cache)
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
