# Plan — Migración de tt-scraper a Cloudflare D1

> Estado (24/08/2026): **backfill COMPLETO y verificado** (fases 1-5 ✅). Cargado
> FCTT+RTBTT+RFETM (rànquings, partits, liga nacional), liga FCTT en curso,
> `player_link` (312), **los 7 índices y la FTS**, e invariantes de integridad
> pasados. Siguiente etapa: **fase 6, el Worker de lectura** (§7). Ver "⏩ ESTADO
> ACTUAL Y PRÓXIMOS PASOS".
> Complementa a [`handover_cloudflare_d1.md`](handover_cloudflare_d1.md): el
> handover explica *por qué* D1 y da el esquema base; este documento fija las
> decisiones tomadas, el conteo real de cuota medido contra D1, la arquitectura
> de lectura/escritura y el plan de implementación por fases.

---

## 1. Decisiones tomadas (16/08/2026)

| # | Decisión | Elegido |
|---|---|---|
| A | Dónde vive la BD | **Una D1 nueva y separada en la misma cuenta de Cloudflare** que Kanto (la cuota de escritura es por cuenta, no por base — ver §4). En el plan gratuito no hay "proyectos" que aíslen cuentas; sí caben 10 bases D1. |
| B | Quién consume | **Kanto** (sobre todo rànquings) **y** una futura **app agéntica** (otro repo, Flutter, con su propio Firestore) que además leerá de esta D1. |
| C | API de lectura | **Worker nuevo dedicado**, con su binding a la D1, **ubicado en este monorepo junto al scraper** (p. ej. `tt-scraper/worker/`). Fuente única para los dos consumidores; deploy independiente; no acopla ni infla el Worker de Kanto (`apps/api`). |
| D | Búsqueda de jugadores | **FTS5 en D1** (server-side). Sustituye al documento de índice de 224 KB que hacía falta en Firestore. |
| E | Escritura del scraper | **Generar `.sql` y volcarlo con `wrangler d1 execute --file`.** Auditable y directo para la carga inicial; para la mensual, un wrapper fino sobre el mismo mecanismo. |
| F | Carga inicial vs cuota | Medido con probe remoto (§4): ~700k escrituras. No cabe en un día gratis → se **reparte en tandas por federación/edición a lo largo de varios días** (plan gratuito, sin coste). |
| G | Estado actual | **Nada vivo.** Corte limpio: cuando la D1 sirva a Kanto, se borra Firestore. No hace falta escritura dual. |

Y de las notas de dominio (confirmadas):

- **`competition.federation`** es columna, no constante en código: quién **organiza**
  (`organization`) no es la federación contra la que **resuelven las licencias**
  (`federation`). La territorial de Barcelona la organiza RTBTT pero se juega con
  licencia catalana → `rtbtt_2526.federation = 'FCTT'`. `individual_match` llega a
  la federación por `competition_id`. Verificado 40/40 por competición; **re-medir
  al cargar otra temporada**, no darlo por ley.
- **Siempre `LEFT JOIN`** partido→jugador/ranking: 237 jugadores con licencia
  juegan liga y no están en ningún ranking (sin clasificar o baja). Con `JOIN`
  desaparecerían del acta.
- **Un mismo partido de liga nacional vive en los dos espacios de licencia** — en
  `individual_match` de `rfetm_2526` con licencia española y en `player_match` de
  la FCTT con código catalán. **No es duplicación a corregir**: son dos vistas del
  mismo hecho y el material con el que deducir la equivalencia entre federaciones
  sin casar nombres (§6, `player_link`).

---

## 2. Lo que NO se toca

Los parsers, scrapers, modelos, tests (81) e invariantes de verificación son
independientes del almacenamiento y se quedan igual. Solo cambia la capa fina de
storage:

```
storage/firestore_client.py   →   storage/d1_client.py   (misma interfaz)
```

El CLI (`main.py`) y los scrapers no cambian: construyen el mismo objeto de
storage con otra implementación detrás.

---

## 3. Arquitectura

```
   PDFs y HTML de las federaciones
              │
              ▼
   tt-scraper (Python, cron en VPS o local)      ← parsers/scrapers intactos
              │  storage/d1_client.py
              │  genera un .sql por tabla/lote
              ▼
   wrangler d1 execute <db> --remote --file lote.sql
              │
   ┌──────────────────────────┐
   │  D1  «tt-data» (base propia, misma cuenta)  │
   └──────────┬───────────────┘
              │  binding de solo lectura
              ▼
   Worker de consulta dedicado  ──┬──►  Kanto (apps/api / app Flutter)
   (FTS5, endpoints de ficha,     └──►  App agéntica (otro repo)
    ranking, head-to-head)
```

- **Escritura**: solo el scraper Python, fuera de Cloudflare (es Python + `pdfplumber`,
  proceso por lotes de ~20 min; **no cabe en un Worker**). Emite `.sql` idempotente.
