-- tt-data · migración: PF/PC (partidos individuales a favor/en contra) en la
-- tabla de clasificación de equipos. Sirven de desempate cuando dos equipos
-- tienen los mismos encuentros ganados/perdidos.
-- Aditivo y no destructivo. SQLite no tiene "ADD COLUMN IF NOT EXISTS": si ya
-- se aplicó, estas sentencias fallan con "duplicate column name" (ignorable).
ALTER TABLE team ADD COLUMN games_for INTEGER;
ALTER TABLE team ADD COLUMN games_against INTEGER;
