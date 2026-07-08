"""
country_registry.py — single source of truth for country name → iso2 resolution.

Every script that ingests an external source with its own country-naming
convention (Wikipedia, Wikidata, eloratings.net, api-football, …) should
resolve raw country strings through resolve_iso2() before writing them to an
output file. Free-text names in output files should be produced by
canonical_name(iso2), not carried over from the raw source.

Data sources:
  countries.json        — id / alpha2 / alpha3 / canonical name, keyed by numeric id
  country_aliases.json  — known raw-name spelling variants, keyed by iso2,
                           plus the current WC2026 48-team field

An unresolved name raises UnknownCountryError rather than falling through to a
heuristic — a new spelling variant should be a loud build failure, not a
silent wrong-flag bug.
"""
import json
from pathlib import Path

_ROOT           = Path(__file__).parent
_COUNTRIES_PATH = _ROOT / "countries.json"
_ALIASES_PATH   = _ROOT / "country_aliases.json"


class UnknownCountryError(ValueError):
    pass


# Historical-entity judgment calls specific to interpreting a *birth country*
# in this player/coach dataset — NOT universal country-identity facts (e.g.
# "Soviet Union" could be any of 15 present-day countries; here it's always
# resolved to the one actually applicable to this dataset's Uzbek-born
# players). resolve_iso2() does not consult this — callers resolving
# birth-country strings should apply it as a pre-pass first.
HISTORICAL_BIRTH_COUNTRY_ALIASES = {
    "West Germany":         "Germany",
    "Soviet Union":         "Uzbekistan",   # all current cases are Uzbek-born players
    "Netherlands Antilles": "Curaçao",
}

# Wikidata occasionally returns malformed birth-country values.
INVALID_BIRTH_COUNTRY_VALUES = {"]"}

# Cities in the UK -> home nation. Upstream sources (Wikidata P17, Wikipedia
# infoboxes) resolve a UK birthplace to the country-level "United Kingdom" —
# they don't distinguish England/Scotland/Wales/Northern Ireland. This is the
# single shared table both wc2026_birthplaces.py (via build_json.py) and
# wc2026_coaches.py resolve a UK birth city against; previously each script
# kept its own hand-maintained copy, so a city added to one silently stayed
# missing from the other. A city missing from this table must be treated as
# a build failure by the caller (see resolve_uk_home_nation) — not a warning
# that's easy to miss — otherwise a person birth-city-only-in-the-UK ships as
# country-level "United Kingdom" (id 826) instead of one of the four home
# nations (ids 8260-8263).
UK_CITY_TO_NATION = {
    # Scotland
    "Glasgow": "Scotland", "Edinburgh": "Scotland", "Aberdeen": "Scotland",
    "Inverness": "Scotland", "Dumfries": "Scotland", "Irvine": "Scotland",
    "Rutherglen": "Scotland", "Leuchars": "Scotland", "Dalry": "Scotland",
    "Balfron": "Scotland", "Kirriemuir": "Scotland", "Saltcoats": "Scotland",
    "Kilmarnock": "Scotland", "Hamilton": "Scotland",
    # Wales
    "Cardiff": "Wales", "Swansea": "Wales",
    # Northern Ireland
    "Belfast": "Northern Ireland",
    # England — Greater London
    "London": "England", "Croydon": "England", "Ealing": "England",
    "Walthamstow": "England", "Barnet": "England", "Greenwich": "England",
    "Mitcham": "England", "Harold Wood": "England",
    "London Borough of Newham": "England", "Kingston upon Thames": "England",
    "Redbridge": "England",
    # England — North West
    "Manchester": "England", "Liverpool": "England", "Stockport": "England",
    "Warrington": "England", "Macclesfield": "England", "Lancaster": "England",
    # England — North East
    "Sunderland": "England", "Blyth": "England", "Whitley Bay": "England",
    "Washington": "England", "Whitehaven": "England",
    # England — Yorkshire
    "Leeds": "England", "Sheffield": "England", "Barnsley": "England",
    # England — Midlands
    "Birmingham": "England", "Solihull": "England", "Northampton": "England",
    "Leicester": "England", "Stourbridge": "England", "Halesowen": "England",
    # England — East / South / South West
    "Milton Keynes": "England", "Cockermouth": "England", "Torquay": "England",
    "Bristol": "England", "Norwich": "England",
}


