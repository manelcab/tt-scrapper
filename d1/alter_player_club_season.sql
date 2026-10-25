-- tt-data · migración: player_club_season (club vigente por licencia y temporada,
-- derivado de las alineaciones). design/plan_identidad_persona.md, paso 8.
-- Aditivo y re-ejecutable.
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
