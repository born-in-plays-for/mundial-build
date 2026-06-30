"""
ONS Regional Gross Disposable Household Income (GDHI) fetcher.

Source:
  ONS bulletin "Regional gross disposable household income, UK: 1997 to 2022"
  https://www.ons.gov.uk/economy/regionalaccounts/householdaccounts/bulletins/
  regionalgrossdisposablehouseholdincome/latest

Returns GDHI per head (£) for:
    GB-ENG  England
    GB-SCT  Scotland
    GB-WLS  Wales
    GB-NIR  Northern Ireland

GDHI per head is the closest ONS equivalent to "what households actually
receive after redistribution" — it includes wages, social transfers, and
investment income, minus taxes and social contributions.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
from bs4 import BeautifulSoup

from . import GDHI_PC, UK_NATIONS, make_session, make_rows, empty_df, throttle

ONS_GDHI_URL = (
    'https://www.ons.gov.uk/economy/regionalaccounts/householdaccounts/bulletins/'
    'regionalgrossdisposablehouseholdincome/latest'
)

NATIONS_CANONICAL = {
    'England':          'GB-ENG',
    'Scotland':         'GB-SCT',
    'Wales':            'GB-WLS',
    'Northern Ireland': 'GB-NIR',
}

NS = {
    'ss': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r':  'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def _clean(v) -> str:
    if v is None:
        return ''
    return re.sub(r'\s+', ' ', str(v)).strip()


def _to_num(v) -> float | None:
    if isinstance(v, (int, float)) and not (isinstance(v, float) and pd.isna(v)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(',', '').strip())
        except ValueError:
            pass
    return None


def _find_xlsx_links(session, url: str) -> list[str]:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '.xlsx' in href.lower() or ('download' in href.lower() and 'xlsx' in href.lower()):
            if not href.startswith('http'):
                href = 'https://www.ons.gov.uk' + href
            links.append(href)
    return links


def _read_xlsx_sheets(content: bytes) -> dict[str, pd.DataFrame]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = zf.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            for si in ET.parse(zf.open('xl/sharedStrings.xml')).getroot().findall('ss:si', NS):
                t = si.find('ss:t', NS)
                parts = si.findall('.//ss:t', NS)
                shared.append(''.join(p.text or '' for p in parts) if t is None else (t.text or ''))

        wb = ET.parse(zf.open('xl/workbook.xml')).getroot()
        sheet_rids = {
            sh.get('name', ''): sh.get(
                '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            for sh in wb.findall('.//ss:sheet', NS)
        }
        rid_to_path = {}
        if 'xl/_rels/workbook.xml.rels' in names:
            for rel in ET.parse(zf.open('xl/_rels/workbook.xml.rels')).getroot():
                rid_to_path[rel.get('Id')] = 'xl/' + rel.get('Target', '').lstrip('/')

        def parse_sheet(path):
            rows_out = []
            for row in ET.parse(zf.open(path)).getroot().findall('.//ss:row', NS):
                row_data = []
                for c in row.findall('ss:c', NS):
                    ctype = c.get('t', 'n')
                    v = c.find('ss:v', NS)
                    val = None
                    if v is not None and v.text is not None:
                        if ctype == 's':
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

        return {
            name: parse_sheet(path)
            for name, rid in sheet_rids.items()
            if (path := rid_to_path.get(rid)) and path in names
        }


def _parse_gdhi_sheet(df: pd.DataFrame) -> dict[str, dict[int, float]] | None:
    """
    Look for 'GDHI per head' rows in the sheet (time-series or summary).
    Returns {nation: {year: £_per_head}} or None.

    ONS GDHI tables typically have a column for each year and a row per region.
    We look for a row labeled "GDHI per head" (or similar) for each nation.
    """
    # Find year-header row
    year_row_idx = None
    for i, row in df.iterrows():
        years = [v for v in row if isinstance(v, (int, float)) and 1990 <= v <= 2030]
        if len(years) >= 5:
            year_row_idx = i
            break
    if year_row_idx is None:
        return None

    year_cols = {int(df.at[year_row_idx, c]): c for c in df.columns
                 if isinstance(df.at[year_row_idx, c], (int, float))
                 and 1990 <= df.at[year_row_idx, c] <= 2030}
    if not year_cols:
        return None

    # Find rows for each nation — look for a sub-section header then data rows
    # ONS layout: nation name in col A, GDHI per head values in year columns
    found: dict[str, dict[int, float]] = {}
    current_nation: str | None = None

    for i, row in df.iterrows():
        if i <= year_row_idx:
            continue
        label = _clean(row.iloc[0])
        if label in NATIONS_CANONICAL:
            current_nation = label
        # Look for "per head" row within a nation section
        if current_nation and any(kw in label.lower() for kw in ('per head', 'per capita', 'gdhi')):
            series = {}
            for year, col in year_cols.items():
                n = _to_num(row[col])
                if n is not None and n > 100:   # GDHI per head is typically £10k–£30k
                    series[year] = n
            if series:
                found.setdefault(current_nation, {}).update(series)

    return found if found else None


def _parse_summary_gdhi(df: pd.DataFrame) -> dict[str, tuple[int, float]] | None:
    """
    Fallback: parse single-year GDHI per head table.
    Returns {nation: (year, gdhi_per_head_£)} or None.
    """
    year = None
    for _, row in df.iterrows():
        cell = _clean(row.iloc[0])
        m = re.search(r'\b(19|20)\d{2}\b', cell)
        if m:
            year = int(m.group())
            break

    found = {}
    current_nation = None
    for _, row in df.iterrows():
        label = _clean(row.iloc[0])
        if label in NATIONS_CANONICAL:
            current_nation = label
        if current_nation and 'per head' in label.lower():
            for idx in range(1, len(row)):
                n = _to_num(row.iloc[idx])
                if n is not None and n > 100:
                    found[current_nation] = (year or 2022, n)
                    break

    return found if found else None


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    session = make_session()

    try:
        links = _find_xlsx_links(session, ONS_GDHI_URL)
    except Exception as e:
        print(f'  ONS GDHI: could not load bulletin page: {e}')
        return empty_df()

    if verbose:
        print(f'  ONS GDHI: {len(links)} Excel link(s) found')

    ts_data: dict[str, dict[int, float]] = {}
    summary_data: dict[str, tuple[int, float]] = {}

    for url in links:
        if verbose:
            print(f'    Downloading {url} …')
        try:
            content = session.get(url, timeout=60).content
        except Exception as e:
            print(f'    HTTP error: {e}')
            continue

        sheets = _read_xlsx_sheets(content)
        for sname, df in sheets.items():
            # Prefer sheets that mention GDHI
            if not any(kw in sname.lower() for kw in ('gdhi', 'household', 'income', 'table')):
                continue
            ts = _parse_gdhi_sheet(df)
            if ts and not ts_data:
                ts_data = ts
                if verbose:
                    print(f'    Time-series GDHI found in sheet "{sname}"')
            sm = _parse_summary_gdhi(df)
            if sm and not summary_data:
                summary_data = sm
        throttle(0.5)

    records = []
    if ts_data:
        for nation, series in ts_data.items():
            code = NATIONS_CANONICAL[nation]
            for year, val in series.items():
                records.append({
                    'entity_code': code,
                    'entity_name': nation,
                    'year':        year,
                    'indicator':   GDHI_PC,
                    'value':       val,
                    'source':      'ONS',
                    'is_proxy':    False,   # GDHI per head is the canonical measure
                })
    elif summary_data:
        for nation, (year, val) in summary_data.items():
            code = NATIONS_CANONICAL[nation]
            records.append({
                'entity_code': code,
                'entity_name': nation,
                'year':        year,
                'indicator':   GDHI_PC,
                'value':       val,
                'source':      'ONS',
                'is_proxy':    False,
            })
    else:
        print('  ONS GDHI: no data extracted')
        return empty_df()

    df_out = make_rows(records)
    if verbose:
        for nation in NATIONS_CANONICAL:
            n = len(df_out[df_out['entity_name'] == nation])
            print(f'    {nation}: {n} year(s)')
    return df_out


if __name__ == '__main__':
    df = fetch_all(verbose=True)
    print(df)