def resolve_uk_home_nation(city: str):
    """UK birth city -> home nation display name (England/Scotland/Wales/
    Northern Ireland), or None if city isn't in UK_CITY_TO_NATION yet.

    Callers resolving a birth_country of "United Kingdom" must treat None as
    a build failure (add the missing city to UK_CITY_TO_NATION above), not a
    silent pass-through — "United Kingdom" resolves fine via resolve_iso2()
    on its own, so nothing else catches an unresolved home nation."""
    return UK_CITY_TO_NATION.get(city)


def _load():
    countries = json.loads(_COUNTRIES_PATH.read_text(encoding="utf-8"))
    aliases   = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))

    by_iso2 = {v["alpha2"].lower(): v for v in countries.values()}

    name_to_iso2 = {v["name"].lower(): iso2 for iso2, v in by_iso2.items()}

    alias_to_iso2 = {}
    for iso2, names in aliases.get("aliases", {}).items():
        for name in names:
            alias_to_iso2[name.lower()] = iso2

    wc2026_iso2 = aliases.get("wc2026_nations", [])
    wc2026_display_overrides = aliases.get("wc2026_display_overrides", {})

    return by_iso2, name_to_iso2, alias_to_iso2, wc2026_iso2, wc2026_display_overrides


_by_iso2, _name_to_iso2, _alias_to_iso2, _wc2026_iso2, _wc2026_display_overrides = _load()


def resolve_iso2(raw_name: str) -> str:
    """Resolve a raw country-name string to its canonical lowercase iso2.

    Raises UnknownCountryError if the name doesn't match countries.json's
    canonical name or any known alias — add the missing spelling to
    pipeline/country_aliases.json rather than guessing at the call site.
    """
    key = (raw_name or "").strip().lower()
    iso2 = _name_to_iso2.get(key) or _alias_to_iso2.get(key)
    if not iso2:
        raise UnknownCountryError(
            f"Unrecognized country name {raw_name!r} — add it to "
            f"pipeline/country_aliases.json"
        )
    return iso2


def canonical_name(iso2: str) -> str:
    """iso2 -> the display name from countries.json."""
    entry = _by_iso2.get(iso2.lower())
    if entry is None:
        raise UnknownCountryError(f"Unrecognized iso2 {iso2!r}")
    return entry["name"]


def canonical_id(iso2: str) -> int:
    """iso2 -> the numeric id from countries.json."""
    entry = _by_iso2.get(iso2.lower())
    if entry is None:
        raise UnknownCountryError(f"Unrecognized iso2 {iso2!r}")
    return entry["id"]


def display_name(iso2: str) -> str:
    """iso2 -> this project's established display string (Wikipedia squad-page
    headings / CSV nation & birth_country convention). Same as canonical_name()
    except for the handful of entries in wc2026_display_overrides (e.g. "Czech
    Republic" instead of countries.json's "Czechia"). Use this — not
    canonical_name() directly — to normalize any name string that ends up as a
    display value or grouping key in this project's CSVs/map_data.json, so two
    spelling variants of the same country always collapse to one identical
    string instead of silently forming two separate groups."""
    iso2 = iso2.lower()
    return _wc2026_display_overrides.get(iso2, canonical_name(iso2))


def wc2026_nations() -> frozenset:
    """Display names of the 48 nations in the current WC2026 field."""
    return frozenset(display_name(iso2) for iso2 in _wc2026_iso2)


if __name__ == "__main__":
    # Smoke test: every alias + every wc2026 nation name must resolve.
    failures = []
    for iso2, names in json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))["aliases"].items():
        for name in names:
            try:
                got = resolve_iso2(name)
                assert got == iso2, f"{name!r} resolved to {got!r}, expected {iso2!r}"
            except Exception as e:
                failures.append(f"{name!r}: {e}")

    for name in wc2026_nations():
        try:
            resolve_iso2(name)
        except Exception as e:
            failures.append(f"{name!r}: {e}")

    for bad in ("Narnia", "Not A Country", ""):
        try:
            resolve_iso2(bad)
            failures.append(f"{bad!r} unexpectedly resolved")
        except UnknownCountryError:
            pass

    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  {f}")
        raise SystemExit(1)
    print(f"OK: {len(_wc2026_iso2)} wc2026 nations, "
          f"{sum(len(v) for v in json.loads(_ALIASES_PATH.read_text(encoding='utf-8'))['aliases'].values())} aliases all resolve.")
