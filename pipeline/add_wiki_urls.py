"""
Enriches wc2026_map_data.json with Wikipedia identity, and writes one
per-language title file per language so a client only ever has to fetch the
single language it actually needs (not all 5, as the old wiki_langs blob
required).

Step 1 — fetch the WC2026 squads page, extract player name → EN wiki title.
Step 2 — batch-query the Wikipedia API (prop=langlinks) for FR/DE/IT/ES titles.
Step 3 — write data/wiki_<lang>.json for each of en/fr/de/it/es:
            {"urlTemplate": "https://<lang>.wikipedia.org/wiki/{title}",
             "titles": {<EN title>: <url-ready title fragment for <lang>>}}
          keyed by the EN title, since that's the one identity every player
          object (in map_data.json and player_wiki.json) already carries —
          the client fetches one file, looks up the EN title, and does a
          plain string substitution into urlTemplate. No URL-building logic
          needed client-side.
Step 4 — set player["wikiTitle"] = <EN title> on every player object
         (replaces the old wiki/wiki_langs full-URL fields).
"""
import json, re, time, requests
from pathlib import Path
from urllib.parse import unquote, quote
from bs4 import BeautifulSoup

import country_registry as reg

ROOT      = Path(__file__).parent.parent / "data"
JSON_PATH = ROOT / "map_data.json"

WIKI_URL  = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"
WIKI_API  = "https://en.wikipedia.org/w/api.php"
HEADERS   = {"User-Agent": "mundial-build/1.0 (github.com/born-in-plays-for)"}
LANGS     = ["fr", "de", "it", "es"]
ALL_LANGS = ["en"] + LANGS
BATCH     = 50  # max titles per API call


def url_ready_title(title):
    """MediaWiki title -> the exact path fragment used in a wiki URL (same
    escaping the old wiki_url() helper produced, e.g. spaces->underscores,
    parens/commas left unescaped to match Wikipedia's own URLs)."""
    return quote(title.replace(' ', '_'), safe=":@!$&'()*+,;=/")


# ── Step 1: squad page → (nation, name) → EN title ───────────────────────────
# Keyed by (nation, name), NOT by name alone: two different players can share
# a display name (Argentina's and Uruguay's Emiliano Martínez both did at
# WC2026), and a flat name key silently gives one of them the other's
# article. Each squad table's country comes from its preceding heading; a
# heading that doesn't resolve to a country is one of the statistics tables
# at the bottom of the page, not a squad — skipped.
print("Step 1 — fetching Wikipedia squad page…")
r = requests.get(WIKI_URL, headers=HEADERS, timeout=30)
r.raise_for_status()
soup = BeautifulSoup(r.text, "lxml")

name_to_title = {}   # (nation display name, linked name) -> EN title
squad_tables = 0
for table in soup.find_all("table", class_=re.compile(r"wikitable")):
    heading = table.find_previous(["h2", "h3"])
    try:
        nation = reg.display_name(reg.resolve_iso2(heading.get_text(strip=True))) \
                 if heading else None
    except reg.UnknownCountryError:
        continue
    if nation is None:
        continue
    squad_tables += 1
    for a in table.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/wiki/") and ":" not in href:
            title = unquote(href[6:]).replace("_", " ")
            name  = a.get_text(strip=True)
            if name and title:
                name_to_title[(nation, name)] = title

# Also load coach wiki titles from coaches CSV
import csv
COACHES_CSV = Path(__file__).parent / "wc2026_coaches.csv"
if COACHES_CSV.exists():
    with open(COACHES_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("wiki_title") and row.get("coach"):
                nation = reg.display_name(reg.resolve_iso2(row["nation"]))
                name_to_title[(nation, row["coach"])] = row["wiki_title"]

print(f"  {len(name_to_title)} linked names found across {squad_tables} squads (incl. coaches)")

# ── Step 2: load JSON, collect titles used by actual players ──────────────────
with open(JSON_PATH, encoding="utf-8") as f:
    data = json.load(f)

all_players  = [(p["nation"], p) for rec in data["data"] for p in rec["players"]]
all_players += [(nation, p) for nation, players in data.get("natives", {}).items()
                for p in players]
needed_titles = list({name_to_title[(n, p["name"])] for n, p in all_players
                      if (n, p["name"]) in name_to_title})
print(f"  {len(needed_titles)} unique EN titles to query for langlinks")

# ── Step 3: batch-fetch langlinks (one language at a time) ────────────────────
# lllimit=max is 500 *total across all pages* in a batch — with 50 articles
# × ~60 langlinks each we'd hit the cap. Using lllang=<one lang> means each
# article returns at most 1 langlink, so batching 50 is always safe.
print(f"Step 2 — querying Wikipedia API for {LANGS} langlinks…")
title_to_langs = {t: {} for t in needed_titles}

for lang in LANGS:
    print(f"  language: {lang}")
    for i in range(0, len(needed_titles), BATCH):
        batch = needed_titles[i:i + BATCH]
        params = {
            "action":  "query",
            "prop":    "langlinks",
            "lllang":  lang,
            "lllimit": "max",
            "titles":  "|".join(batch),
            "format":  "json",
        }
        for attempt in range(5):
            resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=20)
            if resp.status_code == 429:
                wait = 10 * (2 ** attempt)
                print(f"    429 — waiting {wait}s…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        for page in resp.json()["query"]["pages"].values():
            lls = page.get("langlinks", [])
            if lls:
                title_to_langs[page["title"]][lang] = lls[0]["*"]
        time.sleep(1.0)

# ── Step 4: write per-language title files + set wikiTitle on players ────────
print("Step 3 — writing per-language title files…")
lang_titles = {lang: {} for lang in ALL_LANGS}
for en_title in needed_titles:
    lang_titles["en"][en_title] = url_ready_title(en_title)
    for lang in LANGS:
        t = title_to_langs.get(en_title, {}).get(lang)
        if t:
            lang_titles[lang][en_title] = url_ready_title(t)

for lang in ALL_LANGS:
    out_path = ROOT / f"wiki_{lang}.json"
    payload = {
        "urlTemplate": f"https://{lang}.wikipedia.org/wiki/{{title}}",
        "titles": lang_titles[lang],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  {out_path.name}: {len(lang_titles[lang])} titles")

print("Step 4 — enriching player objects with wikiTitle…")
matched = unmatched = 0
for nation, p in all_players:
    en_title = name_to_title.get((nation, p["name"]))
    p.pop("wiki", None)
    p.pop("wiki_langs", None)
    if not en_title:
        unmatched += 1
        p.pop("wikiTitle", None)
        continue
    matched += 1
    p["wikiTitle"] = en_title

print(f"  Matched: {matched}/{len(all_players)}  |  unmatched: {unmatched}")
for lang in LANGS:
    print(f"  {lang}: {len(lang_titles[lang])} players have a {lang}.wikipedia.org page")

with open(JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

print(f"{JSON_PATH} updated.")
