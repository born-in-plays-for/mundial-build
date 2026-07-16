#!/usr/bin/env python3
"""
Mondial 2026 — Extraction des joueurs par lieu de naissance
============================================================
Sources :
  1. Wikipedia "2026 FIFA World Cup squads"  (liste des joueurs)
  2. Wikidata SPARQL API  (lieu de naissance via P19)
  3. Wikipedia (pages joueurs individuelles, fallback infobox pour les données Wikidata manquantes)

Prérequis :
    pip install requests beautifulsoup4 pandas lxml nameparser

Usage :
    python wc2026_birthplaces.py

Sortie :
    wc2026_players.csv            — tous les joueurs avec lieu de naissance
"""

import io
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup
from nameparser import HumanName
from nameparser.config import CONSTANTS as NAME_CONSTANTS

import country_registry as reg
from geocode_birthplaces import strip_admin_qualifier

# "Abu"/"Al"/"El" are recognized as surname prefixes by nameparser out of the
# box; "Ben"/"Bani" (Arabic patronymic prefixes, e.g. "Anis Ben Slimane" ->
# surname "Ben Slimane") aren't, so they're added here. Safe for ordinary
# two-token Western names too (e.g. "Ben Foster" still splits First=Ben,
# Last=Foster — prefix-merging only kicks in for a *middle* token).
NAME_CONSTANTS.prefixes.add('ben')
NAME_CONSTANTS.prefixes.add('bani')

# ── Configuration ──────────────────────────────────────────────────────────────

WIKI_URL      = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"
WIKI_API      = "https://en.wikipedia.org/w/api.php"
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
    "User-Agent": "WC2026BirthplaceBot/1.0 (research; christophe.t60@gmail.com)",
}


OUT_PLAYERS = Path(__file__).parent / "wc2026_players.csv"

# Hand-verified birthplaces for players Wikidata/Wikipedia don't have (or get
# wrong), keyed by nation then exact player name as they appear on the WC2026
# squads page. Applied as a final patch after all automated enrichment so it
# survives a from-scratch rerun of this script.
OVERRIDES_PATH = Path(__file__).parent / "birthplace_overrides.json"

# Hand-corrected sortable surnames for players nameparser's automatic
# "Firstname Surname" split gets wrong (transliteration quirks, a nickname
# that diverges from the display name), keyed by nation then exact player
# name. Unlike OVERRIDES_PATH, nameparser never returns a blank, so there's
# no "only fill blanks" rule here — a present entry always wins.
SURNAME_OVERRIDES_PATH = Path(__file__).parent / "surname_overrides.json"

# Exact nation names as they appear on the Wikipedia squads page. Only these
# 48 nations qualified for WC 2026 — any other heading is rejected. Sourced
# from pipeline/country_aliases.json (single source of truth, shared with
# wc2026_coaches.py) instead of a locally hardcoded, easy-to-drift copy.
QUALIFIED_NATIONS = reg.wc2026_nations()

# ── Helpers ────────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    text = re.sub(r'\[[\w\s]*\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    return text.strip()


def wiki_title_from_href(href: str):
    """-> EN Wikipedia title from an <a> tag's href, or None if it isn't a
    plain article link. Handles both root-relative ("/wiki/Title") and
    protocol-relative ("//en.wikipedia.org/wiki/Title") hrefs — Wikipedia
    renders either depending on context, and a naive
    href.startswith('/wiki/') silently matches nothing when the
    protocol-relative form is what's actually served (this broke wiki_title
    extraction pipeline-wide the day Wikipedia started doing that live —
    urlparse(href).path normalizes both forms the same way)."""
    path = urlparse(href).path
    return unquote(path[6:]).replace('_', ' ') if path.startswith('/wiki/') else None


def extract_birth_info(raw: str) -> tuple:
    raw = clean(raw)
    if not raw or raw.lower() in ('', 'nan', '—', '-'):
        return ('', '')
    parts = [p.strip() for p in raw.split(',')]
    if len(parts) >= 2:
        return (parts[0], parts[-1])
    return ('', parts[0])


