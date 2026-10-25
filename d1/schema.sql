-- tt-scraper · esquema D1 (tablas, sin índices secundarios)
-- Ref: design/plan_migracion_d1.md §5. Los índices y el FTS van aparte
-- (indexes.sql, fts.sql) y se crean DESPUÉS del volcado inicial (§4).
-- IF NOT EXISTS para que aplicar el esquema sea re-ejecutable.
--
-- WITHOUT ROWID en todas las tablas: la PK compuesta/textual PASA A SER el árbol
-- de la tabla, sin índice implícito (sqlite_autoindex). Medido en D1: halva las
-- escrituras de inserción (200 filas → 200 rows_written en vez de 400). Encaja
-- porque nunca usamos rowid y la FTS es autónoma. Ver plan §4.

-- ─────────────────────────────────────────────────────────── identidad
CREATE TABLE IF NOT EXISTS player (
  federation  TEXT NOT NULL,          -- 'FCTT' | 'RFETM' (espacios de licencia distintos)
  lic         TEXT NOT NULL,          -- licencia/código tal cual la publica la fuente
  name        TEXT NOT NULL,
  club        TEXT,
  fa          TEXT,                   -- federación autonómica (solo RFETM)
  sex         TEXT,                   -- 'M' | 'F'
  birth_year  INTEGER,                -- solo RFETM
  cat         TEXT,                   -- categoría de edad declarada
  updated_at  TEXT,
  PRIMARY KEY (federation, lic)
) WITHOUT ROWID;

-- Puente entre federaciones: schema-ready, se puebla en fase posterior (§6).
-- La equivalencia se DEDUCE de partidos de liga nacional presentes en ambos
-- espacios de licencia (mismo día/marcador/ratings), NUNCA casando nombres.
CREATE TABLE IF NOT EXISTS player_link (
  federation_a TEXT NOT NULL, lic_a TEXT NOT NULL,   -- p.ej. FCTT
  federation_b TEXT NOT NULL, lic_b TEXT NOT NULL,   -- p.ej. RFETM
  method       TEXT NOT NULL,         -- 'nat_league_match'
  confidence   REAL,                  -- nº de partidos que confirman la equivalencia
  verified_at  TEXT,
  PRIMARY KEY (federation_a, lic_a, federation_b, lic_b)
) WITHOUT ROWID;

-- Identidad de persona por encima de las federaciones (design/plan_identidad_persona.md).
-- Tablas DERIVADAS: las escribe d1/build_person.py (SQL crudo, como player_link),
-- no pasan por D1Client._TABLES. Una persona agrupa sus licencias (FCTT/RFETM).
CREATE TABLE IF NOT EXISTS person (
  id           TEXT NOT NULL,         -- 'p_<hash>' estable (ver plan §3)
  display_name TEXT,                  -- nombre con el que se le llama
  birth_year   INTEGER,               -- cuando se conozca (hoy solo RFETM)
  sex          TEXT,                  -- 'M' | 'F'
  updated_at   TEXT,
  PRIMARY KEY (id)
) WITHOUT ROWID;

-- Una licencia pertenece a UNA persona (PK por licencia): así el emit idempotente
-- la mueve de persona sin dejar duplicados. status distingue lo seguro de lo
-- probable; evidence dice por qué se juntaron (para poder retirar un criterio).
CREATE TABLE IF NOT EXISTS person_licence (
  person_id   TEXT NOT NULL,
  federation  TEXT NOT NULL,          -- 'FCTT' | 'RFETM'
  lic         TEXT NOT NULL,
  status      TEXT NOT NULL,          -- 'confirmed' | 'probable' | 'rejected'
  evidence    TEXT NOT NULL,          -- 'nat_league_match' | 'name_and_club'
                                      --  | 'birth_year' | 'manual' | 'singleton'
  confidence  REAL,                   -- cuando la evidencia sea estadística
  decided_at  TEXT,
  decided_by  TEXT,                   -- 'scraper' | 'admin:<quien>'
  PRIMARY KEY (federation, lic)
) WITHOUT ROWID;

-- Fusiones de personas: si dos clusters confirmados se unen, el id superviviente
-- se registra aquí (nunca se reescribe en silencio). Los consumidores resuelven
-- alias old_id → new_id.
CREATE TABLE IF NOT EXISTS person_id_alias (
  old_id   TEXT NOT NULL,
  new_id   TEXT NOT NULL,
  merged_at TEXT,
  PRIMARY KEY (old_id)
) WITHOUT ROWID;

-- Equivalencia de club FCTT-código ↔ RFETM-nombre, DERIVADA de los pares
-- confirmados de player_link (d1/build_club_link.py). Convierte "mismo club" de
-- fuzzy (código vs nombre) a resoluble. confidence = nº de pares que lo votan.
CREATE TABLE IF NOT EXISTS club_link (
  fctt_code  TEXT NOT NULL,
  rfetm_name TEXT NOT NULL,
  confidence INTEGER,
  derived_at TEXT,
  PRIMARY KEY (fctt_code)
) WITHOUT ROWID;

-- Club por temporada de cada licencia, DERIVADO de las alineaciones
-- (individual_match → encounter: el equipo con el que juega), no del ranking
-- (que trae el club del final de la temporada anterior). Permite "cambió de
-- club" y usar el club con la temporada correcta (d1/build_player_club_season.py).
-- club = equipo mayoritario normalizado; clubs = nº de clubes distintos vistos
-- esa temporada (>1 señala préstamo o dato raro).
CREATE TABLE IF NOT EXISTS player_club_season (
  federation TEXT NOT NULL,
  lic        TEXT NOT NULL,
  season     TEXT NOT NULL,
  club       TEXT,
  matches    INTEGER,
  clubs      INTEGER,
  updated_at TEXT,
  PRIMARY KEY (federation, lic, season)
) WITHOUT ROWID;

