-- tt-data · migración: capa de identidad de persona por encima de las
-- federaciones (design/plan_identidad_persona.md). Añade person, person_licence
-- y person_id_alias. Tablas DERIVADAS (las escribe d1/build_person.py).
-- Aditivo y no destructivo: CREATE TABLE IF NOT EXISTS es re-ejecutable.
CREATE TABLE IF NOT EXISTS person (
  id           TEXT NOT NULL,
  display_name TEXT,
  birth_year   INTEGER,
  sex          TEXT,
  updated_at   TEXT,
  PRIMARY KEY (id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS person_licence (
  person_id   TEXT NOT NULL,
  federation  TEXT NOT NULL,
  lic         TEXT NOT NULL,
  status      TEXT NOT NULL,          -- 'confirmed' | 'probable' | 'rejected'
  evidence    TEXT NOT NULL,          -- 'nat_league_match' | 'name_and_club' | 'birth_year' | 'manual' | 'singleton'
  confidence  REAL,
  decided_at  TEXT,
  decided_by  TEXT,
  PRIMARY KEY (federation, lic)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS person_id_alias (
  old_id    TEXT NOT NULL,
  new_id    TEXT NOT NULL,
  merged_at TEXT,
  PRIMARY KEY (old_id)
) WITHOUT ROWID;
