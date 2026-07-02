-- mundial.db — canonical data model for the mundial pipeline.
--
-- Two-phase build:
--   phase 1 (load):   scraper outputs (CSV/JSON) -> resolve identities -> INSERT
--   phase 2 (export): SELECT from the view_* views -> data/ files, atomically
--
-- The DB file is a rebuildable artifact (gitignored). Sources of truth stay
-- the committed scraper outputs plus pipeline/person_registry.csv (pid
-- stability across builds — see person.pid).
--
-- Identity resolution (free-text country spellings -> iso2, api-football
-- names -> Wikipedia identity) happens in Python BEFORE insert, via
-- country_registry.py and the player-alias files. Those JSON files stay
-- authoritative and are NOT mirrored here; the schema enforces integrity
-- only after resolution.

PRAGMA foreign_keys = ON;

-- ── country ─────────────────────────────────────────────────────────────
-- PK is ISO 3166-1 numeric, extended by the pipeline's patch ids:
-- Kosovo 383, UK home nations 8260-8263. iso2 is the pipeline-wide join
-- key for external sources ('xk', 'gb-eng'); it never leaves lowercase.
CREATE TABLE country (
    id         INTEGER PRIMARY KEY,
    iso2       TEXT    NOT NULL UNIQUE CHECK (iso2 = lower(iso2)),
    iso3       TEXT    UNIQUE CHECK (iso3 <> ''),  -- loaders normalize '' -> NULL (UK nations have no iso3)
    name       TEXT    NOT NULL UNIQUE,   -- canonical EN display name (country_registry canonical_name)
    population INTEGER,                   -- absolute count, not millions (views scale for display)
    pop_year   TEXT,
    is_wc2026  INTEGER NOT NULL DEFAULT 0 CHECK (is_wc2026 IN (0, 1)),
    -- Actual tournament squad size when != 26 (injury withdrawals shrink a
    -- squad but the withdrawn player stays on Wikipedia's list, so this is
    -- NOT derivable from person rows — e.g. Austria/Canada 25 in WC2026).
    squad_size_override INTEGER CHECK (squad_size_override BETWEEN 1 AND 26)
);

-- Localized capital names (from countries.json). One row per language that
-- actually differs is NOT attempted — sources give all 5, store all 5.
CREATE TABLE capital_name (
    country INTEGER NOT NULL REFERENCES country(id),
    lang    TEXT    NOT NULL CHECK (lang IN ('en', 'fr', 'de', 'it', 'es')),
    name    TEXT    NOT NULL,
    PRIMARY KEY (country, lang)
);

-- ── Elo (eloratings.net) ───────────────────────────────────────────────
-- Ranks are NOT unique (real ties exist). Eight entrants are not ISO
-- countries (Zanzibar, Tibet, …): those rows carry a name instead of a
-- country FK — exactly one of the two, never both.
CREATE TABLE elo_ranking (
    country     INTEGER UNIQUE REFERENCES country(id),
    name        TEXT    UNIQUE,           -- only for non-ISO entrants
    -- rank/pts NULL together: FIFA member eloratings.net doesn't rate
    -- (Kosovo placeholder from patch_kosovo.py)
    rank        INTEGER CHECK (rank >= 1),
    pts         INTEGER,
    fifa_member INTEGER NOT NULL CHECK (fifa_member IN (0, 1)),
    weirdo      INTEGER NOT NULL DEFAULT 0 CHECK (weirdo IN (0, 1)),
    CHECK ((country IS NULL) <> (name IS NULL)),
    CHECK ((rank IS NULL) = (pts IS NULL))
);

-- ── api-football team identity ──────────────────────────────────────────
-- af_team_id is api-football's team id (external key), distinct from both
-- country.id and person.af_id id spaces.
CREATE TABLE af_team (
    country    INTEGER PRIMARY KEY REFERENCES country(id),
    af_team_id INTEGER NOT NULL UNIQUE
);

