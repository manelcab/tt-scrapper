# tt-scraper — traspaso al proyecto Cloudflare

> Redactado el 16/08/2026 para que otra sesión, sin ningún contexto previo,
> pueda incorporar `tt-scraper` a un proyecto Cloudflare existente y sustituir
> Firestore por D1.
>
> Documentos hermanos, en esta misma carpeta:
> - [`functionalities_overview.md`](functionalities_overview.md) — **la referencia**: cada fuente, cada parser, cada trampa, el modelo actual y los comandos.
> - `Concepto UX-UI — Aplicación Agéntica de Tenis de Mesa.md` §27 — qué permite hacer este dato en la app. Vive en el repo `gravity`, en `kayzen-agent/design/`: es el documento de producto de la app y allí está la copia única.
> - [`plan_partits_jugats.md`](plan_partits_jugats.md) y [`plan_rankings_rfetm.md`](plan_rankings_rfetm.md) — por qué cada modelo es como es, con las alternativas descartadas y sus números.
> - [`todos_a_revisar.md`](todos_a_revisar.md) — deuda técnica y trabajo pendiente acordado.

---

## 1. Qué es tt-scraper, en una página

Un scraper en **Python** que carga datos de tenis de mesa de tres federaciones y
los publica en una base de datos para que los consuma una aplicación.

| Fuente | Qué se saca | Formato |
|---|---|---|
| **RTBTT** — territorial de Barcelona | 5 categorías, actas con partidos individuales | PDF por jornada |
| **FCTT** — federación catalana | TDM (3 grupos) + Copa Catalana Femenina (2), rànquings mensuales, **histórico partido a partido** | HTML + PDF |
| **RFETM** — federación española | Liga nacional (32 grupos), rànquings mensuales | HTML |

Volumen actual cargado, temporada 2025-2026:

```
7.358 jugadores          (3.007 FCTT + 4.351 RFETM)
~120.000 filas de ranking
~50.000 partidos con rival, marcador, rating de ambos y puntos ganados
5.365 encuentros de liga con ~27.000 partidos individuales
```

**La pieza que lo une todo:** las actas de las tres ligas guardan la licencia de
cada jugador en cada partido individual, y todas resuelven contra el ranking de
su federación (verificado sobre 120 licencias, 100 %). Desde cualquier partido
se llega a la ficha completa del jugador sin casar nombres nunca.

### Lo que NO hay que tocar al migrar

Los parsers y las comprobaciones son el 90 % del trabajo hecho y son
independientes del almacenamiento:

- `parsers/` — un módulo por fuente, con sus trampas documentadas.
- `scrapers/` — orquestación, caché por hash de contenido, reintentos.
- `tests/` — 81 tests, que fijan cada trampa encontrada.
- Los **invariantes de verificación**, que ya han cazado errores reales: el
  marcador de un encuentro tiene que ser la suma de sus individuales, y los
  puntos que un jugador gana en un mes tienen que ser la suma de los de sus
  partidos (`python main.py fctt verify`).

Lo único que cambia es `storage/firestore_client.py`, que es una capa fina.

---

## 2. Por qué se cambia a D1

No es preferencia de tecnología: es que el modelo es **relacional** y forzarlo en
un almacén de documentos ha costado, punto por punto, esto:

| Lo que pasó con Firestore | En SQL |
|---|---|
| Cuota diaria de escrituras agotada dos veces; la carga histórica (20.045) no cabía en un día | Un `INSERT` masivo |
| `Transaction too big`: hubo que trocear lotes **por número de campos hoja**, porque indexa cada campo anidado | No existe el problema |
| Dos exenciones de índice para que `history` y `match_log` no generaran 1,5 M de entradas; la primera se creó mal **sin dar error** | `CREATE INDEX` sobre lo que se consulta |
| Renombrar categorías `40M` → `V40M`: una ruta de campo **no puede empezar por dígito** y sin escapar la consulta devuelve **cero resultados en silencio** | Un valor de columna |
| Meter el sexo en la clave (`NAC_M`) porque filtrar y ordenar a la vez exigiría un índice compuesto por categoría | `WHERE category=? AND sex=? ORDER BY pos` |
| Un documento de 224 KB como sucedáneo de búsqueda de texto | `LIKE` o FTS5 |
| "¿Sus victorias son contra rivales inferiores?" obliga a descargar los partidos y calcular en el cliente | `SELECT AVG(opp_rk - rk) … WHERE won=1` |

