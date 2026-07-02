#!/usr/bin/env python3
"""
build_player_wiki.py — resolve every api-football player/coach id seen in a
finished WC2026 fixture to this repo's Wikipedia data, once, at build time.

Same root problem as pipeline/country_registry.py, one level down: the same
person shows up under different name strings from different sources
(abbreviated initials, transliteration spelling variants, dropped middle
names, nicknames...), and mundial/wc2026_live.html used to reconcile that at
render time via a fragile 3-tier name-matching heuristic that silently
dropped enrichment on a miss (e.g. "Lionel Mpasi" vs "Lionel Mpasi Nzau").

This script does that resolution here instead, and exports a static
id -> {wikiTitle, birthCountry} lookup so the frontend does a plain
dictionary lookup by api-football's numeric player id — no string matching
at render time at all. Keyed by id, not name, because the same person's
rendered name has been observed to change between fixtures (abbreviated in
some, full in others) — the id is the stable identity, the name string isn't.

wikiTitle is the EN Wikipedia title — the same join key map_data.json's
players carry — used to look up a localized title in whichever single
data/wiki_<lang>.json file the frontend actually needs (see
add_wiki_urls.py), rather than embedding all 5 languages' URLs here too.

Resolution order per (still-unmatched) api id within a team:
  1. pipeline/player_aliases_confirmed.json (manually verified, keyed by id)
  2. The 7-tier rule-based matcher (norm / initials+tail / prefix /
     middle-optional / phonetic / mononym / soundex)
Anything left over is written to pipeline/player_aliases_manual.json for
manual research (see player_aliases_confirmed.json's structure to promote an
entry once confirmed).

This is a living dataset, not a one-time export: new fixtures introduce ids
never seen before (injury returns, Round of 16 onward, etc.), so re-run this
whenever fixtures/lineups are refreshed — same cadence as elo_rank.json /
r32_teams.json.

Usage:
    pip install requests jellyfish
    export API_FOOTBALL_KEY=your_key_here
    python3 pipeline/build_player_wiki.py
"""
import csv
import json
import os
import re
import sys
import time
import unicodedata
import difflib
from pathlib import Path

try:
    import requests
    import jellyfish
except ImportError:
    print("Missing deps. Run: pip install requests jellyfish", file=sys.stderr)
    sys.exit(1)

import country_registry as reg

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

WC2026_LEAGUE_ID = 1
WC2026_SEASON = 2026
API_BASE = "https://v3.football.api-sports.io"

ROOT = Path(__file__).parent
PLAYERS_CSV = ROOT / "wc2026_players.csv"
COACHES_CSV = ROOT / "wc2026_coaches.csv"
MAP_DATA = ROOT.parent / "data" / "map_data.json"
CONFIRMED_JSON = ROOT / "player_aliases_confirmed.json"
MANUAL_JSON = ROOT / "player_aliases_manual.json"
OUT_JSON = ROOT.parent / "data" / "player_wiki.json"


# ── Matching rules (unchanged from the prototype) ────────────────────────

def frontend_norm(s):
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def norm2(s):
    s = frontend_norm(s)
    s = s.replace('ß', 'ss')
    s = s.replace('ı', 'i')
    s = s.replace('-', ' ')
    s = re.sub(r'\.(?=\S)', '. ', s)
    s = s.replace('.', '')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def tokens(s):
    n = norm2(s)
    return n.split(' ') if n else []


def is_initial(tok):
    return len(tok) == 1


def tokens_compatible(t1, t2):
    return (
        t1 == t2
        or (is_initial(t1) and t1[0] == t2[0])
        or (is_initial(t2) and t2[0] == t1[0])
    )


def is_subsequence_compatible(sub, full):
    it = iter(full)
    for s in sub:
        if not any(tokens_compatible(s, f) for f in it):
            return False
    return True


def match_tier(wiki_name, api_name):
    if frontend_norm(wiki_name) == frontend_norm(api_name):
        return ('norm', 5)

    wt, at = tokens(wiki_name), tokens(api_name)
    if not wt or not at:
        return None

    shorter, longer = (wt, at) if len(wt) <= len(at) else (at, wt)

    if len(shorter) >= 2:
        if all(
            tokens_compatible(shorter[-(i + 1)], longer[-(i + 1)])
            for i in range(len(shorter))
        ):
            exact_positions = sum(
                shorter[-(i + 1)] == longer[-(i + 1)] for i in range(len(shorter))
            )
            return ('initials+tail', 4 if exact_positions == len(shorter) else 3)

        if all(tokens_compatible(shorter[i], longer[i]) for i in range(len(shorter))):
            return ('prefix', 3)

        if (
            tokens_compatible(shorter[0], longer[0])
            and shorter[-1] == longer[-1]
            and is_subsequence_compatible(shorter[1:-1], longer[1:-1])
        ):
            return ('middle-optional', 3)

        if (
            tokens_compatible(shorter[0], longer[0])
            and shorter[-1] != longer[-1]
            and jellyfish.metaphone(shorter[-1]) == jellyfish.metaphone(longer[-1])
            and is_subsequence_compatible(shorter[1:-1], longer[1:-1])
        ):
            return ('phonetic', 2)

    if len(wt) == 1 and wt[0] in at:
        return ('mononym', 1)
    if len(at) == 1 and at[0] in wt:
        return ('mononym', 1)

    if len(shorter) >= 2:
        if (
            tokens_compatible(shorter[0], longer[0])
            and shorter[-1] != longer[-1]
            and jellyfish.soundex(shorter[-1]) == jellyfish.soundex(longer[-1])
            and is_subsequence_compatible(shorter[1:-1], longer[1:-1])
        ):
            return ('soundex', 0)

    return None