- **Lectura**: un Worker dedicado con el binding D1. Expone una API acotada; para
  el agente, además, una herramienta de consulta parametrizada (no SQL libre).
- **D1 corre en local con `wrangler` contra SQLite de verdad**, así que toda la
  carga y las consultas se prueban sin gastar cuota. Ojo: el modo `--local` **no
  reporta `rows_written`/`rows_read`** (solo `duration`); el conteo de cuota solo
  se ve en `--remote`.

### Una sola base, multi-temporada (no una por temporada)

`season` forma parte de la clave primaria de todas las tablas, así que el
multi-temporada es nativo (`WHERE season=?`). **No se hace una base por
temporada**, por tres razones:

1. **D1 no hace JOIN entre bases.** La trayectoria del jugador *a lo largo de los
   años* (evolución, mejor victoria histórica, cómo ha cambiado un rival desde el
   último cruce) es justo lo que da valor al agente, y exige leer varias
   temporadas juntas. Con una base por temporada eso serían N consultas + merge
   en cliente.
2. **La cuota de escritura es por cuenta**, no por base: shardear no la alivia.
   Y el plan gratuito solo da 10 bases, algunas ya en uso por Kanto.
3. **El tamaño no aprieta**: ~100 MB/temporada sobre un límite de 500 MB/base →
   caben ~5 temporadas. Cuando llegue ese día (años), la salida es Workers Paid
   (10 GB) o **archivar las temporadas frías** en una `tt-data-archive`,
   manteniendo juntas la actual y las recientes para las consultas cruzadas. Es
   una optimización futura y reversible.

**Nombre de la base**: `tt-data` (binding `TTDATA` o `DB` en el Worker de
consulta), **no** `tt-data-2526`. La antigua `ttwin-db` se borrará y no interfiere.

### Layout en disco por temporada

Hoy los PDFs cuelgan de `output/pdfs/` **sin temporada en la ruta**
(`PREF_G1_jornada21.pdf`), y los números de jornada se repiten cada temporada, así
que la siguiente machacaría el archivo de esta. Como esa carpeta **es el archivo**
(FCTT republica los PDFs y nada garantiza que las temporadas viejas sigan colgadas,
y sus URLs no llevan temporada — §5bis.3 del overview), se raíza todo por temporada:

```
output/
└── 2025-2026/
    ├── pdfs/      (actas, rankings/, partits/)
    ├── cache/     hashes.d1.json · hashes.firestore.json   ← por destino (ver §7/§9)
    └── logs/
```

`SEASON` ya está en el `.env` y alimenta `sources.py`; cablearlo en
`PDFS_DIR`/`CACHE_DIR` (config + `local_cache.py`) es un cambio pequeño. Beneficio
extra: la caché de hashes pasa a ser **por temporada y por destino**, lo que hace
estructuralmente imposible el gotcha de §9 (carga contra una base vacía saltándose
todo).

---

## 4. Cuota: conteo real medido contra D1 (16/08/2026)

Probe remoto con una D1 desechable (tabla de 5.000 filas, luego borrada):

| Operación | `rows_written` | Regla |
|---|---:|---|
| `INSERT` sin índices | 1 / fila | coste base |
| `INSERT` con 2 índices en la tabla | 9 por 3 filas | **1 + nº índices** por fila |
| `CREATE INDEX` (1 col) sobre 5.000 filas | 5.001 | **N escrituras** (una por fila existente) |
| `CREATE INDEX` compuesto (2 cols) | 5.001 | igual — **no depende de nº de columnas** |

**Fórmula de escritura**: `coste(fila) = 1 + nº_índices_de_su_tabla`.
Construir un índice sobre una tabla ya poblada cuesta **N filas escritas** (N = filas de *esa* tabla), por índice.

### PK compuesta → `WITHOUT ROWID` (medido 16/08/2026)

El probe anterior usaba PK `INTEGER` (rowid), pero **todas nuestras tablas tienen
PK compuesta/textual**, y eso crea un **índice implícito** (`sqlite_autoindex`):
cada inserción cuesta **2 escrituras** (fila + índice de PK) *antes* de cualquier
índice secundario. Medido en D1 con 200 filas:

| Tabla (PK compuesta) | rows_written |
|---|---:|
| Normal (con `sqlite_autoindex`) | **400** (2×) |
| `WITHOUT ROWID` | **200** (1×) |

Con `WITHOUT ROWID` la PK **es** el árbol de la tabla: no hay autoindex y la
inserción vuelve a costar 1 escritura/fila. Encaja porque nunca usamos rowid y la
FTS es autónoma. **Decisión: todas las tablas son `WITHOUT ROWID`** → el coste de
inserción del backfill se **halva** (~204k filas de A: 408k → 204k escrituras;
el backfill completo pasa de ~1M a ~0,6M). La fórmula queda
`coste(fila) = 1 + nº_índices_secundarios`.

