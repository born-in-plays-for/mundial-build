"""
Fetch population + capital city (multilingual) for all countries/territories.

Sources:
  - mledoze/countries (GitHub raw JSON): name, ISO codes, capital (English)
  - World Bank API (SP.POP.TOTL): population, most recent year
  - Wikidata SPARQL: capital city names in fr, de, it, es, en

Output: data/countries.json  (data submodule root), keyed by ISO numeric id (string).

Entry shape:
  {
    "id":         249,
    "alpha2":     "FR",
    "alpha3":     "FRA",
    "name":       "France",
    "capital":    {"en": "Paris", "fr": "Paris", "de": "Paris", "it": "Parigi", "es": "París"},
    "population": 68374591
  }
"""

import json, subprocess, sys, time, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent

MLEDOZE_URL = "https://raw.githubusercontent.com/mledoze/countries/master/countries.json"
WB_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
    "?format=json&per_page=300&mrv=1"
)
WD_SPARQL = """
SELECT ?alpha2 ?capEn ?capFr ?capDe ?capIt ?capEs WHERE {
  ?country wdt:P31 wd:Q3624078 ;
           wdt:P297 ?alpha2 ;
           wdt:P36 ?capital .
  OPTIONAL { ?capital rdfs:label ?capEn FILTER(LANG(?capEn) = "en") }
  OPTIONAL { ?capital rdfs:label ?capFr FILTER(LANG(?capFr) = "fr") }
  OPTIONAL { ?capital rdfs:label ?capDe FILTER(LANG(?capDe) = "de") }
  OPTIONAL { ?capital rdfs:label ?capIt FILTER(LANG(?capIt) = "it") }
  OPTIONAL { ?capital rdfs:label ?capEs FILTER(LANG(?capEs) = "es") }
}
"""

def fetch(req_or_url, retries=3, backoff=65):
    if isinstance(req_or_url, str):
        req_or_url = urllib.request.Request(req_or_url, headers={"User-Agent": "mundial-map/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req_or_url, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                print(f"  429 rate-limit — waiting {backoff}s …", flush=True)
                time.sleep(backoff)
            else:
                raise

# ── 1. mledoze/countries ─────────────────────────────────────────────────────
print("Fetching mledoze/countries …", flush=True)
mledoze = fetch(MLEDOZE_URL)
print(f"  {len(mledoze)} entries", flush=True)

base = {}   # alpha3 → entry dict
for c in mledoze:
    ccn3 = c.get("ccn3", "")
    if not ccn3:
        continue
    alpha3 = c.get("cca3", "")
    capitals = c.get("capital") or []
    cap_en = capitals[0] if capitals else None
    base[alpha3] = {
        "id":         int(ccn3),
        "alpha2":     c.get("cca2", ""),
        "alpha3":     alpha3,
        "name":       c.get("name", {}).get("common", ""),
        "capital":    {"en": cap_en} if cap_en else None,
        "population": 0,
    }

# ── 2. World Bank population ─────────────────────────────────────────────────
print("Fetching World Bank population …", flush=True)
wb_raw = fetch(WB_URL)
wb_rows = wb_raw[1]
print(f"  {len(wb_rows)} rows (includes aggregates)", flush=True)

wb_pop = {}  # alpha3 → (population int, year str)
for row in wb_rows:
    code = row.get("countryiso3code", "")
    val  = row.get("value")
    if code and val is not None:
        wb_pop[code] = (int(val), row.get("date", ""))

matched = 0
for alpha3, entry in base.items():
    if alpha3 in wb_pop:
        pop, year = wb_pop[alpha3]
        entry["population"] = pop
        entry["pop_year"]   = year
        matched += 1
print(f"  Population matched for {matched}/{len(base)} countries", flush=True)

# ── 3. Wikidata capital translations ─────────────────────────────────────────
print("Fetching Wikidata capital translations …", flush=True)
wd_body = urllib.parse.urlencode({"query": WD_SPARQL}).encode()
wd_req  = urllib.request.Request(
    "https://query.wikidata.org/sparql",
    data=wd_body,
    headers={
        "User-Agent":   "mundial-map/1.0 (cthiebaud)",
        "Accept":       "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    },
)
wd_data = fetch(wd_req)
wd_rows = wd_data["results"]["bindings"]
print(f"  {len(wd_rows)} rows", flush=True)

# Build lowercase alpha2 → {en, fr, de, it, es}
wd_caps = {}
for row in wd_rows:
    a2 = row["alpha2"]["value"].lower()
    wd_caps[a2] = {
        lang: row[key]["value"]
        for lang, key in [("en","capEn"),("fr","capFr"),("de","capDe"),("it","capIt"),("es","capEs")]
        if key in row
    }

# Merge into base entries
wd_matched = 0
for entry in base.values():
    a2 = entry["alpha2"].lower()
    if a2 in wd_caps:
        cap_obj = wd_caps[a2]
        # Keep mledoze English if Wikidata doesn't have it
        if "en" not in cap_obj and entry["capital"]:
            cap_obj["en"] = entry["capital"]["en"]
        entry["capital"] = cap_obj or None
        wd_matched += 1
print(f"  Capital translations matched for {wd_matched}/{len(base)} countries", flush=True)

# ── 4. Save ───────────────────────────────────────────────────────────────────
result = {str(e["id"]): e for e in sorted(base.values(), key=lambda x: x["id"])}

out_path = ROOT / "countries.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"  Written {len(result)} entries → {out_path}")

# ── 5. Apply patches (UK home nations + Kosovo + Northern Cyprus/Somaliland) ──
here = Path(__file__).parent
for patch in ("patch_uk_nations.py", "patch_kosovo.py", "patch_weirdo_territories.py"):
    print(f"\n── {patch} ──────────────────────────────────────────────")
    subprocess.run([sys.executable, str(here / patch)], check=True)