-- Tournament elimination status, one row per WC2026 team (fetch_team_status.py
-- seeds all 48 as 'alive'; a loss updates the row to 'eliminated'). A team
-- absent from this table is a load bug, not "still alive" — absence-means-
-- alive is a data/v2/status.json export-time convention, not a DB one.
--
-- eliminated_round is the API's own round-name string for a knockout exit
-- ('Round of 32', 'Round of 16', 'Quarter-finals', 'Semi-finals', 'Final')
-- or the literal 'Group Stage' when a team misses the round-of-32 cut.
-- eliminated_date is NULL for a Group Stage exit: WC2026's 8-best-thirds
-- tie-break isn't recomputed here — non-appearance in the round of 32
-- bracket, once every group fixture is finished, IS the tie-break result,
-- so there's no single fixture that "decided" it the way a knockout loss has.
CREATE TABLE team_status (
    country          INTEGER PRIMARY KEY REFERENCES country(id),
    status           TEXT NOT NULL DEFAULT 'alive' CHECK (status IN ('alive', 'eliminated')),
    eliminated_round TEXT,
    eliminated_date  TEXT,
    CHECK ((status = 'alive') = (eliminated_round IS NULL))
);

-- ── person ──────────────────────────────────────────────────────────────
-- Players and coaches, one row per human, stored exactly once.
--
-- pid is the proprietary integer id used by every exported view and by the
-- frontend as the wiki-lookup key. It must stay stable across builds so
-- submodule diffs stay meaningful and clients can cache: the loader seeds
-- pids from the committed pipeline/person_registry.csv (pid, af_id, name)
-- and only allocates fresh pids for genuinely new persons, appending them
-- to the registry.
CREATE TABLE person (
    pid      INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,            -- display name (EN-Wikipedia-derived)
    role     TEXT    NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'coach')),
    nation   INTEGER NOT NULL REFERENCES country(id),  -- plays for / coaches
    birth    INTEGER REFERENCES country(id),           -- NULL = birthplace unresolved (kept, not dropped)
    caps     INTEGER NOT NULL DEFAULT 0 CHECK (caps >= 0),  -- 0 for coaches
    en_title TEXT,                        -- EN Wikipedia title ONLY when it differs from name
                                          -- (disambiguated articles etc.); NULL = same as name
    -- duplicate-name safety net: two same-named people may exist across the
    -- tournament, never inside one squad
    UNIQUE (nation, name),
    UNIQUE (pid, role),                   -- FK target for af_person's role check
    CHECK (en_title IS NULL OR en_title <> name)
);

CREATE INDEX idx_person_nation ON person(nation);
CREATE INDEX idx_person_birth  ON person(birth);

-- api-football person ids (external). 1:n with person — api-football has
-- issued duplicate ids for the same human (Iraq's Ahmed Maknzi is both
-- 665478 and 292253; see player_aliases_confirmed.json 'duplicate-id').
-- Coach ids and player ids are SEPARATE api-football id spaces that
-- collide numerically (player Craig Gordon and coach Georgios Donis are
-- both 1106), so uniqueness is per role, not global: PK (role, af_id).
-- The composite FK forces role to match the person's actual role, so the
-- column is an enforced projection, not duplicated data.
CREATE TABLE af_person (
    af_id INTEGER NOT NULL,
    role  TEXT    NOT NULL,
    pid   INTEGER NOT NULL,
    PRIMARY KEY (role, af_id),
    FOREIGN KEY (pid, role) REFERENCES person(pid, role)
);

-- ── Wikipedia localization ─────────────────────────────────────────────
-- URL-ready article title per language (what wiki_<lang>.json ships).
-- No row = no article in that language (frontend falls back to EN).
-- UNIQUE (lang, title): a Wikipedia article is about exactly one person —
-- two persons resolving to the same article means the matcher picked the
-- wrong article for one of them (this constraint caught a real case:
-- Uruguay's Emiliano Martínez linked to the Argentine keeper's article).
CREATE TABLE wiki_title (
    pid   INTEGER NOT NULL REFERENCES person(pid),
    lang  TEXT    NOT NULL CHECK (lang IN ('en', 'fr', 'de', 'it', 'es')),
    title TEXT    NOT NULL,
    PRIMARY KEY (pid, lang),
    UNIQUE (lang, title)
);

-- ── provenance ──────────────────────────────────────────────────────────
-- Source attribution + freshness for exported files ("source"/"updated"
-- fields, popSource, …).
CREATE TABLE provenance (
    dataset TEXT PRIMARY KEY,             -- 'elo', 'r32', 'population', …
    source  TEXT NOT NULL,
    updated TEXT                          -- ISO date, when the source reports one
);