### Cuotas D1 (plan gratuito, verificadas en el handover)

- **100.000 filas escritas / día**, por **cuenta** (se comparte con Kanto), reset a las 00:00 UTC.
- 5 M filas leídas / día · 500 MB por base · 10 bases. Los datos ocupan ~100 MB.

### Carga inicial (una vez)

| | Estimación |
|---|---:|
| Filas de datos | ~300.000 |
| Escrituras de construir índices (crear **después** del volcado) | ~400.000 |
| **Total** | **~700.000** |

No cabe en un día gratis (7-8 días a 100k/día). **Decisión (F): repartir el
volcado en tandas** por federación/edición a lo largo de varios días. Es una carga
única e idempotente (`ON CONFLICT`), así que trocearla no tiene coste ni riesgo;
los `.sql` quedan en disco y se reejecutan, y cada tanda ancla en un día de cuota
distinto (reset a las 00:00 UTC). La alternativa descartada era activar Workers
Paid el mes del backfill (~5 $, 700k dentro de los 50 M incluidos) — innecesaria
dado que el reparto es gratis.

> Crear los índices **después** del volcado sigue siendo lo correcto: no evita el
> coste (medido: N por índice), pero lo hace predecible, agrupado y espaciable, y
> acelera el `INSERT` masivo (sin mantener índices fila a fila).

### Régimen mensual

En SQL **insertamos solo las filas de la edición nueva** (`INSERT … ON CONFLICT`),
no reescribimos el histórico como obligaba el `merge` de Firestore. Con los índices
ya creados, el coste mensual es `filas_nuevas × (1 + índices)`:

- Nueva edición de rànquings (FCTT + RFETM) + `player_match` de la edición + actas
  nuevas ≈ **decenas de miles de filas**, cómodamente por debajo de 100k/día.

Recomendación operativa: mantener las tablas calientes a **≤ 2–3 índices** (cada
índice extra multiplica el coste de cada escritura), y si un mes se acercara al
límite, partir la ejecución en dos días — trivial por la idempotencia.

---

## 5. Esquema SQL refinado

Base el del handover §4, con los cambios acordados marcados **`← cambio`**.