Y para un producto **agéntico** pesa aún más: con SQL se le puede dar al agente
una herramienta de consulta acotada y que formule preguntas que nadie anticipó.
Con Firestore solo puede leer lo que alguien decidió denormalizar de antemano.

> **Importante para quien lea el código:** el renombrado de categorías
> (`40M`→`V40M`, `21F`→`S21F`) y las claves con sexo (`NAC_M`) son deuda de
> Firestore, no del dominio. En SQL sobran. Se recomienda **mantenerlas igualmente**
> por continuidad con lo ya cargado y porque son ids legibles, pero conviene
> saber que su origen es una limitación que ya no aplica.

---

## 3. Cuotas reales de D1 (verificadas el 16/08/2026)

| | Workers Free | Workers Paid (~5 $/mes) |
|---|---|---|
| Filas leídas | 5 M / día | 25.000 M / mes incluidas, luego 0,001 $/M |
| Filas escritas | **100.000 / día** | 50 M / mes incluidas, luego 1 $/M |
| Bases de datos por cuenta | 10 | 50.000 |
| Tamaño máximo por base | **500 MB** | 10 GB |
| Almacenamiento total | 5 GB | 1 TB |

**Dos matices que cambian las cuentas y que hay que tener presentes:**

1. **Los límites son por cuenta, no por base de datos.** Usar una base D1
   separada —que es lo recomendable, y caben 10 en el plan gratuito— aísla el
   esquema y el radio de daño, pero **la cuota se comparte con el otro
   proyecto**. Se reinicia a las 00:00 UTC.
2. **Los índices cuentan como filas escritas.** La documentación es explícita:
   cada escritura que afecte a una columna indexada suma una fila escrita
   adicional. Con 3 índices por tabla, insertar 300.000 filas se acerca al
   **millón de filas escritas**.

### Qué implica

| | Filas de datos | Con índices (~×4) | En plan gratuito |
|---|---:|---:|---|
| **Carga inicial** | ~300.000 | ~1.200.000 | ~12 días, o crear los índices después del volcado |
| **Carga mensual** | ~30.000 | ~120.000 | **cabe en un día** |

O sea: **el régimen mensual vive cómodamente en el plan gratuito**; la carga
inicial no. Dos salidas, y la primera es la buena:

- **Crear los índices después del volcado inicial.** Es la práctica habitual en
  SQLite y evita el multiplicador. Pero **hay que medirlo antes de contar con
  ello**: ver más abajo.
- Activar Workers Paid un mes. 1,2 M de escrituras están dentro de los 50 M
  incluidos, así que el coste sería el de la suscripción, no del uso.

### Cómo medir el coste real (experimento pendiente)

D1 devuelve `rows_read` y `rows_written` **en la respuesta de cada consulta**, así
que la pregunta se contesta midiendo, no razonando.

> **Ojo: hay que hacerlo contra una base remota, no con `wrangler --local`.**
> En local se ejecuta SQLite a secas: dirá si el SQL es correcto, pero el
> multiplicador por índice y la contabilidad del `CREATE INDEX` son
> comportamientos de la plataforma D1, no de SQLite. Una medición local puede
> dar un número perfectamente creíble y perfectamente inútil.

Experimento, en una base de usar y tirar (el plan gratuito permite 10):

1. Crear una tabla sin índices e insertar ~10.000 filas en lotes; sumar los
   `meta.rows_written` de las respuestas. → **coste base por fila**.
