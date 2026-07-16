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
-- eliminated_by (who beat them) matters beyond record-keeping: a team that
-- appears as someone else's eliminated_by has thereby proven it WON that
-- round and is now playing the next one. That derives every ALIVE team's
-- current round too, with no separate field or concept — walk the chain
-- of eliminated_by values to find the furthest round any team is known to
-- have won, and its current round is one past that (see view_current_round).
-- NULL for Group Stage exits (round-robin, no single deciding opponent —
-- same condition as eliminated_date being NULL) or an unresolved name.
CREATE TABLE team_status (
    country          INTEGER PRIMARY KEY REFERENCES country(id),
    status           TEXT NOT NULL DEFAULT 'alive' CHECK (status IN ('alive', 'eliminated')),
    eliminated_round TEXT,
    eliminated_date  TEXT,
    eliminated_by    INTEGER REFERENCES country(id),
    CHECK ((status = 'alive') = (eliminated_round IS NULL)),
    CHECK (eliminated_by IS NULL OR eliminated_round <> 'Group Stage')
);

-- ── Discipline (fouls/cards) ────────────────────────────────────────────
-- One row per (team, stage) — that stage's OWN totals, not cumulative —
-- aggregated by load.py from fetch_discipline_stats.py's per-fixture output,
-- bucketed by fixture round via the same classify_round() team_status's
-- elimination logic uses (so a discipline stage can never disagree with an
-- elimination round). "stage" is 'Group Stage' or one of load.py's
-- KNOCKOUT_STAGES; a fixture in an unrecognized round is dropped here (and
-- already fails the load loudly elsewhere via compute_eliminated if it
-- matters — see load.py). Per-match averages, fouls-per-card, and
-- cumulative "through this stage" totals are ALL derived in view_discipline
-- below (window function over stage order), same division of labour as
-- view_squad_size deriving squad size from person rows instead of storing
-- it. fouls_suffered isn't an api-football field: it's the opponent's Fouls
-- value in that same fixture.
CREATE TABLE team_discipline (
    country          INTEGER NOT NULL REFERENCES country(id),
    stage            TEXT    NOT NULL,
    matches_played   INTEGER NOT NULL CHECK (matches_played >= 0),
    fouls_committed  INTEGER NOT NULL CHECK (fouls_committed >= 0),
    fouls_suffered   INTEGER NOT NULL CHECK (fouls_suffered >= 0),
    yellow_cards     INTEGER NOT NULL CHECK (yellow_cards >= 0),
    red_cards        INTEGER NOT NULL CHECK (red_cards >= 0),
    PRIMARY KEY (country, stage)
);

