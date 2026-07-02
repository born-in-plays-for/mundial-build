"""
export.py — phase 2 of the two-phase DB build: export pipeline/mundial.db
(built by pipeline/load.py) into the pid-keyed v2 view files:

  data/v2/map.json          same shape as map_data.json, but every
                            player/coach carries an integer "pid" instead
                            of the "wikiTitle" string
  data/v2/live.json         player_wiki.json's successor:
                            {iso2: {af_id: {pid, birthCountry}}}
  data/v2/wiki_<lang>.json  {urlTemplate, titles: [...]} — array indexed
                            by pid (null = no article in that language)
  data/v2/status.json       {iso2: {round, date?}} — ELIMINATED teams only.
                            A team absent from this file is still alive;
                            the client never needs a positive "alive" list.

All files are written together, atomically from one DB state — pids can
never disagree across them.
"""
import gzip
import json
import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).parent
DB_PATH  = PIPELINE / "mundial.db"
OUT_DIR  = PIPELINE.parent / "data" / "v2"

LANGS = ["en", "fr", "de", "it", "es"]


def player_obj(name, caps, role, pid, nation=None):
    obj = {"name": name}
    if nation is not None:
        obj["nation"] = nation
    obj["caps"] = caps
    if role == "coach":
        obj["role"] = role
    obj["pid"] = pid
    return obj


def build_map(db):
    persons = db.execute("""
        SELECT p.pid, p.name, p.role, p.caps,
               n.id, n.name, b.id, b.name, b.iso2
        FROM person p
        JOIN country n ON n.id = p.nation
        JOIN country b ON b.id = p.birth
        ORDER BY p.pid""").fetchall()

    # exports: birth != nation, grouped by birth country in pid order
    groups = {}  # birth name -> {"id", "iso2", "players": [...]}
    natives = {}
    for pid, name, role, caps, n_id, n_name, b_id, b_name, b_iso2 in persons:
        if b_id != n_id:
            g = groups.setdefault(b_name, {"id": b_id, "iso2": b_iso2, "players": []})
            g["players"].append(player_obj(name, caps, role, pid, nation=n_name))
        else:
            natives.setdefault(n_name, []).append(player_obj(name, caps, role, pid))

    records = []
    for country, g in groups.items():
        players = sorted(g["players"],
                         key=lambda p: (0 if p.get("role") != "coach" else 1, -p["caps"]))
        nations = {}
        for p in g["players"]:
            nations[p["nation"]] = nations.get(p["nation"], 0) + 1
        records.append({
            "country": country,
            "id":      g["id"],
            "iso2":    g["iso2"],
            "count":   len(players),
            # tie-break alphabetically — deterministic, unlike build_json.py's
            # CSV-insertion order, which isn't recoverable from the DB
            "nations": sorted(([n, c] for n, c in nations.items()),
                              key=lambda x: (-x[1], x[0])),
            # top is display-only (tooltip preview) — no pid, matching the
            # old file where add_wiki_urls.py never wrote wikiTitle into it
            "top":     [{k: v for k, v in p.items() if k != "pid"} for p in players[:5]],
            "players": players,
        })
    records.sort(key=lambda r: -r["count"])

    for nation in natives:
        natives[nation].sort(key=lambda p: -p["caps"])

    pop = {iso2: round(p / 1_000_000, 2) for iso2, p in db.execute(
        "SELECT iso2, population FROM country WHERE population IS NOT NULL ORDER BY id")}
    capital = {}
    for iso2, lang, name in db.execute("""
            SELECT c.iso2, k.lang, k.name FROM capital_name k
            JOIN country c ON c.id = k.country ORDER BY c.id"""):
        capital.setdefault(iso2, {})[lang] = name
    pop_source, pop_updated = db.execute(
        "SELECT source, updated FROM provenance WHERE dataset = 'population'").fetchone()

    return {"data": records, "pop": pop, "capital": capital, "natives": natives,
            "popSource": pop_source, "popUpdated": pop_updated}


def build_live(db):
    live = {}
    for iso2, af_id, pid, birth in db.execute("""
            SELECT n.iso2, a.af_id, p.pid, b.name
            FROM af_person a
            JOIN person  p ON p.pid = a.pid
            JOIN country n ON n.id = p.nation
            LEFT JOIN country b ON b.id = p.birth
            ORDER BY n.iso2, a.af_id"""):
        entry = {"pid": pid}
        if birth is not None:
            entry["birthCountry"] = birth
        live.setdefault(iso2, {})[str(af_id)] = entry
    return live


def build_status(db):
    """Eliminated teams only — absence from this file IS the "still alive"
    signal (see schema.sql's view_eliminated / team_status comments).
    lostTo (who beat them) also derives every ALIVE team's current round —
    see schema.sql's view_current_round for the walk-the-chain logic."""
    status = {}
    for iso2, rnd, dt, lost_to in db.execute("SELECT * FROM view_eliminated ORDER BY iso2"):
        entry = {"round": rnd}
        if dt is not None:
            entry["date"] = dt
        if lost_to is not None:
            entry["lostTo"] = lost_to
        status[iso2] = entry
    return status


def build_wiki(db, lang):
    size = db.execute("SELECT MAX(pid) + 1 FROM person").fetchone()[0]
    titles = [None] * size
    for pid, title in db.execute("SELECT pid, title FROM wiki_title WHERE lang = ?", (lang,)):
        titles[pid] = title
    return {"urlTemplate": f"https://{lang}.wikipedia.org/wiki/{{title}}",
            "titles": titles}


def main():
    if not DB_PATH.exists():
        sys.exit(f"FATAL: {DB_PATH} not found — run pipeline/load.py first")
    db = sqlite3.connect(DB_PATH)

    anomalies = db.execute("""
        SELECT * FROM view_anomalies
        WHERE anomaly != 'person without EN wiki title'""").fetchall()
    if anomalies:
        for a, d in anomalies:
            print(f"  ANOMALY: {a}: {d}", file=sys.stderr)
        sys.exit(1)

    files = {"map.json": build_map(db), "live.json": build_live(db),
              "status.json": build_status(db)}
    for lang in LANGS:
        files[f"wiki_{lang}.json"] = build_wiki(db, lang)
    db.close()

    OUT_DIR.mkdir(exist_ok=True)
    for name, obj in files.items():
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (OUT_DIR / name).write_bytes(raw)
        gz = len(gzip.compress(raw, 9))
        print(f"  data/v2/{name:<14} {len(raw):>8,} bytes  ({gz:>7,} gzipped)")
    print(f"Wrote {len(files)} files to {OUT_DIR}")


if __name__ == "__main__":
    main()