```sql
-- ─────────────────────────────────────────────────────────── identidad
CREATE TABLE player (
  federation  TEXT NOT NULL,          -- 'FCTT' | 'RFETM'  (espacios de licencia distintos)
  lic         TEXT NOT NULL,          -- licencia/código tal cual la publica la fuente
  name        TEXT NOT NULL,
  club        TEXT,
  fa          TEXT,                   -- federación autonómica (solo RFETM)
  sex         TEXT,                   -- 'M' | 'F'
  birth_year  INTEGER,                -- solo RFETM
  cat         TEXT,                   -- categoría de edad declarada
  updated_at  TEXT,
  PRIMARY KEY (federation, lic)
);

-- ── puente entre federaciones (schema-ready; se puebla en fase posterior) ← cambio
CREATE TABLE player_link (
  federation_a TEXT NOT NULL, lic_a TEXT NOT NULL,   -- p.ej. FCTT
  federation_b TEXT NOT NULL, lic_b TEXT NOT NULL,   -- p.ej. RFETM
  method      TEXT NOT NULL,          -- 'nat_league_match'  (deducido, NUNCA por nombre)
  confidence  REAL,                   -- nº de partidos que confirman la equivalencia
  verified_at TEXT,
  PRIMARY KEY (federation_a, lic_a, federation_b, lic_b)
);

-- ─────────────────────────────────────────────────────────── rànquings
CREATE TABLE ranking_edition (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL,
  label TEXT, year INTEGER, month INTEGER,
  PRIMARY KEY (federation, season, edition_id)
);
CREATE TABLE ranking_snapshot (              -- una fila por jugador y edición
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  pts REAL, pts_prev REAL, d_pts REAL, rd REAL,
  pj INTEGER, pg INTEGER, pp INTEGER,
  p_any INTEGER,                       -- acumulado del año (solo FCTT)
  classified INTEGER,                  -- 0/1 (solo FCTT)
  by_event TEXT,                       -- JSON {llnn, c_esp, …} (solo RFETM)
  PRIMARY KEY (federation, season, edition_id, lic)
);
CREATE TABLE ranking_position (              -- una fila por jugador, edición y categoría
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  category TEXT NOT NULL, sex TEXT, pos INTEGER NOT NULL,
  PRIMARY KEY (federation, season, edition_id, lic, category)
);
-- Ojo: 99999 (RFETM) = "no compite en esa categoría", NO es posición → no insertar.

-- ──────────────────────────────── histórico partido a partido (FCTT: Partits_Jugats)
CREATE TABLE player_match (
  federation TEXT NOT NULL, season TEXT NOT NULL, edition_id TEXT NOT NULL, lic TEXT NOT NULL,
  seq INTEGER NOT NULL,                -- desempata repeticiones dentro de (lic, edición)
  date TEXT NOT NULL, event TEXT NOT NULL, event_type TEXT, category TEXT,
  opp_lic TEXT,                        -- NULL si el rival no tiene licencia de esa federación
  opp_name TEXT NOT NULL, opp_external INTEGER DEFAULT 0,
  sets INTEGER, opp_sets INTEGER, won INTEGER,
  rk REAL, opp_rk REAL, d_rk REAL, rd REAL,   -- rating de cada uno EN EL MOMENTO del partido
  PRIMARY KEY (federation, season, edition_id, lic, seq)
);

-- ─────────────────────────────────────────────────────────── competición
CREATE TABLE competition (
  id TEXT PRIMARY KEY,                 -- 'fctt_2526', 'rtbtt_2526', 'rfetm_2526'
  organization TEXT,                   -- quién organiza (RTBTT, FCTT, RFETM)
  federation   TEXT NOT NULL,          -- ← cambio: federación contra la que resuelven las licencias
  season TEXT, name TEXT, source_url TEXT
);
CREATE TABLE comp_group (
  competition_id TEXT NOT NULL, category_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, phase TEXT, source_url TEXT,
  PRIMARY KEY (competition_id, category_id, id)
);
CREATE TABLE team (
  competition_id TEXT NOT NULL, group_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, club TEXT, position INTEGER, points INTEGER,
  played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER,
  PRIMARY KEY (competition_id, group_id, id)
);
CREATE TABLE matchday (
  competition_id TEXT NOT NULL, group_id TEXT NOT NULL, number INTEGER NOT NULL,
  date TEXT, status TEXT, pdf_url TEXT, pdf_hash TEXT,
  PRIMARY KEY (competition_id, group_id, number)
);
CREATE TABLE encounter (
  id TEXT NOT NULL, competition_id TEXT NOT NULL, group_id TEXT NOT NULL,
  matchday_number INTEGER NOT NULL, date TEXT, status TEXT, acta_url TEXT,
  team_local_id TEXT, team_local_name TEXT, score_local INTEGER,
  team_visitor_id TEXT, team_visitor_name TEXT, score_visitor INTEGER,
  PRIMARY KEY (competition_id, id)
);
CREATE TABLE individual_match (
  competition_id TEXT NOT NULL, encounter_id TEXT NOT NULL, ord INTEGER NOT NULL,  -- ord = puesto 1..5
  player_local_lic TEXT, player_local_name TEXT,
  player_visitor_lic TEXT, player_visitor_name TEXT,
  winner TEXT, sets TEXT,               -- sets como JSON [{local, visitor}, …]
  PRIMARY KEY (competition_id, encounter_id, ord)
);
-- La federación de las licencias de individual_match se obtiene por
-- competition_id → competition.federation (LEFT JOIN a player).
```

### Índices y FTS — crear **después** del volcado inicial

```sql
CREATE INDEX idx_pos_top     ON ranking_position(federation, season, edition_id, category, sex, pos);
CREATE INDEX idx_snap_player ON ranking_snapshot(federation, lic, edition_id);
CREATE INDEX idx_pm_player   ON player_match(federation, lic, date);
CREATE INDEX idx_pm_opponent ON player_match(federation, opp_lic);         -- head-to-head
CREATE INDEX idx_im_local    ON individual_match(player_local_lic);
CREATE INDEX idx_im_visitor  ON individual_match(player_visitor_lic);
CREATE INDEX idx_enc_group   ON encounter(competition_id, group_id, matchday_number);

-- Búsqueda (decisión D): FTS5 server-side en el Worker de consulta
CREATE VIRTUAL TABLE player_fts USING fts5(name, club, content='player');
```

> Recordatorio de coste (§4): cada índice sube el coste de cada `INSERT` futuro en
> +1 fila. `player_match` (2 índices) e `individual_match` (2 índices) son las
> tablas calientes; no añadir un tercero sin justificarlo.

---

## 6. Puente entre federaciones (`player_link`) — ✅ poblado (22/08/2026)

> Implementado en `d1/build_player_link.py` (votación por firmas fecha+marcador
> con emparejamiento mutuo). 312 enlaces 1↔1 cargados. Ver §7 "Estado de
> implementación" para el detalle y la validación.

La equivalencia licencia RFETM ↔ código FCTT se **deduce de los datos**, nunca por
nombre: los partidos de liga nacional aparecen en `individual_match` de `rfetm_2526`
(licencia española) y en `player_match` de la FCTT (código catalán) con el mismo
día, marcador y ratings. Cruzándolos sale la equivalencia sin ambigüedad.

- La tabla existe desde el inicio (schema-ready) para no migrar esquema después.
- Se **puebla en fase posterior**, cuando esté cargada la **fase 2 de RFETM**
  (`accion=5`, el partido a partido español), que es la otra mitad del cruce.
