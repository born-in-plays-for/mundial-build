#!/usr/bin/env python3
"""
Download UK constituent-nation GDP from the ONS bulletin
"Regional economic activity by gross domestic product, UK: 1998 to 2023"
(published 17 April 2025).

Primary source (single-year summary, 2023):
  ONS bulletin Table 1 — GDP at current market prices (£ million)
  https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/
  regionaleconomicactivitybygrossdomesticproductuk/1998to2023

Time-series source (1998–2023):
  ONS Regional GDP reference tables dataset
  https://www.ons.gov.uk/economy/grossdomesticproductgdp/datasets/
  regionalgrossdomesticproductgdp

Cross-checks (informational, printed to stdout):
  - Scottish Government quarterly national accounts
  - NISRA Northern Ireland Composite Economic Index

Outputs:
  pipeline/uk_regional_gdp.csv  — year, england_gdp_m, scotland_gdp_m,
                                   wales_gdp_m, northern_ireland_gdp_m
                                   (£ millions, current market prices)

Usage:
  pip install requests beautifulsoup4 pandas openpyxl
  python3 pipeline/add_uk_regional_gdp.py

  # Inject most recent year into wc2026_gdp.json (GBP→USD conversion):
  python3 pipeline/add_uk_regional_gdp.py --inject [--gbp-usd 1.27]
"""
import argparse
import io
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT     = Path(__file__).parent.parent
PIPELINE = Path(__file__).parent
OUT_CSV  = PIPELINE / 'uk_regional_gdp.csv'
GDP_JSON = ROOT / 'wc2026_gdp.json'

NATIONS = ['England', 'Scotland', 'Wales', 'Northern Ireland']

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; mundial-data-pipeline/1.0)'}

ONS_BULLETIN = (
    'https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/'
    'regionaleconomicactivitybygrossdomesticproductuk/1998to2023'
)
ONS_DATASET = (
    'https://www.ons.gov.uk/economy/grossdomesticproductgdp/datasets/'
    'regionalgrossdomesticproductgdp'
)

NS = {'ss': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r':  'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}

# ── low-level helpers ─────────────────────────────────────────────────────────

def get(url, **kw):
    resp = requests.get(url, headers=HEADERS, timeout=60, **kw)
    resp.raise_for_status()
    return resp


