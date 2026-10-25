-- tt-data · migración: club_link (equivalencia club FCTT-código ↔ RFETM-nombre),
-- derivada de los pares confirmados de player_link (d1/build_club_link.py).
-- Sube el recall de "mismo club" para la identidad de persona
-- (design/plan_identidad_persona.md, paso 7). Aditivo y re-ejecutable.
CREATE TABLE IF NOT EXISTS club_link (
  fctt_code  TEXT NOT NULL,
  rfetm_name TEXT NOT NULL,
  confidence INTEGER,
  derived_at TEXT,
  PRIMARY KEY (fctt_code)
) WITHOUT ROWID;
