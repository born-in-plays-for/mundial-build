#!/usr/bin/env python3
"""
Parse eloratings.net/graph.tsv → wc2026_elo_history.json.

graph.tsv encodes the full Elo history in a compact format
(reverse-engineered from eloratings.net/scripts/ratings.js):

  Date prefix on each line (one of):
    YYYYMMDD   — new year + month + day
    MMDD       — new month + day (same year)
    DD         — new day (same year + month)

  After the date, tokens parsed left-to-right:
    CCCCn      — match: home code CC1, away code CC2, home delta ±n
    CCCC       — team rename: old code CC1 → new code CC2
                 (CC2 == "XX" means the team dissolves)
    CCn        — initialize or adjust team CC by ±n Elo points

Usage:
    pip install requests
    cd <repo-root>
    python3 pipeline/build_elo_history.py
"""
import re, json, sys
from datetime import date as Date
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit('pip install requests')

ROOT    = Path(__file__).parent.parent / "data"
OUT     = ROOT / 'wc2026_elo_history.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; mundial-bot/1.0)'}

# UK home nations use non-ISO codes on eloratings.net
UK_ISO2 = {'EN': 'gb-eng', 'SQ': 'gb-sct', 'WA': 'gb-wls', 'EI': 'gb-nir'}

# eloratings codes that differ from standard ISO 3166-1 alpha-2
CODE_ISO2 = {'KO': 'xk', 'NM': 'mk', **UK_ISO2}

# Historical / dissolved / non-standard entities: skip flag
NO_FLAG = {
    'AH',  # Austria-Hungary
    'BC',  # Belgian Congo
    'BO',  # Bohemia
    'CS',  # Czechoslovakia
    'DD',  # East Germany
    'HA',  # historical Hejaz / Bohemia alias
    'MN',  # historical
    'NF',  # Newfoundland
    'OR',  # Byelorussia (pre-independence variant)
    'RH',  # Rhodesia
    'SU',  # Soviet Union
    'UT',  # Basutoland
    'VD',  # historical
    'XX',  # dissolution marker (not a team)
    'YU',  # Yugoslavia
    'ZR',  # Zaire
}

DATE_RE  = re.compile(r'^(\d{4})(\d{2})(\d{2})(.*)',  re.DOTALL)
MONTH_RE = re.compile(r'^(\d{1,2})(\d{2})(.*)',       re.DOTALL)
DAY_RE   = re.compile(r'^(\d{1,2})(.*)',               re.DOTALL)
MATCH_RE = re.compile(r'^([A-Z]{2})([A-Z]{2})(-?\d+)(.*)', re.DOTALL)
TEAMS_RE = re.compile(r'^([A-Z]{2})([A-Z]{2})(.*)',   re.DOTALL)
TEAM_RE  = re.compile(r'^([A-Z]{2})(-?\d+)(.*)',       re.DOTALL)


def iso2_for(code):
    if code in CODE_ISO2:
        return CODE_ISO2[code]
    if code in NO_FLAG:
        return None
    return code.lower()


def parse_graph(text):
    """
    Returns (snapshots, start_year, start_month, all_codes).
    One snapshot per calendar month; ratings carried forward for months with no matches.
    """
    ratings   = {}   # code → current Elo
    year = month = day = None
    snap_key  = None   # (year, month) of the current tracking position
    month_end = {}     # (year, month) → {code: elo}
    all_codes = set()

    def advance_to(to_y, to_m):
        """Snapshot every month from snap_key up to (but not including) (to_y, to_m)."""
        nonlocal snap_key
        if snap_key is None:
            snap_key = (to_y, to_m)
            return
        y, m = snap_key
        while y * 12 + m < to_y * 12 + to_m:
            month_end[(y, m)] = dict(ratings)
            m += 1
            if m > 12:
                m = 1
                y += 1
        snap_key = (to_y, to_m)

    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue

        rest = line

        # ── date prefix ──────────────────────────────────────────────
        mo = DATE_RE.match(rest)
        if mo:
            ny, nm = int(mo.group(1)), int(mo.group(2))
            advance_to(ny, nm)
            year, month, day = ny, nm, int(mo.group(3))
            rest = mo.group(4)
        else:
            mo = MONTH_RE.match(rest)
            if mo:
                nm = int(mo.group(1))
                advance_to(year, nm)
                month, day = nm, int(mo.group(2))
                rest = mo.group(3)
            else:
                mo = DAY_RE.match(rest)
                if mo:
                    day  = int(mo.group(1))
                    rest = mo.group(2)

        # ── event tokens ─────────────────────────────────────────────
        while rest:
            mo = MATCH_RE.match(rest)
            if mo:
                c1, c2, inc = mo.group(1), mo.group(2), int(mo.group(3))
                rest = mo.group(4)
                ratings[c1] = ratings.get(c1, 0) + inc
                ratings[c2] = ratings.get(c2, 0) - inc
                all_codes.add(c1)
                all_codes.add(c2)
                continue

            mo = TEAMS_RE.match(rest)
            if mo:
                c1, c2 = mo.group(1), mo.group(2)
                rest = mo.group(3)
                if c2 == 'XX':
                    ratings.pop(c1, None)
                elif c1 in ratings:
                    ratings[c2] = ratings.pop(c1)
                    all_codes.add(c2)
                continue

            mo = TEAM_RE.match(rest)
            if mo:
                code, val = mo.group(1), int(mo.group(2))
                rest = mo.group(3)
                ratings[code] = ratings.get(code, 0) + val
                all_codes.add(code)
                continue

            break  # unparseable remainder

    # Snapshot the final month
    if snap_key is not None:
        month_end[snap_key] = dict(ratings)

    if not month_end:
        return [], 1872, 11, set()

    start_ym = min(month_end)
    end_ym   = max(month_end)
    sy, sm   = start_ym

    # Build flat list, carrying ratings forward for months with no matches
    snapshots = []
    prev = {}
    y, m = sy, sm
    while (y, m) <= end_ym:
        ym = (y, m)
        if ym in month_end:
            prev = month_end[ym]
        snapshots.append(dict(prev))
        m += 1
        if m > 12:
            m = 1
            y += 1

    return snapshots, sy, sm, all_codes


def main():
    print('Fetching graph.tsv …')
    r = requests.get('https://www.eloratings.net/graph.tsv',
                     headers=HEADERS, timeout=60)
    r.raise_for_status()

    print('Fetching en.teams.tsv …')
    r2 = requests.get('https://www.eloratings.net/en.teams.tsv',
                      headers=HEADERS, timeout=30)
    r2.raise_for_status()

    # code → primary English name (first tab field after the code)
    code_to_name = {}
    for line in r2.text.strip().split('\n'):
        parts = line.strip().split('\t')
        if len(parts) >= 2 and not parts[0].endswith('_loc'):
            code_to_name[parts[0]] = parts[1]

    print('Parsing …')
    snapshots, start_year, start_month, all_codes = parse_graph(r.text)

    teams = {
        code: {'name': code_to_name.get(code, code), 'iso2': iso2_for(code)}
        for code in sorted(all_codes)
    }

    output = {
        'generated':   str(Date.today()),
        'source':      'eloratings.net/graph.tsv',
        'start_year':  start_year,
        'start_month': start_month,
        'teams':       teams,
        'snapshots':   snapshots,
    }

    OUT.write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    end_off  = start_year * 12 + (start_month - 1) + len(snapshots) - 1
    end_year = end_off // 12
    print(f'Written {OUT.name}  ({len(teams)} teams, {start_year}/{start_month:02d}–{end_year}, {len(snapshots)} monthly snapshots)')


if __name__ == '__main__':
    main()