2. `CREATE INDEX` sobre una columna y leer el `rows_written` de esa sentencia.
   → **¿cobra el índice sobre lo ya poblado, y cuánto?**
3. Insertar otras ~10.000 filas con el índice ya creado y comparar con el paso 1.
   → **multiplicador por índice en escrituras nuevas**.

Coste del experimento: ~20-30.000 filas escritas de las 100.000 diarias.
Barato y concluyente, y contesta a la vez las dos preguntas que importan: si
conviene crear los índices al final y cuánto encarece cada índice la carga
mensual.

Los 500 MB por base del plan gratuito no aprietan: estos datos ocupan ~100 MB.

---

## 4. Esquema SQL propuesto

Dos bloques que se enlazan por licencia: **competición** (qué se jugó) y
**ranking** (quién es cada uno y cómo evoluciona).

```sql
-- ─────────────────────────────────────────────────────────── identidad
CREATE TABLE player (
  federation  TEXT NOT NULL,          -- 'FCTT' | 'RFETM'
  lic         TEXT NOT NULL,          -- licencia/código, tal cual la publica la fuente
  name        TEXT NOT NULL,
  club        TEXT,
  fa          TEXT,                   -- federación autonómica (solo RFETM)
  sex         TEXT,                   -- 'M' | 'F'
  birth_year  INTEGER,                -- solo RFETM
  cat         TEXT,                   -- categoría de edad declarada: SEN, V+40…
  updated_at  TEXT,
  PRIMARY KEY (federation, lic)
);
```

> **Las dos federaciones usan numeraciones distintas.** YEH Chih-Wei es el 33851
> de la RFETM y el 13209 de la FCTT. Por eso `federation` forma parte de la clave
> primaria. Unificar las identidades se puede hacer **deduciéndolo de los
> datos** —los partidos de liga nacional aparecen en ambas fuentes con el mismo
> día, marcador y ratings— pero **nunca casando nombres**: hay 19 nombres
> repetidos en una sola edición de la FCTT.

```sql
-- ─────────────────────────────────────────────────────────── rànquings
CREATE TABLE ranking_edition (
  federation  TEXT NOT NULL,
  season      TEXT NOT NULL,          -- '2025-2026'
  edition_id  TEXT NOT NULL,          -- FCTT: '02'..'10','ini' · RFETM: '2026-06'
  label       TEXT,
  year        INTEGER,
  month       INTEGER,
  PRIMARY KEY (federation, season, edition_id)
);

-- Una fila por jugador y edición: cómo le fue ese mes.
CREATE TABLE ranking_snapshot (
  federation  TEXT NOT NULL,
  season      TEXT NOT NULL,
  edition_id  TEXT NOT NULL,
  lic         TEXT NOT NULL,
  pts         REAL,                   -- FCTT lo llama RK; RFETM, ACT
  pts_prev    REAL,
  d_pts       REAL,
  rd          REAL,                   -- desviación tipo Glicko (solo FCTT)
  pj          INTEGER, pg INTEGER, pp INTEGER,
  p_any       INTEGER,                -- partidos acumulados en el año (solo FCTT)
  classified  INTEGER,                -- 0/1 (solo FCTT)
  by_event    TEXT,                   -- JSON {llnn, c_esp, …} (solo RFETM)
  PRIMARY KEY (federation, season, edition_id, lic)
);

-- Una fila por jugador, edición y categoría: su posición.
CREATE TABLE ranking_position (
  federation  TEXT NOT NULL,
  season      TEXT NOT NULL,
  edition_id  TEXT NOT NULL,
  lic         TEXT NOT NULL,
  category    TEXT NOT NULL,          -- SEM, V40M, S21F… / NAC, SEN, V40…
  sex         TEXT,
  pos         INTEGER NOT NULL,
  PRIMARY KEY (federation, season, edition_id, lic, category)
);
```

