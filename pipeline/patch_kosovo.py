"""
Patch Kosovo into countries.json and wc2026_elo_rank.json.

Kosovo uses the user-assigned ISO alpha-2 'XK' with no official numeric code;
we assign it numeric 383 (the widely-used user-assigned value).

Population: ~1.76M (World Bank 2022 estimate — Kosovo is not a WB member but
data is published; source: data.worldbank.org/indicator/SP.POP.TOTL?locations=XK)

Capital: Pristina (Q120816 on Wikidata).
"""
import json, urllib.request, urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

WD_QUERY = """
SELECT ?capEn ?capFr ?capDe ?capIt ?capEs WHERE {
  VALUES (?cap) { (wd:Q25270) }
  OPTIONAL { ?cap rdfs:label ?capEn FILTER(LANG(?capEn) = "en") }
  OPTIONAL { ?cap rdfs:label ?capFr FILTER(LANG(?capFr) = "fr") }
  OPTIONAL { ?cap rdfs:label ?capDe FILTER(LANG(?capDe) = "de") }
  OPTIONAL { ?cap rdfs:label ?capIt FILTER(LANG(?capIt) = "it") }
  OPTIONAL { ?cap rdfs:label ?capEs FILTER(LANG(?capEs) = "es") }
}
"""

# ── Patch countries.json (skip if Kosovo already present) ────────────────────
countries_path = Path(__file__).parent / "countries.json"
with open(countries_path, encoding="utf-8") as f:
    countries = json.load(f)

if "383" not in countries:
    print("Querying Wikidata for Pristina translations …", flush=True)
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

    capital = {}
    for row in rows:
        for lang, key in [("en","capEn"),("fr","capFr"),("de","capDe"),("it","capIt"),("es","capEs")]:
            if key in row:
                capital[lang] = row[key]["value"]
    en = capital.get("en", "Pristina")
    for lang in ("fr", "de", "it", "es"):
        capital.setdefault(lang, en)
    print(f"  Capital: {capital}", flush=True)

    countries["383"] = {
        "id":         383,
        "alpha2":     "xk",
        "alpha3":     "xkx",
        "name":       "Kosovo",
        "capital":    capital,
        "population": 1_762_009,   # World Bank 2022
    }
    with open(countries_path, "w", encoding="utf-8") as f:
        json.dump(countries, f, ensure_ascii=False, indent=2)
    print(f"Patched countries.json → Kosovo (id=383, pop=1.76M, cap={capital.get('en')})")
else:
    print("countries.json: Kosovo already present — skipped.")

# ── Patch wc2026_elo_rank.json ────────────────────────────────────────────────
elo_path = DATA_DIR / "wc2026_elo_rank.json"
with open(elo_path, encoding="utf-8") as f:
    elo = json.load(f)

# Remove Kosovo from fifaAbsences (it's now in rankings)
elo["fifaAbsences"] = [e for e in elo.get("fifaAbsences", []) if e.get("iso2") != "xk"]
if elo.get("stats"):
    elo["stats"]["fifaAbsences"] = len(elo["fifaAbsences"])

# Add Kosovo at the end of rankings (rank=null, pts=null — not on eloratings.net)
existing_ids = {r.get("id") for r in elo["rankings"]}
if 383 not in existing_ids:
    elo["rankings"].append({
        "rank":       None,
        "id":         383,
        "iso2":       "xk",
        "name":       "Kosovo",
        "pts":        None,
        "fifaMember": True,
        "weirdo":     False,
    })
    if elo.get("stats"):
        elo["stats"]["total"] = len(elo["rankings"])
        elo["stats"]["fifaMembers"] = sum(1 for r in elo["rankings"] if r.get("fifaMember"))

with open(elo_path, "w", encoding="utf-8") as f:
    json.dump(elo, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"Patched wc2026_elo_rank.json → Kosovo added to rankings (rank=null, pts=null)")