# ── api-football fetch ───────────────────────────────────────────────────

def fetch_json(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        print(f"API error: {data['errors']}", file=sys.stderr)
        sys.exit(1)
    return data


def fetch_finished_fixtures(headers):
    data = fetch_json(
        f"{API_BASE}/fixtures",
        {"league": WC2026_LEAGUE_ID, "season": WC2026_SEASON},
        headers,
    )
    fixtures = data.get("response", [])
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
    team_names = {}
    for f in fixtures:
        team_names[f["teams"]["home"]["id"]] = f["teams"]["home"]["name"]
        team_names[f["teams"]["away"]["id"]] = f["teams"]["away"]["name"]
    return finished, team_names


def fetch_rosters(finished_fixtures, headers):
    """team id -> {"players": {id: name}, "coaches": {id: name}}"""
    roster = {}
    for i, f in enumerate(finished_fixtures):
        fid = f["fixture"]["id"]
        data = fetch_json(f"{API_BASE}/fixtures/lineups", {"fixture": fid}, headers)
        for team in data.get("response", []):
            tid = team["team"]["id"]
            slot = roster.setdefault(tid, {"players": {}, "coaches": {}})
            for p in team["startXI"] + team["substitutes"]:
                slot["players"][p["player"]["id"]] = p["player"]["name"]
            coach = team.get("coach")
            if coach and coach.get("id"):
                slot["coaches"][coach["id"]] = coach["name"]
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(finished_fixtures)} fixtures fetched", flush=True)
        time.sleep(0.15)
    return roster


# ── Matching per nation ───────────────────────────────────────────────────