> Separar `snapshot` de `position` no es purismo: en la RFETM una fila trae la
> posición del jugador en **14 categorías a la vez** con unos puntos únicos, y en
> la FCTT cada PDF es una categoría. Este par de tablas absorbe las dos formas
> sin repetir los puntos ni inventar columnas nulas.
>
> Ojo: `99999` en la RFETM significa "no compite en esa categoría". **No es una
> posición** y no debe insertarse.

```sql
-- ──────────────────────────────────── histórico partido a partido (FCTT)
CREATE TABLE player_match (
  federation   TEXT NOT NULL,
  season       TEXT NOT NULL,
  edition_id   TEXT NOT NULL,
  lic          TEXT NOT NULL,
  seq          INTEGER NOT NULL,      -- orden dentro de (lic, edición): desempata repeticiones
  date         TEXT NOT NULL,         -- ISO 'YYYY-MM-DD'
  event        TEXT NOT NULL,         -- 'Lliga RTB J4', 'Top Català'
  event_type   TEXT,                  -- 'lliga' | 'torneig'
  category     TEXT,
  opp_lic      TEXT,                  -- NULL si el rival no tiene licencia de esa federación
  opp_name     TEXT NOT NULL,
  opp_external INTEGER DEFAULT 0,
  sets         INTEGER, opp_sets INTEGER, won INTEGER,
  rk           REAL, opp_rk REAL,     -- rating de cada uno **en el momento del partido**
  d_rk         REAL, rd REAL,
  PRIMARY KEY (federation, season, edition_id, lic, seq)
);
```

> `opp_rk` es lo que permite responder de verdad a "¿a quién gana?": es el rating
> del rival ese día, no el de hoy.
>
> El 4,7 % de las filas tienen `opp_external = 1`: son partidos de competición
> estatal contra jugadores de otras federaciones, a los que la FCTT no calcula
> ranking. Ese código **no existe en ningún sitio**; no es un fallo del parseo.

```sql
-- ─────────────────────────────────────────────────────────── competición
CREATE TABLE competition (
  id TEXT PRIMARY KEY,                -- 'fctt_2526', 'rtbtt_2526', 'rfetm_2526'
  organization TEXT,                  -- quién organiza: RTBTT | FCTT | RFETM
  federation TEXT NOT NULL,           -- contra qué ranking resuelven sus licencias
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
  date TEXT, status TEXT,             -- 'completed' | 'scheduled' | 'pending'
  pdf_url TEXT, pdf_hash TEXT,
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
  competition_id TEXT NOT NULL, encounter_id TEXT NOT NULL, ord INTEGER NOT NULL,
  player_local_lic TEXT, player_local_name TEXT,
  player_visitor_lic TEXT, player_visitor_name TEXT,
  winner TEXT,                        -- 'local' | 'visitor'
  sets TEXT,                          -- JSON [{local, visitor}, …]
  PRIMARY KEY (competition_id, encounter_id, ord)
);
```

> `individual_match.ord` es el **puesto** (1 a 5) en el que se alineó el jugador.
> Es lo que permite predecir alineaciones: quién juega por un equipo, cuántas
> veces y **en qué puesto**.

#### Quién organiza no es contra quién resuelven las licencias

`individual_match` **no lleva columna de federación a propósito**: se llega a
ella por `competition_id`. Y hace falta, porque **el organizador y el espacio de
licencias no coinciden**:

| `competition.id` | Organiza | Licencias resuelven contra | Verificado |
|---|---|---|---|
| `rtbtt_2526` | RTBTT | **`player` con `federation='FCTT'`** | 40/40 |
| `fctt_2526` | FCTT | `federation='FCTT'` | 40/40 |
| `rfetm_2526` | RFETM | `federation='RFETM'` | 40/40 |

La territorial de Barcelona es una competición de la RTBTT pero se juega con
**licencia catalana**, así que sus actas resuelven contra el ranking de la FCTT.
Por eso `competition.federation` es una columna y no una constante del código:

