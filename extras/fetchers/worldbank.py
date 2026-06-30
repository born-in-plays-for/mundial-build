"""
World Bank Open Data API fetcher.

Fetches multiple indicators for all countries, all available years,
and returns a normalized DataFrame.

Usage:
    from fetchers.worldbank import fetch_all
    df = fetch_all()          # all indicators, ~2 min
    df = fetch_all(['NY.GDP.PCAP.PP.CD'])  # single indicator
"""

from __future__ import annotations

import pandas as pd
from . import (
    GDP_PC_PPP, GDHI_PC, LIFE_EXP, EDU_PCT, POPULATION, GNI_PC_PPP, ANNI_PC,
    make_session, make_rows, throttle, empty_df,
)

# World Bank indicator code → canonical indicator name
WB_INDICATORS: dict[str, str] = {
    'NY.GDP.PCAP.PP.CD': GDP_PC_PPP,    # GDP per capita, PPP (current intl $)
    'NY.ADJ.NNTY.PC.CD': ANNI_PC,       # Adjusted net national income per capita
    'NY.GNP.PCAP.PP.CD': GNI_PC_PPP,   # GNI per capita, PPP
    'SP.DYN.LE00.IN':    LIFE_EXP,      # Life expectancy at birth
    'SE.TER.ENRR':       EDU_PCT,       # Tertiary school enrollment, gross %
    'SP.POP.TOTL':       POPULATION,    # Total population
}

# Proxy: use ANNI/capita as GDHI proxy when no direct GDHI available
GDHI_PROXY_CODE = 'NY.ADJ.NNTY.PC.CD'

BASE = 'https://api.worldbank.org/v2/country/all/indicator'


def _fetch_indicator(
    session,
    wb_code: str,
    canonical: str,
    mrv: int = 30,
) -> pd.DataFrame:
    """Fetch one indicator for all countries, up to `mrv` most recent years."""
    url = f'{BASE}/{wb_code}?format=json&per_page=500&mrv={mrv}'
    records = []
    page = 1
    while True:
        resp = session.get(f'{url}&page={page}', timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if len(payload) < 2 or not payload[1]:
            break
        meta, data = payload[0], payload[1]
        for entry in data:
            if entry.get('value') is None:
                continue
            iso3 = entry.get('countryiso3code', '').strip()
            name = entry['country']['value'].strip()
            year = int(entry['date'])
            is_proxy = (wb_code == GDHI_PROXY_CODE and canonical == ANNI_PC)
            records.append({
                'entity_code': iso3,
                'entity_name': name,
                'year':        year,
                'indicator':   canonical,
                'value':       float(entry['value']),
                'source':      'WorldBank',
                'is_proxy':    False,
            })
            # Also emit as GDHI proxy
            if wb_code == GDHI_PROXY_CODE:
                records.append({
                    'entity_code': iso3,
                    'entity_name': name,
                    'year':        year,
                    'indicator':   GDHI_PC,
                    'value':       float(entry['value']),
                    'source':      'WorldBank',
                    'is_proxy':    True,   # ANNI used as GDHI proxy
                })
        if page >= meta.get('pages', 1):
            break
        page += 1
        throttle(0.3)

    return make_rows(records) if records else empty_df()


def fetch_all(
    wb_codes: list[str] | None = None,
    mrv: int = 30,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Fetch all (or a subset of) World Bank indicators.

    Args:
        wb_codes: list of WB indicator codes; None = all WB_INDICATORS
        mrv:      most-recent-values window (years)
        verbose:  print progress

    Returns:
        Normalized DataFrame.
    """
    codes = wb_codes or list(WB_INDICATORS)
    session = make_session()
    frames = []
    for code in codes:
        canonical = WB_INDICATORS.get(code, code)
        if verbose:
            print(f'  WorldBank: fetching {code} ({canonical}) …')
        try:
            df = _fetch_indicator(session, code, canonical, mrv=mrv)
            frames.append(df)
            if verbose:
                print(f'    {len(df):,} rows')
        except Exception as e:
            print(f'  ERROR fetching {code}: {e}')
        throttle(0.5)

    return pd.concat(frames, ignore_index=True) if frames else empty_df()


if __name__ == '__main__':
    print('Fetching World Bank indicators …')
    df = fetch_all(verbose=True)
    print(f'\nTotal rows: {len(df):,}')
    print(df.groupby('indicator')['entity_code'].nunique().rename('entities'))
    print(df.head())
