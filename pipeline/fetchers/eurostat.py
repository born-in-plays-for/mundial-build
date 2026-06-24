"""
Eurostat API fetcher — GDP and GDHI at NUTS1/NUTS2 level.

Used for EU member states with higher granularity than World Bank,
and for sub-national entities (e.g. Catalonia, Flanders, Brussels).

Key datasets:
  nama_10r_2gdp  — GDP at current market prices, NUTS2 (€ millions)
  tgs00026       — GDHI per inhabitant, NUTS2 (PPS, index EU=100)
  nama_10r_2hhinc — GDHI at NUTS2 (€ millions)

Returns normalized DataFrame with entity_code = NUTS2 code (e.g. "ES51" for Catalonia).
"""

from __future__ import annotations

import pandas as pd

from . import GDP_PC_PPP, GDHI_PC, POPULATION, make_session, make_rows, empty_df, throttle

EUROSTAT_BASE = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'

# NUTS1 codes for UK constituent nations (pre-Brexit vintage)
UK_NUTS1 = {
    'UKC': 'North East England',
    'UKD': 'North West England',
    'UKE': 'Yorkshire and The Humber',
    'UKF': 'East Midlands',
    'UKG': 'West Midlands',
    'UKH': 'East of England',
    'UKI': 'London',
    'UKJ': 'South East England',
    'UKK': 'South West England',
    'UKL': 'Wales',
    'UKM': 'Scotland',
    'UKN': 'Northern Ireland',
    # UKC-UKK aggregate → England (no direct NUTS1 code)
}

# NUTS2 codes for selected sub-national entities of interest
SUBNATIONAL_NUTS2 = {
    'ES51': 'Catalonia',
    'ES30': 'Madrid',
    'BE10': 'Brussels',
    'BE21': 'Antwerp (Flanders)',
    'BE22': 'Limburg (Flanders)',
    'BE23': 'East Flanders',
    'BE24': 'Flemish Brabant',
    'BE25': 'West Flanders',
    'CA':   None,  # Quebec — not in Eurostat; handled separately
}


def _fetch_dataset(
    session,
    dataset: str,
    params: dict,
    verbose: bool = True,
) -> dict:
    """Fetch a JSON-stat Eurostat dataset."""
    url = f'{EUROSTAT_BASE}/{dataset}'
    if verbose:
        print(f'  Eurostat: {dataset} …')
    resp = session.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _jsonstat_to_df(js: dict, code_col: str = 'geo') -> pd.DataFrame:
    """
    Convert a Eurostat JSON-stat 2.0 response to a flat DataFrame.
    Columns: geo, time, value.
    """
    dims = js.get('dimension', {})
    size = js.get('size', [])
    dim_ids = js.get('id', [])
    values = js.get('value', {})

    if not dim_ids or not size:
        return pd.DataFrame()

    # Build index → label maps for each dimension
    cats = {}
    for dim_id in dim_ids:
        cat = dims[dim_id]['category']
        idx_map = {int(i): lab for lab, i in cat['index'].items()}
        label_map = cat.get('label', {})
        cats[dim_id] = (idx_map, label_map)

    # Enumerate all cells
    import itertools
    ranges = [range(s) for s in size]
    records = []
    for flat_idx, indices in enumerate(itertools.product(*ranges)):
        v = values.get(str(flat_idx)) or values.get(flat_idx)
        if v is None:
            continue
        row = {}
        for dim_id, idx in zip(dim_ids, indices):
            idx_map, label_map = cats[dim_id]
            code = idx_map.get(idx, str(idx))
            row[dim_id] = code
        row['value'] = v
        records.append(row)

    return pd.DataFrame(records) if records else pd.DataFrame()


def fetch_gdp_nuts2(
    nuts_level: int = 2,
    unit: str = 'MIO_EUR',
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Fetch NUTS2 GDP (nama_10r_2gdp) for all regions, all available years.
    Returns normalized rows with entity_code = NUTS code.
    """
    session = make_session()
    params = {
        'unit': unit,
        'format': 'JSON',
        'lang': 'EN',
    }
    try:
        js = _fetch_dataset(session, 'nama_10r_2gdp', params, verbose)
    except Exception as e:
        print(f'  Eurostat GDP fetch failed: {e}')
        return empty_df()

    df = _jsonstat_to_df(js)
    if df.empty:
        return empty_df()

    records = []
    for _, row in df.iterrows():
        geo  = str(row.get('geo', '')).strip()
        time = str(row.get('time', '')).strip()
        val  = row.get('value')
        if not geo or not time or val is None:
            continue
        try:
            year = int(time)
            v    = float(val)
        except (ValueError, TypeError):
            continue
        records.append({
            'entity_code': geo,
            'entity_name': geo,   # name enrichment done in orchestrator
            'year':        year,
            'indicator':   GDP_PC_PPP,   # total GDP here; per-capita in orchestrator
            'value':       v * 1e6,      # € millions → €
            'source':      'Eurostat',
            'is_proxy':    True,         # total, not per capita yet
        })

    return make_rows(records) if records else empty_df()


def fetch_gdhi_nuts2(verbose: bool = True) -> pd.DataFrame:
    """
    Fetch NUTS2 GDHI per inhabitant (tgs00026) — PPS, index EU27=100.
    Returns normalized rows.
    """
    session = make_session()
    params = {'format': 'JSON', 'lang': 'EN'}
    try:
        js = _fetch_dataset(session, 'tgs00026', params, verbose)
    except Exception as e:
        print(f'  Eurostat GDHI fetch failed: {e}')
        return empty_df()

    df = _jsonstat_to_df(js)
    if df.empty:
        return empty_df()

    records = []
    for _, row in df.iterrows():
        geo  = str(row.get('geo', '')).strip()
        time = str(row.get('time', '')).strip()
        val  = row.get('value')
        if not geo or not time or val is None:
            continue
        try:
            year = int(time)
            v    = float(val)
        except (ValueError, TypeError):
            continue
        records.append({
            'entity_code': geo,
            'entity_name': geo,
            'year':        year,
            'indicator':   GDHI_PC,
            'value':       v,            # PPS index (EU27=100)
            'source':      'Eurostat',
            'is_proxy':    True,         # index, not absolute €
        })

    return make_rows(records) if records else empty_df()


def fetch_all(verbose: bool = True) -> pd.DataFrame:
    """Fetch both GDP and GDHI NUTS2 datasets and concatenate."""
    frames = []
    for fn in (fetch_gdp_nuts2, fetch_gdhi_nuts2):
        df = fn(verbose=verbose)
        if not df.empty:
            frames.append(df)
        throttle(1.0)
    return pd.concat(frames, ignore_index=True) if frames else empty_df()


if __name__ == '__main__':
    df = fetch_all(verbose=True)
    print(df.head())
    print(f'\n{df["entity_code"].nunique()} NUTS entities')
