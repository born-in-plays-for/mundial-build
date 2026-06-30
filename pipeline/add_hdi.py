#!/usr/bin/env python3
"""
Fetch Human Development Index (HDI) from UNDP HDR bulk CSV and write
wc2026_hdi.json in the root directory.  Values are HDI (0–1 scale).

Source: UNDP Human Development Report composite indices, complete time series.
"""
import io
import json
import requests
import pandas as pd
from pathlib import Path

DATA_DIR   = Path(__file__).parent.parent / "data"
EXTRAS_DIR = Path(__file__).parent.parent / "extras"

HDR_CSV_URL = (
    'https://hdr.undp.org/sites/default/files/2023-24_HDR/'
    'HDR23-24_Composite_indices_complete_time_series.csv'
)

# UNDP country name → our project name
UNDP_NAME_MAP = {
    'Iran (Islamic Republic of)':          'Iran',
    'Türkiye':                             'Turkey',
    'Korea (Republic of)':                 'South Korea',
    "Côte d'Ivoire":                       'Ivory Coast',
    'Congo (Democratic Republic of the)':  'DR Congo',
    'Congo':                               'Republic of the Congo',
    'Egypt':                               'Egypt',
    'Cabo Verde':                          'Cape Verde',
    'Czechia':                             'Czech Republic',
    'United States':                       'United States',
    'Bolivia (Plurinational State of)':    'Bolivia',
    'Venezuela (Bolivarian Republic of)':  'Venezuela',
    'Tanzania (United Republic of)':       'Tanzania',
    'Moldova (Republic of)':               'Moldova',
    'Palestine, State of':                 'Palestine',
    'Micronesia (Federated States of)':    'Micronesia',
    'Viet Nam':                            'Vietnam',
    'Syrian Arab Republic':                'Syria',
    'Lao People\'s Democratic Republic':   'Laos',
    'North Macedonia':                     'North Macedonia',
    "Dem. People's Republic of Korea":     'North Korea',
}

print(f'Fetching {HDR_CSV_URL}')
resp = requests.get(HDR_CSV_URL, timeout=60)
resp.raise_for_status()

df = pd.read_csv(io.BytesIO(resp.content), encoding='latin-1', low_memory=False)
print(f'CSV shape: {df.shape}  columns: {list(df.columns[:10])} …')

# HDI columns are named like "hdi_2022"
hdi_cols = sorted([c for c in df.columns if c.startswith('hdi_') and c[4:].isdigit()],
                  reverse=True)
print(f'HDI year columns (latest first): {hdi_cols[:5]}')

# Country name column varies; try common names
name_col = next((c for c in ('country', 'Country', 'country_name', 'Country Name') if c in df.columns), None)
if name_col is None:
    raise ValueError(f'Cannot find country name column. Columns: {list(df.columns)}')

hdi = {}
for _, row in df.iterrows():
    name = str(row[name_col]).strip()
    name = UNDP_NAME_MAP.get(name, name)
    # Use most recent non-null HDI
    for col in hdi_cols:
        val = row.get(col)
        if pd.notna(val) and val != '..':
            try:
                hdi[name] = round(float(val), 4)
            except (ValueError, TypeError):
                pass
            break

print(f'Extracted HDI for {len(hdi)} countries')

pop = json.load(open(DATA_DIR / 'wc2026_map_data.json', encoding='utf-8'))['pop']
missing = [k for k in pop if k not in hdi]
if missing:
    print(f'No HDI for {len(missing)} countries:')
    for m in sorted(missing):
        print(f'  {m!r}')
else:
    print('All pop countries covered.')

out_path = EXTRAS_DIR / 'wc2026_hdi.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(hdi, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
print(f'Done → {out_path.name}')
