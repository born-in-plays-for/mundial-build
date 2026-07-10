#!/usr/bin/env python3
"""
Mondial 2026 — Extraction des sélectionneurs par lieu de naissance
===================================================================
Sources :
  1. Wikipedia "2026 FIFA World Cup squads"  (head coach per team)
  2. Wikidata SPARQL API  (lieu de naissance via P19)
  3. Wikipedia (pages individuelles, fallback infobox)

Prérequis :
    pip install requests beautifulsoup4 pandas lxml nameparser

Usage :
    python wc2026_coaches.py

Sortie :
    wc2026_coaches.csv  — 48 coaches with birth city/country
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
import pandas as pd
from bs4 import BeautifulSoup

import country_registry as reg
from wc2026_birthplaces import compute_surname

# Hand-corrected surnames for coaches nameparser's "Firstname Surname"
# assumption gets wrong (e.g. Korean family-name-first order), keyed by
# nation then exact coach name. A present entry always wins — see
# wc2026_birthplaces.py's SURNAME_OVERRIDES_PATH docstring for why.
COACH_SURNAME_OVERRIDES_PATH = Path(__file__).parent / "coach_surname_overrides.json"


def apply_coach_surname_overrides(coaches: list) -> None:
    if not COACH_SURNAME_OVERRIDES_PATH.exists():
        return
    overrides = json.loads(COACH_SURNAME_OVERRIDES_PATH.read_text(encoding='utf-8'))
    by_nation = {c['nation']: c for c in coaches}
    applied = 0
    for nation, by_coach in overrides.items():
        for coach_name, fields in by_coach.items():
            c = by_nation.get(nation)
            if c is None or c['coach'] != coach_name:
                print(f"   ⚠ Coach surname override introuvable : {nation} / {coach_name}")
                continue
            if fields.get('surname'):
                c['surname'] = fields['surname']
                applied += 1
    if applied:
        print(f"   ✓ {applied} coach surname override(s) appliqué(s) depuis {COACH_SURNAME_OVERRIDES_PATH.name}")

# ── Configuration ──────────────────────────────────────────────────────────────

WIKI_URL        = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"
WIKI_API        = "https://en.wikipedia.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SPARQL_HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "WC2026CoachBot/1.0 (research; christophe.t60@gmail.com)",
}

# Exact nation names as they appear on the Wikipedia squads page. Only these
# 48 nations qualified for WC 2026 — any other heading is rejected. Sourced
# from pipeline/country_aliases.json (single source of truth, shared with
# wc2026_birthplaces.py) instead of a locally hardcoded, easy-to-drift copy.
QUALIFIED_NATIONS = reg.wc2026_nations()

OUT_CSV = Path(__file__).parent / "wc2026_coaches.csv"

# UK city -> home nation table lives in country_registry.py (shared with
# wc2026_birthplaces.py, via build_json.py, which faces the same ambiguity).

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    text = re.sub(r'\[[\w\s]*\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    return text.strip()


def _get_with_backoff(url, params, headers, timeout=15, max_retries=5):
    delay = 1.0
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return {}


# ── Parsing Wikipedia ────────────────────────────────────────────────────────

# Map Wikipedia flag alt-text → nation name used in our data
FLAG_ALT_TO_NATION = {
    "United States":  "United States",
    "Belgium":        "Belgium",
    "Spain":          "Spain",
    "France":         "France",
    "Germany":        "Germany",
    "Italy":          "Italy",
    "Portugal":       "Portugal",
    "Argentina":      "Argentina",
    "Brazil":         "Brazil",
    "Netherlands":    "Netherlands",
    "England":        "England",
    "Scotland":       "Scotland",
    "Wales":          "Wales",
    "Northern Ireland": "Northern Ireland",
    "Serbia":         "Serbia",
    "Croatia":        "Croatia",
    "Uruguay":        "Uruguay",
    "Colombia":       "Colombia",
    "Mexico":         "Mexico",
    "Japan":          "Japan",
    "South Korea":    "South Korea",
    "Australia":      "Australia",
    "Switzerland":    "Switzerland",
    "Austria":        "Austria",
    "Sweden":         "Sweden",
    "Norway":         "Norway",
    "Denmark":        "Denmark",
    "Czech Republic": "Czech Republic",
    "Czechia":        "Czech Republic",
    "Turkey":         "Turkey",
    "Türkiye":        "Turkey",
    "Morocco":        "Morocco",
    "Algeria":        "Algeria",
    "Tunisia":        "Tunisia",
    "Egypt":          "Egypt",
    "Senegal":        "Senegal",
    "Ghana":          "Ghana",
    "Ivory Coast":    "Ivory Coast",
    "South Africa":   "South Africa",
    "DR Congo":       "DR Congo",
    "Iran":           "Iran",
    "Iraq":           "Iraq",
    "Qatar":          "Qatar",
    "Saudi Arabia":   "Saudi Arabia",
    "Jordan":         "Jordan",
    "Uzbekistan":     "Uzbekistan",
    "Ecuador":        "Ecuador",
    "Paraguay":       "Paraguay",
    "Panama":         "Panama",
    "Canada":         "Canada",
    "Haiti":          "Haiti",
    "New Zealand":    "New Zealand",
    "Cape Verde":     "Cape Verde",
    "Curaçao":        "Curaçao",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Romania":        "Romania",
    "Hungary":        "Hungary",
    "Poland":         "Poland",
    "Greece":         "Greece",
    "Chile":          "Chile",
    "Peru":           "Peru",
    "Venezuela":      "Venezuela",
    "Russia":         "Russia",
    "Ukraine":        "Ukraine",
    "Nigeria":        "Nigeria",
    "Cameroon":       "Cameroon",
}


def parse_coaches(soup: BeautifulSoup) -> list:
    """Extract head coach for each qualified nation from the squads page."""
    coaches = []
    current_nation = None

    content = soup.find('div', id='mw-content-text') or soup
    elements = content.find_all(['h2', 'h3', 'p', 'div', 'table'])

    for el in elements:
        # Track current nation from headings
        if el.name in ('h2', 'h3'):
            txt = re.sub(r'\[.*?\]', '', el.get_text()).strip()
            skip = {'Contents', 'References', 'External links', 'See also',
                    'Notes', 'Navigation menu', 'Groups', 'Squads'}
            if txt in skip or len(txt) < 2:
                continue
            if re.match(r'^Group [A-Z]$', txt):
                continue
            nation = re.sub(r'\s*\([A-Z]{3}\)', '', txt).strip()
            if nation in QUALIFIED_NATIONS:
                current_nation = nation
            else:
                current_nation = None
            continue

        if current_nation is None:
            continue

        # Look for "Coach:" or "Manager:" text
        text = el.get_text()
        if not re.search(r'\b(?:Coach|Manager|Head coach)\b', text, re.IGNORECASE):
            continue

        # Already found this nation's coach?
        if any(c['nation'] == current_nation for c in coaches):
            continue

        # Extract coach name from the link closest to "Coach:"/"Manager:" label
        coach_name = ''
        wiki_title = ''
        coach_nationality = current_nation  # default: same as team

        # Find all links in this element
        links = el.find_all('a', href=True)

        # The coach name link is typically right after the "Coach:" label
        # For foreign coaches, a flag image link precedes the coach name link
        coach_link = None
        for a in links:
            href = a['href']
            if not href.startswith('/wiki/'):
                continue
            # Skip country/flag links (they link to countries, not people)
            link_title = unquote(href[6:]).replace('_', ' ')
            # Check if this link has a flag image (→ it's the nationality link)
            img = a.find('img')
            if img:
                alt = img.get('alt', '')
                if alt in FLAG_ALT_TO_NATION:
                    coach_nationality = FLAG_ALT_TO_NATION[alt]
                continue
            # This should be the coach name link. Don't stop at the first
            # match: a team that fired its coach mid-tournament gets a
            # "Coach: A (first match) / B (remaining matches)" line with two
            # name links — Wikipedia lists the former coach first and the
            # current one last, so keep overwriting and end up with the
            # last (most current) one. For the common single-coach case
            # this loop only ever finds one match anyway, so behavior is
            # unchanged.
            name = a.get_text(strip=True)
            if name and len(name) > 1 and not re.match(r'^(Coach|Manager|Head coach)$', name, re.I):
                coach_name = clean(name)
                wiki_title = link_title

        if not coach_name:
            # Fallback: extract name from text after "Coach:" label
            m = re.search(r'(?:Coach|Manager|Head coach)\s*:\s*(.+)', text, re.IGNORECASE)
            if m:
                coach_name = clean(m.group(1).split('\n')[0])

        if coach_name:
            coaches.append({
                'nation':       current_nation,
                'coach':        coach_name,
                'surname':      compute_surname(coach_name),
                'wiki_title':   wiki_title,
                'nationality':  coach_nationality,
                'birth_city':   '',
                'birth_country': '',
            })
            print(f"  ✓ {current_nation:<30} {coach_name:<30} (nationality: {coach_nationality})")

    return coaches


# ── Wikidata enrichment (birth city/country) ─────────────────────────────────

def get_wikidata_ids(titles: list) -> dict:
    mapping = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i+50]
        params = {
            "action": "query",
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "titles": "|".join(batch),
            "format": "json",
        }
        try:
            data = _get_with_backoff(WIKI_URL.replace('/wiki/2026_FIFA_World_Cup_squads', '/w/api.php'),
                                     params, HEADERS)
            for page in data.get("query", {}).get("pages", {}).values():
                qid = page.get("pageprops", {}).get("wikibase_item", "")
                if qid:
                    mapping[page.get("title", "")] = qid
        except Exception as e:
            print(f"   ⚠ Wikipedia API: {e}")
        time.sleep(1.5)
    return mapping


def get_birthplaces(qids: list) -> dict:
    mapping = {}
    values = " ".join(f"wd:{q}" for q in qids)
    query = f"""
