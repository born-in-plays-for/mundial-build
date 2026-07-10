#!/usr/bin/env python3
"""
Cross-checks pipeline/wc2026_players.csv and pipeline/wc2026_coaches.csv
against FIFA's official squad-list PDF (pipeline/fifa_squad_lists_2026.pdf).

The PDF and our CSVs don't share a join key, so players are matched within
each nation by date of birth (present in both sources) rather than by name
— the PDF's "SURNAME Firstname" formatting doesn't string-match our CSV's
Wikipedia-scraped "Firstname Surname" values directly.

Usage:
    python3 check_fifa_pdf_consistency.py
"""
import csv
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import country_registry as reg

PDF_PATH = Path(__file__).parent / "fifa_squad_lists_2026.pdf"
PLAYERS_CSV = Path(__file__).parent / "wc2026_players.csv"
COACHES_CSV = Path(__file__).parent / "wc2026_coaches.csv"

TEAM_HEADER_RE = re.compile(r"^([A-Za-zÀ-ÖØ-öø-ÿ .'-]+)\s+\(([A-Z]{3})\)$")
SKIP_LINE_RE = re.compile(
    r"^(#|ROLE|DOB|SQUAD LIST|\d+ June 2026|\w+day, \d+ \w+ 2026)"
)


def normalize(s: str) -> str:
    """Accent/case-insensitive comparison key."""
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z ]', '', s.lower()).strip()


def pdf_common_name(first_names: str, last_names: str) -> str:
    """Best-effort 'Firstname Surname' reconstruction from the PDF's split
    FIRST NAME(S) / LAST NAME(S) columns, to compare against our CSV's
    Wikipedia-scraped 'player' column (e.g. 'Ramiz Larbi' + 'ZERROUKI' ->
    'Ramiz Zerrouki', matching the CSV's 'Ramiz Zerrouki')."""
    first = first_names.split()[0] if first_names.strip() else ''
    last = last_names.title()
    return f"{first} {last}".strip()


# ── PDF parsing ──────────────────────────────────────────────────────────

def pdf_text() -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(PDF_PATH), "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def parse_pdf():
    """Returns (players, coaches) — lists of dicts, each carrying iso2."""
    players, coaches = [], []
    current_iso2 = None
    current_nation_pdf = None

    for raw_line in pdf_text().split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        m = TEAM_HEADER_RE.match(line)
        if m and not line.startswith('#'):
            current_nation_pdf = m.group(1)
            try:
                current_iso2 = reg.resolve_iso2(current_nation_pdf)
            except Exception as e:
                print(f"⚠ unresolved PDF nation name: {current_nation_pdf!r} ({e})", file=sys.stderr)
                current_iso2 = None
            continue

        if SKIP_LINE_RE.match(line):
            continue

        parts = re.split(r'\s{2,}', line)

        if current_iso2 and re.match(r'^\d{1,2}$', parts[0]) and len(parts) >= 11:
            num, pos, _player_name, first_names, last_names, _shirt, dob, club, height, caps, goals = parts[:11]
            try:
                dob_date = datetime.strptime(dob, "%d/%m/%Y").date()
            except ValueError:
                dob_date = None
            players.append({
                'iso2': current_iso2,
                'nation_pdf': current_nation_pdf,
                'number': num,
                'pos': pos,
                'name': pdf_common_name(first_names, last_names),
                'dob': dob_date,
                'club': club,
                'height_cm': int(height) if height.isdigit() else None,
                'caps': int(caps) if caps.isdigit() else None,
                'goals': int(goals) if goals.isdigit() else None,
            })
        elif current_iso2 and parts[0] == 'Head coach' and len(parts) >= 5:
            _role, _name, first_names, last_names, nationality = parts[:5]
            coaches.append({
                'iso2': current_iso2,
                'nation_pdf': current_nation_pdf,
                'name': pdf_common_name(first_names, last_names),
                'nationality': nationality,
            })

    return players, coaches


# ── CSV loading ──────────────────────────────────────────────────────────