def find_xlsx_links(page_url):
    """Return list of (label, absolute_url) for every .xlsx link on a page."""
    print(f"Scanning: {page_url}")
    soup = BeautifulSoup(get(page_url).text, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '.xlsx' in href.lower() or ('download' in href.lower() and 'xlsx' in href.lower()):
            if not href.startswith('http'):
                href = 'https://www.ons.gov.uk' + href
            links.append((a.get_text(strip=True) or href, href))
    return links


def read_xlsx_sheets(content: bytes) -> dict[str, pd.DataFrame]:
    """
    Parse an xlsx file as raw zip + XML (avoids openpyxl stylesheet bugs).
    Returns {sheet_name: DataFrame} for all sheets.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = zf.namelist()

        # shared strings
        shared = []
        if 'xl/sharedStrings.xml' in names:
            tree = ET.parse(zf.open('xl/sharedStrings.xml'))
            for si in tree.getroot().findall('ss:si', NS):
                t = si.find('ss:t', NS)
                if t is None:
                    parts = si.findall('.//ss:t', NS)
                    shared.append(''.join(p.text or '' for p in parts))
                else:
                    shared.append(t.text or '')

        # sheet name → rId
        wb_tree = ET.parse(zf.open('xl/workbook.xml'))
        sheet_rids = {}
        for sh in wb_tree.getroot().findall('.//ss:sheet', NS):
            rid = sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            sheet_rids[sh.get('name', '')] = rid

        # rId → file path
        rid_to_path = {}
        rels = 'xl/_rels/workbook.xml.rels'
        if rels in names:
            for rel in ET.parse(zf.open(rels)).getroot():
                target = rel.get('Target', '').lstrip('/')
                rid_to_path[rel.get('Id')] = f'xl/{target}'

        def parse_sheet(path):
            rows_out = []
            for row in ET.parse(zf.open(path)).getroot().findall('.//ss:row', NS):
                row_data = []
                for c in row.findall('ss:c', NS):
                    t = c.get('t', '')
                    v = c.find('ss:v', NS)
                    val = None
                    if v is not None and v.text is not None:
                        if t == 's':
                            val = shared[int(v.text)]
                        else:
                            try:
                                val = float(v.text)
                                if val == int(val):
                                    val = int(val)
                            except ValueError:
                                val = v.text
                    row_data.append(val)
                rows_out.append(row_data)
            if not rows_out:
                return pd.DataFrame()
            maxlen = max(len(r) for r in rows_out)
            return pd.DataFrame([r + [None] * (maxlen - len(r)) for r in rows_out])

        result = {}
        for sname, rid in sheet_rids.items():
            path = rid_to_path.get(rid)
            if path and path in names:
                result[sname] = parse_sheet(path)
        return result


# ── table-specific parsers ────────────────────────────────────────────────────

def _clean(v) -> str:
    """Strip whitespace and collapse embedded newlines/multiple spaces."""
    if v is None:
        return ''
    import re
    return re.sub(r'\s+', ' ', str(v)).strip()


def _to_num(v):
    """Parse a cell value to float, handling comma-formatted strings like '2,329,630'."""
    if isinstance(v, (int, float)) and not pd.isna(v):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(',', '').strip())
        except ValueError:
            pass
    return None


def parse_summary_table(df: pd.DataFrame) -> dict | None:
    """
    Parse ONS 'Table 1' layout: a single-year cross-section.
    Columns: region name | population | GDP £m | GDP per head | growth | ...
    Returns {nation: gdp_£m} for the 4 UK nations, or None.
    """
    found = {}
    for _, row in df.iterrows():
        label = _clean(row.iloc[0])
        for nation in NATIONS:
            if label == nation:
                for col_idx in range(1, len(row)):
                    n = _to_num(row.iloc[col_idx])
                    if n is not None and n > 1000:
                        found[nation] = int(n)
                        break
    return found if len(found) == len(NATIONS) else None


def parse_timeseries_table(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Parse a multi-year time-series layout.
    Expects a year-header row and nation-label rows.
    Returns DataFrame indexed by nation, columns = years.
    """
    # Find row with year integers spanning 1990–2030
    year_row_idx = None
    for i, row in df.iterrows():
        years_found = [v for v in row if isinstance(v, (int, float)) and 1990 <= v <= 2030]
        if len(years_found) >= 10:
            year_row_idx = i
            break
    if year_row_idx is None:
        return None

    # Map year → column index
    year_cols = {}
    for c_idx, v in enumerate(df.iloc[year_row_idx]):
        if isinstance(v, (int, float)) and 1990 <= v <= 2030:
            year_cols[int(v)] = c_idx

    # Find rows for each nation
    result = {}
    for _, row in df.iterrows():
        label = _clean(row.iloc[0])
        for nation in NATIONS:
            if label == nation:
                result[nation] = {yr: row.iloc[c] for yr, c in year_cols.items()}

    if len(result) < len(NATIONS):
        return None
    return pd.DataFrame(result).T.rename_axis('nation')


def try_parse_sheet(df: pd.DataFrame, sheet_name: str):
    """Try both parsers; return ('summary', data) or ('timeseries', data) or None."""
    ts = parse_timeseries_table(df)
    if ts is not None:
        print(f"    → time-series layout in '{sheet_name}'")
        return 'timeseries', ts
    sm = parse_summary_table(df)
    if sm is not None:
        print(f"    → single-year summary in '{sheet_name}'")
        return 'summary', sm
    return None


# ── cross-checks ──────────────────────────────────────────────────────────────

def crosscheck_scotland():
    url = 'https://www.gov.scot/collections/gdp-quarterly-national-accounts/'
    print(f"\nCross-check — Scottish Government: {url}")
    try:
        soup = BeautifulSoup(get(url).text, 'html.parser')
        files = [a['href'] for a in soup.find_all('a', href=True)
                 if any(a['href'].lower().endswith(e) for e in ('.xlsx', '.csv', '.xls'))]
        print(f"  Spreadsheets found: {files[:3] or 'none'}")
    except Exception as e:
        print(f"  Could not fetch: {e}")


def crosscheck_nisra():
    url = ('https://www.nisra.gov.uk/statistics/economic-output/'
           'gross-value-added-and-gross-domestic-product')
    print(f"\nCross-check — NISRA NI GVA/GDP: {url}")
    try:
        soup = BeautifulSoup(get(url).text, 'html.parser')
        files = [a['href'] for a in soup.find_all('a', href=True)
                 if any(a['href'].lower().endswith(e) for e in ('.xlsx', '.csv', '.xls', '.ods'))]
        print(f"  Spreadsheets found: {files[:3] or 'none'}")
    except Exception as e:
        print(f"  Could not fetch: {e}")


# ── inject ────────────────────────────────────────────────────────────────────

def inject_into_gdp_json(csv_path: Path, gbp_to_usd: float = 1.27):
    df = pd.read_csv(csv_path, index_col='year')
    latest = int(df.index.max())
    row = df.loc[latest]
    print(f"\nInjecting {latest} UK nation data (£→$ rate {gbp_to_usd}):")
    gdp = json.loads(GDP_JSON.read_text(encoding='utf-8'))
    for nation in NATIONS:
        col = f"{nation.lower().replace(' ', '_')}_gdp_m"
        if col not in row or pd.isna(row[col]):
            print(f"  {nation}: missing, skipped")
            continue
        val_b = round(float(row[col]) * gbp_to_usd / 1000, 2)
        gdp[nation] = val_b
        print(f"  {nation}: £{row[col]:,.0f}M → ${val_b}B")
    GDP_JSON.write_text(
        json.dumps(gdp, ensure_ascii=False, separators=(',', ':'), sort_keys=True),
        encoding='utf-8'
    )
    print(f"Updated {GDP_JSON.name}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inject', action='store_true')
    parser.add_argument('--gbp-usd', type=float, default=1.27)
    args = parser.parse_args()

    timeseries: pd.DataFrame | None = None   # multi-year
    summary_2023: dict | None = None          # fallback single-year

    # ── 1. Search bulletin page and dataset page ──────────────────────────────
    for source_url in (ONS_BULLETIN, ONS_DATASET):
        try:
            links = find_xlsx_links(source_url)
        except Exception as e:
            print(f"  Skipping {source_url}: {e}")
            continue
        if not links:
            print(f"  No .xlsx links found on {source_url}")
            continue
        print(f"  {len(links)} link(s): {[l[0] for l in links]}")
        for label, url in links:
            print(f"  Downloading [{label}] ...")
            try:
                content = get(url).content
            except Exception as e:
                print(f"    HTTP error: {e}")
                continue
            sheets = read_xlsx_sheets(content)
            print(f"    Sheets: {list(sheets)}")
            for sname, df in sheets.items():
                result = try_parse_sheet(df, sname)
                if result is None:
                    continue
                kind, data = result
                if kind == 'timeseries' and timeseries is None:
                    timeseries = data
                elif kind == 'summary' and summary_2023 is None:
                    summary_2023 = data
            if timeseries is not None:
                break
        if timeseries is not None:
            break

    # ── 2. Build output DataFrame ─────────────────────────────────────────────
    col_map = {n: f"{n.lower().replace(' ', '_')}_gdp_m" for n in NATIONS}

    if timeseries is not None:
        ts = timeseries.apply(pd.to_numeric, errors='coerce')
        years = sorted(c for c in ts.columns if isinstance(c, int))
        tidy = ts[years].T.rename_axis('year').reset_index()
        tidy = tidy.rename(columns=col_map)
        print(f"\nTime-series data: {len(tidy)} years")
    elif summary_2023 is not None:
        print("\nFalling back to 2023 single-year summary.")
        tidy = pd.DataFrame([{
            'year': 2023,
            **{col_map[n]: summary_2023.get(n) for n in NATIONS}
        }])
    else:
        print("\nERROR: could not extract any UK nation GDP data.", file=sys.stderr)
        sys.exit(1)

    tidy.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")
    print(tidy.to_string(index=False))

    # ── 3. Cross-checks ───────────────────────────────────────────────────────
    crosscheck_scotland()
    crosscheck_nisra()

    # ── 4. Inject ─────────────────────────────────────────────────────────────
    if args.inject:
        inject_into_gdp_json(OUT_CSV, gbp_to_usd=args.gbp_usd)


if __name__ == '__main__':
    main()