-- ═════════════════════════════════════════════════════════════════════
-- Views — phase 2 exports SELECT from these. Everything below is derived;
-- nothing below is ever hand-edited or separately loaded.
-- ═════════════════════════════════════════════════════════════════════

-- Foreign-born players ("exports" from birth country to nation) — the map
-- arcs and the birth-country grouping of map_data.
CREATE VIEW view_export_player AS
SELECT p.pid, p.name, p.role, p.caps,
       b.id AS birth_id,  b.name AS birth_country,
       n.id AS nation_id, n.name AS nation
FROM person p
JOIN country b ON b.id = p.birth
JOIN country n ON n.id = p.nation
WHERE p.birth <> p.nation;

-- Home-born players, grouped by nation ("natives" in map_data).
CREATE VIEW view_native_player AS
SELECT p.pid, p.name, p.role, p.caps,
       n.id AS nation_id, n.name AS nation
FROM person p
JOIN country n ON n.id = p.nation
WHERE p.birth = p.nation;

-- Squad size per nation — replaces the SQUAD_SIZE override table that
-- currently lives hardcoded in the frontend (Austria/Canada 25). The
-- roster COUNT is nominal (withdrawn players stay listed), so the
-- explicit override wins when present.
CREATE VIEW view_squad_size AS
SELECT c.id AS nation,
       COALESCE(c.squad_size_override, COUNT(p.pid)) AS squad_size
FROM country c
JOIN person p ON p.nation = c.id AND p.role = 'player'
GROUP BY c.id;

-- Live-page identity: per team, api-football id -> pid + birth country
-- (source of player_wiki.json's successor). One row per af_id, so a
-- duplicate-id person appears once per id — exactly what lineup lookup needs.
CREATE VIEW view_live_person AS
SELECT n.iso2, a.af_id, p.pid, p.name, p.role,
       b.id AS birth_id, b.name AS birth_country
FROM af_person a
JOIN person  p ON p.pid = a.pid
JOIN country n ON n.id = p.nation
LEFT JOIN country b ON b.id = p.birth;

-- Per-language wiki table (source of wiki_<lang>.json: array indexed by pid).
CREATE VIEW view_wiki AS
SELECT lang, pid, title FROM wiki_title;

-- Eliminated teams only (source of data/v2/status.json — absence from this
-- view is the client-facing "still alive" signal, not a NULL/'alive' row).
CREATE VIEW view_eliminated AS
SELECT c.iso2, t.eliminated_round, t.eliminated_date
FROM team_status t JOIN country c ON c.id = t.country
WHERE t.status = 'eliminated';

-- Integrity checks that go beyond per-row constraints; the load phase
-- fails if this view returns any rows.
CREATE VIEW view_anomalies AS
SELECT 'wc2026 nation with no squad' AS anomaly, c.name AS detail
FROM country c
WHERE c.is_wc2026 = 1
  AND NOT EXISTS (SELECT 1 FROM person p WHERE p.nation = c.id AND p.role = 'player')
UNION ALL
SELECT 'wc2026 nation with no coach', c.name
FROM country c
WHERE c.is_wc2026 = 1
  AND NOT EXISTS (SELECT 1 FROM person p WHERE p.nation = c.id AND p.role = 'coach')
UNION ALL
SELECT 'person with nation not in wc2026 field', p.name
FROM person p JOIN country c ON c.id = p.nation
WHERE c.is_wc2026 = 0
UNION ALL
SELECT 'person without EN wiki title', p.name
FROM person p
WHERE NOT EXISTS (SELECT 1 FROM wiki_title w WHERE w.pid = p.pid AND w.lang = 'en')
UNION ALL
-- The live view keys persons by af_id within a team; a player and the
-- coach of the SAME team sharing a numeric id would silently merge there.
SELECT 'af_id collision within one team', c.name || ' / af_id ' || a.af_id
FROM af_person a JOIN person p ON p.pid = a.pid JOIN country c ON c.id = p.nation
GROUP BY p.nation, a.af_id HAVING COUNT(DISTINCT a.pid) > 1
UNION ALL
SELECT 'wc2026 nation with no team_status row', c.name
FROM country c
WHERE c.is_wc2026 = 1
  AND NOT EXISTS (SELECT 1 FROM team_status t WHERE t.country = c.id);