def load_players_csv():
    with open(PLAYERS_CSV, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            iso2 = reg.resolve_iso2(r['nation'])
        except Exception:
            iso2 = None
        try:
            dob = datetime.strptime(r['birth_date'], "%d %B %Y").date()
        except ValueError:
            dob = None
        out.append({**r, 'iso2': iso2, 'dob': dob})
    return out


def load_coaches_csv():
    with open(COACHES_CSV, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            iso2 = reg.resolve_iso2(r['nation'])
        except Exception:
            iso2 = None
        out.append({**r, 'iso2': iso2})
    return out


# ── Matching ─────────────────────────────────────────────────────────────

def match_players(csv_players, pdf_players):
    """Match within each nation, primarily by DOB, falling back to
    normalized-name matching for anything DOB couldn't pair (e.g. a squad
    change between when we scraped Wikipedia and the PDF's 9 July 2026
    cutoff, or a DOB typo on one side)."""
    by_nation_csv = defaultdict(list)
    by_nation_pdf = defaultdict(list)
    for p in csv_players:
        if p['iso2']:
            by_nation_csv[p['iso2']].append(p)
    for p in pdf_players:
        if p['iso2']:
            by_nation_pdf[p['iso2']].append(p)

    matched, unmatched_csv, unmatched_pdf = [], [], []

    for iso2 in set(by_nation_csv) | set(by_nation_pdf):
        c_list = list(by_nation_csv.get(iso2, []))
        p_list = list(by_nation_pdf.get(iso2, []))

        # Pass 1: exact DOB match, only when unambiguous on both sides.
        dob_counts_c = Counter(p['dob'] for p in c_list if p['dob'])
        dob_counts_p = Counter(p['dob'] for p in p_list if p['dob'])
        for c in list(c_list):
            if c['dob'] and dob_counts_c[c['dob']] == 1 and dob_counts_p.get(c['dob']) == 1:
                p = next(p for p in p_list if p['dob'] == c['dob'])
                matched.append((c, p))
                c_list.remove(c)
                p_list.remove(p)

        # Pass 2: normalized-name match among what's left.
        p_by_name = defaultdict(list)
        for p in p_list:
            p_by_name[normalize(p['name'])].append(p)
        for c in list(c_list):
            key = normalize(c['player'])
            candidates = p_by_name.get(key, [])
            if len(candidates) == 1:
                p = candidates[0]
                matched.append((c, p))
                c_list.remove(c)
                p_list.remove(p)
                p_by_name[key].remove(p)

        unmatched_csv.extend(c_list)
        unmatched_pdf.extend(p_list)

    return matched, unmatched_csv, unmatched_pdf


def match_coaches(csv_coaches, pdf_coaches):
    by_iso2_csv = {c['iso2']: c for c in csv_coaches if c['iso2']}
    by_iso2_pdf = {c['iso2']: c for c in pdf_coaches if c['iso2']}
    matched, only_csv, only_pdf = [], [], []
    for iso2 in set(by_iso2_csv) | set(by_iso2_pdf):
        c, p = by_iso2_csv.get(iso2), by_iso2_pdf.get(iso2)
        if c and p:
            matched.append((c, p))
        elif c:
            only_csv.append(c)
        else:
            only_pdf.append(p)
    return matched, only_csv, only_pdf


# ── Report ───────────────────────────────────────────────────────────────

def strip_club_country(club: str) -> str:
    return re.sub(r'\s*\([A-Z]{3}\)\s*$', '', club).strip()


def main():
    if not PDF_PATH.exists():
        print(f"❌ {PDF_PATH} not found", file=sys.stderr)
        sys.exit(1)

    pdf_players, pdf_coaches = parse_pdf()
    csv_players = load_players_csv()
    csv_coaches = load_coaches_csv()

    print("=" * 72)
    print("FIFA official squad-list PDF vs pipeline data — consistency check")
    print("=" * 72)
    print(f"PDF:  {len(pdf_players)} players parsed (expect 48×26=1248), {len(pdf_coaches)} coaches (expect 48)")
    print(f"CSV:  {len(csv_players)} players, {len(csv_coaches)} coaches")

    # -- nation coverage --
    pdf_nations = {p['iso2'] for p in pdf_players if p['iso2']}
    csv_nations = {p['iso2'] for p in csv_players if p['iso2']}
    only_pdf_nat, only_csv_nat = pdf_nations - csv_nations, csv_nations - pdf_nations
    print("\n--- Nation coverage ---")
    if only_pdf_nat or only_csv_nat:
        if only_pdf_nat:
            print("  In PDF only:", sorted(only_pdf_nat))
        if only_csv_nat:
            print("  In CSV only:", sorted(only_csv_nat))
    else:
        print(f"  ✓ Same {len(pdf_nations)} nations in both sources.")

    # -- squad size per nation --
    pdf_counts = Counter(p['iso2'] for p in pdf_players if p['iso2'])
    csv_counts = Counter(p['iso2'] for p in csv_players if p['iso2'])
    size_mismatch = {iso2: (csv_counts.get(iso2, 0), pdf_counts.get(iso2, 0))
                      for iso2 in pdf_nations | csv_nations
                      if csv_counts.get(iso2, 0) != pdf_counts.get(iso2, 0)}
    print("\n--- Squad size (csv vs pdf) ---")
    if size_mismatch:
        for iso2, (c, p) in sorted(size_mismatch.items()):
            print(f"  {iso2}: csv={c} pdf={p}")
    else:
        print("  ✓ Every nation has matching squad size in both sources.")

    # -- player matching --
    matched, unmatched_csv, unmatched_pdf = match_players(csv_players, pdf_players)
    print(f"\n--- Player matching ---")
    print(f"  Matched:        {len(matched)}")
    print(f"  CSV unmatched:  {len(unmatched_csv)}")
    print(f"  PDF unmatched:  {len(unmatched_pdf)}")

    if unmatched_csv:
        print("\n  In our CSV, no PDF counterpart found (possible: player dropped from final squad,")
        print("  DOB typo on our side, or a name our matcher couldn't reconcile):")
        for c in unmatched_csv:
            print(f"    {c['nation']:<15} {c['player']:<28} DOB={c['birth_date']}")

    if unmatched_pdf:
        print("\n  In the PDF, no CSV counterpart found (possible: added to squad after our scrape,")
        print("  or a name our matcher couldn't reconcile):")
        for p in unmatched_pdf:
            print(f"    {p['nation_pdf']:<15} {p['name']:<28} DOB={p['dob']}")

    # -- field-level diffs among matched players --
    name_mismatches, caps_diffs, club_diffs = [], [], []
    for c, p in matched:
        if normalize(c['player']) != normalize(p['name']):
            name_mismatches.append((c, p))
        c_caps = c['caps'].strip()
        if c_caps.isdigit() and p['caps'] is not None and int(c_caps) != p['caps']:
            caps_diffs.append((c, p))
        if strip_club_country(c['club']).lower() != strip_club_country(p['club']).lower():
            club_diffs.append((c, p))
    heights = sum(1 for p in pdf_players if p['height_cm'] is not None)
    goals_total = sum(1 for p in pdf_players if p['goals'] is not None)

    print(f"\n--- Field-level diffs among {len(matched)} matched players ---")
    print(f"  Name text differs (spelling/order — informational): {len(name_mismatches)}")
    for c, p in name_mismatches[:15]:
        print(f"    csv={c['player']!r:<26} pdf={p['name']!r:<26} ({c['nation']})")
    if len(name_mismatches) > 15:
        print(f"    ... and {len(name_mismatches) - 15} more")

    print(f"\n  Caps differ: {len(caps_diffs)} (expected — PDF is dated 9 July 2026, our CSV is from an earlier scrape;")
    print(f"  caps naturally accrue with each match played)")

    print(f"\n  Club differs: {len(club_diffs)} (mostly naming convention, e.g. 'PSV Eindhoven' vs 'PSV';")
    print(f"  a few may be real transfers since our scrape)")
    for c, p in club_diffs[:10]:
        print(f"    csv={c['club']!r:<28} pdf={strip_club_country(p['club'])!r:<28} ({c['nation']}, {c['player']})")
    if len(club_diffs) > 10:
        print(f"    ... and {len(club_diffs) - 10} more")

    # -- coaches --
    coach_matched, coach_only_csv, coach_only_pdf = match_coaches(csv_coaches, pdf_coaches)
    print(f"\n--- Coaches ---")
    print(f"  Matched by nation: {len(coach_matched)}")
    coach_name_mismatches = [(c, p) for c, p in coach_matched if normalize(c['coach']) != normalize(p['name'])]
    if coach_name_mismatches:
        print(f"  Name differs (possible coaching change since our scrape, or formatting): {len(coach_name_mismatches)}")
        for c, p in coach_name_mismatches:
            print(f"    csv={c['coach']!r:<26} pdf={p['name']!r:<26} ({c['nation']})")
    else:
        print("  ✓ All matched coach names agree.")
    if coach_only_csv:
        print("  In CSV only:", [(c['nation'], c['coach']) for c in coach_only_csv])
    if coach_only_pdf:
        print("  In PDF only:", [(p['nation_pdf'], p['name']) for p in coach_only_pdf])

    # -- what the PDF has that we don't --
    print("\n--- Fields present in the PDF but not in our data model ---")
    print(f"  HEIGHT (CM): present for {heights}/{len(pdf_players)} PDF players — we don't track this at all.")
    print(f"  GOALS: present for {goals_total}/{len(pdf_players)} PDF players — we track caps but not goals.")
    print("  Shirt NUMBER: not tracked (we don't assign/store jersey numbers).")
    print("  NAME ON SHIRT: not tracked (the exact string FIFA prints on the jersey).")
    print("\n--- What the PDF does NOT have ---")
    print("  No place of birth (city or country) for players or coaches — it cannot help fill")
    print("  the remaining birth_city gaps in wc2026_players.csv.")


if __name__ == "__main__":
    main()
