"""
load.py — phase 1 of the two-phase DB build: load pipeline artifacts into
pipeline/mundial.db, the canonical relational model defined by
pipeline/schema.sql. The DB file is a rebuildable artifact (gitignored);
run this any time an input below changes, then pipeline/export.py.

Sits AFTER the existing pipeline scripts and reads their outputs as-is —
nothing upstream changes:

  pipeline/countries.json         (fetch_countries.py)
  pipeline/map_data.json          (build_json.py + add_wiki_urls.py)
  pipeline/player_wiki.json       (build_player_wiki.py)
  pipeline/wiki_<lang>.json x5    (add_wiki_urls.py)
  pipeline/r32_teams.json         (fetch_r32_teams.py)
  pipeline/discipline_stats.json  (fetch_discipline_stats.py)
  pipeline/geocode_cache.json     (geocode_birthplaces.py)
  data/elo_rank.json              (update_elo_rankings.py)
  data/fixtures.json              (fetch_fixtures.py)

All but data/elo_rank.json and data/fixtures.json are pipeline-internal
intermediates now, not frontend-facing — only this script reads them
(r32_teams.json's iso2 map moved to data/v2/live.json's "teams" key). They
live in pipeline/, committed (not gitignored), because add_wiki_urls.py/
build_player_wiki.py/fetch_r32_teams.py/fetch_discipline_stats.py hit live
external APIs to produce them and aren't cheap to regenerate on a whim,
same as wc2026_players.csv/wc2026_coaches.csv.

Team elimination status (the team_status table / data/v2/status.json) is
derived here from data/fixtures.json's round/status/winner fields — a pure
classification (see classify_round/compute_eliminated below), not a
separate api-football fetch.

pid stability: pipeline/person_registry.csv (committed) pins every person
ever seen to a pid, matched by api-football id first, then by
(nation iso2, name). New persons get fresh pids appended; a pid is never
reused, and rows for persons no longer in the data are kept as tombstones.

Fails loudly on anything unexpected. The one tolerated defect is the known
duplicate-wiki-title case (see UNIQUE(lang, title) in schema.sql): the
conflicting titles are skipped with a warning, so the affected person
ships without a wiki link until the source data is fixed upstream.
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

import country_registry as reg
from geocode_birthplaces import strip_admin_qualifier

PIPELINE = Path(__file__).parent
DATA     = PIPELINE.parent / "data"  # still-submodule output: elo_rank.json

DB_PATH       = PIPELINE / "mundial.db"
SCHEMA_PATH   = PIPELINE / "schema.sql"
REGISTRY_PATH = PIPELINE / "person_registry.csv"

LANGS = ["en", "fr", "de", "it", "es"]

# Actual tournament squad size when != 26 (injury withdrawals shrink the
# squad but the withdrawn player stays on Wikipedia's roster, so this is
# not derivable from person rows). Source of the frontend's SQUAD_SIZE
# override, which migrates here.
SQUAD_SIZE_OVERRIDES = {"at": 25, "ca": 25}

POP_SOURCE = "data.worldbank.org/indicator/SP.POP.TOTL"

FINISHED = {"FT", "AET", "PEN"}

# Knockout stage names as api-football actually returns them for this
# league/season (verified live — see fetch_r32_teams.py's find_r32_round
# for the naming-varies-by-edition caveat; add fallbacks here if a future
# re-run reports an unrecognized round name for a knockout fixture).
KNOCKOUT_STAGES = ["Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final"]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def classify_round(round_name):
    """-> 'group' | one of KNOCKOUT_STAGES | None (unrecognized)."""
    if round_name.lower().startswith("group stage"):
        return "group"
    for stage in KNOCKOUT_STAGES:
        if stage.lower() == round_name.lower():
            return stage
    return None


def compute_eliminated(fixtures):
    """-> {iso2: {"round", "date", "lostTo"}}, from data/fixtures.json's
    fixture list. See fetch_team_status.py's former docstring (git history)
    for the elimination-logic rationale this ports verbatim:

      - Knockout rounds: a finished fixture's loser is eliminated at that
        round, dated to kickoff, with lostTo recording the winner.
      - Group stage: decided only once every group fixture is finished, by
        checking which WC2026 teams are absent from the Round of 32 field —
        that absence IS the tie-break result, already computed by whoever
        seeded that round.
    """
    by_stage = {}
    for f in fixtures:
        by_stage.setdefault(classify_round(f["round"]), []).append(f)

    # An unrecognized round with a FINISHED fixture in it means real
    # elimination data would be silently dropped — fail loudly instead.
    # An unrecognized round with nothing decided yet is harmless
    # forward-looking noise — warn only.
    unrecognized = by_stage.get(None, [])
    decided_unrecognized = [f for f in unrecognized if f["status"] in FINISHED]
    if decided_unrecognized:
        names = sorted({f["round"] for f in decided_unrecognized})
        sys.exit(f"FATAL: {len(decided_unrecognized)} finished fixture(s) in unrecognized "
                 f"round(s) {names} — their eliminations would be silently dropped. Add the "
                 f"round name to KNOCKOUT_STAGES (or the group-stage prefix check) and re-run.")
    if unrecognized:
        names = sorted({f["round"] for f in unrecognized})
        print(f"  Warning: unrecognized round name(s) with nothing decided yet, ignored: "
              f"{names}", file=sys.stderr)

    eliminated = {}

    # ── Group stage: decide only once every group fixture is finished ────
    group_fixtures = by_stage.get("group", [])
    group_done = bool(group_fixtures) and all(f["status"] in FINISHED for f in group_fixtures)
    r32_fixtures = by_stage.get("Round of 32", [])
    if group_done:
        if r32_fixtures:
            r32_iso2 = {f[side] for f in r32_fixtures for side in ("home", "away") if f[side]}
            wc2026_iso2 = {reg.resolve_iso2(n) for n in reg.wc2026_nations()}
            for iso2 in sorted(wc2026_iso2 - r32_iso2):
                eliminated[iso2] = {"round": "Group Stage", "date": None, "lostTo": None}
        else:
            print("  Group stage finished but Round of 32 isn't scheduled/known "
                  "yet — skipping group-stage elimination for now", file=sys.stderr)

    # ── Knockout rounds: each finished fixture's loser is eliminated ─────
    for stage in KNOCKOUT_STAGES:
        for f in by_stage.get(stage, []):
            if f["status"] not in FINISHED or f["winner"] is None:
                continue
            loser_side = "away" if f["winner"] == "home" else "home"
            iso2, winner_iso2 = f[loser_side], f[f["winner"]]
            if not iso2:
                print(f"  Warning: could not resolve country for eliminated "
                      f"team in fixture {f['id']}", file=sys.stderr)
                continue
            eliminated[iso2] = {"round": stage, "date": f["date"][:10], "lostTo": winner_iso2}

    return eliminated


def compute_discipline(fixtures, fixture_stats):
    """-> {iso2: {stage: {"matches", "fouls_committed", "fouls_suffered",
    "yellow_cards", "red_cards"}}}, crossing data/fixtures.json with
    fetch_discipline_stats.py's pipeline/discipline_stats.json (raw per-
    fixture, per-team counts). Stage bucketing reuses classify_round() — the
    same function compute_eliminated() uses — so a fixture's discipline
    stage can never disagree with its elimination round. A finished fixture
    missing from fixture_stats (discipline_stats.json not re-run since it
    finished) is skipped with a warning rather than failing the load:
    discipline stats are supplementary, unlike elimination status."""
    totals = {}
    missing = []
    for f in fixtures:
        if f["status"] not in FINISHED:
            continue
        stats = fixture_stats.get(str(f["id"]))
        if stats is None:
            missing.append(f["id"])
            continue
        stage = classify_round(f["round"])
        if stage is None:
            continue  # compute_eliminated() already fails loudly on this if it matters
        if stage == "group":
            stage = "Group Stage"
        for iso2, opp_iso2 in ((f["home"], f["away"]), (f["away"], f["home"])):
            if iso2 not in stats or opp_iso2 not in stats:
                continue
            t = totals.setdefault(iso2, {}).setdefault(
                stage, {"matches": 0, "fouls_committed": 0, "fouls_suffered": 0,
                        "yellow_cards": 0, "red_cards": 0})
            t["matches"] += 1
            t["fouls_committed"] += stats[iso2]["foulsCommitted"]
            t["fouls_suffered"] += stats[opp_iso2]["foulsCommitted"]
            t["yellow_cards"] += stats[iso2]["yellowCards"]
            t["red_cards"] += stats[iso2]["redCards"]

    if missing:
        print(f"  Warning: {len(missing)} finished fixture(s) missing from "
              f"discipline_stats.json (re-run fetch_discipline_stats.py): "
              f"{sorted(missing)}", file=sys.stderr)
    return totals


def collect_persons(map_data):
    """One tuple per person, in map_data file order (this order feeds pid
    assignment for new persons, and export re-derives the file's sort
    orders stably from it). surname/shirt_number/birth_city/position/
    birth_lat/birth_lon are appended last so the existing (name, role,
    nation, birth, caps, title) positions — the ones assign_pids() and the
    wiki-title pass key off — stay unchanged. birth_lat/birth_lon (from
    build_json.py, ultimately Wikidata's P19-target P625 coordinate — see
    wc2026_birthplaces.py's enrich_birth_coordinates) are None unless that
    coordinate was resolved and agreed with birthCity."""
    persons = []
    for rec in map_data["data"]:
        birth_iso2 = reg.resolve_iso2(rec["country"])
        for p in rec["players"]:
            persons.append((p["name"], p.get("role", "player"),
                            reg.resolve_iso2(p["nation"]), birth_iso2,
                            p["caps"], p["wikiTitle"],
                            p.get("surname") or p["name"], p.get("shirtNumber"),
                            p.get("birthCity"), p.get("position"),
                            p.get("birthLat"), p.get("birthLon")))
    for nation, players in map_data["natives"].items():
        iso2 = reg.resolve_iso2(nation)
        for p in players:
            persons.append((p["name"], p.get("role", "player"),
                            iso2, iso2, p["caps"], p["wikiTitle"],
                            p.get("surname") or p["name"], p.get("shirtNumber"),
                            p.get("birthCity"), p.get("position"),
                            p.get("birthLat"), p.get("birthLon")))
    return persons


def load_registry():
    """-> (rows, af_to_pid, key_to_pid, next_pid). rows keyed by pid.
    af_to_pid is keyed by (role, af_id): api-football coach and player ids
    are separate id spaces that collide numerically (see schema.sql)."""
    rows, af_to_pid, key_to_pid = {}, {}, {}
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                pid = int(r["pid"])
                af_ids = [int(a) for a in r["af_ids"].split(";") if a]
                rows[pid] = {"role": r["role"], "iso2": r["iso2"],
                             "name": r["name"], "af_ids": af_ids}
                key_to_pid[(r["iso2"], r["name"])] = pid
                for af in af_ids:
                    af_to_pid[(r["role"], af)] = pid
    next_pid = max(rows, default=-1) + 1
    return rows, af_to_pid, key_to_pid, next_pid


def assign_pids(persons, af_ids_of):
    """Match each person against the registry ((role, af id) first, then
    (iso2, name)), allocate fresh pids for new ones, rewrite the registry."""
    rows, af_to_pid, key_to_pid, next_pid = load_registry()
    pids, added = [], 0
    for (name, role, nation, birth, caps, title, surname, shirt_number, birth_city, position,
         _birth_lat, _birth_lon) in persons:
        af_ids = af_ids_of.get((nation, title), [])
        pid = next((af_to_pid[(role, a)] for a in af_ids if (role, a) in af_to_pid), None)
        if pid is None:
            pid = key_to_pid.get((nation, name))
        if pid is None:
            pid, next_pid = next_pid, next_pid + 1
            added += 1
        # refresh the matched/new row (pid is the only immutable part)
        rows[pid] = {"role": role, "iso2": nation, "name": name,
                     "af_ids": sorted(set(rows.get(pid, {}).get("af_ids", []) + af_ids))}
        key_to_pid[(nation, name)] = pid
        for a in af_ids:
            af_to_pid[(role, a)] = pid
        pids.append(pid)

    if len(set(pids)) != len(pids):
        sys.exit("FATAL: two persons resolved to the same pid — registry is ambiguous")

    with open(REGISTRY_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pid", "role", "iso2", "name", "af_ids"])
        for pid in sorted(rows):
            r = rows[pid]
            w.writerow([pid, r["role"], r["iso2"], r["name"],
                        ";".join(str(a) for a in r["af_ids"])])
    return pids, added


def main():
    countries   = read_json(PIPELINE / "countries.json")
    map_data    = read_json(PIPELINE / "map_data.json")
    player_wiki = read_json(PIPELINE / "player_wiki.json")
    elo         = read_json(DATA / "elo_rank.json")
    r32         = read_json(PIPELINE / "r32_teams.json")
    discipline  = read_json(PIPELINE / "discipline_stats.json")
    geocode_all = read_json(PIPELINE / "geocode_cache.json")
    geocode     = geocode_all["cities"]
    fixtures    = read_json(DATA / "fixtures.json")
    wiki        = {lang: read_json(PIPELINE / f"wiki_{lang}.json")["titles"] for lang in LANGS}

    DB_PATH.unlink(missing_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # ── country + capital_name ──────────────────────────────────────────
    wc2026 = reg.wc2026_nations()  # display names of the 48-team field
    for c in countries.values():
        iso2 = c["alpha2"].lower()
        name = reg.display_name(iso2)
        db.execute("INSERT INTO country VALUES (?,?,?,?,?,?,?,?)",
                   (c["id"], iso2, c.get("alpha3") or None, name,
                    c.get("population") or None,  # source uses 0 for "no data"
                    c.get("pop_year"),
                    1 if name in wc2026 else 0,
                    SQUAD_SIZE_OVERRIDES.get(iso2)))
        for lang, cap in (c.get("capital") or {}).items():
            db.execute("INSERT INTO capital_name VALUES (?,?,?)", (c["id"], lang, cap))

    # ── person + af_person + wiki_title ─────────────────────────────────
    persons = collect_persons(map_data)

    # (nation iso2, wikiTitle) -> api-football ids; one person can have
    # several (api-football duplicate-id records, see schema.sql).
    af_ids_of, af_total = {}, 0
    for iso2, entries in player_wiki.items():
        for af_id, info in entries.items():
            af_ids_of.setdefault((iso2, info["wikiTitle"]), []).append(int(af_id))
            af_total += 1

    pids, added = assign_pids(persons, af_ids_of)

    cid = reg.canonical_id
    # (name, country id, lat, lon) -> city.id. lat/lon are part of the dedup
    # key, not just (name, country) — a bare (name, country) key would force
    # every person sharing a city NAME into one row/one coordinate even when
    # they're really different places (the Montreuil homonym bug this
    # schema change fixes; see schema.sql's city comment).
    city_id_of = {}

    def get_or_create_city(name, country_id, lat, lon, population, actual_name, source):
        key = (name, country_id, lat, lon)
        if key not in city_id_of:
            cur = db.execute(
                "INSERT INTO city (name, country, lat, lon, population, actual_name, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, country_id, lat, lon, population, actual_name, source))
            city_id_of[key] = cur.lastrowid
        return city_id_of[key]

    af_used = 0
    for pid, (name, role, nation, birth, caps, title, surname, shirt_number, birth_city, position,
              birth_lat, birth_lon) in zip(pids, persons):
        city_id = None
        if birth_city:
            # A pure string derivation of birth_city itself (see
            # strip_admin_qualifier/FALLBACK_PATTERNS) — computed uniformly
            # here regardless of which source resolves the coordinate below,
            # not sourced from geocode_cache.json's own (Nominatim-only)
            # copy of the same computation. A city.source='wikidata' row
            # skipping this entirely used to silently drop actualCityName
            # for anyone whose birth_city is a sub-city administrative unit
            # but who resolves via Wikidata (most such cases now do, since
            # Wikidata often has its own distinct, more precise entity per
            # arrondissement/district) — e.g. "12th arrondissement of
            # Paris" lost its "Paris" actualCityName the moment that person
            # started resolving via Wikidata instead of Nominatim.
            actual_name = strip_admin_qualifier(birth_city)
            if birth_lat is not None and birth_lon is not None:
                # The person's own Wikidata P19 claim already disambiguates
                # this exact place — trust it directly, skip the (city,
                # country) TEXT-keyed geocode_cache.json lookup entirely
                # (which can't tell apart two people sharing a city NAME but
                # not the same actual place).
                city_id = get_or_create_city(birth_city, cid(birth), birth_lat, birth_lon,
                                              None, actual_name, "wikidata")
            else:
                geo = geocode.get(f"{birth_city}, {reg.display_name(birth)}")
                lat = geo["lat"] if geo else None
                lon = geo["lon"] if geo else None
                population = geo.get("population") if geo else None
                source = None
                if geo:
                    source = "override" if geo.get("addresstype") == "override" else "nominatim"
                city_id = get_or_create_city(birth_city, cid(birth), lat, lon,
                                              population, actual_name, source)
        db.execute("INSERT INTO person VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, name, role, cid(nation), cid(birth), caps,
                    title if title != name else None, surname, shirt_number, position, city_id))
        for af in af_ids_of.get((nation, title), []):
            db.execute("INSERT INTO af_person VALUES (?,?,?)", (af, role, pid))
            af_used += 1
    if af_used != af_total:
        sys.exit(f"FATAL: {af_total - af_used} player_wiki entries matched no person "
                 f"in map_data — run build_player_wiki.py and build_json.py from the "
                 f"same squad state")

    # wiki titles, in pid order so a duplicate-title conflict resolves
    # deterministically (first pid keeps the title, later ones are skipped)
    title_of = dict(zip(pids, (p[5] for p in persons)))
    skipped = set()
    for pid in sorted(title_of):
        for lang in LANGS:
            t = wiki[lang].get(title_of[pid])
            if t is None:
                continue
            try:
                db.execute("INSERT INTO wiki_title VALUES (?,?,?)", (pid, lang, t))
            except sqlite3.IntegrityError:
                name = db.execute("SELECT name FROM person WHERE pid=?", (pid,)).fetchone()[0]
                skipped.add(name)
    for name in sorted(skipped):
        print(f"  WARNING: wiki title conflict — '{name}' shares an article with "
              f"another person and ships WITHOUT a wiki link; fix the source title "
              f"(add_wiki_urls.py / player aliases)", file=sys.stderr)

    # ── elo_ranking ─────────────────────────────────────────────────────
    for r in elo["rankings"]:
        country = cid(r["iso2"]) if r.get("iso2") else None
        db.execute("INSERT INTO elo_ranking VALUES (?,?,?,?,?,?)",
                   (country, None if country else r["name"],
                    r["rank"], r["pts"], r["fifaMember"], r["weirdo"]))

    # ── af_team ─────────────────────────────────────────────────────────
    for t in r32["teams"]:
        db.execute("INSERT INTO af_team VALUES (?,?)", (cid(t["iso2"]), t["id"]))

    # ── team_status: every WC2026 team starts 'alive', a loss updates it ─
    eliminated = compute_eliminated(fixtures["fixtures"])
    for row in db.execute("SELECT id FROM country WHERE is_wc2026 = 1"):
        db.execute("INSERT INTO team_status (country) VALUES (?)", (row[0],))
    for iso2, info in eliminated.items():
        lost_to = info.get("lostTo")
        db.execute("""UPDATE team_status SET status='eliminated', eliminated_round=?,
                     eliminated_date=?, eliminated_by=? WHERE country=?""",
                   (info["round"], info["date"], cid(lost_to) if lost_to else None, cid(iso2)))

    # ── team_discipline: bucketed by stage (see compute_discipline). Seed
    # every wc2026 team with a zero Group Stage row so the anomaly gate
    # below stays meaningful even before any fixture has finished. ────────
    discipline_totals = compute_discipline(fixtures["fixtures"], discipline["fixtures"])
    for row in db.execute("SELECT iso2 FROM country WHERE is_wc2026 = 1"):
        discipline_totals.setdefault(row[0], {}).setdefault(
            "Group Stage", {"matches": 0, "fouls_committed": 0, "fouls_suffered": 0,
                            "yellow_cards": 0, "red_cards": 0})
    for iso2, stages in discipline_totals.items():
        for stage, t in stages.items():
            db.execute("INSERT INTO team_discipline VALUES (?,?,?,?,?,?,?)",
                       (cid(iso2), stage, t["matches"], t["fouls_committed"],
                        t["fouls_suffered"], t["yellow_cards"], t["red_cards"]))

    # ── provenance ──────────────────────────────────────────────────────
    pop_updated = max((c["pop_year"] for c in countries.values() if c.get("pop_year")),
                      default=None)
    db.execute("INSERT INTO provenance VALUES ('elo',?,?)", (elo["source"], elo["updated"]))
    db.execute("INSERT INTO provenance VALUES ('r32',?,?)", (r32["source"], r32["updated"]))
    db.execute("INSERT INTO provenance VALUES ('population',?,?)", (POP_SOURCE, pop_updated))
    db.execute("INSERT INTO provenance VALUES ('team_status',?,?)",
               (fixtures["source"], fixtures["updated"]))
    db.execute("INSERT INTO provenance VALUES ('discipline',?,?)",
               (discipline["source"], discipline["updated"]))
    db.execute("INSERT INTO provenance VALUES ('geocode',?,?)",
               (geocode_all["source"], geocode_all["updated"]))

    # ── anomaly gate ────────────────────────────────────────────────────
    # The only tolerated anomaly is the missing-EN-title consequence of a
    # duplicate-title skip warned about above.
    hard = [(a, d) for a, d in db.execute("SELECT * FROM view_anomalies")
            if not (a == "person without EN wiki title" and d in skipped)]
    if hard:
        for a, d in hard:
            print(f"  ANOMALY: {a}: {d}", file=sys.stderr)
        sys.exit(1)

    db.commit()
    n = lambda t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"Wrote {DB_PATH}")
    eliminated_n = db.execute("SELECT COUNT(*) FROM team_status WHERE status='eliminated'").fetchone()[0]
    geocoded_n = db.execute("SELECT COUNT(*) FROM view_birthplace").fetchone()[0]
    print(f"  {n('country')} countries, {n('person')} persons ({added} new pids), "
          f"{n('af_person')} af ids, {n('wiki_title')} wiki titles, "
          f"{n('elo_ranking')} elo entries, {n('team_status')} teams tracked "
          f"({eliminated_n} eliminated), {n('team_discipline')} discipline rows, "
          f"{n('city')} cities ({geocoded_n} geocoded)")
    db.close()


if __name__ == "__main__":
    main()
