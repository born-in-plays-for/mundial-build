"""
Enriches wc2026_map_data.json with per-language Wikipedia URLs.

Step 1 — fetch the WC2026 squads page, extract player name → EN wiki title.
Step 2 — batch-query the Wikipedia API (prop=langlinks) for FR/DE/IT/ES titles.
Step 3 — write wiki_langs: {en, fr?, de?, it?, es?} onto every player object.
"""
import json, re, time, requests
from pathlib import Path
from urllib.parse import unquote, quote
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent / "data"
JSON_PATH = ROOT / "wc2026_map_data.json"

WIKI_URL  = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"
WIKI_API  = "https://en.wikipedia.org/w/api.php"
HEADERS   = {"User-Agent": "mundial-build/1.0 (github.com/born-in-plays-for)"}
LANGS     = ["fr", "de", "it", "es"]
BATCH     = 50  # max titles per API call

def wiki_url(lang, title):
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe=':@!$&\'()*+,;=/')}"

# ── Step 1: squad page → name → EN title ─────────────────────────────────────
print("Step 1 — fetching Wikipedia squad page…")
r = requests.get(WIKI_URL, headers=HEADERS, timeout=30)
r.raise_for_status()
soup = BeautifulSoup(r.text, "lxml")

name_to_title = {}
for table in soup.find_all("table", class_=re.compile(r"wikitable")):
    for a in table.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/wiki/") and ":" not in href:
            title = unquote(href[6:]).replace("_", " ")
            name  = a.get_text(strip=True)
            if name and title:
                name_to_title[name] = title

# Also load coach wiki titles from coaches CSV
import csv
COACHES_CSV = Path(__file__).parent / "wc2026_coaches.csv"
if COACHES_CSV.exists():
    with open(COACHES_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("wiki_title") and row.get("coach"):
                name_to_title[row["coach"]] = row["wiki_title"]

print(f"  {len(name_to_title)} linked names found (incl. coaches)")

# ── Step 2: load JSON, collect titles used by actual players ──────────────────
with open(JSON_PATH, encoding="utf-8") as f:
    data = json.load(f)

all_players  = [p for rec in data["data"] for p in rec["players"]]
all_players += [p for players in data.get("natives", {}).values() for p in players]
needed_titles = list({name_to_title[p["name"]] for p in all_players if p["name"] in name_to_title})
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

# ── Step 4: enrich player objects ─────────────────────────────────────────────
print("Step 3 — enriching player objects…")
matched = unmatched = 0
lang_counts = {l: 0 for l in LANGS}

for p in all_players:
    en_title = name_to_title.get(p["name"])
    if not en_title:
        unmatched += 1
        p.pop("wiki", None)
        p.pop("wiki_langs", None)
        continue
    matched += 1
    langs = title_to_langs.get(en_title, {})
    wiki_langs = {"en": wiki_url("en", en_title)}
    for l in LANGS:
        if l in langs:
            wiki_langs[l] = wiki_url(l, langs[l])
            lang_counts[l] += 1
    p["wiki"]       = wiki_langs["en"]   # backward compat
    p["wiki_langs"] = wiki_langs

print(f"  Matched: {matched}/{len(all_players)}  |  unmatched: {unmatched}")
for l in LANGS:
    print(f"  {l}: {lang_counts[l]} players have a {l}.wikipedia.org page")

with open(JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

print(f"{JSON_PATH} updated.")
