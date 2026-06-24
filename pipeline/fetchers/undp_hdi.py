"""
UNDP Human Development Index fetcher.

Downloads the official UNDP HDI composite dataset (bulk CSV/Excel) from
https://hdr.undp.org/data-center/documentation-and-downloads

Strategy:
  1. Scrape the documentation page for a direct CSV/Excel bulk download link.
  2. If scraping fails, fall back to the known stable API endpoint.
  3. Parse the HDI time-series table: columns are years, rows are countries.

Returns a normalized DataFrame with indicator = 'hdi'.
"""

from __future__ import annotations

import io
import re
import pandas as pd
from bs4 import BeautifulSoup

from . import HDI, LIFE_EXP, GNI_PC_PPP, make_session, make_rows, empty_df, throttle

UNDP_DOCS_PAGE = 'https://hdr.undp.org/data-center/documentation-and-downloads'

# Known fallback URLs (update when UNDP restructures the site)
FALLBACK_URLS = [
    # Bulk CSV download — HDI and components, all countries, all years
    'https://hdr.undp.org/sites/default/files/2023-24_HDR/HDR23-24_Composite_indices_complete_time_series.csv',
    'https://hdr.undp.org/sites/default/files/2025_statistical_annex_tables_all.xlsx',
]

# UNDP country name → ISO3 overrides (covers common mismatches)
UNDP_NAME_MAP: dict[str, str] = {
    'Korea (Republic of)':            'KOR',
    'Iran (Islamic Republic of)':     'IRN',
    "Côte d'Ivoire":                  'CIV',
    "Lao People's Democratic Republic": 'LAO',
    'Viet Nam':                       'VNM',
    'Bolivia (Plurinational State of)': 'BOL',
    'Venezuela (Bolivarian Republic of)': 'VEN',
    'Tanzania (United Republic of)':  'TZA',
    'Syrian Arab Republic':           'SYR',
    'Moldova (Republic of)':          'MDA',
    'Congo (Democratic Republic of the)': 'COD',
    'Micronesia (Federated States of)': 'FSM',
    'Palestine, State of':            'PSE',
    'Eswatini (Kingdom of)':          'SWZ',
    'Türkiye':                        'TUR',
    'Cabo Verde':                     'CPV',
    'Czechia':                        'CZE',
    'North Macedonia':                'MKD',
    'Hong Kong, China (SAR)':         'HKG',
    'Kosovo':                         'XKX',
}

# Column patterns to detect the HDI series in a wide-format file
HDI_COL_PATTERN = re.compile(r'^hdi_(\d{4})$', re.I)
YEAR_COL_PATTERN = re.compile(r'^\d{4}$')


