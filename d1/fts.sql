-- tt-scraper · búsqueda de jugadores (FTS5)
-- Ref: design/plan_migracion_d1.md §5 (decisión D). Sustituye al documento de
-- índice de 224 KB que hacía falta en Firestore.
--
-- Se crea DESPUÉS de cargar `player`, y se repuebla en cada carga (drop + rebuild:
-- ~7.400 filas, trivial). FTS5 autónoma (no external-content) con federation+lic
-- como columnas UNINDEXED, de modo que una búsqueda devuelve la identidad del
-- jugador directamente, sin JOIN. `remove_diacritics 2` = búsqueda sin acentos.

DROP TABLE IF EXISTS player_fts;

CREATE VIRTUAL TABLE player_fts USING fts5(
  name,
  club,
  federation UNINDEXED,
  lic UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO player_fts (name, club, federation, lic)
  SELECT name, club, federation, lic FROM player;
