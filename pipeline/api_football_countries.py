"""
api_football_countries.py — cached wrapper around api-football's /countries
endpoint.

This is only a fallback name -> iso2 map for country names that
country_registry.py's alias table doesn't recognize at all (see its module
docstring) — it's api-football's own country list, not our alias data, and
known to be unreliable on some entries (see fetch_r32_teams.py's
extract_teams comment re: Congo/Congo-DR). It's static reference data that
essentially never changes between runs, so it's cached to
pipeline/country_codes_cache.json instead of costing an API credit on every
pipeline run. Delete the cache file, or pass refresh=True, to force a
refetch (e.g. if api-football adds a country api-football never had before).
"""
import json
from pathlib import Path

_ROOT = Path(__file__).parent
CACHE_PATH = _ROOT / "country_codes_cache.json"


def fetch_country_codes(fetch_json, api_base: str, headers: dict, refresh: bool = False) -> dict[str, str]:
    """Return a normalised-name -> ISO-alpha-2 map, from cache if present.

    fetch_json: the caller's own callable(url, params, headers) -> parsed
    JSON wrapper (so this stays request-library agnostic and API errors
    surface the same way as the rest of the calling script).
    """
    if not refresh and CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    data = fetch_json(f"{api_base}/countries", {}, headers)
    result = {}
    for c in data.get("response", []):
        if not c.get("code"):
            continue
        code = c["code"].lower()
        name = c["name"]
        result[name.lower()] = code
        result[name.lower().replace("-", " ")] = code

    CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
