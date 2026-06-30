"""
Shared utilities for the triadic economic profile fetchers.

Normalized DataFrame schema (returned by every fetcher):
    entity_code   str   ISO 3166-1 alpha-3, or custom sub-national code (e.g. GB-ENG)
    entity_name   str   Human-readable English name
    year          int
    indicator     str   One of the INDICATORS constants below
    value         float
    source        str   Short label: "WorldBank", "UNDP", "IMF_WEO", "ONS", "Eurostat"
    is_proxy      bool  True when the value substitutes for the canonical indicator
"""

from __future__ import annotations

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── canonical indicator names ─────────────────────────────────────────────────
GDP_PC_PPP   = 'gdp_pc_ppp'        # GDP per capita, PPP (current intl $)
GDHI_PC      = 'gdhi_proxy_pc'     # GDHI or closest proxy per capita
HDI          = 'hdi'               # Human Development Index (0–1)
LIFE_EXP     = 'life_expectancy'   # Years at birth
EDU_PCT      = 'edu_attainment_pct'# Tertiary enrollment or edu attainment %
POPULATION   = 'population'        # Total population
GNI_PC_PPP   = 'gni_pc_ppp'       # GNI per capita, PPP (UNDP uses this for HDI)
ANNI_PC      = 'anni_pc'           # Adjusted net national income per capita

INDICATORS = (GDP_PC_PPP, GDHI_PC, HDI, LIFE_EXP, EDU_PCT, POPULATION, GNI_PC_PPP, ANNI_PC)

SCHEMA = ['entity_code', 'entity_name', 'year', 'indicator', 'value', 'source', 'is_proxy']

# ── UK sub-national entity registry ──────────────────────────────────────────
UK_NATIONS = {
    'GB-ENG': 'England',
    'GB-SCT': 'Scotland',
    'GB-WLS': 'Wales',
    'GB-NIR': 'Northern Ireland',
}

# ── name → ISO3 lookup (extend as needed) ────────────────────────────────────
NAME_TO_ISO3: dict[str, str] = {
    'United Kingdom':          'GBR',
    'United States':           'USA',
    'South Korea':             'KOR',
    'Iran':                    'IRN',
    'Turkey':                  'TUR',
    'Ivory Coast':             'CIV',
    'DR Congo':                'COD',
    'Republic of the Congo':   'COG',
    'Egypt':                   'EGY',
    'Cape Verde':              'CPV',
    'Curaçao':                 'CUW',
    'Czech Republic':          'CZE',
    'Bosnia and Herzegovina':  'BIH',
    'North Macedonia':         'MKD',
    'Slovakia':                'SVK',
    'England':                 'GB-ENG',
    'Scotland':                'GB-SCT',
    'Wales':                   'GB-WLS',
    'Northern Ireland':        'GB-NIR',
}


def make_session(retries: int = 4, backoff: float = 1.0) -> requests.Session:
    """Return a requests.Session with automatic retry on transient errors."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    session.headers['User-Agent'] = 'mundial-triadic-pipeline/1.0 (research)'
    return session


def empty_df() -> 'pd.DataFrame':
    import pandas as pd
    return pd.DataFrame(columns=SCHEMA)


def make_rows(records: list[dict]) -> 'pd.DataFrame':
    """Construct a normalized DataFrame from a list of record dicts."""
    import pandas as pd
    df = pd.DataFrame(records, columns=SCHEMA)
    df['year']     = df['year'].astype('Int64')
    df['value']    = pd.to_numeric(df['value'], errors='coerce')
    df['is_proxy'] = df['is_proxy'].astype(bool)
    return df


def throttle(seconds: float = 0.5):
    """Simple rate-limiter between API calls."""
    time.sleep(seconds)
