"""
IMF World Economic Outlook (WEO) bulk CSV fetcher.

Downloads the full WEO database (all countries, all indicators, all years)
from the IMF datamapper API — no account required.

Used as a cross-check / gap-filler for GDP per capita where World Bank data
is missing (e.g. Syria, Venezuela, Cuba).

Returns normalized rows for GDP_PC_PPP only (PPPPC indicator).
"""

from __future__ import annotations

import io
import re
import pandas as pd

from . import GDP_PC_PPP, GNI_PC_PPP, make_session, make_rows, empty_df, throttle

# IMF datamapper REST API (no auth required)
DATAMAPPER_BASE = 'https://www.imf.org/external/datamapper/api/v1'

# WEO indicator codes relevant to the triad
IMF_INDICATORS = {
    'PPPPC':  GDP_PC_PPP,   # GDP per capita, PPP (current intl $)
    'NGDPDPC': GDP_PC_PPP,  # GDP per capita, current USD (fallback)
}

# Bulk download fallback: the WEO full CSV via download page
# URL format changes each edition; try both April and October releases
WEO_BULK_URLS = [
    'https://www.imf.org/external/pubs/ft/weo/2024/02/weodata/WEOOct2024all.ashx',
    'https://www.imf.org/external/pubs/ft/weo/2024/01/weodata/WEOApr2024all.ashx',
]

IMF_NAME_MAP: dict[str, str] = {
    'Korea':                     'KOR',
    'Iran':                      'IRN',
    'Turkey':                    'TUR',
    "Côte d'Ivoire":             'CIV',
    'Congo, Dem. Rep. of the':   'COD',
    'Congo, Republic of':        'COG',
    'Egypt':                     'EGY',
    'Cabo Verde':                'CPV',
    'Czech Republic':            'CZE',
    'Slovak Republic':           'SVK',
    'Kyrgyz Republic':           'KGZ',
    "Lao P.D.R.":                'LAO',
    'North Macedonia':           'MKD',
    'Kosovo':                    'XKX',
    'Taiwan Province of China':  'TWN',
    'Hong Kong SAR':             'HKG',
    'Macao SAR':                 'MAC',
}


def _fetch_via_api(session, verbose: bool) -> pd.DataFrame:
    """
    Fetch PPPPC (GDP per capita PPP) via the IMF datamapper REST API.
    Returns one row per (country, year).
    """
    records = []
    for imf_code, canonical in IMF_INDICATORS.items():
        url = f'{DATAMAPPER_BASE}/PPPPC'
        if verbose:
            print(f'  IMF datamapper: fetching {imf_code} …')
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f'    datamapper failed: {e}')
            continue

        # Response: {"values": {"PPPPC": {"AFG": {"1980": 1234, ...}, ...}}}
        values = payload.get('values', {}).get(imf_code, {})
        for iso2_or_3, year_dict in values.items():
            for year_str, val in year_dict.items():
                if val is None:
                    continue
                try:
                    year = int(year_str)
                    v    = float(val)
                except (ValueError, TypeError):
                    continue
                records.append({
                    'entity_code': iso2_or_3,
                    'entity_name': iso2_or_3,   # no name in this endpoint
                    'year':        year,
                    'indicator':   canonical,
                    'value':       v,
                    'source':      'IMF_WEO',
                    'is_proxy':    False,
                })
        throttle(0.5)
        break   # one indicator is enough for cross-check

    return make_rows(records) if records else empty_df()


def _parse_weo_tsv(content: bytes) -> pd.DataFrame:
    """
    Parse the full WEO bulk tab-separated download.
    Layout: columns = [WEO Country Code, ISO, Country, Subject, Units, Scale,
                       ..., 1980, 1981, ..., 2029, Estimates Start After]
    """
    try:
        # WEO files use Windows-1252 encoding
        text = content.decode('windows-1252', errors='replace')
        df = pd.read_csv(io.StringIO(text), sep='\t', low_memory=False)
    except Exception as e:
        print(f'  WEO TSV parse error: {e}')
        return empty_df()

    df.columns = [str(c).strip() for c in df.columns]

    # Keep only PPPPC rows
    subj_col = next((c for c in df.columns if 'subject' in c.lower()), None)
    iso_col  = next((c for c in df.columns if c.strip().upper() in ('ISO', 'ISO3')), None)
    name_col = next((c for c in df.columns if 'country' in c.lower() and 'code' not in c.lower()), None)

    if subj_col is None:
        return empty_df()

    mask = df[subj_col].astype(str).str.contains('PPPPC|NGDPDPC', na=False)
    df = df[mask]

    year_cols = {int(c): c for c in df.columns if re.fullmatch(r'\d{4}', str(c))}

    records = []
    for _, row in df.iterrows():
        iso3 = str(row[iso_col]).strip() if iso_col else ''
        name = str(row[name_col]).strip() if name_col else iso3
        iso3 = IMF_NAME_MAP.get(name, iso3)
        for year, col in year_cols.items():
            raw = str(row[col]).replace(',', '').strip()
            if raw in ('', 'n/a', '--', 'nan'):
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            records.append({
                'entity_code': iso3,
                'entity_name': name,
                'year':        year,
                'indicator':   GDP_PC_PPP,
                'value':       val,
                'source':      'IMF_WEO',
                'is_proxy':    False,
            })
    return make_rows(records) if records else empty_df()


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    """
    Fetch IMF WEO GDP per capita data.
    Tries datamapper API first (fast, no bulk download), then bulk TSV.
    """
    session = make_session()

    df = _fetch_via_api(session, verbose)
    if not df.empty:
        if verbose:
            print(f'    {len(df):,} rows via datamapper API')
        return df

    if verbose:
        print('  IMF datamapper empty; trying bulk TSV …')
    for url in WEO_BULK_URLS:
        if verbose:
            print(f'  IMF WEO bulk: {url} …')
        try:
            resp = session.get(url, timeout=120)
            resp.raise_for_status()
            df = _parse_weo_tsv(resp.content)
            if not df.empty:
                if verbose:
                    print(f'    {len(df):,} rows')
                return df
        except Exception as e:
            print(f'    failed: {e}')

    print('  IMF WEO: all sources exhausted')
    return empty_df()


if __name__ == '__main__':
    df = fetch_all(verbose=True)
    print(df.head())
    print(f'{df["entity_code"].nunique()} entities')