```sql
SELECT p.name, p.club, rs.pts
FROM individual_match im
JOIN competition c  ON c.id = im.competition_id
LEFT JOIN player p  ON p.federation = c.federation AND p.lic = im.player_local_lic
LEFT JOIN ranking_snapshot rs
       ON rs.federation = p.federation AND rs.lic = p.lic AND rs.edition_id = ?
WHERE im.competition_id = ? AND im.encounter_id = ?;
```

**`LEFT JOIN`, no `JOIN`.** Un jugador puede tener licencia y jugar la liga sin
aparecer en el ranking: hay **237 jugadores** en ese caso, sin clasificar o con
la licencia dada de baja. Con `JOIN` desaparecerían del acta.

> Dos avisos más sobre identidades:
>
> - La correspondencia RTBTT→FCTT está **verificada empíricamente** (40 de 40
>   licencias muestreadas resuelven), no documentada por la fuente. Es
>   estructuralmente razonable —la RTBTT es territorial dentro de la federación
>   catalana— pero conviene medirla de nuevo al cargar otra temporada.
> - **Un mismo partido de liga nacional existe en los dos espacios de
>   licencias**: en `individual_match` de `rfetm_2526` con licencia española, y
>   en `player_match` de la FCTT (eventos `LLNN` y `ESTATAL`) con código
>   catalán. No es duplicación a corregir: son dos vistas del mismo hecho. Es
>   además el material con el que deducir la equivalencia entre federaciones sin
>   casar nombres.

### Índices — crear **después** del volcado inicial

```sql
CREATE INDEX idx_pos_top      ON ranking_position(federation, season, edition_id, category, sex, pos);
CREATE INDEX idx_snap_player  ON ranking_snapshot(federation, lic, edition_id);
CREATE INDEX idx_pm_player    ON player_match(federation, lic, date);
CREATE INDEX idx_pm_opponent  ON player_match(federation, opp_lic);      -- head-to-head
CREATE INDEX idx_im_local     ON individual_match(player_local_lic);
CREATE INDEX idx_im_visitor   ON individual_match(player_visitor_lic);
CREATE INDEX idx_enc_group    ON encounter(competition_id, group_id, matchday_number);

-- Búsqueda: sustituye al documento de índice de 224 KB que hacía falta en Firestore
CREATE VIRTUAL TABLE player_fts USING fts5(name, club, content='player');
```

### Idempotencia

Todo el cargador es re-ejecutable. En SQL eso es `INSERT … ON CONFLICT(pk) DO
UPDATE`, y por eso **todas las tablas tienen clave primaria natural**. Se
mantiene además la caché local de hashes de contenido (`output/cache/hashes.json`)
para no reprocesar ni reenviar lo que no ha cambiado en origen.

---

## 5. Dónde corre el scraper

**No puede correr en un Worker.** Es Python y depende de `pdfplumber` para leer
las actas y los rànquings en PDF. Es un proceso por lotes, no una petición web:
una carga completa tarda ~20 minutos, la mayor parte parseando PDFs.

```
   PDFs y HTML de las federaciones
              ↓
   tt-scraper (Python, cron en VPS o local)
              ↓  genera SQL / usa la API HTTP de D1
   ┌──────────────────────┐
   │  D1  (base propia)   │
   └──────────┬───────────┘
              ↓
        Worker de consulta  ──→  aplicación
```

Dos formas de empujar, a decidir en el proyecto destino:

1. **Generar un `.sql` y volcarlo con `wrangler`** (`d1 execute --file`). Simple,
   auditable, y va bien con el volcado inicial.
2. **API HTTP de D1** desde el propio Python, en lotes. Mejor para la carga
   mensual y para un cron desatendido.

Ventaja nada menor: **D1 corre en local con `wrangler` contra SQLite de verdad**,
así que toda la carga se puede probar sin gastar cuota.

