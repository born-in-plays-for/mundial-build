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
  data/elo_rank.json              (update_elo_rankings.py)
  data/r32_teams.json             (fetch_r32_teams.py)
  pipeline/team_status.json       (fetch_team_status.py)

The first five are pipeline-internal intermediates now, not frontend-facing
— only this script reads them. They live in pipeline/, committed (not
gitignored), because add_wiki_urls.py/build_player_wiki.py hit live
external APIs to produce them and aren't cheap to regenerate on a whim,
same as wc2026_players.csv/wc2026_coaches.csv.

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

PIPELINE = Path(__file__).parent
DATA     = PIPELINE.parent / "data"  # still-submodule outputs: elo_rank.json, r32_teams.json

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


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collect_persons(map_data):
    """One tuple per person, in map_data file order (this order feeds pid
    assignment for new persons, and export re-derives the file's sort
    orders stably from it)."""
    persons = []
    for rec in map_data["data"]:
        birth_iso2 = reg.resolve_iso2(rec["country"])
        for p in rec["players"]:
            persons.append((p["name"], p.get("role", "player"),
                            reg.resolve_iso2(p["nation"]), birth_iso2,
                            p["caps"], p["wikiTitle"]))
    for nation, players in map_data["natives"].items():
        iso2 = reg.resolve_iso2(nation)
        for p in players:
            persons.append((p["name"], p.get("role", "player"),
                            iso2, iso2, p["caps"], p["wikiTitle"]))
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
    for name, role, nation, birth, caps, title in persons:
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
    r32         = read_json(DATA / "r32_teams.json")
    team_status = read_json(PIPELINE / "team_status.json")
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
    af_used = 0
    for pid, (name, role, nation, birth, caps, title) in zip(pids, persons):
        db.execute("INSERT INTO person VALUES (?,?,?,?,?,?,?)",
                   (pid, name, role, cid(nation), cid(birth), caps,
                    title if title != name else None))
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
    for row in db.execute("SELECT id FROM country WHERE is_wc2026 = 1"):
        db.execute("INSERT INTO team_status (country) VALUES (?)", (row[0],))
    for iso2, info in team_status["eliminated"].items():
        lost_to = info.get("lostTo")
        db.execute("""UPDATE team_status SET status='eliminated', eliminated_round=?,
                     eliminated_date=?, eliminated_by=? WHERE country=?""",
                   (info["round"], info["date"], cid(lost_to) if lost_to else None, cid(iso2)))

    # ── provenance ──────────────────────────────────────────────────────
    pop_updated = max((c["pop_year"] for c in countries.values() if c.get("pop_year")),
                      default=None)
    db.execute("INSERT INTO provenance VALUES ('elo',?,?)", (elo["source"], elo["updated"]))
    db.execute("INSERT INTO provenance VALUES ('r32',?,?)", (r32["source"], r32["updated"]))
    db.execute("INSERT INTO provenance VALUES ('population',?,?)", (POP_SOURCE, pop_updated))
    db.execute("INSERT INTO provenance VALUES ('team_status',?,?)",
               (team_status["source"], team_status["updated"]))

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
    print(f"  {n('country')} countries, {n('person')} persons ({added} new pids), "
          f"{n('af_person')} af ids, {n('wiki_title')} wiki titles, "
          f"{n('elo_ranking')} elo entries, {n('team_status')} teams tracked "
          f"({eliminated_n} eliminated)")
    db.close()


if __name__ == "__main__":
    main()
