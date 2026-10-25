-- tt-data · migración: terreny de joc / lugar + árbitro por encuentro.
-- Ref: petición "guardar direcciones de los equipos y árbitro".
--   venue   = Terreny de joc (FCTT) / Lugar (RFETM): dirección donde se juega
--             (la pista del equipo LOCAL). Útil para el visitante que se desplaza.
--   referee = Àrbitre (FCTT) / Árbitro (RFETM): solo categorías nacionales, y no
--             siempre (en provinciales no hay).
-- Aditivo y no destructivo. SQLite no tiene "ADD COLUMN IF NOT EXISTS": si ya
-- se aplicó, estas sentencias fallan con "duplicate column name" (se puede ignorar).
ALTER TABLE encounter ADD COLUMN venue TEXT;
ALTER TABLE encounter ADD COLUMN referee TEXT;
