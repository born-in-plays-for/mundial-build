"""
ONS Life Expectancy fetcher for UK constituent nations.

Source:
  ONS bulletin "National life tables – UK and constituent countries"
  https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/
  lifeexpectancies/bulletins/nationallifetablesunitedkingdomandconstituentcountries/latest

Returns life expectancy at birth (period, both sexes average) for:
    GBR    United Kingdom
    GB-ENG England
    GB-SCT Scotland
    GB-WLS Wales
    GB-NIR Northern Ireland

The ONS tables are usually structured as:
  - One table per country
  - Rows: age groups (0, 1, 2, …)
  - Columns: time periods (e.g. "2020–2022"), male ex, female ex, [both sexes ex]
  - Life expectancy at birth = the row where age (x) = 0
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
from bs4 import BeautifulSoup

from . import LIFE_EXP, make_session, make_rows, empty_df, throttle

ONS_LIFEEXP_URL = (
    'https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/'
    'lifeexpectancies/bulletins/'
    'nationallifetablesunitedkingdomandconstituentcountries/latest'
)

NATIONS_CANONICAL = {
    'United Kingdom': 'GBR',
    'England':        'GB-ENG',
    'England and Wales': 'GB-ENG',   # treated as England proxy when separate unavailable
    'Scotland':       'GB-SCT',
    'Wales':          'GB-WLS',
    'Northern Ireland': 'GB-NIR',
}

NS = {
    'ss': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r':  'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}

# Period label → midpoint year (e.g. "2020-2022" → 2021)
_PERIOD_RE = re.compile(r'(\d{4})\s*[-–—]\s*(\d{4})')


def _period_to_year(label: str) -> int | None:
    m = _PERIOD_RE.search(str(label))
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m2 = re.search(r'\b(19|20)\d{2}\b', str(label))
    return int(m2.group()) if m2 else None


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


def _parse_life_table_sheet(
    sheet_name: str,
    df: pd.DataFrame,
) -> list[dict] | None:
    """
    Parse one ONS life table sheet.
    Sheet name often indicates the country (e.g. "UK", "England", "Scotland").
    Looks for:
      - Header row with period labels ("2018-2020", "2019-2021", …)
      - Data row where age column = 0 (life expectancy at birth)
      - Columns for male ex, female ex, and optionally both-sexes ex
    Returns list of record dicts, or None.
    """
    # Identify nation from sheet name
    nation = None
    for n in NATIONS_CANONICAL:
        if n.lower() in sheet_name.lower():
            nation = n
            break
    if nation is None:
        return None

    code = NATIONS_CANONICAL[nation]

    # Find the header row containing period labels
    header_row_idx = None
    header_cols: dict[int, int] = {}   # col_idx → year

    for i, row in df.iterrows():
        periods = {}
        for c_idx, v in enumerate(row):
            yr = _period_to_year(v)
            if yr is not None and 1980 <= yr <= 2030:
                periods[c_idx] = yr
        if len(periods) >= 3:
            header_row_idx = i
            header_cols = periods
            break

    if header_row_idx is None or not header_cols:
        return None

    # Find the row where age = 0 (life expectancy at birth)
    records = []
    for _, row in df.iloc[header_row_idx + 1:].iterrows():
        # Age column: first numeric cell in the row
        first_val = _to_num(row.iloc[0])
        if first_val is None:
            first_val = _to_num(row.iloc[1])
        if first_val != 0:
            continue

        # Collect life expectancy values: look for columns whose values are
        # plausible life-expectancy figures (60–90 years)
        # The ONS tables alternate: male ex | female ex | [both sexes ex]
        le_vals: list[tuple[int, float]] = []
        for c_idx, year in header_cols.items():
            v = _to_num(row.iloc[c_idx])
            if v is not None and 60 <= v <= 95:
                le_vals.append((year, v))

        if not le_vals:
            continue

        # If multiple columns per year (male/female), average them
        from collections import defaultdict
        by_year: dict[int, list[float]] = defaultdict(list)
        for year, v in le_vals:
            by_year[year].append(v)

        for year, vals in by_year.items():
            avg = sum(vals) / len(vals)
            records.append({
                'entity_code': code,
                'entity_name': nation,
                'year':        year,
                'indicator':   LIFE_EXP,
                'value':       round(avg, 2),
                'source':      'ONS',
                'is_proxy':    len(vals) > 1,  # proxy if averaged male+female
            })
        break   # only one age-0 row expected

    return records if records else None


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    session = make_session()

    try:
        links = _find_xlsx_links(session, ONS_LIFEEXP_URL)
    except Exception as e:
        print(f'  ONS LifeExp: could not load bulletin page: {e}')
        return empty_df()

    if verbose:
        print(f'  ONS LifeExp: {len(links)} Excel link(s) found')

    all_records = []
    seen_nations: set[str] = set()

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
            recs = _parse_life_table_sheet(sname, df)
            if recs:
                nation_name = recs[0]['entity_name']
                if nation_name not in seen_nations:
                    all_records.extend(recs)
                    seen_nations.add(nation_name)
                    if verbose:
                        print(f'    {nation_name}: {len(recs)} period(s)')
        throttle(0.5)

    if not all_records:
        print('  ONS LifeExp: no data extracted')
        return empty_df()

    return make_rows(all_records)


if __name__ == '__main__':
    df = fetch_all(verbose=True)
    print(df)
