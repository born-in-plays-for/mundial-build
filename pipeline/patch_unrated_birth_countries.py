"""
Detect birth countries with no eloratings.net entry and patch them into
data/elo_rank.json with a null ranking — generalizes what patch_kosovo.py
already does for Kosovo specifically to any such country.

A player/coach's birth_country doesn't have to be a WC2026 team, or even a
FIFA member, or even rated by eloratings.net at all — it just has to be a
real place someone was born. When that country is a real, resolvable ISO
entry (already present in countries.json) but eloratings.net simply doesn't
track it, it has no elo_rank.json entry at all — so it can't render as a
normal country pill anywhere in the frontend (map, country list, …), only
ever show up as a birth-city value with no pill of its own.

Reads map_data.json's own data[] array (build_json.py's already-resolved,
one-row-per-exporting-birth-country list — "exports" meaning birth_country
!= nation, which is exactly the interesting case: a birth_country equal to
the player's own nation is already a WC2026 team, already Elo-rated) rather
than re-parsing wc2026_players.csv/wc2026_coaches.csv's raw birth_country
column directly. That raw column is NOT the fully-resolved source for
players: build_json.py drills a UK birth city down to a specific home nation
(England/Scotland/Wales/Northern Ireland) as part of building map_data.json,
so wc2026_players.csv itself can still carry a bare "United Kingdom" string
pre-drill-down. Reading the CSV directly first surfaced exactly that false
positive here (a spurious country-level "United Kingdom" entry) before this
script was pointed at map_data.json's data[] instead, which every entry
already carries a cross-validated (see validate_country_coverage.py's
check_iso2) resolved iso2 for.

Concrete case found 2026-07: the Isle of Man (id 833, iso2 'im') — Kieran
Tierney (Scotland) was born in Douglas. Not a FIFA member (unlike Kosovo), so
unlike patch_kosovo.py this never touches elo_rank.json's fifaAbsences list —
that list is specifically the hand-curated set of FIFA members missing from
the rankings (see update_elo_rankings.py's own hardcoded fifa_absences),
which a non-FIFA-member addition never affects. A future gap that IS a FIFA
member absent from eloratings.net (Kosovo's own situation) still gets its
fifaMember flag set correctly here (from fifa_members_cache.json), just
without the fifaAbsences bookkeeping — add it there by hand if that ever
matters, same as Kosovo was.

Explicitly OUT of scope: a birth country that isn't in countries.json at all
— no ISO entry the pipeline recognizes, a Kosovo-level problem (no official
code, Wikidata lookups for population/capital, synthetic id assignment,
frontend topojson/i18n overrides) that needs its own dedicated one-off patch,
same as patch_kosovo.py/patch_uk_nations.py already are for THEIR countries.
This script only ever adds a country map_data.json already carries an iso2
for — itself only ever resolved via country_registry.resolve_iso2(), so
always backed by a real countries.json entry.

Idempotent and safe to re-run every build. Run standalone, or (like
patch_kosovo.py) automatically at the end of update_elo_rankings.py, so a
future squad change surfacing a *different* unrated birth country is picked
up automatically next build, without writing another one-off script.
"""
import json
from pathlib import Path

import country_registry as reg

ROOT            = Path(__file__).parent
DATA_DIR        = ROOT.parent / "data"
MAP_DATA        = ROOT / "map_data.json"
ELO_PATH        = DATA_DIR / "elo_rank.json"
FIFA_CACHE_PATH = ROOT / "fifa_members_cache.json"


def _birth_country_iso2s():
    """Every birth-country iso2 map_data.json's data[] already carries —
    build_json.py's own resolution, so no re-resolution needed here."""
    if not MAP_DATA.exists():
        print(f"  (skipping {MAP_DATA.name} — not found)")
        return set()
    rows = json.loads(MAP_DATA.read_text(encoding="utf-8")).get("data", [])
    return {r["iso2"] for r in rows if r.get("iso2")}


def main():
    if not FIFA_CACHE_PATH.exists():
        raise SystemExit(
            f"FATAL: {FIFA_CACHE_PATH.name} missing — run update_elo_rankings.py "
            f"at least once first (it builds this cache)."
        )
    fifa_members_iso2 = set(json.loads(FIFA_CACHE_PATH.read_text(encoding="utf-8"))["members"])

    elo = json.loads(ELO_PATH.read_text(encoding="utf-8"))
    existing_ids = {r.get("id") for r in elo["rankings"]}

    added = []
    for iso2 in sorted(_birth_country_iso2s()):
        country_id = reg.canonical_id(iso2)
        if country_id in existing_ids:
            continue
        entry = {
            "rank":       None,
            "id":         country_id,
            "iso2":       iso2,
            "name":       reg.canonical_name(iso2),
            "pts":        None,
            "fifaMember": iso2 in fifa_members_iso2,
            "weirdo":     False,
        }
        elo["rankings"].append(entry)
        existing_ids.add(country_id)
        added.append(entry)

    if not added:
        print("elo_rank.json: no unrated birth countries found — nothing to patch.")
        return

    if elo.get("stats"):
        elo["stats"]["total"] = len(elo["rankings"])
        elo["stats"]["fifaMembers"] = sum(1 for r in elo["rankings"] if r.get("fifaMember"))

    ELO_PATH.write_text(json.dumps(elo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for e in added:
        print(f"  Patched elo_rank.json → {e['name']} (id={e['id']}, iso2={e['iso2']}, "
              f"fifaMember={e['fifaMember']}) added to rankings (rank=null, pts=null)")


if __name__ == "__main__":
    main()
