"""
Triadic Economic Profile — Orchestrator.

Merges data from all fetchers, applies source priority rules, and outputs:
    triadic_profile_world.csv   — all entities, all years 2000–2023
    triadic_profile_latest.csv  — latest available year per entity

Source priority (highest → lowest):
    For sovereign nations:   UNDP > WorldBank > IMF_WEO
    For UK sub-nations:      ONS > WorldBank (WorldBank has no sub-national data)
    For GDHI:                ONS (UK only) > WorldBank (ANNI proxy)
    For NUTS sub-regions:    Eurostat

Usage:
    python3 pipeline/orchestrator.py [--skip-eurostat] [--skip-imf] [--fast]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT     = Path(__file__).parent.parent / "data"
PIPELINE = Path(__file__).parent
sys.path.insert(0, str(PIPELINE))

from fetchers import (
    GDP_PC_PPP, GDHI_PC, HDI, LIFE_EXP, EDU_PCT, POPULATION, GNI_PC_PPP, ANNI_PC,
    INDICATORS, UK_NATIONS,
)

# ── Source priority ────────────────────────────────────────────────────────────
# Lower number = higher priority.  If two sources supply the same
# (entity, year, indicator) the lower-priority row is dropped.
SOURCE_PRIORITY = {
    'UNDP':      1,
    'ONS':       1,     # ONS is authoritative for UK sub-nations
    'WorldBank': 2,
    'Eurostat':  2,
    'IMF_WEO':   3,
}

UK_SUB_CODES = set(UK_NATIONS.keys())   # {'GB-ENG', 'GB-SCT', 'GB-WLS', 'GB-NIR'}

# Methodological notes attached to specific entities/indicators in the output
NOTES: dict[tuple[str, str], str] = {
    ('GB-ENG', GDP_PC_PPP): (
        'England GDP is a residual: UK minus Scotland, Wales, N.Ireland, Extra-Regio. '
        'GVA-based from ONS; taxes-less-subsidies adjustment at UK level only.'
    ),
    ('GB-SCT', GDP_PC_PPP): (
        'Scotland GDP includes allocated North Sea oil & gas revenue. '
        'Ex-North-Sea variant not available from this source.'
    ),
    ('GB-ENG', GDHI_PC): 'ONS GDHI per head, £ current prices. Not PPP-adjusted.',
    ('GB-SCT', GDHI_PC): 'ONS GDHI per head, £ current prices. Not PPP-adjusted.',
    ('GB-WLS', GDHI_PC): 'ONS GDHI per head, £ current prices. Not PPP-adjusted.',
    ('GB-NIR', GDHI_PC): 'ONS GDHI per head, £ current prices. Not PPP-adjusted.',
}

# is_sovereign lookup: entity_codes NOT in this set are non-sovereign
NON_SOVEREIGN_PREFIXES = ('GB-', 'ES5', 'BE1', 'BE2')   # sub-national NUTS codes


def is_sovereign(code: str) -> bool:
    return not any(code.startswith(p) for p in NON_SOVEREIGN_PREFIXES)


# ── Fetch all sources ─────────────────────────────────────────────────────────

def run_fetchers(args) -> pd.DataFrame:
    from fetchers.worldbank import fetch_all as wb_fetch
    from fetchers.undp_hdi  import fetch_all as undp_fetch
    from fetchers.ons_gdp   import fetch_all as ons_gdp_fetch
    from fetchers.ons_gdhi  import fetch_all as ons_gdhi_fetch
    from fetchers.ons_lifeexp import fetch_all as ons_le_fetch

    frames = []

    print('── World Bank ──────────────────────────────────────────')
    frames.append(wb_fetch(verbose=True))

    print('\n── UNDP HDI ────────────────────────────────────────────')
    frames.append(undp_fetch(verbose=True))

    print('\n── ONS GDP (UK nations) ────────────────────────────────')
    frames.append(ons_gdp_fetch(verbose=True))

    print('\n── ONS GDHI (UK nations) ───────────────────────────────')
    frames.append(ons_gdhi_fetch(verbose=True))

    print('\n── ONS Life Expectancy (UK nations) ────────────────────')
    frames.append(ons_le_fetch(verbose=True))

    if not args.skip_imf:
        from fetchers.imf_weo import fetch_all as imf_fetch
        print('\n── IMF WEO ─────────────────────────────────────────────')
        frames.append(imf_fetch(verbose=True))

    if not args.skip_eurostat:
        from fetchers.eurostat import fetch_all as eurostat_fetch
        print('\n── Eurostat (NUTS) ─────────────────────────────────────')
        frames.append(eurostat_fetch(verbose=True))

    raw = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    print(f'\nRaw combined: {len(raw):,} rows')
    return raw


# ── Deduplication / priority resolution ──────────────────────────────────────

def resolve_conflicts(raw: pd.DataFrame) -> pd.DataFrame:
    """
    For each (entity_code, year, indicator), keep only the row from the
    highest-priority source.  If same priority, keep the first encountered.
    """
    raw = raw.copy()
    raw['_priority'] = raw['source'].map(SOURCE_PRIORITY).fillna(99)
    raw = raw.sort_values('_priority')
    deduped = raw.drop_duplicates(subset=['entity_code', 'year', 'indicator'], keep='first')
    deduped = deduped.drop(columns=['_priority'])
    print(f'After dedup: {len(deduped):,} rows  ({raw["_priority"].eq(1).sum()} high-priority)')
    return deduped


# ── Pivot to wide format ──────────────────────────────────────────────────────

def pivot_wide(long: pd.DataFrame, year_range: tuple[int, int] = (2000, 2023)) -> pd.DataFrame:
    """
    Pivot from long (entity, year, indicator, value) to wide
    (entity, year, gdp_pc_ppp, gdhi_proxy_pc, hdi, …).
    """
    lo, hi = year_range
    long = long[long['year'].between(lo, hi)].copy()

    wide = long.pivot_table(
        index=['entity_code', 'entity_name', 'year'],
        columns='indicator',
        values='value',
        aggfunc='first',
    ).reset_index()
    wide.columns.name = None

    # Ensure all output columns exist
    output_cols = [GDP_PC_PPP, GDHI_PC, HDI, LIFE_EXP, EDU_PCT, POPULATION, GNI_PC_PPP, ANNI_PC]
    for col in output_cols:
        if col not in wide.columns:
            wide[col] = float('nan')

    # is_sovereign flag
    wide['is_sovereign'] = wide['entity_code'].apply(is_sovereign)

    # notes column
    wide['notes'] = wide.apply(
        lambda r: '; '.join(
            v for k, v in NOTES.items()
            if k[0] == r['entity_code'] and k[1] in wide.columns
        ),
        axis=1,
    )

    final_cols = (
        ['entity_code', 'entity_name', 'is_sovereign', 'year']
        + output_cols
        + ['notes']
    )
    return wide[[c for c in final_cols if c in wide.columns]]


# ── Latest-year snapshot ──────────────────────────────────────────────────────

def latest_snapshot(wide: pd.DataFrame) -> pd.DataFrame:
    """
    For each entity, keep the row with the most recent year that has at least
    one non-NaN value among the triad columns.
    """
    triad_cols = [GDP_PC_PPP, GDHI_PC, HDI]
    triad_present = [c for c in triad_cols if c in wide.columns]
    wide = wide.copy()
    wide['_any_triad'] = wide[triad_present].notna().any(axis=1)
    wide = wide[wide['_any_triad']]
    latest = wide.sort_values('year', ascending=False).drop_duplicates('entity_code')
    return latest.drop(columns=['_any_triad']).sort_values('entity_code')


# ── Coverage summary ──────────────────────────────────────────────────────────

def print_coverage(latest: pd.DataFrame):
    triad_cols = [GDP_PC_PPP, GDHI_PC, HDI]
    present = [c for c in triad_cols if c in latest.columns]
    total = len(latest)
    complete = latest[present].notna().all(axis=1).sum()
    partial  = latest[present].notna().any(axis=1).sum() - complete
    gdp_only = (latest[[GDP_PC_PPP]].notna().all(axis=1) &
                latest[[c for c in present if c != GDP_PC_PPP]].isna().all(axis=1)).sum()

    print(f'\n── Coverage ({total} entities) ─────────────────────────────')
    print(f'  Full triad (GDP + GDHI + HDI):  {complete:4d}')
    print(f'  Partial (at least one):         {partial:4d}')
    print(f'  GDP only:                       {gdp_only:4d}')
    print(f'  No data:                        {total - complete - partial:4d}')

    # UK nations separately
    uk = latest[latest['entity_code'].isin(UK_SUB_CODES)]
    if not uk.empty:
        print(f'\n  UK sub-nations ({len(uk)}):')
        for _, row in uk.iterrows():
            flags = [c for c in present if pd.notna(row.get(c))]
            print(f'    {row["entity_name"]:20s}  {", ".join(flags) or "—"}')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Build triadic economic profile')
    parser.add_argument('--skip-eurostat', action='store_true')
    parser.add_argument('--skip-imf',      action='store_true')
    parser.add_argument('--fast', action='store_true',
                        help='Skip Eurostat and IMF (equivalent to both --skip flags)')
    parser.add_argument('--out-dir', default=str(ROOT), help='Output directory')
    args = parser.parse_args()
    if args.fast:
        args.skip_eurostat = True
        args.skip_imf = True

    out_dir = Path(args.out_dir)

    # 1. Fetch
    raw = run_fetchers(args)

    # 2. Resolve conflicts
    long = resolve_conflicts(raw)

    # 3. Pivot wide
    wide = pivot_wide(long)

    # 4. Latest snapshot
    snap = latest_snapshot(wide)

    # 5. Write outputs
    world_path  = out_dir / 'triadic_profile_world.csv'
    latest_path = out_dir / 'triadic_profile_latest.csv'
    wide.to_csv(world_path,  index=False)
    snap.to_csv(latest_path, index=False)
    print(f'\nWrote {world_path}')
    print(f'Wrote {latest_path}')

    # 6. Coverage
    print_coverage(snap)


if __name__ == '__main__':
    main()