SELECT ?item ?birthCityLabel ?birthCountryLabel WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{
    ?item wdt:P19 ?birthCity.
    OPTIONAL {{ ?birthCity wdt:P17 ?birthCountry. }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""
    try:
        data = _get_with_backoff(
            WIKIDATA_SPARQL,
            {"query": query},
            SPARQL_HEADERS,
            timeout=60,
        )
        for row in data.get("results", {}).get("bindings", []):
            qid = row["item"]["value"].split("/")[-1]
            city    = row.get("birthCityLabel", {}).get("value", "")
            country = row.get("birthCountryLabel", {}).get("value", "")
            if city.startswith("Q") and city[1:].isdigit():
                city = ""
            if country.startswith("Q") and country[1:].isdigit():
                country = ""
            mapping[qid] = (city, country)
    except Exception as e:
        print(f"   ⚠ Wikidata SPARQL: {e}")
    return mapping


# ── Wikipedia page fallback for birthplace ───────────────────────────────────

def _parse_wikipedia_birthplace(soup: BeautifulSoup) -> tuple:
    infobox = soup.find('table', class_=lambda c: c and 'infobox' in c)
    if infobox:
        for row in infobox.find_all('tr'):
            th = row.find('th')
            if not th:
                continue
            th_text = th.get_text().lower()
            if 'place of birth' in th_text or th_text.strip() == 'birthplace':
                td = row.find('td')
                if td:
                    text = td.get_text(separator=', ', strip=True)
                    text = re.sub(r'\s+', ' ', text).strip()
                    parts = [p.strip() for p in text.split(',') if p.strip()]
                    if len(parts) >= 2:
                        return (parts[0], parts[-1])
                    if parts:
                        return ('', parts[0])
            if 'born' in th_text or 'full name' in th_text:
                continue

    bp = soup.find('span', class_='birthplace') or soup.find('span', class_='place-of-birth')
    if bp:
        text = bp.get_text(', ', strip=True)
        parts = [p.strip() for p in text.split(',') if p.strip()]
        if len(parts) >= 2:
            return (parts[0], parts[-1])
        if parts:
            return ('', parts[0])

    return ('', '')


def enrich_birthplaces(coaches: list) -> None:
    need = [c for c in coaches if not c['birth_city'] and c['wiki_title']]
    if not need:
        return

    titles = list({c['wiki_title'] for c in need})
    print(f"\n🌐 Wikidata enrichment for {len(titles)} coaches ...")

    title_to_qid = get_wikidata_ids(titles)
    print(f"   ✓ {len(title_to_qid)} QIDs found")

    qids = list(set(title_to_qid.values()))
    qid_to_birth = get_birthplaces(qids)
    print(f"   ✓ {len(qid_to_birth)} birthplaces found")

    enriched = 0
    for c in coaches:
        if c['birth_city'] or not c['wiki_title']:
            continue
        qid = title_to_qid.get(c['wiki_title'])
        if not qid:
            continue
        city, country = qid_to_birth.get(qid, ('', ''))
        if city or country:
            c['birth_city'] = city
            c['birth_country'] = country
            enriched += 1
    print(f"   ✓ {enriched} coaches enriched via Wikidata")

    # Fallback: individual Wikipedia pages
    still_missing = [c for c in coaches if not c['birth_city'] and c['wiki_title']]
    if still_missing:
        print(f"\n📖 Wikipedia page fallback for {len(still_missing)} coaches ...")
        fb = 0
        for i, c in enumerate(still_missing, 1):
            slug = c['wiki_title'].replace(' ', '_')
            try:
                r = requests.get(
                    f"https://en.wikipedia.org/wiki/{slug}",
                    headers=HEADERS,
                    timeout=15,
                )
                if r.status_code == 200:
                    city, country = _parse_wikipedia_birthplace(BeautifulSoup(r.text, 'lxml'))
                    if city or country:
                        c['birth_city'] = city
                        c['birth_country'] = country
                        fb += 1
            except Exception:
                pass
            print(f"\r   → {i}/{len(still_missing)} checked, {fb} enriched", end="", flush=True)
            time.sleep(0.5)
        print(f"\n   ✓ {fb} coaches enriched via Wikipedia pages")

    # Resolve "United Kingdom" → specific home nation
    resolved = 0
    unresolved = []
    for c in coaches:
        if c['birth_country'] == 'United Kingdom':
            nation = reg.resolve_uk_home_nation(c['birth_city'])
            if nation:
                c['birth_country'] = nation
                resolved += 1
            else:
                unresolved.append(c)
    if resolved:
        print(f"\n🏴 Resolved {resolved} 'United Kingdom' → home nation")
    if unresolved:
        print(f"\n❌ Could not resolve: "
              f"{', '.join(c['birth_city'] for c in unresolved)}", file=sys.stderr)
        print("   Add these cities to UK_CITY_TO_NATION in pipeline/country_registry.py",
              file=sys.stderr)
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  MONDIAL 2026 — Coaches by birthplace")
    print("=" * 60)

    # 1. Fetch Wikipedia
    print(f"\n📥 Fetching: {WIKI_URL}")
    r = requests.get(WIKI_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    print(f"   ✓ {len(r.text):,} chars")

    # 2. Parse coaches
    print("\n🔍 Extracting coaches ...")
    coaches = parse_coaches(soup)
    print(f"\n   → {len(coaches)} coaches extracted")

    if len(coaches) < 40:
        print(f"\n⚠  Only {len(coaches)} coaches found (expected 48). "
              "The page structure may have changed.")

    # 3. Enrich birthplaces
    enrich_birthplaces(coaches)

    # 3b. Surname overrides
    apply_coach_surname_overrides(coaches)

    # 4. Summary
    n_with = sum(1 for c in coaches if c['birth_country'])
    foreign = [c for c in coaches if c['birth_country'] and c['birth_country'] != c['nation']]
    print(f"\n✅ {len(coaches)} coaches")
    print(f"   • With birthplace:  {n_with}")
    print(f"   • Foreign coaches:  {len(foreign)}")

    # 5. Export CSV
    df = pd.DataFrame(coaches)
    df = df[['nation', 'coach', 'surname', 'nationality', 'birth_city', 'birth_country', 'wiki_title']]
    df.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')
    print(f"\n💾 {OUT_CSV}  ({len(df)} rows)")

    # 6. Display foreign coaches
    if foreign:
        print(f"\n{'='*60}")
        print("  Foreign coaches (born outside the team's country)")
        print("="*60)
        for c in sorted(foreign, key=lambda x: x['nation']):
            print(f"  {c['nation']:<25} ← {c['coach']:<25} (born: {c['birth_city']}, {c['birth_country']})")

    # Display all coaches
    print(f"\n{'='*60}")
    print("  All coaches")
    print("="*60)
    print(f"  {'Nation':<25} {'Coach':<30} {'Nationality':<20} {'Birth city':<20} {'Birth country'}")
    print(f"  {'-'*25} {'-'*30} {'-'*20} {'-'*20} {'-'*20}")
    for c in sorted(coaches, key=lambda x: x['nation']):
        nat = c['nationality'] if c['nationality'] != c['nation'] else ''
        print(f"  {c['nation']:<25} {c['coach']:<30} {nat:<20} {c['birth_city']:<20} {c['birth_country']}")


if __name__ == "__main__":
    main()
