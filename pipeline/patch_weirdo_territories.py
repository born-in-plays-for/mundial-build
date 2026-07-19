"""
Patch Northern Cyprus and Somaliland into countries.json.

Both are real, populated territories with live eloratings.net ratings but no
official ISO 3166-1 alpha-2/numeric code — update_elo_rankings.py's own
ELO_SPECIAL table already assigns them project-synthetic ids (8264, 8265,
continuing the UK home nations' own self-assigned block) and routes their
Elo entries in directly, the same way it already does for the UK home
nations and Tahiti. This script is the countries.json-side counterpart —
population + capital, for parity with every other non-qualified country
pill (population-scaled sizing, etc.) — same role patch_kosovo.py/
patch_uk_nations.py already play for their own countries.

Population and capital (P1082/P36) pulled live from each territory's own
Wikidata item, not hardcoded — both happen to already carry a P1082 value
(unlike Kosovo, whose 1.76M figure in patch_kosovo.py is a manually-cited
World Bank estimate because Kosovo has no P1082 statement of its own).
"""
import json, urllib.request, urllib.parse
from pathlib import Path

COUNTRIES_PATH = Path(__file__).parent / "countries.json"

TERRITORIES = {
    "8264": {"alpha2": "northern_cyprus", "name": "Northern Cyprus", "qid": "Q23681"},
    "8265": {"alpha2": "somaliland",      "name": "Somaliland",      "qid": "Q34754"},
}

WD_QUERY = """
SELECT ?item ?cap ?pop ?capEn ?capFr ?capDe ?capIt ?capEs WHERE {
  VALUES ?item { wd:Q23681 wd:Q34754 }
  OPTIONAL { ?item wdt:P36 ?cap }
  OPTIONAL { ?item wdt:P1082 ?pop }
  OPTIONAL { ?cap rdfs:label ?capEn FILTER(LANG(?capEn) = "en") }
  OPTIONAL { ?cap rdfs:label ?capFr FILTER(LANG(?capFr) = "fr") }
  OPTIONAL { ?cap rdfs:label ?capDe FILTER(LANG(?capDe) = "de") }
  OPTIONAL { ?cap rdfs:label ?capIt FILTER(LANG(?capIt) = "it") }
  OPTIONAL { ?cap rdfs:label ?capEs FILTER(LANG(?capEs) = "es") }
}
"""


def _query_wikidata():
    body = urllib.parse.urlencode({"query": WD_QUERY}).encode()
    req = urllib.request.Request(
        "https://query.wikidata.org/sparql",
        data=body,
        headers={
            "User-Agent":   "mundial-map/1.0 (cthiebaud)",
            "Accept":       "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read())["results"]["bindings"]

    by_qid = {}
    for row in rows:
        qid = row["item"]["value"].split("/")[-1]
        entry = by_qid.setdefault(qid, {"capital": {}, "population": None})
        for lang, key in [("en","capEn"),("fr","capFr"),("de","capDe"),("it","capIt"),("es","capEs")]:
            if key in row:
                entry["capital"][lang] = row[key]["value"]
        if "pop" in row:
            entry["population"] = int(float(row["pop"]["value"]))
    return by_qid


def main():
    with open(COUNTRIES_PATH, encoding="utf-8") as f:
        countries = json.load(f)

    missing = {k: v for k, v in TERRITORIES.items() if k not in countries}
    if not missing:
        print("countries.json: Northern Cyprus + Somaliland already present — skipped.")
        return

    print("Querying Wikidata for Northern Cyprus / Somaliland capital + population …", flush=True)
    by_qid = _query_wikidata()

    for key, info in missing.items():
        wd = by_qid.get(info["qid"], {})
        capital = wd.get("capital") or {}
        en = capital.get("en")
        if en:
            for lang in ("fr", "de", "it", "es"):
                capital.setdefault(lang, en)
        countries[key] = {
            "id":         int(key),
            "alpha2":     info["alpha2"],
            "alpha3":     "",
            "name":       info["name"],
            "capital":    capital or None,
            "population": wd.get("population"),
        }
        print(f"  {info['name']}: pop={wd.get('population'):,} cap={capital}", flush=True)

    with open(COUNTRIES_PATH, "w", encoding="utf-8") as f:
        json.dump(countries, f, ensure_ascii=False, indent=2)
    print(f"Written → {COUNTRIES_PATH}")


if __name__ == "__main__":
    main()