- Producto: permite que la ficha de un rival catalán que juega la nacional muestre
  sus dos rànquings y su historial completo en vez de dos jugadores inconexos.

---

## 7. Plan de implementación por fases

Los pasos 1–5 no tocan ni un parser (solo storage + esquema + carga).

| # | Fase | Qué | Toca |
|---|---|---|---|
| 1 | Esquema | `schema.sql` (§5 sin índices) + `indexes.sql` aparte. Crear la D1 `tt-data` en la cuenta. Probar en `--local`. | nuevo SQL |
| 2 | Storage | `storage/d1_client.py` con la interfaz de `firestore_client.py`, emitiendo `.sql` idempotente (`INSERT … ON CONFLICT(pk) DO UPDATE`). `main.py` selecciona backend. | `storage/`, `main.py` (mínimo) |
| 3 | Carga sin red | Volcado completo desde los PDFs ya en disco (201 MB) → `.sql` por tabla/lote. **Empezar con caché vacía para el destino D1** (ver §9): con la caché por destino, `hashes.d1.json` no existe aún y no hay que acordarse de nada; sin ella, `mv output/cache/hashes.json output/cache/hashes.firestore.json` a mano. Los grupos HTML (FCTT/RFETM liga) sí necesitan red. | ejecución + `local_cache.py` (layout §3) |
| 4 | Índices + FTS | Tras el volcado, ejecutar `indexes.sql` y crear `player_fts` (+ `INSERT INTO player_fts SELECT …`). Espaciado según §4. | ejecución |
| 5 | Verificación | Re-pasar los invariantes (`python main.py fctt verify`) y comparar totales fila a fila con lo que había en Firestore (jugadores, filas de ranking, partidos). | tests |
| 6 | Worker de lectura | Worker dedicado nuevo (repo/carpeta a decidir) con binding a `tt-data`: endpoints de ficha de jugador, ranking por categoría/edición, head-to-head, alineación por equipo, y **búsqueda FTS5**. Lo consumen Kanto y la app agéntica. | nuevo Worker |
| 7 | Consumo en Kanto | Kanto llama al Worker de lectura para rànquings/resultados. | `apps/mobile` / `apps/api` |
| 8 | Retirar Firestore | Con el Worker en producción y verificado, borrar los datos de Firestore. | limpieza |

Backfill inicial (paso 3–4): **tandas repartidas en varios días** (decisión F, §4).
El régimen mensual posterior es gratuito.

### ⏩ ESTADO ACTUAL Y PRÓXIMOS PASOS (21/08/2026) — leer esto primero

**Contexto operativo para continuar en una sesión nueva:**
- D1 de destino: **`tt-data`** (`database_id 748eb38c-…`), cuenta Cloudflare
  `lenambac@gmail.com`. Wrangler: `apps/api/node_modules/.bin/wrangler`, ejecutar
  desde cwd `apps/api`. Red IPv6 rota → **siempre** `NODE_OPTIONS="--dns-result-order=ipv4first"`.
- **Cuota diaria compartida con Kanto producción** (misma cuenta), reset **00:00 UTC**
  (02:00 CEST). Estrategia: **repartir en tandas, una por día**. Los volcados de
  190-211k escrituras/día no han disparado bloqueo, pero el paso de índices es
  mucho mayor (~½ millón) → **darle su propio día**.
- Scrapear a `.sql`: `STORAGE_BACKEND=d1 ./venv/bin/python main.py <comando>` (desde
  `tt-scraper/`). Cargar: `NODE_OPTIONS=… python3 d1/load_chunks.py output/sql/<f>.sql
  --db tt-data` (troceo 2.500/lote, reanudable con `--start N`, para en cuota).
  La caché ya está separada por destino (`hashes.d1.json`) → RFETM/FCTT no se saltan.

**Ya cargado y verificado en `tt-data`** (conteos exactos, sin bloqueo de cuota):
| Tanda | Fecha | Resultado |
|---|---|---|
| Rànquings FCTT (9 ed.) + RTBTT completo | 18/08 | ranking_edition 10, player 2770, snapshot 24361, position 82795 |
| Partits FCTT | 19/08 | `player_match` 100.152 (todas FCTT) |
| Rànquings RFETM (10 meses) | 20/08 | player 7.121, edition 20, snapshot 62.705, position 146.077 |
| Partidos liga nacional RFETM (`rfetm run`) | 21/08 | individual_match 28.966 (rtbtt 5.607 + **rfetm 23.359**), encounter 4.855, team 370, matchday 796, comp_group 56, competition 2 |
| Liga FCTT en curso (`fctt run`) | 22/08 | competition +1 (**fctt_2526**), comp_group 5, team 52, matchday 78, encounter **509**, individual_match **3.022** → totales: competition 3, individual_match 31.988 |
| Puente `player_link` (FCTT↔RFETM) | 22/08 | **312 enlaces** 1↔1 (0 dup en ambas direcciones), confianza 6–150. Deducidos por liga nacional (fecha+marcador), NO por nombre. 624 escrituras. |