Y buena parte de la carga es **reproducible sin red**, porque los PDFs quedan en
disco (201 MB en `output/pdfs/`):

| | ¿Necesita red para recargar? |
|---|---|
| Rànquings FCTT y `Partits_Jugats` (PDF) | **No** — se reparsean del disco |
| Actas de RTBTT (PDF) | **No** |
| Páginas de liga de FCTT y RFETM (HTML) | **Sí** — el HTML no se guarda, solo su hash |
| Rànquings RFETM (HTML) | **Sí**, pero son 20 peticiones y 78 segundos |

Eso importa más de lo que parece: la FCTT **republica** PDFs bajo el mismo
nombre (la revisión 06 va por la v2) y nada garantiza que las temporadas viejas
sigan colgadas. Esa carpeta es, a efectos prácticos, el archivo.

### Al cargar por primera vez en una base vacía

`output/cache/hashes.json` guarda 1.111 hashes que dicen "esto ya está cargado".
Si se copia tal cual y se lanza la carga contra una D1 recién creada, **el
scraper se saltará casi todo y la base quedará vacía**, sin ningún error. Hay que
apartarlo antes de la primera carga:

```bash
mv output/cache/hashes.json output/cache/hashes.firestore.json
```

---

## 6. Plan de migración

| # | Paso |
|---|---|
| 1 | Crear la base D1 **propia** para estos datos y aplicar el esquema sin índices |
| 2 | `storage/d1_client.py` con la misma interfaz que `firestore_client.py` (el CLI y los scrapers no cambian) |
| 3 | Carga completa desde los PDFs ya descargados, sin red |
| 4 | Crear los índices y la tabla FTS |
| 5 | Volver a pasar los invariantes (`fctt verify`) y comparar totales con Firestore |
| 6 | Worker de consulta para la app |
| 7 | Retirar la escritura a Firestore cuando el Worker esté en producción |

Los pasos 1-5 no tocan ni un parser.

---

## 7. Consideraciones y cabos sueltos

1. **La cuota es de la cuenta, no de la base.** La carga inicial competirá con el
   otro proyecto. Conviene hacerla en una ventana tranquila o con Workers Paid
   activo ese mes.
2. **Verificar cómo cuenta D1 el `CREATE INDEX`** sobre una tabla ya poblada.
   De ello depende que la carga inicial quepa en el plan gratuito. El
   experimento está descrito en §3 — **contra base remota**, porque en local se
   mide SQLite y no la contabilidad de D1.
3. **Falta el calendario futuro de la RTBTT.** La FCTT marca `scheduled` los
   encuentros sin resultado y la RFETM `pending`, así que entran solos; la RTBTT
   no, porque su fuente son las actas de lo ya jugado. Sin eso no se puede
   preparar el "próximo encuentro" en la territorial.
4. **Diccionario de alias de club, pendiente.** El mismo club es `VILCA` en el
   ranking y `CTT VILANOVA DEL CAMI` en las actas. Se deduce por voto mayoritario
   de los jugadores de cada equipo: verificado en 7 equipos con 100 % de acuerdo.
   En SQL es una tabla `club_alias` y un `JOIN`.
5. **Fase 2 de la RFETM sin hacer.** El histórico partido a partido existe
   (`accion=5`, una petición por jugador y mes, ~10.000 filtrando por quien jugó)
   pero no está cargado. Es lo que igualaría a la RFETM con la FCTT.
6. **Queda un 0,3 % de partidos de la FCTT con el marcador dudoso.** Está
   documentado en `todos_a_revisar.md` §10, con la solución propuesta:
   reconciliar contra el ranking del mes, que en SQL es un `JOIN` y deja de ser
   la heurística incómoda que es hoy.
7. **La temporada forma parte de las claves.** Cambiar `SEASON` en el `.env`
   mueve a la vez las URLs y los identificadores, y la temporada anterior queda
   intacta. En SQL, `season` es columna de las claves primarias, así que ese
   comportamiento se conserva.