-- ─────────────────────────────────────────────────────────── rànquings
CREATE TABLE IF NOT EXISTS ranking_edition (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL,
  label TEXT, year INTEGER, month INTEGER,
  PRIMARY KEY (federation, season, edition_id)
) WITHOUT ROWID;

-- Una fila por jugador y edición.
CREATE TABLE IF NOT EXISTS ranking_snapshot (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  pts REAL, pts_prev REAL, d_pts REAL, rd REAL,
  pj INTEGER, pg INTEGER, pp INTEGER,
  p_any INTEGER,                      -- acumulado del año (solo FCTT)
  classified INTEGER,                 -- 0/1 (solo FCTT)
  by_event TEXT,                      -- JSON {llnn, c_esp, …} (solo RFETM)
  PRIMARY KEY (federation, season, edition_id, lic)
) WITHOUT ROWID;

-- Una fila por jugador, edición y categoría.
-- Ojo: 99999 (RFETM) = "no compite en esa categoría", NO es posición → no insertar.
CREATE TABLE IF NOT EXISTS ranking_position (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  category TEXT NOT NULL, sex TEXT, pos INTEGER NOT NULL,
  PRIMARY KEY (federation, season, edition_id, lic, category)
) WITHOUT ROWID;

-- ─────────────────────── histórico partido a partido (FCTT: Partits_Jugats)
CREATE TABLE IF NOT EXISTS player_match (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  seq INTEGER NOT NULL,               -- desempata repeticiones dentro de (lic, edición)
  date TEXT NOT NULL, event TEXT NOT NULL, event_type TEXT, category TEXT,
  opp_lic TEXT,                       -- NULL si el rival no tiene licencia de esa federación
  opp_name TEXT NOT NULL, opp_external INTEGER DEFAULT 0,
  sets INTEGER, opp_sets INTEGER, won INTEGER,
  rk REAL, opp_rk REAL, d_rk REAL, rd REAL,   -- rating de cada uno EN EL MOMENTO del partido
  PRIMARY KEY (federation, season, edition_id, lic, seq)
) WITHOUT ROWID;

-- ─────────────────────────────────────────────────────────── competición
CREATE TABLE IF NOT EXISTS competition (
  id TEXT NOT NULL,                   -- 'fctt_2526', 'rtbtt_2526', 'rfetm_2526'
  organization TEXT,                  -- quién organiza (RTBTT, FCTT, RFETM)
  federation   TEXT NOT NULL,         -- federación contra la que resuelven las licencias
  season TEXT, name TEXT, source_url TEXT,
  PRIMARY KEY (id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS comp_group (
  competition_id TEXT NOT NULL, category_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, phase TEXT, source_url TEXT,
  PRIMARY KEY (competition_id, category_id, id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS team (
  competition_id TEXT NOT NULL, group_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, club TEXT, position INTEGER, points INTEGER,
  played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER,
  -- PF / PC: partidos individuales a favor / en contra (desempate de la tabla).
  games_for INTEGER, games_against INTEGER,
  PRIMARY KEY (competition_id, group_id, id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS matchday (
  competition_id TEXT NOT NULL, group_id TEXT NOT NULL, number INTEGER NOT NULL,
  date TEXT, status TEXT, pdf_url TEXT, pdf_hash TEXT,
  PRIMARY KEY (competition_id, group_id, number)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS encounter (
  id TEXT NOT NULL, competition_id TEXT NOT NULL, group_id TEXT NOT NULL,
  matchday_number INTEGER NOT NULL, date TEXT, status TEXT, acta_url TEXT,
  team_local_id TEXT, team_local_name TEXT, score_local INTEGER,
  team_visitor_id TEXT, team_visitor_name TEXT, score_visitor INTEGER,
  -- Terreny de joc / Lugar: dirección donde se juega (pista del equipo local).
  -- referee = Àrbitre / Árbitro (solo categorías nacionales, no siempre).
  venue TEXT, referee TEXT,
  PRIMARY KEY (competition_id, id)
) WITHOUT ROWID;

-- ord = puesto (1..5) en el que se alineó el jugador.
-- La federación de las licencias se obtiene por competition_id → competition.federation.
CREATE TABLE IF NOT EXISTS individual_match (
  competition_id TEXT NOT NULL, encounter_id TEXT NOT NULL, ord INTEGER NOT NULL,
  player_local_lic TEXT, player_local_name TEXT,
  player_visitor_lic TEXT, player_visitor_name TEXT,
  winner TEXT,                        -- 'local' | 'visitor'
  sets TEXT,                          -- JSON [{local, visitor}, …]
  -- Dobles (RTBTT): el partido que decide un encuentro 3-3. Lleva DOS jugadores
  -- por lado; los segundos van aquí para no perderlos (columnas nullable).
  is_doubles INTEGER DEFAULT 0,
  player_local_lic_2 TEXT, player_local_name_2 TEXT,
  player_visitor_lic_2 TEXT, player_visitor_name_2 TEXT,
  PRIMARY KEY (competition_id, encounter_id, ord)
) WITHOUT ROWID;
