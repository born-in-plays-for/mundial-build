#!/usr/bin/env python3
"""
Fetch GDP (current USD, most recent year) from World Bank API and write
wc2026_gdp.json in the root directory.  Values are GDP in billions USD.

UK home nations (England, Scotland, Wales, Northern Ireland) are excluded —
World Bank only has data for "United Kingdom" as a whole.
"""
import json
import requests
from pathlib import Path

DATA_DIR   = Path(__file__).parent.parent / "data"
EXTRAS_DIR = Path(__file__).parent.parent / "extras"

WB_NAME_MAP = {
    'Iran, Islamic Rep.':              'Iran',
    'Turkiye':                         'Turkey',
    'Korea, Rep.':                     'South Korea',
    "Cote d'Ivoire":                   'Ivory Coast',
    'Congo, Dem. Rep.':                'DR Congo',
    'Congo, Rep.':                     'Republic of the Congo',
    'Egypt, Arab Rep.':                'Egypt',
    'Cabo Verde':                      'Cape Verde',
    'Czechia':                         'Czech Republic',
    'Curacao':                         'Curaçao',
    'Kyrgyz Republic':                 'Kyrgyzstan',
    'Slovak Republic':                 'Slovakia',
    'Venezuela, RB':                   'Venezuela',
    'Yemen, Rep.':                     'Yemen',
    'Bahamas, The':                    'Bahamas',
    'Gambia, The':                     'Gambia',
    'Lao PDR':                         'Laos',
    'Micronesia, Fed. Sts.':           'Micronesia',
    "St. Lucia":                       'Saint Lucia',
    'St. Kitts and Nevis':             'Saint Kitts and Nevis',
    'St. Vincent and the Grenadines':  'Saint Vincent and the Grenadines',
}

# ── 1. Fetch World Bank API ───────────────────────────────────────────────────
url = (
    'https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.CD'
    '?format=json&per_page=300&mrv=1'
)
print(f"Fetching {url}")
resp = requests.get(url, timeout=30)
resp.raise_for_status()
payload = resp.json()

gdp = {}
for entry in payload[1]:
    if entry['value'] is None:
        continue
    name = entry['country']['value']
    name = WB_NAME_MAP.get(name, name)
    gdp[name] = round(entry['value'] / 1e9, 2)

print(f"Fetched {len(gdp)} countries")

# ── 2. Coverage report ────────────────────────────────────────────────────────
pop = json.load(open(DATA_DIR / 'wc2026_map_data.json', encoding='utf-8'))['pop']
missing = [k for k in pop if k not in gdp]
if missing:
    print(f"No GDP for {len(missing)} pop countries (expected for UK home nations):")
    for m in sorted(missing):
        print(f"  {m!r}")
else:
    print("All pop countries covered.")

# ── 3. Write standalone JSON ──────────────────────────────────────────────────
out_path = EXTRAS_DIR / 'wc2026_gdp.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(gdp, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
print(f"Done → {out_path.name}")