> Nota `fctt run` (22/08): el scrape (~21 min, 0 errores) emitió 510 encounter /
> 3.027 individual_match, pero un encuentro salió **duplicado** en el `.sql`
> (`fctt_2526 | 2_ctt_els_amics_terrassa_ctt_castellgali`, con sus 5 individuales);
> el upsert `ON CONFLICT` lo dedupe → 509 / 3.022 en base. No es pérdida: es
> idempotencia. Carga: `load_20260822_092456.sql`, 2 lotes, 7.346 escrituras D1.

**Backfill COMPLETO (24/08/2026).** Índices + FTS ✅ y `verify` ✅ (detalle en
"Estado de implementación"). Los 7 índices y la FTS creados; invariantes de
integridad pasados (0 duplicados de snapshot, 0 pos=99999, 0 huérfanos,
FTS==player, player_link 1↔1).

**Siguiente etapa (ya no es carga): fase 6, el Worker de lectura** (§7, tabla de
fases): Worker dedicado con binding a `tt-data` que exponga ficha de jugador,
ranking por categoría/edición, head-to-head, alineación por equipo y **búsqueda
FTS5**; lo consumen Kanto y la app agéntica. Luego fase 7 (consumo en Kanto) y
fase 8 (retirar Firestore).

**Nota de commit**: `d1/load_chunks.py` (loader) y `d1/build_player_link.py`
(generador del puente) están **sin commitear**, igual que las ediciones de este
plan; `output/` está gitignored. El usuario aún no ha pedido commit de tt-scraper.

---

### Estado de implementación

- **Fase 1 ✅** — `d1/{schema,indexes,fts}.sql` escritos y validados en `--local`.
  D1 `tt-data` creada (`database_id 748eb38c-001f-4de1-9b9a-b38fdf6865c8`) con el
  esquema aplicado (tablas; índices y FTS tras el volcado).
- **Fase 2 ✅** — `storage/d1_client.py` (misma interfaz que `FirestoreClient`,
  emite `.sql` idempotente `ON CONFLICT`), `storage/__init__.py:make_storage()`
  (selector de backend por `STORAGE_BACKEND=d1|firestore`), `config.py`
  (`storage_backend`, `sql_out_dir`). Validado end-to-end contra una D1 local.
