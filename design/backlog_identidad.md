# Backlog · identidad de persona (tt-scraper)

> Mejoras sobre la capa de identidad ya en producción
> (`design/plan_identidad_persona.md`). Actualizado 2026-10-25: aplicados los
> puntos 3, 4 y 5; el 1 lo cubre `run_all.sh`; el 2 depende de Kayzen; el 6 queda.

## 1. Automatizar el encadenado periódico — HECHO (falta programarlo)

`scripts/run_all.sh` lanza las cargas semanales y, de paso, `weekly_identity.sh`
(y el report). Con esto el encadenado periódico **existe**: cuando corra solo
(varias veces/día), Kayzen podrá leer solo `person_licence` y retirar su escalón
propio (PARA-TT-SCRAPER-identidad.md §8.2). **Queda**: programarlo (cron) y
llevarlo a otro ordenador (fuera de alcance por ahora, a petición).

## 2. Canal para los descartes (`rejected`) de Kayzen → `identity_overrides.csv` — LISTO EN NUESTRO LADO, pendiente de Kayzen

El mecanismo está (paso 6): `build_person` aplica las líneas `...,rejected,
admin:…` del CSV y suben a `person_licence` con `status='rejected'`. **Falta** que
Kayzen EXPORTE sus pares `descartado` en ese formato (su §8.2). Nada que
implementar aquí hasta tener su export; el formato está en
`d1/identity_overrides.csv`.

## 3. Unificar la representación de club (código ↔ nombre) — HECHO

`build_person` ya usa el club de TEMPORADA (`player_club_season`, un nombre en
ambas federaciones) como club efectivo en la fase `probable`, con fallback a
`player.club` + `club_link`. Así deja de comparar el código FCTT contra un nombre
RFETM. Efecto medido: los `probable` suben de 443 a **458** y se cazan 2 ambiguos
nuevos (más clubes resueltos por nombre que con los 35 códigos de `club_link`).
Pendiente opcional: un club canónico por federación (tabla `club`) unificaría
también `clubs>1` (punto 6).

## 4. Revertir filas `manual` al quitar una línea del CSV — HECHO

`build_person` detecta las filas `decided_by='admin:…'` que ya no están en el CSV
(`revert_lics`) y las reescribe sin la guarda, devolviéndolas a su estado
deducido. (Limitación: si la fila manual había FUSIONADO dos personas, el alias
de la fusión queda; es un caso raro y el alias es inocuo.)

## 5. Medir la grieta "mismo nombre, mismo pueblo, club con palabra común" — HECHO

`measure_name_club.py` reporta los candidatos `probable` cuyo club casa por UNA
sola palabra significativa (subconjunto de tamaño 1). Medido: **31/653 (4,7 %)**,
casi todos abreviaturas legítimas del mismo club (PARETS ≈ TT PARETS); la grieta
real es pequeña y la precisión se mantiene en 99,5 %.

## 6. Club canónico por federación / colapsar variantes de patrocinador — QUEDA (MEDIA)

~439 licencias salen con >1 club en una temporada, en buena parte por variantes
de nombre con patrocinador entre competiciones (EUROCLIMA BADALONA ↔ CTT
BADALONA), no por préstamo. Una tabla `club` canónica por federación (+
equivalencias) colapsaría esas variantes, haría que `clubs>1` signifique de
verdad "más de un club" y cerraría del todo el punto 3. Coste medio; no urgente.

## Fuera de alcance (bloqueado por la fuente)

- **`birth_year` de la FCTT** (paso 4): la FCTT no lo publica (la columna `NAC`
  del ranking inicial es nacionalidad). No reintentar sin una fuente nueva.