def match_team(wiki_names, api_id_to_name, confirmed_ids):
    """Returns (matched: {id: wiki_name}, unmatched_api: [(id,name)],
    unmatched_wiki: [name])."""
    matched = {}
    remaining_wiki = list(wiki_names)
    remaining_api = list(api_id_to_name.items())

    # Pass 1: confirmed ids (manually verified, immune to name drift).
    still_api = []
    for aid, name in remaining_api:
        if aid in confirmed_ids:
            wiki_name = confirmed_ids[aid]
            matched[aid] = wiki_name
            if wiki_name in remaining_wiki:
                remaining_wiki.remove(wiki_name)
        else:
            still_api.append((aid, name))
    remaining_api = still_api

    # Safety net: two different real people can render as the exact same
    # string within one team (e.g. Argentina's "L. Martinez" ×2 — Lisandro
    # and Lautaro Martínez). Nothing in either dataset's text distinguishes
    # them, so a string-similarity tiebreak would just be guessing —
    # confirmed by hand once (already got this backwards once, caught only
    # by an independent birth-date check). Pull duplicate-named api entries
    # out entirely rather than let Pass 2/3 silently pick one.
    from collections import Counter
    name_counts = Counter(name for _, name in remaining_api)
    ambiguous_api = [(aid, name) for aid, name in remaining_api if name_counts[name] > 1]
    remaining_api = [(aid, name) for aid, name in remaining_api if name_counts[name] == 1]

    # Pass 2: exact string matches.
    still_api, still_wiki = [], list(remaining_wiki)
    for aid, name in remaining_api:
        if name in still_wiki:
            matched[aid] = name
            still_wiki.remove(name)
        else:
            still_api.append((aid, name))
    remaining_api, remaining_wiki = still_api, still_wiki

    # Pass 3: ranked rule-based matching (bipartite greedy, index-tracked so
    # two different people who happen to render identically stay distinct).
    candidates = []
    for wi, w in enumerate(remaining_wiki):
        for ai, (aid, a) in enumerate(remaining_api):
            t = match_tier(w, a)
            if t is None:
                continue
            tier, rank = t
            ratio = difflib.SequenceMatcher(None, norm2(w), norm2(a)).ratio()
            candidates.append((rank, ratio, wi, ai))
    candidates.sort(key=lambda x: (-x[0], -x[1]))

    used_wi, used_ai = set(), set()
    for rank, ratio, wi, ai in candidates:
        if wi in used_wi or ai in used_ai:
            continue
        aid, _ = remaining_api[ai]
        matched[aid] = remaining_wiki[wi]
        used_wi.add(wi)
        used_ai.add(ai)

    unmatched_api = [remaining_api[i] for i in range(len(remaining_api)) if i not in used_ai]
    unmatched_api += ambiguous_api
    unmatched_wiki = [remaining_wiki[i] for i in range(len(remaining_wiki)) if i not in used_wi]
    return matched, unmatched_api, unmatched_wiki


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", default=os.environ.get("API_FOOTBALL_KEY"), help="api-football key")
    args = parser.parse_args()
    if not args.key:
        print("Error: API key required. Set API_FOOTBALL_KEY env var or use --key.", file=sys.stderr)
        sys.exit(1)
    headers = {"x-apisports-key": args.key}

    print("Fetching WC2026 fixtures…", flush=True)
    finished, team_names = fetch_finished_fixtures(headers)
    print(f"  {len(finished)} finished fixtures, {len(team_names)} distinct teams", flush=True)

    print("Fetching lineups for each finished fixture…", flush=True)
    rosters = fetch_rosters(finished, headers)

    team_id_to_iso2 = {}
    for tid, name in team_names.items():
        try:
            team_id_to_iso2[tid] = reg.resolve_iso2(name)
        except reg.UnknownCountryError as e:
            print(f"  Warning: {e}", file=sys.stderr)

    wiki_by_nation = {}
    with open(PLAYERS_CSV, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            wiki_by_nation.setdefault(row['nation'], []).append(row['player'])
    if COACHES_CSV.exists():
        with open(COACHES_CSV, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row.get('coach'):
                    wiki_by_nation.setdefault(row['nation'], []).append(row['coach'])

    map_data = json.loads(MAP_DATA.read_text(encoding='utf-8'))
    name_to_record = {}
    for rec in map_data.get('data', []):
        for p in rec['players']:
            name_to_record[p['name']] = {'wiki_title': p.get('wikiTitle'), 'birth_country': rec['country']}
    for nation, players in map_data.get('natives', {}).items():
        for p in players:
            name_to_record[p['name']] = {'wiki_title': p.get('wikiTitle'), 'birth_country': nation}

    confirmed_raw = json.loads(CONFIRMED_JSON.read_text(encoding='utf-8'))
    confirmed_ids = {}
    for nation, entries in confirmed_raw.items():
        if nation.startswith('_'):
            continue
        for e in entries:
            confirmed_ids[e['api_football_id']] = e['wikipedia_name']

    output = {}
    unresolved_by_nation = {}
    n_confirmed = n_ruled = n_exact = 0

    for tid, iso2 in sorted(team_id_to_iso2.items(), key=lambda x: x[1]):
        nation = reg.display_name(iso2)
        wiki_names = wiki_by_nation.get(nation, [])
        roster = rosters.get(tid, {"players": {}, "coaches": {}})
        api_id_to_name = {**roster['players'], **roster['coaches']}
        if not api_id_to_name:
            continue

        matched, unmatched_api, unmatched_wiki = match_team(wiki_names, api_id_to_name, confirmed_ids)

        team_out = {}
        for aid, wiki_name in matched.items():
            rec = name_to_record.get(wiki_name)
            if not rec:
                continue
            team_out[str(aid)] = {
                'wikiTitle': rec['wiki_title'],
                'birthCountry': rec['birth_country'],
            }
        if team_out:
            output[iso2] = team_out

        if unmatched_api or unmatched_wiki:
            unresolved_by_nation[nation] = {
                "api_football": sorted(
                    [f"{name} (id {aid})" for aid, name in unmatched_api]
                ),
                "wikipedia": sorted(unmatched_wiki),
            }

    total_matched = sum(len(v) for v in output.values())
    print(f"Resolved: {total_matched} ids across {len(output)} teams", flush=True)
    n_unresolved = sum(
        len(v["api_football"]) + len(v["wikipedia"]) for v in unresolved_by_nation.values()
    )
    print(f"Unresolved names: {n_unresolved} across {len(unresolved_by_nation)} nations", flush=True)

    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {OUT_JSON}", flush=True)

    if unresolved_by_nation:
        existing_manual = {}
        if MANUAL_JSON.exists():
            existing_manual = json.loads(MANUAL_JSON.read_text(encoding='utf-8'))
        # Preserve any hand-written "_note" already on a nation's entry.
        for nation, lists in unresolved_by_nation.items():
            note = existing_manual.get(nation, {}).get('_note')
            if note:
                lists['_note'] = note
        with open(MANUAL_JSON, 'w', encoding='utf-8') as f:
            json.dump(dict(sorted(unresolved_by_nation.items())), f, ensure_ascii=False, indent=2)
        print(f"Wrote {MANUAL_JSON} ({len(unresolved_by_nation)} nations still need review)", flush=True)
    elif MANUAL_JSON.exists():
        MANUAL_JSON.write_text("{}\n", encoding='utf-8')
        print(f"{MANUAL_JSON} cleared — nothing unresolved.", flush=True)


if __name__ == "__main__":
    main()