- **Fase 3 🚧 en curso**:
  - **Caché por destino ✅** — `LocalCache(backend=…)` usa `hashes.<backend>.json`;
    `hashes.json` renombrado a `hashes.firestore.json`. Una carga a D1 arranca con
    caché vacía por construcción → el gotcha de §9 queda cerrado.
  - **Layout por temporada** — diferido (archivístico; el `.env` fija `CACHE_DIR`/
    `PDFS_DIR` planos y no bloquea cargar esta temporada, cuyos PDFs ya están en
    disco). Pendiente al pasar a la 2026-2027.
  - **Pipeline real validado ✅** — `STORAGE_BACKEND=d1 python main.py fctt rankings
    --edition 10` (offline, desde PDFs en disco) emitió un `.sql` de 5 MB con
    2769 `player` + 2769 `ranking_snapshot` + 9703 `ranking_position`; cargado en
    SQLite (motor de D1) los conteos, el TOP derivado de `ranking_position` y el
    invariante `snapshot==distinct(lic)` cuadran. Nota: wrangler `--local` tiene un
    bug de miniflare con ficheros grandes → para la carga remota, **trocear el
    `.sql` en tandas** (que además es la unidad de reparto por cuota, §4).
  - **Esquema `WITHOUT ROWID` ✅** — adoptado tras medir el 2× de la PK compuesta
    (arriba). `tt-data` recreada; inserción al 1×.
  - **Cargas remotas hechas ✅** — en `tt-data`: rànquings FCTT **edición 10**
    (2769 player · 2769 snapshot · 9703 position) y **RTBTT completo** (1 comp ·
    24 grp · 188 md · 907 enc · 5607 im, 165 dobles). Verificado en remoto.
  - **`.sql` de A generados ✅** (offline, `output/sql/`, gitignored): rànquings
    FCTT (9 ed., ~97k filas), RTBTT (~6,7k), partits FCTT (~100k). 0 errores.
  - **Rànquings FCTT completo cargado ✅ (18/08/2026)** — `load_20260816_142532.sql`
    (97.460 stmts) volcado en remoto con `d1/load_chunks.py` (troceo 2.500/lote,
    reintento de transitorios). Verificado por conteo exacto: ranking_edition 10,
    player 2770, ranking_snapshot 24361 (=21592+2769 ed.10), ranking_position 82795
    (=73092+9703). Notas: D1 reporta ~2 escrituras/fila en snapshot/position (coste
    del upsert, no del esquema: las tablas son `WITHOUT ROWID`); el volcado (~190k
    reported) **no** disparó el límite de cuota diaria.
  - **Loader reutilizable**: `tt-scraper/d1/load_chunks.py <sql> --db tt-data
    [--start N]`. Idempotente y reanudable (para en cuota, retoma con `--start`).
  - **Partits FCTT cargados ✅ (19/08/2026)** — `load_20260816_145031.sql`
    (100.152 stmts) volcado en remoto con el mismo loader (41 lotes). Verificado:
    `player_match` = 100.152 filas (baseline 0), todas FCTT, 2.975 jugadores,
    temporada 2025-2026 (2025-09-06 → 2026-06-13). D1 reportó ~200.304 escrituras
    (2×/fila por el upsert; `player_match` es `WITHOUT ROWID`); **no** disparó la
    cuota diaria.
  - **Rànquings RFETM cargados ✅ (20/08/2026)** — scrapeado de rfetm.es con
    `STORAGE_BACKEND=d1 python main.py rfetm rankings` (10 meses, 0 errores) →
    `load_20260820_091255.sql` (105.987 stmts) volcado con el loader (43 lotes).
    Verificado por conteo exacto: player 7.121 (2.770 FCTT + 4.351 RFETM),
    ranking_edition 20 (10+10), ranking_snapshot 62.705 (24.361+38.344),
    ranking_position 146.077 (82.795+63.282). D1 reportó ~211.974 escrituras;
    **no** disparó la cuota diaria.
  - **Partidos liga nacional RFETM cargados ✅ (21/08/2026)** — scrapeado de
    rfetm.es con `STORAGE_BACKEND=d1 python main.py rfetm run` (32 grupos, 608
    jornadas, 3.948 encuentros, 23.359 partidos individuales, 0 errores; ~56 min)
    → `load_20260821_083721.sql` (28.318 stmts) volcado con el loader (12 lotes,
    un fallo de red transitorio reintentado OK). Verificado por conteo exacto:
    competition 2, comp_group 56 (24+32), team 370, matchday 796 (188+608),
    encounter 4.855 (907+3.948), individual_match 28.966 (rtbtt 5.607 + rfetm
    23.359). D1 reportó ~56.636 escrituras; **no** disparó la cuota diaria.
  - **Liga FCTT en curso cargada ✅ (22/08/2026)** — scrapeado de fctt.cat con
    `STORAGE_BACKEND=d1 python main.py fctt run` (2 categorías, 5 grupos, 78
    jornadas, 510 encuentros, 3.027 partidos individuales, 0 errores; ~21 min) →
    `load_20260822_092456.sql` (3.673 stmts) volcado con el loader (2 lotes, un
    fallo de red transitorio reintentado OK; 7.346 escrituras D1). Verificado:
    competition 3 (+fctt_2526), individual_match 31.988. Un encuentro con PK
    duplicada en el `.sql` deduplicado por `ON CONFLICT` (509/3.022 en base, no es
    pérdida).
  - **`player_link` poblado ✅ (22/08/2026)** — nuevo `d1/build_player_link.py`:
    lee las firmas `(fecha, sets, sets_rival)` de `player_match` LLNN (FCTT) y de
    `individual_match` (RFETM, tally vía `json_each`), vota y acepta solo el par
    con **emparejamiento mutuo** + margen (el mutuo descarta compañeros de equipo,
    que comparten fechas). **312 enlaces** 1↔1 (0 duplicados), confianza 6–150,
    `method='nat_league_match'`. Validación por nombre (informativa): 285/285
    verificables correctos — las 22 "discrepancias" eran acentos/orden/
    transliteración (DÍEZ/DIEZ, DOS SANTOS…), justo donde el cruce por nombre
    fallaría: confirma el "nunca por nombre". 624 escrituras.
  - **FTS + 4 índices ✅ (22/08/2026)** — `player_fts` reconstruida desde `player`
    (7.121 filas; búsqueda sin acentos OK: `MATCH 'barreneche'` devuelve las dos
    federaciones). Índices creados: `idx_enc_group`, `idx_im_local`,
    `idx_im_visitor`, `idx_snap_player` (verificados en `sqlite_master`;
    ~132k escrituras). Total del día ~147k (fctt run + player_link + FTS + estos 4),
    dentro del margen seguro observado (~200k).
  - **`idx_pos_top` ✅ (23/08/2026)** — `ranking_position` (~146k, día propio;
    verificado en `sqlite_master`). Ya hay 5 índices creados.
  - **Índices completos + `verify` ✅ (24/08/2026)** — creados `idx_pm_player` e
    `idx_pm_opponent` (`player_match`, ~100k c/u) → **7 índices** en total
    (`idx_enc_group`, `idx_im_local`, `idx_im_visitor`, `idx_snap_player`,
    `idx_pos_top`, `idx_pm_player`, `idx_pm_opponent`) + FTS. **Verify (solo
    lectura) OK**: snapshot 62.705 sin duplicados por (fed,edición,lic);
    0 `ranking_position` con pos=99999; 0 `individual_match`/`encounter` huérfanos;
    `player_fts`==`player` (7.121); `player_link` 1↔1 (312, 0 dup). Snapshot final:
    competition 3 · player 7.121 · ranking_edition 20 · snapshot 62.705 ·
    position 146.077 · player_match 100.152 · individual_match 31.988 ·
    encounter 5.364 · links 312.
  - **Backfill terminado.** Siguiente: fase 6 (Worker de lectura).

