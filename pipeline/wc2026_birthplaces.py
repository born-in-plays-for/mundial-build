#!/usr/bin/env python3
"""
Mondial 2026 — Extraction des joueurs par lieu de naissance
============================================================
Sources :
  1. Wikipedia "2026 FIFA World Cup squads"  (liste des joueurs)
  2. Wikidata SPARQL API  (lieu de naissance via P19)
  3. Wikipedia (pages joueurs individuelles, fallback infobox pour les données Wikidata manquantes)

Prérequis :
    pip install requests beautifulsoup4 pandas lxml

Usage :
    python wc2026_birthplaces.py

Sortie :
    wc2026_players.csv            — tous les joueurs avec lieu de naissance
"""

import io
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
import pandas as pd
from bs4 import BeautifulSoup

import country_registry as reg

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

        idx_name  = find_col('player', 'name')
        idx_pos   = find_col('pos')
        idx_dob   = find_col('date of birth', 'born')
        idx_place = find_col('place of birth', 'birthplace', 'birth place', 'birth city')
        idx_caps  = find_col('caps', 'cap')
        idx_club  = find_col('club')

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
                if link and link['href'].startswith('/wiki/'):
                    wiki_title = unquote(link['href'][6:]).replace('_', ' ')

            place_raw = get(idx_place) if idx_place is not None else ''
            city, country = extract_birth_info(place_raw) if place_raw else ('', '')

            players.append({
                'nation':        current_nation,
                'nation_code':   current_code or '',
                'pos':           get(idx_pos),
                'player':        name,
                'wiki_title':    wiki_title,
                'birth_date':    get(idx_dob),
                'birth_city':    city,
                'birth_country': country,
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
                'pos':           str(row.get(pos_col, '')) if pos_col else '',
                'player':        clean(name),
                'wiki_title':    '',
                'birth_date':    str(row.get(dob_col, '')) if dob_col else '',
                'birth_city':    city,
                'birth_country': country,
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


def get_birthplaces(qids: list) -> dict:
    """Interroge Wikidata SPARQL pour P19 (lieu de naissance) par lots de 200."""
    mapping = {}
    batch_size = 200
    total = len(qids)
    for i in range(0, total, batch_size):
        batch = qids[i:i+batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
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
                # Ignorer les labels qui sont juste l'ID Wikidata (ex: "Q12345")
                if city.startswith("Q") and city[1:].isdigit():
                    city = ""
                if country.startswith("Q") and country[1:].isdigit():
                    country = ""
                mapping[qid] = (city, country)
        except Exception as e:
            print(f"   ⚠ Wikidata SPARQL (lot {i//batch_size + 1}) : {e}")
        done = min(i + batch_size, total)
        print(f"\r   → Lieux récupérés : {done}/{total}", end="", flush=True)
        time.sleep(1.0)
    print()
    return mapping


def enrich_with_wikidata(players: list) -> None:
    """Complète birth_city / birth_country via Wikidata pour les joueurs sans lieu."""
    need_enrichment = [p for p in players
                       if p['wiki_title'] and (not p['birth_city'] or p['birth_city'] == p['birth_country'])]
    if not need_enrichment:
        return

    titles = list({p['wiki_title'] for p in need_enrichment})
    print(f"\n🌐 Enrichissement Wikidata pour {len(need_enrichment)} joueurs "
          f"({len(titles)} pages uniques) ...")

    title_to_qid = get_wikidata_ids(titles)
    print(f"   ✓ {len(title_to_qid)} QIDs trouvés")

    qids = list(set(title_to_qid.values()))
    qid_to_birth = get_birthplaces(qids)
    print(f"   ✓ {len(qid_to_birth)} lieux de naissance récupérés")

    enriched = 0
    for p in players:
        if not p['wiki_title']:
            continue
        if p['birth_city'] and p['birth_city'] != p['birth_country']:
            continue
        qid = title_to_qid.get(p['wiki_title'])
        if not qid:
            continue
        city, country = qid_to_birth.get(qid, ('', ''))
        if city and city != country:
            p['birth_city']    = city
            p['birth_country'] = country
            enriched += 1
    print(f"   ✓ {enriched} joueurs enrichis")


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

    # 3. Enrichir via Wikidata, puis FBref pour les joueurs restants
    enrich_with_wikidata(players)
    enrich_with_wikipedia_pages(players)

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