-- ── city (birthplaces) ─────────────────────────────────────────────────
-- Distinct birth cities, deduplicated across persons — many players share
-- one (e.g. several Brazilians born in São Paulo), so this is a normal
-- entity with an FK from person, the same pattern country/capital_name use,
-- rather than repeating the name/lat/lon on every person row. lat/lon NULL
-- means the city name is known but unresolved — kept (not dropped) so a
-- rerun doesn't have to re-resolve it. Two sources, load.py picks per
-- person (see `source` below): a direct Wikidata P19 coordinate when the
-- person has one, else pipeline/geocode_birthplaces.py's Nominatim search.
--
-- UNIQUE (name, country, lat, lon) — NOT just (name, country): `name` alone
-- is not a real identity key even combined with country, because the SAME
-- city-name text can legitimately refer to DIFFERENT real places for
-- different persons (French homonym communes are common — "Montreuil"
-- alone is at least 5 different towns; a bare (name, country) key forced
-- every "Montreuil, France" person into one row/one lat-lon, which is
-- exactly how 6 WC2026 players ended up geocoded to the wrong Montreuil
-- before this was fixed by resolving per-person via Wikidata). Including
-- lat/lon in the key lets same-named-but-different places coexist as
-- separate rows instead of silently merging, while still collapsing
-- persons who share both the name AND the resolved place into one row.
--
-- source distinguishes how lat/lon (when present) were resolved:
-- 'wikidata' (the person's own P19 target's P625 coordinate — disambiguated
-- by construction, preferred whenever available), 'nominatim' (free-text
-- search over (name, country), only used when no Wikidata coordinate
-- exists), 'override' (pipeline/geocode_overrides.json, hand-verified),
-- or NULL (name known, nothing resolved it). Diagnostic only — not
-- exposed via view_birthplace/data/v2/birthplace.json — same
-- "kept for a future audit, not consumed downstream" precedent as
-- geocode_cache.json's own addresstype field.
--
-- population comes from whichever source resolved the coordinate: for
-- source='nominatim' rows, Nominatim's own OSM `population` extratag; for
-- source='wikidata' rows (including one adopted via a sibling — see
-- load.py's wikidata_canonical_coords), the SAME place entity's P1082
-- statement. Deliberately read off the entity already resolved for lat/lon
-- rather than a separate join against a different dataset (e.g. GeoNames,
-- used elsewhere for KDE population weighting), which would need its own
-- coordinate-matching logic on top of the city-identity resolution already
-- done here. NULL is the normal case for most small places (or a place
-- entity lacking that particular statement), not a failure. TEXT, not
-- INTEGER: neither source's value is reliably clean (a malformed OSM tag
-- like "2.618" has been seen live) and nothing here does arithmetic on it,
-- so it's carried through as given rather than coerced. A Wikidata
-- coordinate switched a lot of persons off Nominatim in one commit
-- (converging same-city coordinates across sources) BEFORE P1082 was
-- wired in as a counterpart — population briefly, silently dropped for
-- everyone switched, since only Nominatim had it at the time; fixed by
-- reading P1082 off the same query already fetching P625, not by trying
-- to preserve the old Nominatim value across a source change.
--
-- actual_name: `name` is sometimes a sub-city administrative unit rather
-- than a plain city ("12th arrondissement of Paris") — see
-- geocode_birthplaces.py's FALLBACK_PATTERNS/strip_admin_qualifier.
-- actual_name holds the stripped plain-city form ("Paris") ONLY when it
-- differs from `name`; NULL means `name` already is the plain city name.
-- Same only-when-it-differs convention as person.en_title below. UNLIKE
-- population, this is set for every source (load.py derives it from
-- `name` itself, uniformly, regardless of whether the coordinate came
-- from Wikidata, Nominatim, or an override) — it's a pure string
-- transformation of the scraped city text, independent of how the
-- coordinate was resolved. An earlier version computed and cached this
-- only on the Nominatim path, which silently dropped the field for anyone
-- who moved to a Wikidata-sourced coordinate instead (most sub-city
-- administrative units DO resolve via Wikidata today, since it often has
-- its own distinct, more precise entity per arrondissement/district).
CREATE TABLE city (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    country     INTEGER NOT NULL REFERENCES country(id),
    lat         REAL,
    lon         REAL,
    population  TEXT,
    actual_name TEXT,
    source      TEXT CHECK (source IN ('wikidata', 'nominatim', 'override')),
    UNIQUE (name, country, lat, lon),
    CHECK ((lat IS NULL) = (lon IS NULL))
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
    pid          INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,        -- display name (EN-Wikipedia-derived)
    role         TEXT    NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'coach')),
    nation       INTEGER NOT NULL REFERENCES country(id),  -- plays for / coaches
    birth        INTEGER REFERENCES country(id),           -- NULL = birthplace unresolved (kept, not dropped)
    caps         INTEGER NOT NULL DEFAULT 0 CHECK (caps >= 0),  -- 0 for coaches
    en_title     TEXT,                    -- EN Wikipedia title ONLY when it differs from name
                                          -- (disambiguated articles etc.); NULL = same as name
    -- Sortable surname, derived from `name` via nameparser (never copied
    -- from FIFA's official squad-list PDF — see pipeline/CLAUDE.md's
    -- "Sortable surname" section for why no single FIFA column is trustworthy
    -- enough to source this from instead). Mononyms (Zizo, Neymar, Rodri,
    -- ...) have name = surname; never NULL.
    surname      TEXT    NOT NULL,
    -- Tournament shirt number, from the Wikipedia squads table's "No."
    -- column — NULL for coaches (not applicable) or an unresolved player.
    shirt_number INTEGER CHECK (shirt_number IS NULL OR shirt_number BETWEEN 1 AND 99),
    -- Playing position, from the same "No." column's leading GK/DF/MF/FW
    -- code (Wikipedia sorts squads goalkeepers-first, so the column reads
    -- "1 GK", "2 DF", ... — see build_json.py). NULL for coaches (not
    -- applicable) or an unresolved player.
    position     TEXT CHECK (position IS NULL OR position IN ('GK', 'DF', 'MF', 'FW')),
    -- Birth city (Wikipedia/Wikidata scrape, wc2026_players.csv/
    -- wc2026_coaches.csv's own birth_city column — see build_json.py).
    -- NULL = no scraped city. Points into city, not a repeated name/lat/lon.
    birth_city   INTEGER REFERENCES city(id),
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
SELECT p.pid, p.name, p.surname, p.shirt_number, p.position, p.role, p.caps,
       b.id AS birth_id,  b.name AS birth_country,
       n.id AS nation_id, n.name AS nation
FROM person p
JOIN country b ON b.id = p.birth
JOIN country n ON n.id = p.nation
WHERE p.birth <> p.nation;

-- Home-born players, grouped by nation ("natives" in map_data).
CREATE VIEW view_native_player AS
SELECT p.pid, p.name, p.surname, p.shirt_number, p.position, p.role, p.caps,
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

-- Geocoded birth cities (source of data/v2/birthplace.json) — only persons
-- with a successfully geocoded city; a known-but-ungeocodable or altogether
-- missing city is simply absent here rather than shipping a null lat/lon.
CREATE VIEW view_birthplace AS
SELECT p.pid, c.name AS birth_city, c.lat AS birth_lat, c.lon AS birth_lon,
       c.population AS birth_population, c.actual_name AS birth_actual_city_name
FROM person p
JOIN city c ON c.id = p.birth_city
WHERE c.lat IS NOT NULL;

-- Eliminated teams only (source of data/v2/status.json — absence from this
-- view is the client-facing "still alive" signal, not a NULL/'alive' row).
CREATE VIEW view_eliminated AS
SELECT c.iso2, t.eliminated_round, t.eliminated_date, w.iso2 AS lost_to_iso2
FROM team_status t
JOIN country c ON c.id = t.country
LEFT JOIN country w ON w.id = t.eliminated_by
WHERE t.status = 'eliminated';

-- Debug/verification view only — NOT exported. Proves eliminated_by alone
-- is enough to derive every alive team's current round: walk the furthest
-- round any team is recorded as having WON (i.e. appears as someone's
-- eliminated_by), current round = the one after that. A team with no win
-- yet is either still in the group stage, or contesting Round of 32 if the
-- group stage has concluded — distinguished by whether any 'Group Stage'
-- elimination has been recorded at all: fetch_team_status.py only ever
-- writes those once every group fixture is finished AND Round of 32 is
-- scheduled, and WC2026's format always eliminates exactly 16 teams there
-- (never zero), so that existence check is a reliable signal, not a guess.
CREATE VIEW view_current_round AS
WITH round_order(round, ord) AS (
    VALUES ('Round of 32', 1), ('Round of 16', 2), ('Quarter-finals', 3),
           ('Semi-finals', 4), ('Final', 5)
),
furthest_won AS (
    SELECT t.eliminated_by AS country, MAX(ro.ord) AS max_ord
    FROM team_status t JOIN round_order ro ON ro.round = t.eliminated_round
    WHERE t.eliminated_by IS NOT NULL
    GROUP BY t.eliminated_by
)
SELECT c.iso2,
       CASE WHEN NOT EXISTS (SELECT 1 FROM team_status WHERE eliminated_round = 'Group Stage')
                                   THEN 'Group Stage'
            WHEN fw.max_ord IS NULL THEN 'Round of 32'
            WHEN fw.max_ord = 5     THEN 'Champion'
            ELSE (SELECT round FROM round_order WHERE ord = fw.max_ord + 1)
       END AS current_round
FROM team_status t
JOIN country c ON c.id = t.country
LEFT JOIN furthest_won fw ON fw.country = t.country
WHERE t.status = 'alive';

-- Each team's current stage in one row — factored out of view_discipline so
-- both it and any future consumer share one answer for "what stage is this
-- team at": eliminated_round for a knocked-out team, otherwise
-- view_current_round's walk-the-win-chain result for a team still alive.
CREATE VIEW view_team_stage AS
SELECT c.iso2,
       CASE WHEN t.status = 'eliminated' THEN t.eliminated_round ELSE cr.current_round END AS stage,
       (t.status = 'eliminated') AS eliminated
FROM team_status t
JOIN country c ON c.id = t.country
LEFT JOIN view_current_round cr ON cr.iso2 = c.iso2;

-- Discipline stats, cumulative "through this stage" — one row PER (team,
-- stage reached so far), not one row per team. A red card in the Quarter-
-- finals must not show up when a client asks for a team's Round of 32
-- figures, so each row sums only that team's OWN team_discipline rows up to
-- and including the given stage (window function, partitioned by team,
-- ordered by stage_order.ord) rather than exposing one all-tournament total.
-- export.py's build_discipline() takes the last (highest-ord) row per team
-- as that team's overall "as of now" totals, and view_team_stage's `stage`
-- separately for the team's CURRENT stage — the two can differ for a team
-- still alive whose next round hasn't been played yet (no row exists for it
-- here until a fixture in it actually finishes).
CREATE VIEW view_discipline AS
WITH stage_order(stage, ord) AS (
    VALUES ('Group Stage', 0), ('Round of 32', 1), ('Round of 16', 2),
           ('Quarter-finals', 3), ('Semi-finals', 4), ('Final', 5)
),
cumulative AS (
    SELECT d.country, so.stage, so.ord,
           SUM(d.matches_played)  OVER w AS matches_played,
           SUM(d.fouls_committed) OVER w AS fouls_committed,
           SUM(d.fouls_suffered)  OVER w AS fouls_suffered,
           SUM(d.yellow_cards)    OVER w AS yellow_cards,
           SUM(d.red_cards)       OVER w AS red_cards
    FROM team_discipline d
    JOIN stage_order so ON so.stage = d.stage
    WINDOW w AS (PARTITION BY d.country ORDER BY so.ord
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT c.iso2, cu.stage, cu.ord,
       cu.matches_played,
       cu.fouls_committed, cu.fouls_suffered,
       ROUND(cu.fouls_committed * 1.0 / NULLIF(cu.matches_played, 0), 2) AS avg_fouls_committed,
       ROUND(cu.fouls_suffered * 1.0 / NULLIF(cu.matches_played, 0), 2) AS avg_fouls_suffered,
       cu.yellow_cards, cu.red_cards,
       ROUND(cu.fouls_committed * 1.0 / NULLIF(cu.yellow_cards + cu.red_cards, 0), 2) AS fouls_per_card
FROM cumulative cu
JOIN country c ON c.id = cu.country;

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
  AND NOT EXISTS (SELECT 1 FROM team_status t WHERE t.country = c.id)
UNION ALL
SELECT 'wc2026 nation with no team_discipline row', c.name
FROM country c
WHERE c.is_wc2026 = 1
  AND NOT EXISTS (SELECT 1 FROM team_discipline d WHERE d.country = c.id);