Refinamientos hechos al implementar la fase 2:

1. **Dobles en `individual_match`**: el modelo `IndividualMatch` lleva un partido
   de dobles (RTBTT, decisor de 3-3) con dos jugadores por lado. Se añadieron
   columnas nullable (`is_doubles`, `player_*_lic_2`, `player_*_name_2`) para no
   perder al segundo jugador.
2. **`save_ranking_root` y `save_player_records` son no-op en D1**: son
   denormalizaciones de Firestore derivables con `COUNT`/`MAX`/`SUM` sobre las
   tablas (`player_count`, `last_edition`, el `record` de temporada).
3. **`player_fts` autónoma** (no `content='player'`): con `federation`/`lic`
   `UNINDEXED`, una búsqueda devuelve la identidad sin JOIN; `remove_diacritics`.
4. `federation` y `season` se derivan del `competition_id` (`rtbtt_*`/`fctt_*`→FCTT,
   `rfetm_*`→RFETM; `_2526`→`2025-2026`).

---

## 8. Cabos sueltos y trabajo diferido

Heredado de `todos_a_revisar.md` y los planes; en SQL varios se simplifican:

1. **`player_link` (§6)** — poblar tras la fase 2 de RFETM.
2. **Fase 2 de RFETM** (`accion=5`): ~10.000 peticiones filtrando por quien jugó,
   ~4 h. Iguala la RFETM con la FCTT en detalle partido a partido.
3. **Marcador dudoso de `Partits_Jugats`** (0,27 %, §10 de todos): en SQL se
   reconcilia con el ranking del mes con un `JOIN`, sustituyendo la heurística.
4. **Alias de club** por voto mayoritario: en SQL es una tabla `club_alias` + `JOIN`.
   Habilita buscar por club.
5. **Calendario futuro de la RTBTT**: FCTT (`scheduled`) y RFETM (`pending`) entran
   solos; la RTBTT no (su fuente son actas de lo ya jugado). Sin él no hay "próximo
   encuentro" en la territorial ni predicción de alineación ahí.
6. **Clasificación de RTBTT**: la fuente no la publica; decidir entre calcularla o
   ocultar la pestaña. No es un fallo del parser.
7. **PDF ya descargado no se refresca** (actas corregidas): comparar `Last-Modified`/
   `ETag` o caducar las últimas N jornadas.
8. **Re-medir RTBTT→FCTT (40/40)** al cargar otra temporada.

---

## 9. Notas de operación

- **Día de cuota = 00:00 UTC** (en verano, 02:00 CEST). Lanzar de madrugada mete la
  tanda en la cuota del día anterior.
- **`--local` no mide cuota** (`rows_written` solo en `--remote`): probar la lógica
  en local, medir cuota solo cuando importe.
- Idempotencia total: todas las tablas con PK natural + `ON CONFLICT`.
- **El gotcha de la caché de hashes (crítico).** `output/cache/hashes.json` guarda
  ~1.111 hashes que dicen "esto ya está cargado". Ese "ya está cargado" es falso
  por partida doble para D1: (1) registra lo que fue a **Firestore**, no a D1, y
  (2) es el mecanismo que hace que el scraper se salte lo "sin cambios". Si se
  copia tal cual y se lanza la carga contra una D1 recién creada, **el scraper se
  salta casi todo y la base queda vacía, sin un solo error.**
  - *Fix inmediato* (el que indica la otra sesión): antes de la primera carga,
    `mv output/cache/hashes.json output/cache/hashes.firestore.json`.
  - *Fix estructural* (§3): guardar la caché **por temporada y por destino**
    (`hashes.d1.json` vs `hashes.firestore.json`). Así la carga a D1 arranca con
    caché vacía por construcción y este bug no puede repetirse ni depender de
    acordarse del `mv`.
- La caché sí se conserva (una vez creada) para no reprocesar origen sin cambios;
  la separación por destino es lo que evita que "sin cambios en Firestore" se lea
  como "sin cambios en D1".
- Los `.sql` generados son el artefacto auditable de cada carga: guardarlos.