# ── Parsing Wikipedia ──────────────────────────────────────────────────────────

def fetch_soup(url: str) -> BeautifulSoup:
    print(f"📥 Téléchargement : {url}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    print(f"   ✓ {len(r.text):,} caractères")
    return BeautifulSoup(r.text, "lxml")


def parse_wikipedia(soup: BeautifulSoup) -> list:
    """
    Extrait les joueurs depuis les wikitables de la page Wikipedia.
    N'exige pas de colonne "Place of birth" (enrichissement via Wikidata).
    Extrait le titre Wikipedia de chaque joueur pour résolution Wikidata.
    """
    players = []
    current_nation = None
    current_code   = None

    content = soup.find('div', id='mw-content-text') or soup.find('div', id='bodyContent') or soup
    elements = content.find_all(['h2', 'h3', 'table'])

    for el in elements:

        # ── Mise à jour du pays courant ──
        if el.name in ('h2', 'h3'):
            txt = re.sub(r'\[.*?\]', '', el.get_text()).strip()
            skip = {'Contents', 'References', 'External links', 'See also',
                    'Notes', 'Navigation menu', 'Groups', 'Squads'}
            if txt in skip or len(txt) < 2:
                continue
            if re.match(r'^Group [A-Z]$', txt):
                continue
            m = re.search(r'\(([A-Z]{3})\)', txt)
            if m:
                current_code   = m.group(1)
                current_nation = re.sub(r'\s*\([A-Z]{3}\)', '', txt).strip()
            else:
                current_nation = txt
                current_code   = None
            if current_nation not in QUALIFIED_NATIONS:
                current_nation = None
                current_code   = None
            continue

        # ── Tables de joueurs ──
        if el.name != 'table' or current_nation is None:
            continue
        if 'wikitable' not in ' '.join(el.get('class', [])):
            continue

        rows = el.find_all('tr')
        if not rows:
            continue

        # Détecter le header (supporte les tables avec header sur 2 lignes)
        kw_set = {'pos', 'player', 'name', 'birth', 'place', 'club', 'cap', 'goal'}
        col_labels = []
        header_row_idx = 0
        for ri, header_row in enumerate(rows[:3]):
            cells = header_row.find_all(['th', 'td'])
            labels = [re.sub(r'\s+', ' ', c.get_text()).strip().lower() for c in cells]
            score = sum(1 for lbl in labels if any(kw in lbl for kw in kw_set))
            if score > sum(1 for lbl in col_labels if any(kw in lbl for kw in kw_set)):
                col_labels = labels
                header_row_idx = ri
        if not col_labels:
            continue

        def find_col(*keywords):
            for kw in keywords:
                for i, lbl in enumerate(col_labels):
                    if kw in lbl:
                        return i
            return None

        idx_name   = find_col('player', 'name')
        idx_pos    = find_col('pos')
        idx_dob    = find_col('date of birth', 'born')
        idx_place  = find_col('place of birth', 'birthplace', 'birth place', 'birth city')
        idx_caps   = find_col('caps', 'cap')
        idx_club   = find_col('club')
        idx_number = find_col('no.', 'no')

        # Exiger au moins 2 colonnes de support pour distinguer une vraie table joueurs
        # des tables de stats comme "Player representation by league system"
        support = sum(1 for x in [idx_pos, idx_dob, idx_caps, idx_club] if x is not None)
        if idx_name is None or support < 2:
            continue

        for row in rows[header_row_idx + 1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 3:
                continue

            def get(i):
                if i is not None and i < len(cells):
                    return clean(cells[i].get_text(separator=' '))
                return ''

            name = get(idx_name)
            if not name or name.lower() == 'nan':
                continue

            # Extraire le titre Wikipedia du lien joueur (pour Wikidata)
            wiki_title = ''
            if idx_name is not None and idx_name < len(cells):
                link = cells[idx_name].find('a', href=True)
                if link:
                    wiki_title = wiki_title_from_href(link['href']) or ''

            place_raw = get(idx_place) if idx_place is not None else ''
            city, country = extract_birth_info(place_raw) if place_raw else ('', '')

            players.append({
                'nation':        current_nation,
                'nation_code':   current_code or '',
                'number':        get(idx_number),
                'pos':           get(idx_pos),
                'player':        name,
                'wiki_title':    wiki_title,
                'birth_date':    get(idx_dob),
                'birth_city':    city,
                'birth_country': country,
                'birth_lat':     '',
                'birth_lon':     '',
                'birth_population': '',
                'caps':          get(idx_caps),
                'club':          get(idx_club),
            })

    return players


def parse_wikipedia_pandas(soup: BeautifulSoup) -> list:
    """Fallback pandas si le parser principal extrait trop peu de joueurs."""
    print("   ↩ Fallback pandas read_html ...")
    players = []
    try:
        tables = pd.read_html(io.StringIO(str(soup)))
    except Exception as e:
        print(f"   ✗ read_html échoué : {e}")
        return []

    for df in tables:
        cols_str = {c: str(c).lower() for c in df.columns}
        player_col = next((c for c in df.columns if 'player' in cols_str[c] or 'name' in cols_str[c]), None)
        if player_col is None:
            continue

        place_col  = next((c for c in df.columns if 'place' in cols_str[c] and 'birth' in cols_str[c]), None)
        pos_col    = next((c for c in df.columns if 'pos' in cols_str[c]), None)
        dob_col    = next((c for c in df.columns if 'birth' in cols_str[c] and 'date' in cols_str[c]), None)
        club_col   = next((c for c in df.columns if 'club' in cols_str[c]), None)
        caps_col   = next((c for c in df.columns if 'cap' in cols_str[c]), None)
        number_col = next((c for c in df.columns if str(c).lower().strip() in ('no.', 'no')), None)

        for _, row in df.iterrows():
            name = str(row.get(player_col, '')).strip()
            if not name or name.lower() == 'nan':
                continue
            city, country = ('', '')
            if place_col:
                city, country = extract_birth_info(str(row.get(place_col, '')))
            players.append({
                'nation':        'Unknown',
                'nation_code':   '',
                'number':        str(row.get(number_col, '')) if number_col else '',
                'pos':           str(row.get(pos_col, '')) if pos_col else '',
                'player':        clean(name),
                'wiki_title':    '',
                'birth_date':    str(row.get(dob_col, '')) if dob_col else '',
                'birth_city':    city,
                'birth_country': country,
                'birth_lat':     '',
                'birth_lon':     '',
                'birth_population': '',
                'caps':          str(row.get(caps_col, '')) if caps_col else '',
                'club':          str(row.get(club_col, '')) if club_col else '',
            })
    return players


# ── Enrichissement Wikidata ────────────────────────────────────────────────────

def _get_with_backoff(url, params, headers, timeout=15, max_retries=8):
    """GET with exponential backoff; honours Retry-After on 429."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", delay))
                print(f"\n   ⏳ 429 — attente {wait:.0f}s avant relance ...", flush=True)
                time.sleep(wait)
                delay = max(delay * 2, wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return {}


def get_wikidata_ids(titles: list) -> dict:
    """Convertit des titres Wikipedia en QIDs Wikidata par lots de 50."""
    mapping = {}
    total = len(titles)
    for i in range(0, total, 50):
        batch = titles[i:i+50]
        params = {
            "action": "query",
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "titles": "|".join(batch),
            "format": "json",
        }
        try:
            data = _get_with_backoff(WIKI_API, params, HEADERS)
            for page in data.get("query", {}).get("pages", {}).values():
                qid = page.get("pageprops", {}).get("wikibase_item", "")
                if qid:
                    mapping[page.get("title", "")] = qid
        except Exception as e:
            print(f"   ⚠ Wikipedia API (lot {i//50 + 1}) : {e}")
        print(f"\r   → QIDs résolus : {len(mapping)}/{total}", end="", flush=True)
        time.sleep(2.0)
    print()
    return mapping


_WKT_POINT = re.compile(r'^Point\(([-\d.]+)\s+([-\d.]+)\)$')


def _parse_wkt_point(wkt: str):
    """-> (lat, lon) floats from a Wikidata P625 WKT literal like
    "Point(2.443 48.860)", or (None, None) if absent/unparseable. WKT order
    is LONGITUDE then LATITUDE — the reverse of how this pipeline names its
    own lat/lon pair everywhere else, easy to invert by mistake."""
    m = _WKT_POINT.match(wkt) if wkt else None
    if not m:
        return (None, None)
    lon, lat = float(m.group(1)), float(m.group(2))
    return (lat, lon)


def get_birthplaces(qids: list) -> dict:
    """Interroge Wikidata SPARQL pour P19 (lieu de naissance) par lots de 200.
    -> {qid: (city_label, country_label, lat, lon, population)}. lat/lon
    (from the birth city entity's own P625 coordinate, when it has one) are
    the entity's real coordinates — disambiguated by construction, since
    P19 points at one specific place entity, never a bare name (see this
    module's enrich_birth_coordinates, which is what actually uses them; a
    P19 claim without a P625 coordinate still gets its
    city_label/country_label as before, just no lat/lon). population is the
    SAME entity's P1082 statement (a plain numeric-string quantity, not
    coerced further), when it has one — same "coverage gap, not failure"
    treatment as lat/lon: most small places don't have a P1082 statement
    either. A place with more than one P1082 statement (a population
    history across census years) picks whichever one Wikidata's query
    engine happens to bind first — best-effort, not scientifically exact,
    same tolerance already accepted for OSM's own population extratag
    elsewhere in this pipeline; not worth a GROUP BY/SAMPLE to pin down
    "most recent" for a field that's already documented as approximate."""
    mapping = {}
    batch_size = 200
    total = len(qids)
    for i in range(0, total, batch_size):
        batch = qids[i:i+batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
SELECT ?item ?birthCityLabel ?birthCountryLabel ?coord ?population WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{
    ?item wdt:P19 ?birthCity.
    OPTIONAL {{ ?birthCity wdt:P17 ?birthCountry. }}
    OPTIONAL {{ ?birthCity wdt:P625 ?coord. }}
    OPTIONAL {{ ?birthCity wdt:P1082 ?population. }}
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
                # Ignorer les labels qui sont juste l'ID Wikidata (ex: "Q12345")
                if city.startswith("Q") and city[1:].isdigit():
                    city = ""
                if country.startswith("Q") and country[1:].isdigit():
                    country = ""
                lat, lon = _parse_wkt_point(row.get("coord", {}).get("value"))
                population = row.get("population", {}).get("value")
                mapping[qid] = (city, country, lat, lon, population)
        except Exception as e:
            print(f"   ⚠ Wikidata SPARQL (lot {i//batch_size + 1}) : {e}")
        done = min(i + batch_size, total)
        print(f"\r   → Lieux récupérés : {done}/{total}", end="", flush=True)
        time.sleep(1.0)
    print()
    return mapping


def enrich_with_wikidata(players: list, title_to_qid: dict, qid_to_birth: dict) -> None:
    """Complète birth_city / birth_country via Wikidata pour les joueurs sans
    lieu, à partir des mappings déjà résolus pour TOUS les joueurs (voir
    main() — un seul aller-retour SPARQL, réutilisé aussi par
    enrich_birth_coordinates)."""
    enriched = 0
    for p in players:
        if not p['wiki_title']:
            continue
        if p['birth_city'] and p['birth_city'] != p['birth_country']:
            continue
        qid = title_to_qid.get(p['wiki_title'])
        if not qid:
            continue
        city, country, _lat, _lon, _pop = qid_to_birth.get(qid, ('', '', None, None, None))
        # Wikidata's P19 (place of birth) sometimes points directly at a
        # country entity rather than a city (no specific city recorded) —
        # city == country in that case. Still record the country: it's true
        # and useful (e.g. lets a native-born player show up in map_data.json's
        # natives), just don't fabricate a "city" that's really the country
        # name again.
        if country:
            p['birth_country'] = country
            if city != country:
                p['birth_city'] = city
            enriched += 1
    print(f"   ✓ {enriched} joueurs enrichis via Wikidata")


def enrich_birth_coordinates(persons: list, title_to_qid: dict, qid_to_birth: dict,
                              name_key: str = 'player') -> None:
    """Attache birth_lat/birth_lon (coordonnées P625 de l'entité-lieu P19
    elle-même — voir get_birthplaces) et birth_population (P1082 de cette
    MÊME entité, quand elle en a une — remplace le trou de couverture
    qu'un basculement Nominatim -> Wikidata ouvrirait sinon, puisque
    l'extratag OSM de population n'existe que côté Nominatim) à chaque
    personne (joueur ou coach — name_key donne le champ à citer dans les
    messages, 'player' ou 'coach'), en visant une coordonnée (et, quand
    disponible, une population) IDENTIQUE pour tout le monde affiché sous
    le même nom de ville — pas seulement individuellement correcte pour
    chaque personne.

    Deux passes :

    1. La revendication P19 d'une personne n'est retenue directement que
       si le label Wikidata du lieu correspond à son nom de ville
       CANONIQUE (birth_city débarrassé d'un éventuel qualificatif
       administratif via strip_admin_qualifier — "Lyon" aussi bien pour
       "Lyon" que pour "3rd arrondissement of Lyon"). Comparer au
       birth_city BRUT à la place (comme une version antérieure de cette
       fonction le faisait) accepte telle quelle la revendication P19 d'une
       personne qui pointe vers l'entité infra-urbaine PRÉCISE (un lieu
       réel, valide, souvent doté de son propre QID Wikidata plus précis
       que la ville) — individuellement correct, mais deux personnes
       affichées toutes deux "Lyon, France" (l'une via le repli
       actualCityName) se retrouvent alors avec des points de carte
       visiblement différents pour ce qui est, à l'affichage, le même
       lieu. Chaque correspondance canonique directe alimente
       canonical_coords, un dict {(nom canonique, birth_country): (lat,
       lon)}.
    2. Toute personne dont la revendication ne correspond PAS au nom
       canonique — parce qu'elle pointe justement vers cette entité
       infra-urbaine plus précise, pas une vraie divergence de donnée —
       ADOPTE plutôt la coordonnée qu'une AUTRE personne au nom de ville
       canonique exact a déjà résolue dans ce même run, si elle existe :
       ça garantit que tout le monde sous un même nom canonique converge
       vers un seul point. Reste sans coordonnée seulement si aucun
       homonyme canonique n'existe dans l'effectif courant — retombe alors
       sur geocode_birthplaces.py (Nominatim), lui-même appelé avec ce même
       nom canonique (voir collect_city_country_pairs), donc au moins tous
       les résidus de ce type convergent aussi entre eux.

    Une vraie divergence (le label Wikidata ne correspond ni au birth_city
    brut ni au canonique — ex: P19 seulement à granularité pays) est
    signalée et laissée sans coordonnée plutôt que devinée, même logique
    qu'avant."""
    canonical_coords = {}   # (canonical.lower(), birth_country) -> (lat, lon, population)
    needs_canonical = []    # personnes dont la revendication pointe vers une entité infra-urbaine précise
    attached = 0
    for p in persons:
        if not p['wiki_title'] or not p['birth_city']:
            continue
        qid = title_to_qid.get(p['wiki_title'])
        if not qid:
            continue
        city, _country, lat, lon, population = qid_to_birth.get(qid, ('', '', None, None, None))
        if lat is None or lon is None or not city:
            continue
        canonical = strip_admin_qualifier(p['birth_city']) or p['birth_city']
        if city.strip().lower() == canonical.strip().lower():
            p['birth_lat'], p['birth_lon'] = lat, lon
            p['birth_population'] = population or ''
            canonical_coords.setdefault((canonical.strip().lower(), p['birth_country']), (lat, lon, population))
            attached += 1
        elif strip_admin_qualifier(p['birth_city']) is not None:
            needs_canonical.append(p)
        else:
            print(f"   ⚠ {p[name_key]} ({p['nation']}) : birth_city={p['birth_city']!r} "
                  f"mais Wikidata P19 pointe vers {city!r} — coordonnées ignorées, "
                  f"vérifier à la main", file=sys.stderr)

    from_sibling, still_missing = 0, []
    for p in needs_canonical:
        canonical = strip_admin_qualifier(p['birth_city'])
        key = (canonical.strip().lower(), p['birth_country'])
        if key in canonical_coords:
            lat, lon, population = canonical_coords[key]
            p['birth_lat'], p['birth_lon'] = lat, lon
            p['birth_population'] = population or ''
            from_sibling += 1
        else:
            still_missing.append(p)
    if still_missing:
        details = ', '.join(f"{p[name_key]} ({strip_admin_qualifier(p['birth_city'])})"
                             for p in still_missing)
        print(f"   ℹ {len(still_missing)} personne(s) à granularité infra-urbaine sans "
              f"homonyme canonique dans l'effectif actuel — retombent sur Nominatim: {details}")

    print(f"   ✓ {attached} avec coordonnées Wikidata P19 directes, "
          f"{from_sibling} via un homonyme canonique dans l'effectif")


# ── Enrichissement Wikipedia (pages individuelles) ────────────────────────────

def _parse_wikipedia_birthplace(soup: BeautifulSoup) -> tuple:
    """Extrait le lieu de naissance depuis une page joueur Wikipedia."""
    # Infoboxes football modernes ont une ligne "Place of birth" séparée
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
                    # Citation footnotes (<sup class="reference">...[1]</sup>)
                    # sit right after the country name; get_text's separator
                    # inserts a comma before each of the footnote's own child
                    # spans too ("[", "1", "]"), so the trailing "]" ends up
                    # as parts[-1] instead of the real country. Strip them
                    # before extracting text.
                    for sup in td.find_all('sup'):
                        sup.decompose()
                    text = td.get_text(separator=', ', strip=True)
                    text = re.sub(r'\s+', ' ', text).strip()
                    parts = [p.strip() for p in text.split(',') if p.strip()]
                    if len(parts) >= 2:
                        return (parts[0], parts[-1])
                    if parts:
                        return ('', parts[0])

    # Fallback : anciens formats avec <span class="birthplace">
    bp = soup.find('span', class_='birthplace') or soup.find('span', class_='place-of-birth')
    if bp:
        for sup in bp.find_all('sup'):
            sup.decompose()
        text = bp.get_text(', ', strip=True)
        parts = [p.strip() for p in text.split(',') if p.strip()]
        if len(parts) >= 2:
            return (parts[0], parts[-1])
        if parts:
            return ('', parts[0])

    return ('', '')


def enrich_with_wikipedia_pages(players: list) -> None:
    """Enrichit via les pages Wikipedia individuelles des joueurs sans lieu de naissance."""
    missing = [p for p in players
               if p['wiki_title'] and (not p['birth_city'] or p['birth_city'] == p['birth_country'])]
    if not missing:
        return
    print(f"\n📖 Pages Wikipedia individuelles pour {len(missing)} joueurs ...")
    enriched = 0
    for i, p in enumerate(missing, 1):
        slug = p['wiki_title'].replace(' ', '_')
        try:
            r = requests.get(
                f"https://en.wikipedia.org/wiki/{slug}",
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code == 200:
                city, country = _parse_wikipedia_birthplace(BeautifulSoup(r.text, 'lxml'))
                if city or country:
                    p['birth_city']    = city
                    p['birth_country'] = country
                    enriched += 1
        except Exception:
            pass
        print(f"\r   → {i}/{len(missing)} traités, {enriched} enrichis", end="", flush=True)
        time.sleep(0.5)
    print()
    print(f"   ✓ {enriched} joueurs enrichis via Wikipedia")


# ── Overrides manuelles ────────────────────────────────────────────────────────

def apply_manual_overrides(players: list) -> None:
    """Applique pipeline/birthplace_overrides.json (lieux introuvables via
    Wikidata/Wikipedia, vérifiés à la main). Ne comble que les champs vides
    (birth_city / birth_country) — n'écrase jamais une valeur déjà trouvée
    par le scrape automatisé ; si les deux divergent, avertit sans modifier
    (ça veut dire que la source automatisée a rattrapé le retard, ou qu'il y
    a un vrai désaccord à trancher à la main)."""
    if not OVERRIDES_PATH.exists():
        return
    overrides = json.loads(OVERRIDES_PATH.read_text(encoding='utf-8'))

    by_nation = {}
    for p in players:
        by_nation.setdefault(p['nation'], {})[p['player']] = p

    applied = 0
    for nation, by_player in overrides.items():
        for player_name, fields in by_player.items():
            p = by_nation.get(nation, {}).get(player_name)
            if p is None:
                print(f"   ⚠ Override introuvable dans les données : {nation} / {player_name}")
                continue
            for field in ('birth_city', 'birth_country'):
                override_val = fields.get(field)
                if not override_val:
                    continue
                current_val = p.get(field, '')
                if not current_val:
                    p[field] = override_val
                    applied += 1
                elif current_val != override_val:
                    print(f"   ⚠ Divergence {field} pour {nation} / {player_name} : "
                          f"source auto = {current_val!r}, override = {override_val!r} — override ignoré")
    if applied:
        print(f"   ✓ {applied} champ(s) comblé(s) depuis {OVERRIDES_PATH.name}")


# ── Surname (sortable) ──────────────────────────────────────────────────────────

def compute_surname(full_name: str) -> str:
    """Best-effort sortable surname from a 'Firstname Surname' display name,
    via nameparser. Mononyms (Zizo, Neymar, ...) have no last name to
    extract — the full name is used as-is."""
    return HumanName(full_name).last or full_name


def apply_surname_overrides(players: list) -> None:
    """Applies pipeline/surname_overrides.json — hand-corrected surnames for
    cases nameparser gets wrong, keyed by nation then exact player name.
    A present entry always wins (see SURNAME_OVERRIDES_PATH docstring)."""
    if not SURNAME_OVERRIDES_PATH.exists():
        return
    overrides = json.loads(SURNAME_OVERRIDES_PATH.read_text(encoding='utf-8'))

    by_nation = {}
    for p in players:
        by_nation.setdefault(p['nation'], {})[p['player']] = p

    applied = 0
    for nation, by_player in overrides.items():
        for player_name, fields in by_player.items():
            p = by_nation.get(nation, {}).get(player_name)
            if p is None:
                print(f"   ⚠ Surname override introuvable dans les données : {nation} / {player_name}")
                continue
            if fields.get('surname'):
                p['surname'] = fields['surname']
                applied += 1
    if applied:
        print(f"   ✓ {applied} surname override(s) appliqué(s) depuis {SURNAME_OVERRIDES_PATH.name}")


# ── Classement ────────────────────────────────────────────────────────────────


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  MONDIAL 2026 — Joueurs par pays de naissance")
    print("=" * 60)

    # 1. Télécharger Wikipedia
    try:
        soup = fetch_soup(WIKI_URL)
    except Exception as e:
        print(f"\n❌ Impossible de charger la page : {e}")
        sys.exit(1)

    # 2. Parser les effectifs
    print("\n🔍 Parsing des tables Wikipedia ...")
    players = parse_wikipedia(soup)
    print(f"   → {len(players)} joueurs (parser principal)")

    if len(players) < 200:
        players = parse_wikipedia_pandas(soup)
        print(f"   → {len(players)} joueurs (après fallback)")

    if not players:
        print("\n❌ Aucun joueur extrait. La structure de la page a peut-être changé.")
        sys.exit(1)

    # 3. Résoudre les QIDs Wikidata pour TOUS les joueurs ayant un wiki_title
    #    (pas seulement ceux sans lieu de naissance — enrich_birth_coordinates
    #    ci-dessous en a besoin même pour un joueur dont le birth_city vient
    #    déjà de la table des effectifs Wikipedia), un seul aller-retour
    #    SPARQL réutilisé par les deux enrichissements ci-dessous.
    titled = [p for p in players if p['wiki_title']]
    print(f"\n🌐 Résolution Wikidata pour {len(titled)} joueurs "
          f"({len({p['wiki_title'] for p in titled})} pages uniques) ...")
    title_to_qid = get_wikidata_ids(list({p['wiki_title'] for p in titled}))
    print(f"   ✓ {len(title_to_qid)} QIDs trouvés")
    qid_to_birth = get_birthplaces(list(set(title_to_qid.values())))
    print(f"   ✓ {len(qid_to_birth)} lieux de naissance récupérés")

    # 3a. Compléter birth_city / birth_country manquants via Wikidata, puis
    #     Wikipedia (pages individuelles) pour les joueurs restants
    enrich_with_wikidata(players, title_to_qid, qid_to_birth)
    enrich_with_wikipedia_pages(players)

    # 3b. Overrides manuelles (lieux introuvables ou erronés côté Wikidata/Wikipedia)
    apply_manual_overrides(players)

    # 3c. Coordonnées P625 de l'entité-lieu P19 elle-même (voir
    #     enrich_birth_coordinates) — APRÈS que birth_city soit définitif,
    #     pour toutes les sources (table Wikipedia, Wikidata, infobox,
    #     overrides manuelles) ; évite le bug d'homonymie (une même
    #     birth_city TEXTE peut désigner des lieux réels différents — voir
    #     le cas Montreuil documenté dans pipeline/README.md).
    enrich_birth_coordinates(players, title_to_qid, qid_to_birth)

    # 3d. Nom de famille triable (dérivé de 'player', pas de FIFA — voir surname_overrides.json)
    for p in players:
        p['surname'] = compute_surname(p['player'])
    apply_surname_overrides(players)

    # 4. DataFrame
    df = pd.DataFrame(players)
    df = df.drop_duplicates(subset=['player', 'nation']).reset_index(drop=True)
    for col in ('player', 'birth_country', 'birth_city'):
        df[col] = df[col].str.strip()

    n_with_birth = (df['birth_country'].str.strip() != '').sum()
    print(f"\n✅ {len(df)} joueurs uniques")
    print(f"   • Avec lieu de naissance : {n_with_birth}")
    print(f"   • Sans lieu de naissance : {len(df) - n_with_birth}")
    print(f"   • Sélections             : {df['nation'].nunique()}")

    # 5. Aperçu
    print("\n--- Aperçu (10 premiers) ---")
    print(df[['nation', 'player', 'pos', 'birth_city', 'birth_country']].head(10).to_string(index=False))

    # 6. Export joueurs (sans la colonne wiki_title interne)
    df.drop(columns=['wiki_title'], errors='ignore').to_csv(
        OUT_PLAYERS, index=False, encoding='utf-8-sig'
    )
    print(f"\n💾 {OUT_PLAYERS}  ({len(df)} lignes)")

    # 7. Focus : joueurs nés en France (toutes sélections)
    print("\n" + "=" * 60)
    print("  Focus : joueurs nés en France (toutes sélections)")
    print("=" * 60)
    born_france = df[df['birth_country'].str.lower().str.contains('france', na=False)]
    for _, r in born_france.sort_values('nation').iterrows():
        flag = "🔵⚪🔴" if r['nation'] == 'France' else "🌍"
        print(f"  {flag} {r['player']:<30} → {r['nation']} ({r['nation_code']})")


if __name__ == "__main__":
    main()
