"""
Fetch multilingual capitals for the 4 UK home nations via Wikidata and patch
countries.json with their population + capital data.

These nations use synthetic IDs in our system (8260–8263) and subdivision
flag codes (gb-eng, gb-sct, gb-wls, gb-nir), so they're not in the standard
mledoze/countries dataset. This script adds them explicitly.

Population figures: official 2021/2022 census / mid-year estimates.
"""
import json, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent

UK_NATIONS = {
    "8260": {"alpha2": "gb-eng", "name": "England",          "qid": "Q21",  "cap_qid": "Q84"},
    "8261": {"alpha2": "gb-sct", "name": "Scotland",         "qid": "Q22",  "cap_qid": "Q23436"},
    "8262": {"alpha2": "gb-wls", "name": "Wales",            "qid": "Q25",  "cap_qid": "Q10690"},
    "8263": {"alpha2": "gb-nir", "name": "Northern Ireland", "qid": "Q26",  "cap_qid": "Q10686"},
}

POPULATIONS = {
    "8260": 56_490_048,   # England — 2021 census
    "8261":  5_479_000,   # Scotland — 2022 mid-year estimate
    "8262":  3_107_500,   # Wales — 2021 census
    "8263":  1_903_175,   # Northern Ireland — 2021 census
}

WD_QUERY = """
SELECT ?capQid ?capEn ?capFr ?capDe ?capIt ?capEs WHERE {
  VALUES (?capQid) {
    (wd:Q84)    (wd:Q23436) (wd:Q10690) (wd:Q10686)
  }
  OPTIONAL { ?capQid rdfs:label ?capEn FILTER(LANG(?capEn) = "en") }
  OPTIONAL { ?capQid rdfs:label ?capFr FILTER(LANG(?capFr) = "fr") }
  OPTIONAL { ?capQid rdfs:label ?capDe FILTER(LANG(?capDe) = "de") }
  OPTIONAL { ?capQid rdfs:label ?capIt FILTER(LANG(?capIt) = "it") }
  OPTIONAL { ?capQid rdfs:label ?capEs FILTER(LANG(?capEs) = "es") }
}
"""

print("Querying Wikidata for UK home nation capitals …", flush=True)
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

# capQid → {en, fr, de, it, es}  — merge rows to handle multi-row SPARQL results
cap_by_qid = {}
for row in rows:
    qid = row["capQid"]["value"].split("/")[-1]
    entry = cap_by_qid.setdefault(qid, {})
    for lang, key in [("en","capEn"),("fr","capFr"),("de","capDe"),("it","capIt"),("es","capEs")]:
        if key in row:
            entry[lang] = row[key]["value"]

# Fill missing language labels from English (Cardiff, Belfast are identical in all languages)
for qid, entry in cap_by_qid.items():
    en = entry.get("en")
    if en:
        for lang in ("fr", "de", "it", "es"):
            entry.setdefault(lang, en)
print(f"  {len(cap_by_qid)} capital entries received", flush=True)

countries_path = ROOT / "countries.json"
with open(countries_path, encoding="utf-8") as f:
    countries = json.load(f)

for key, info in UK_NATIONS.items():
    cap = cap_by_qid.get(info["cap_qid"], {}) or None
    countries[key] = {
        "id":         int(key),
        "alpha2":     info["alpha2"],
        "alpha3":     "",
        "name":       info["name"],
        "capital":    cap,
        "population": POPULATIONS[key],
    }
    print(f"  {info['name']}: pop={POPULATIONS[key]:,}  cap={cap}", flush=True)

with open(countries_path, "w", encoding="utf-8") as f:
    json.dump(countries, f, ensure_ascii=False, indent=2)
print(f"Written → {countries_path}")
