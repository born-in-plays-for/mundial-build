#!/usr/bin/env python3
"""
Fetches World Football Elo ratings from eloratings.net/World.tsv and
updates wc2026_elo_rank.json.

Every entry from the source is kept. Two flags are added per entry:
  weirdo=True   non-sovereign / no ISO 3166-1 alpha-2 code (Kurdistan, Saba, …)
  fifaMember    True if the entity is one of the 211 full FIFA members

Non-FIFA sovereign territories (Guadeloupe, Kiribati, …) get
weirdo=False, fifaMember=False.

Usage:
    pip install requests pycountry beautifulsoup4
    python3 pipeline/update_elo_rankings.py
"""
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import requests
    import pycountry
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps. Run: pip install requests pycountry beautifulsoup4", file=sys.stderr)
    sys.exit(1)

import country_registry as reg

ROOT    = Path(__file__).parent.parent / "data"
OUT     = ROOT / 'elo_rank.json'
OUT_TSV = Path(__file__).parent / 'elo_rank.tsv'

ELO_URL           = 'https://www.eloratings.net/World.tsv'
FIFA_WIKI_URL     = 'https://en.wikipedia.org/wiki/List_of_FIFA_country_codes'
FIFA_CACHE_PATH   = Path(__file__).parent / 'fifa_members_cache.json'
FIFA_CACHE_TTL_DAYS = 30

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/tab-separated-values, text/plain, */*',
}

# UK home nations + Tahiti: eloratings code → (app_id, iso2, name, fifaMember)
# These bypass the standard ISO alpha-2 lookup entirely.
ELO_SPECIAL = {
    'EN': (8260, 'gb-eng', 'England',          True),
    'SQ': (8261, 'gb-sct', 'Scotland',         True),
    'WA': (8262, 'gb-wls', 'Wales',            True),
    'EI': (8263, 'gb-nir', 'Northern Ireland', True),
    'TI': (258,  'pf',     'Tahiti',           True),
}

# eloratings codes that differ from ISO 3166-1 alpha-2
ELO_OVERRIDES = {
    'KO': 'KR',  # South Korea
    'NM': 'MK',  # North Macedonia
    'SW': 'SZ',  # Eswatini (eloratings uses legacy Swaziland code)
}

# Non-sovereign entities with no ISO 3166-1 alpha-2 code → weirdo=True
# No further lookup is attempted for these.
# FIFA member names that pycountry cannot resolve automatically (FIFA uses
# non-ISO common names for some countries) are resolved via the shared
# pipeline/country_aliases.json table (country_registry.resolve_iso2) instead
# of a local override dict.

WEIRDO_NAMES = {
    'NS': 'Northern Cyprus',
    'KD': 'Kurdistan',
    'ZN': 'Zanzibar',
    'JS': 'Somaliland',
    'HG': 'Chagos Islands',
    'EU': 'Sint Eustatius',
    'AB': 'Saba',
    'TE': 'Tibet',
}


def _strip_footnotes(text):
    return re.sub(r'\[.*?\]', '', text).strip()


def fetch_fifa_members_iso2():
    """
    Scrape the main FIFA member table on Wikipedia's List of FIFA country codes
    to build the positive set of ISO alpha-2 codes for all 211 full FIFA members.

    Using a positive list is more accurate than a negative one: any entity in
    the eloratings TSV that doesn't appear here gets fifaMember=False, even if
    it also wasn't listed in the non-member table (Vatican, Greenland, etc.).

    UK home nations and Tahiti are not resolvable via pycountry (no ISO alpha-2
    country code) and will appear as unresolved — that is expected and harmless
    because ELO_SPECIAL entries have fifaMember hardcoded to True.

    Returns a frozenset of lowercase ISO2 strings.
    """
    resp = requests.get(FIFA_WIKI_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')

    # The member table is split into several alphabetical sub-tables on this page.
    # Collect all tables that appear before the "Non-FIFA member codes" heading.
    non_member_heading = None
    for tag in soup.find_all(['h2', 'h3', 'h4']):
        text = tag.get_text().lower()
        if 'non' in text and 'member' in text:
            non_member_heading = tag
            break

    member_tables = []
    for table in soup.find_all('table', class_='wikitable'):
        if non_member_heading and table.find_previous(['h2', 'h3', 'h4']) == non_member_heading:
            break
        member_tables.append(table)

    if not member_tables:
        print('Warning: could not find main FIFA members table on Wikipedia',
              file=sys.stderr)
        return frozenset()

    members = set()
    unresolved = []

    all_rows = []
    for table in member_tables:
        all_rows.extend(table.find_all('tr')[1:])  # skip header row per sub-table

    for row in all_rows:
        cells = row.find_all(['td', 'th'])
        if len(cells) < 2:
            continue
        name = _strip_footnotes(cells[0].get_text())
        if not name:
            continue

        # Shared alias table for FIFA names that pycountry can't auto-resolve
        try:
            members.add(reg.resolve_iso2(name))
            continue
        except reg.UnknownCountryError:
            pass

        country = (pycountry.countries.get(name=name) or
                   pycountry.countries.get(common_name=name) or
                   pycountry.countries.get(official_name=name))

        if not country:
            # Prefix match: "Sint Maarten" → "Sint Maarten (Dutch part)", etc.
            prefix_matches = [c for c in pycountry.countries
                              if c.name.startswith(name) or
                                 getattr(c, 'common_name', '').startswith(name)]
            if len(prefix_matches) == 1:
                country = prefix_matches[0]

        if not country:
            try:
                results = pycountry.countries.search_fuzzy(name)
                country = results[0] if results else None
            except LookupError:
                country = None

        if country:
            members.add(country.alpha_2.lower())
        else:
            # Expected: England, Scotland, Wales, Northern Ireland, Tahiti —
            # all handled via ELO_SPECIAL with fifaMember=True.
            unresolved.append(name)

    if unresolved:
        print(f'  Unresolved (ELO_SPECIAL or sub-national): {unresolved}',
              file=sys.stderr)
    print(f'  FIFA member ISO2 codes found: {len(members)}', flush=True)
    return frozenset(members)


FETCH_RETRIES = 3
FETCH_RETRY_DELAY_SECONDS = 5


def fetch_tsv():
    last_error = None
    for attempt in range(1, FETCH_RETRIES + 1):
        resp = requests.get(ELO_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        text = resp.text

        # eloratings.net occasionally answers bot-looking requests with an HTML
        # interstitial ("One moment, please...") instead of the TSV. Detect that
        # before persisting anything, so a transient block can't stomp good data.
        first_line = text.strip().splitlines()[0] if text.strip() else ''
        if re.match(r'^\d+\t', first_line):
            OUT_TSV.write_text(text, encoding='utf-8')
            return text

        last_error = f'Unexpected response from {ELO_URL} (not TSV): {first_line[:80]!r}'
        if attempt < FETCH_RETRIES:
            print(f'  Attempt {attempt}/{FETCH_RETRIES} failed ({last_error}); '
                  f'retrying in {FETCH_RETRY_DELAY_SECONDS}s…', file=sys.stderr, flush=True)
            time.sleep(FETCH_RETRY_DELAY_SECONDS)

    raise ValueError(last_error)


def parse(tsv_text, fifa_members_iso2):
    rankings = []
    seen_ids = set()

    for line in tsv_text.splitlines():
        parts = line.strip().split('\t')
        if len(parts) < 4:
            continue
        try:
            rank = int(parts[0])
            code = parts[2].upper()
            pts  = int(parts[3])
        except ValueError:
            continue

        # ── Weirdos: non-sovereign, no ISO alpha-2 ──────────────────────────
        if code in WEIRDO_NAMES:
            rankings.append({
                'rank': rank, 'id': None, 'iso2': None,
                'name': WEIRDO_NAMES[code], 'pts': pts,
                'fifaMember': False, 'weirdo': True,
            })
            continue

        # ── Special cases (UK home nations + Tahiti) ────────────────────────
        if code in ELO_SPECIAL:
            num_id, iso2, name, is_fifa = ELO_SPECIAL[code]
            if num_id not in seen_ids:
                seen_ids.add(num_id)
                rankings.append({
                    'rank': rank, 'id': num_id, 'iso2': iso2,
                    'name': name, 'pts': pts,
                    'fifaMember': is_fifa, 'weirdo': False,
                })
            continue

        # ── Standard ISO alpha-2 lookup ─────────────────────────────────────
        iso2 = ELO_OVERRIDES.get(code, code)
        country = pycountry.countries.get(alpha_2=iso2)

        if country is None:
            # Code not in ISO 3166-1: treat as weirdo
            rankings.append({
                'rank': rank, 'id': None, 'iso2': iso2.lower(),
                'name': iso2, 'pts': pts,
                'fifaMember': False, 'weirdo': True,
            })
            continue

        try:
            num_id = int(country.numeric)
        except (ValueError, TypeError):
            num_id = None

        if num_id in seen_ids:
            continue
        if num_id is not None:
            seen_ids.add(num_id)

        try:
            name = reg.canonical_name(iso2.lower())
        except reg.UnknownCountryError:
            # Not expected — countries.json is built from the same ISO data
            # pycountry uses — but don't let a display-name nicety crash the
            # whole ratings update over it.
            print(f'  Warning: iso2 {iso2.lower()!r} not in countries.json, '
                  f'falling back to pycountry name {country.name!r}', file=sys.stderr)
            name = country.name

        rankings.append({
            'rank': rank, 'id': num_id, 'iso2': iso2.lower(),
            'name': name, 'pts': pts,
            'fifaMember': iso2.lower() in fifa_members_iso2,
            'weirdo': False,
        })

    return sorted(rankings, key=lambda x: x['rank'])


def _load_fifa_cache():
    """Return cached FIFA iso2 set if fresh, else None."""
    if not FIFA_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(FIFA_CACHE_PATH.read_text(encoding='utf-8'))
        cached_at = datetime.fromisoformat(data['cached_at']).replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - cached_at).days
        if age_days >= FIFA_CACHE_TTL_DAYS:
            print(f'FIFA cache is {age_days} days old — refreshing.', flush=True)
            return None
        return frozenset(data['members'])
    except Exception:
        return None


def _save_fifa_cache(members):
    data = {
        'cached_at': datetime.now(timezone.utc).isoformat(),
        'members': sorted(members),
    }
    FIFA_CACHE_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')


def main():
    fifa_members_iso2 = _load_fifa_cache()
    if fifa_members_iso2 is None:
        print('Scraping FIFA member list from Wikipedia…', flush=True)
        try:
            fifa_members_iso2 = fetch_fifa_members_iso2()
            _save_fifa_cache(fifa_members_iso2)
        except Exception as e:
            print(f'Wikipedia scrape failed: {e} — fifaMember flags will default to False',
                  file=sys.stderr)
            fifa_members_iso2 = frozenset()
    else:
        print(f'FIFA member list: loaded from cache ({len(fifa_members_iso2)} members).', flush=True)

    print('Fetching Elo ratings from eloratings.net…', flush=True)
    try:
        tsv_text = fetch_tsv()
    except Exception as e:
        print(f'Fetch failed: {e}', file=sys.stderr, flush=True)
        sys.exit(1)

    rankings = parse(tsv_text, fifa_members_iso2)

    n_fifa   = sum(1 for r in rankings if r['fifaMember'])
    n_weirdo = sum(1 for r in rankings if r['weirdo'])
    print(f'Parsed {len(rankings)} entries from eloratings.net: {n_fifa} FIFA members, {n_weirdo} weirdos.', flush=True)

    today = date.today().isoformat()

    # FIFA members known to be absent from eloratings.net (no Elo history).
    # Listed here so the UI can explain the discrepancy between FIFA's official
    # 211-member count and the fifaMember=true count in this file.
    fifa_absences = [
        {
            'iso2': 'xk',
            'name': 'Kosovo',
            'reason': 'FIFA member since 2016; absent from eloratings.net World.tsv',
        },
    ]

    new_data = {
        'source':  'eloratings.net',
        'updated': today,
        'stats': {
            'total':            len(rankings),
            'fifaMembers':      n_fifa,
            'nonFifaSovereign': sum(1 for r in rankings if not r['fifaMember'] and not r['weirdo']),
            'weirdos':          n_weirdo,
            'fifaOfficialCount': 211,
            'fifaAbsences':     len(fifa_absences),
        },
        'fifaAbsences': fifa_absences,
        'rankings': rankings,
    }

    if OUT.exists():
        old = json.loads(OUT.read_text(encoding='utf-8'))
        if (old.get('rankings') == rankings and
                old.get('stats') == new_data['stats'] and
                old.get('fifaAbsences') == new_data['fifaAbsences']):
            print('Rankings unchanged — no update needed.', flush=True)
            return

    OUT.write_text(
        json.dumps(new_data, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    print(f'Written {OUT.name}.', flush=True)

    subprocess.run(
        [sys.executable, str(Path(__file__).parent / 'patch_kosovo.py')],
        check=True,
    )

    final = json.loads(OUT.read_text(encoding='utf-8'))
    fr = final['rankings']
    print(f'Final: {len(fr)} entries, {sum(1 for r in fr if r.get("fifaMember"))} FIFA members, {sum(1 for r in fr if r.get("weirdo"))} weirdos.', flush=True)


if __name__ == '__main__':
    main()
