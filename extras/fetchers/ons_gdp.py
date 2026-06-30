"""
ONS GDP by UK constituent nation fetcher.

Primary source:
  ONS bulletin "Regional economic activity by GDP, UK: 1998–2023"
  https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/
  regionaleconomicactivitybygrossdomesticproductuk/1998to2023

Returns GDP at current market prices (£ millions) for:
    GB-ENG  England
    GB-SCT  Scotland
    GB-WLS  Wales
    GB-NIR  Northern Ireland

Methodological notes:
  - Data is GVA-based at the regional level; taxes-less-subsidies adjustment
    applied at UK level. This is documented as is_proxy=True.
  - England GDP is a residual (UK minus Scotland, Wales, N.Ireland, Extra-Regio).
    Flagged in the 'notes' column of the orchestrator output.
  - Scotland figures include allocated North Sea revenue by default.
    A separate Scotland-ex-North-Sea series is not available from this source.
  - Values are in GBP; PPP conversion at UK sovereign level only.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
from bs4 import BeautifulSoup

from . import GDP_PC_PPP, POPULATION, UK_NATIONS, make_session, make_rows, empty_df, throttle

ONS_BULLETIN_URL = (
    'https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/'
    'regionaleconomicactivitybygrossdomesticproductuk/1998to2023'
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
    """Parse xlsx as raw zip+XML, immune to openpyxl stylesheet bugs."""
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


def _parse_summary(df: pd.DataFrame) -> dict[str, tuple[int, float]] | None:
    """
    Parse single-year summary table (Table 1: GDP by country/region, one year).
    Returns {nation_name: (year, gdp_£m)} or None.
    """
    # Detect year from title row
    year = None
    for _, row in df.iterrows():
        cell = _clean(row.iloc[0])
        m = re.search(r'\b(19|20)\d{2}\b', cell)
        if m:
            year = int(m.group())
            break
    if year is None:
        return None

    found = {}
    for _, row in df.iterrows():
        label = _clean(row.iloc[0])
        for nation in NATIONS_CANONICAL:
            if label == nation:
                for idx in range(1, len(row)):
                    n = _to_num(row.iloc[idx])
                    if n is not None and n > 1000:
                        found[nation] = (year, n)
                        break
    return found if found else None


def _parse_timeseries(df: pd.DataFrame) -> dict[str, dict[int, float]] | None:
    """
    Parse multi-year time-series table.
    Returns {nation_name: {year: gdp_£m}} or None.
    """
    # Find year-header row
    year_row_idx = None
    for i, row in df.iterrows():
        years = [v for v in row if isinstance(v, (int, float)) and 1990 <= v <= 2030]
        if len(years) >= 10:
            year_row_idx = i
            break
    if year_row_idx is None:
        return None

    year_cols = {int(df.at[year_row_idx, c]): c for c in df.columns
                 if isinstance(df.at[year_row_idx, c], (int, float))
                 and 1990 <= df.at[year_row_idx, c] <= 2030}

    found: dict[str, dict[int, float]] = {}
    for _, row in df.iterrows():
        label = _clean(row.iloc[0])
        for nation in NATIONS_CANONICAL:
            if label == nation:
                series = {}
                for year, col in year_cols.items():
                    n = _to_num(row[col])
                    if n is not None and n > 0:
                        series[year] = n
                if series:
                    found[nation] = series
    return found if found else None


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    """
    Fetch GDP (£ millions, current prices) for the 4 UK constituent nations.
    Prefers time-series data; falls back to single-year summary.
    """
    session = make_session()

    try:
        links = _find_xlsx_links(session, ONS_BULLETIN_URL)
    except Exception as e:
        print(f'  ONS GDP: could not load bulletin page: {e}')
        return empty_df()

    if verbose:
        print(f'  ONS GDP: {len(links)} Excel link(s) found')

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
            ts = _parse_timeseries(df)
            if ts and not ts_data:
                ts_data = ts
                if verbose:
                    print(f'    Time-series found in sheet "{sname}"')
            sm = _parse_summary(df)
            if sm and not summary_data:
                summary_data = sm
                if verbose:
                    print(f'    Summary table found in sheet "{sname}"')
        throttle(0.5)

    # Build normalized records
    records = []
    source_label = 'ONS'

    if ts_data:
        for nation, series in ts_data.items():
            code = NATIONS_CANONICAL[nation]
            for year, gdp_m in series.items():
                records.append({
                    'entity_code': code,
                    'entity_name': nation,
                    'year':        year,
                    'indicator':   GDP_PC_PPP,   # £m total; orchestrator divides by pop
                    'value':       gdp_m,
                    'source':      source_label,
                    'is_proxy':    True,  # GVA-based, GBP, not PPP-adjusted
                })
    elif summary_data:
        for nation, (year, gdp_m) in summary_data.items():
            code = NATIONS_CANONICAL[nation]
            records.append({
                'entity_code': code,
                'entity_name': nation,
                'year':        year,
                'indicator':   GDP_PC_PPP,
                'value':       gdp_m,
                'source':      source_label,
                'is_proxy':    True,
            })
    else:
        print('  ONS GDP: no data extracted')
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