def _find_download_links(session) -> list[str]:
    """Scrape UNDP docs page for CSV/Excel bulk download links."""
    try:
        resp = session.get(UNDP_DOCS_PAGE, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if any(href.lower().endswith(ext) for ext in ('.csv', '.xlsx', '.xls')):
                if 'composite' in href.lower() or 'time_series' in href.lower() or 'hdi' in href.lower():
                    if not href.startswith('http'):
                        href = 'https://hdr.undp.org' + href
                    links.append(href)
        return links
    except Exception as e:
        print(f'  UNDP scrape failed: {e}')
        return []


def _parse_csv(content: bytes) -> pd.DataFrame | None:
    """
    Parse a wide-format UNDP CSV with columns like:
        iso3, country, hdi_1990, hdi_1991, …, hdi_2023
    or
        iso3, country, year, hdi, gni_pc_ppp, life_expectancy, …
    """
    try:
        df = pd.read_csv(io.BytesIO(content), encoding='utf-8', low_memory=False)
    except Exception:
        try:
            df = pd.read_csv(io.BytesIO(content), encoding='latin-1', low_memory=False)
        except Exception as e:
            print(f'  CSV parse error: {e}')
            return None

    df.columns = [str(c).strip() for c in df.columns]

    # Detect format: wide (hdi_YYYY cols) or long (year col)
    hdi_cols = {c: int(HDI_COL_PATTERN.match(c).group(1))
                for c in df.columns if HDI_COL_PATTERN.match(c)}

    # Find iso3 / country columns
    code_col = next((c for c in df.columns if c.lower() in ('iso3', 'country_code', 'iso')), None)
    name_col = next((c for c in df.columns if c.lower() in ('country', 'country_name', 'nation')), None)

    if not code_col and not name_col:
        return None

    records = []

    if hdi_cols:
        # Wide format: one row per country, one column per year
        for _, row in df.iterrows():
            code = str(row[code_col]).strip() if code_col else ''
            name = str(row[name_col]).strip() if name_col else code
            # resolve name overrides
            if len(code) != 3:
                code = UNDP_NAME_MAP.get(name, '')
            for col, year in hdi_cols.items():
                v = row[col]
                try:
                    val = float(v)
                except (ValueError, TypeError):
                    continue
                if pd.isna(val):
                    continue
                records.append({
                    'entity_code': code, 'entity_name': name,
                    'year': year,       'indicator': HDI,
                    'value': val,       'source': 'UNDP',
                    'is_proxy': False,
                })
    else:
        # Try long format
        year_col = next((c for c in df.columns if c.lower() == 'year'), None)
        hdi_val_col = next((c for c in df.columns if c.lower() in ('hdi', 'hdi_value', 'value')), None)
        if year_col and hdi_val_col:
            for _, row in df.iterrows():
                code = str(row[code_col]).strip() if code_col else ''
                name = str(row[name_col]).strip() if name_col else code
                if len(code) != 3:
                    code = UNDP_NAME_MAP.get(name, '')
                try:
                    year = int(row[year_col])
                    val  = float(row[hdi_val_col])
                except (ValueError, TypeError):
                    continue
                if pd.isna(val):
                    continue
                records.append({
                    'entity_code': code, 'entity_name': name,
                    'year': year,       'indicator': HDI,
                    'value': val,       'source': 'UNDP',
                    'is_proxy': False,
                })

    return make_rows(records) if records else None


def _parse_excel(content: bytes) -> pd.DataFrame | None:
    """Parse UNDP Excel annexe — tries every sheet for HDI time-series data."""
    import zipfile, io as _io, xml.etree.ElementTree as ET

    # Use raw zip parsing to avoid openpyxl stylesheet bugs (same pattern as ons_gdp)
    NS = {'ss': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
          'r':  'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    try:
        with zipfile.ZipFile(_io.BytesIO(content)) as zf:
            names = zf.namelist()
            shared = []
            if 'xl/sharedStrings.xml' in names:
                tree = ET.parse(zf.open('xl/sharedStrings.xml'))
                for si in tree.getroot().findall('ss:si', NS):
                    t = si.find('ss:t', NS)
                    parts = si.findall('.//ss:t', NS)
                    shared.append(''.join(p.text or '' for p in parts) if t is None else (t.text or ''))

            wb_tree = ET.parse(zf.open('xl/workbook.xml'))
            sheet_rids = {sh.get('name', ''): sh.get(
                '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                for sh in wb_tree.getroot().findall('.//ss:sheet', NS)}

            rid_to_path = {}
            if 'xl/_rels/workbook.xml.rels' in names:
                for rel in ET.parse(zf.open('xl/_rels/workbook.xml.rels')).getroot():
                    rid_to_path[rel.get('Id')] = 'xl/' + rel.get('Target', '').lstrip('/')

            def read_sheet_raw(path):
                rows_out = []
                for row in ET.parse(zf.open(path)).getroot().findall('.//ss:row', NS):
                    row_data = []
                    for c in row.findall('ss:c', NS):
                        t = c.get('t', 'n')
                        v = c.find('ss:v', NS)
                        val = None
                        if v is not None and v.text is not None:
                            if t == 's':
                                val = shared[int(v.text)]
                            else:
                                try:
                                    fv = float(v.text)
                                    val = int(fv) if fv == int(fv) else fv
                                except (ValueError, TypeError):
                                    val = v.text
                        row_data.append(val)
                    rows_out.append(row_data)
                if not rows_out:
                    return pd.DataFrame()
                maxlen = max(len(r) for r in rows_out)
                return pd.DataFrame([r + [None] * (maxlen - len(r)) for r in rows_out])

            for sname, rid in sheet_rids.items():
                if 'hdi' not in sname.lower() and 'human' not in sname.lower():
                    continue
                path = rid_to_path.get(rid)
                if not path or path not in names:
                    continue
                df_raw = read_sheet_raw(path)
                result = _parse_wide_df(df_raw, sname)
                if result is not None:
                    return result
    except Exception as e:
        print(f'  Excel parse error: {e}')
    return None


def _parse_wide_df(df: pd.DataFrame, sheet: str) -> pd.DataFrame | None:
    """
    Try to find a country-code column and year columns in a raw DataFrame,
    then extract HDI values. Handles UNDP Excel annexe layout.
    """
    if df.empty:
        return None
    # Find the row containing year integers 1990-2023
    header_row = None
    for i, row in df.iterrows():
        years = [v for v in row if isinstance(v, (int, float)) and 1990 <= v <= 2030]
        if len(years) >= 10:
            header_row = i
            break
    if header_row is None:
        return None

    year_col_map = {int(df.at[header_row, c]): c for c in df.columns
                    if isinstance(df.at[header_row, c], (int, float))
                    and 1990 <= df.at[header_row, c] <= 2030}

    # Find iso3 column (3-letter codes)
    iso3_col = None
    for c in df.columns:
        vals = df[c].dropna().astype(str)
        if vals.str.match(r'^[A-Z]{3}$').sum() > 20:
            iso3_col = c
            break
    if iso3_col is None:
        return None

    name_col = next((c for c in df.columns if c != iso3_col
                     and df[c].dtype == object
                     and df[c].dropna().astype(str).str.len().mean() > 4), None)

    records = []
    for _, row in df.iloc[header_row + 1:].iterrows():
        code = str(row[iso3_col]).strip() if pd.notna(row[iso3_col]) else ''
        if not re.match(r'^[A-Z]{3}$', code):
            continue
        name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else code
        for year, col in year_col_map.items():
            v = row[col]
            try:
                val = float(v)
            except (ValueError, TypeError):
                continue
            if pd.isna(val) or val <= 0:
                continue
            records.append({
                'entity_code': code, 'entity_name': name,
                'year': year,       'indicator': HDI,
                'value': val,       'source': 'UNDP',
                'is_proxy': False,
            })
    return make_rows(records) if records else None


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    """
    Download and parse the UNDP HDI bulk dataset.
    Tries scraped links first, then known fallback URLs.
    """
    session = make_session()

    links = _find_download_links(session)
    links += [u for u in FALLBACK_URLS if u not in links]

    for url in links:
        if verbose:
            print(f'  UNDP HDI: trying {url} …')
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            content = resp.content
        except Exception as e:
            print(f'    failed: {e}')
            continue

        if url.endswith('.csv'):
            result = _parse_csv(content)
        else:
            result = _parse_excel(content) or _parse_csv(content)

        if result is not None and not result.empty:
            if verbose:
                n = result['entity_code'].nunique()
                print(f'    {len(result):,} rows, {n} entities')
            return result
        print('    could not extract HDI rows from this file')
        throttle(1.0)

    print('  UNDP HDI: all sources exhausted — returning empty DataFrame')
    return empty_df()


if __name__ == '__main__':
    df = fetch_all(verbose=True)
    print(df.head(10))
    print(f'\n{df["entity_code"].nunique()} entities, years: {sorted(df["year"].unique()[:5])} …')
