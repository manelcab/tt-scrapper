-- tt-scraper · índices D1
-- Ref: design/plan_migracion_d1.md §5. Se crean DESPUÉS del volcado inicial:
-- construir un índice sobre tabla poblada cuesta N filas escritas (una por fila),
-- por índice (medido §4). Crear antes encarece cada INSERT masivo.
--
-- Coste de escritura por fila = 1 + nº de índices de su tabla. Las tablas
-- calientes son player_match (2 índices) e individual_match (4, los dos lic +
-- los dos lic_2 del doble): no añadir más sin justificarlo.

CREATE INDEX IF NOT EXISTS idx_pos_top
  ON ranking_position(federation, season, edition_id, category, sex, pos);

CREATE INDEX IF NOT EXISTS idx_snap_player
  ON ranking_snapshot(federation, lic, edition_id);

-- El puente entre federaciones se consulta por LOS DOS lados (UNION ALL de
-- `federation_a`/`lic_a` y `federation_b`/`lic_b`), y la PK solo sirve al
-- primero: la otra mitad escanea la tabla entera en cada ficha de rival que
-- abre Kayzen. Con 624 filas cuesta 624 escrituras crearlo, una vez.
CREATE INDEX IF NOT EXISTS idx_link_b
  ON player_link(federation_b, lic_b);

CREATE INDEX IF NOT EXISTS idx_pm_player
  ON player_match(federation, lic, date);

CREATE INDEX IF NOT EXISTS idx_pm_opponent
  ON player_match(federation, opp_lic);          -- head-to-head

CREATE INDEX IF NOT EXISTS idx_im_local
  ON individual_match(player_local_lic);

CREATE INDEX IF NOT EXISTS idx_im_visitor
  ON individual_match(player_visitor_lic);

-- El SEGUNDO de una pareja de dobles vive en player_local_lic_2 /
-- player_visitor_lic_2. Sin índice, abrir su ficha desde el acta recorre la
-- tabla entera. Se paga solo en el último recurso (licencia sin fila en player
-- ni partido en player_match), así que no es urgente; el coste es una vez
-- (~1 escritura por fila al crearlo). Kayzen no los ata por nombre.
-- Ref: kayzen-agent/docs/PETICION-A-TT-SCRAPER.md §5bis (2026-10-07).
CREATE INDEX IF NOT EXISTS idx_im_local_2
  ON individual_match(player_local_lic_2);

CREATE INDEX IF NOT EXISTS idx_im_visitor_2
  ON individual_match(player_visitor_lic_2);

CREATE INDEX IF NOT EXISTS idx_enc_group
  ON encounter(competition_id, group_id, matchday_number);

-- Próximos partidos de un equipo (calendario): Kayzen filtra por equipo local o
-- visitante dentro de una competición. Sin estos índices cada consulta escanea
-- todos los encuentros de la liga. Kayzen depende de estos dos nombres (ver
-- design/PARA-KAYZEN-calendario-rfetm.md); avisar antes de renombrarlos.
CREATE INDEX IF NOT EXISTS idx_enc_local
  ON encounter(competition_id, team_local_id);

CREATE INDEX IF NOT EXISTS idx_enc_visitor
  ON encounter(competition_id, team_visitor_id);

-- Identidad: la PK de person_licence es (federation, lic), así que localizar una
-- licencia es gratis, pero "qué OTRAS licencias tiene esta persona" va por
-- person_id y sin índice escanea las ~7.345 filas en cada consulta. Kayzen cobra
-- por filas escaneadas: sin este índice no despliega la lectura de identidad.
-- Nombre fijado por Kayzen (kayzen-agent/docs/PARA-TT-SCRAPER-identidad.md §8.3).
CREATE INDEX IF NOT EXISTS person_licence_by_person
  ON person_licence(person_id);
