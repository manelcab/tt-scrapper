# Plan: identidad de persona por encima de las federaciones

> Respuesta de tt-scraper a `kayzen-agent/docs/PARA-TT-SCRAPER-identidad.md`
> (Kayzen, 2026-10-24). Escrito el 2026-10-25.
>
> **Objetivo**: dar en `tt-data` un identificador de persona estable con sus
> licencias colgando y un estado que distinga lo seguro de lo probable, para
> poder seguir a un jugador aunque tenga licencia de la FCTT y de la RFETM —
> incluso cuando solo ha jugado en una de ellas.

## 0. Diagnóstico sobre el código actual

Lo que hay hoy en el esquema ([d1/schema.sql](../d1/schema.sql)):

- `player (federation, lic, name, club, fa, sex, birth_year, cat, …)`, PK
  `(federation, lic)`. **Una persona con dos licencias son dos filas sin nada
  que las una.**
- `player_link (federation_a, lic_a, federation_b, lic_b, method, confidence,
  verified_at)`: puente FCTT↔RFETM **deducido de partidos de liga nacional**,
  nunca por nombre. Lo genera [d1/build_player_link.py](../d1/build_player_link.py)
  con votación mutua de firmas `(fecha, sets_ganados, sets_rival)`. En
  producción: ~312 pares, ~304 coinciden al partido.

Lo que falta y Kayzen necesita:

- **No hay tabla de persona.** `player_link` solo cubre a quien ha jugado liga
  nacional en los dos espacios de licencia. Gorka Neyra (116 partidos FCTT, 0
  RFETM pero dado de alta) no se puede juntar con nada.
- **No hay estado** que permita consumir "probable" sabiendo que es probable.
- **No hay `evidence` ni `rejected`**: una corrección manual ("estos dos NO son
  la misma persona") no se puede guardar y se vuelve a deducir en el siguiente
  pase.
- **No hay código de club** canónico: solo nombre en texto libre, y además
  `player.club` es el club de la última edición de ranking cargada (≈ final de
  la temporada anterior, ver `design/PARA-KAYZEN-licencias.md`). Kayzen compara
  hoy nombre deducido contra nombre publicado quitando palabras genéricas:
  "fuzzy sobre fuzzy".
- **`birth_year`**: lo tenemos de **RFETM** (`rfetm_ranking_parser`), **no** de
  FCTT.

Qué respeta este plan de lo que Kayzen pide **que NO hagamos**:

- No adivinar: un `person_id` que junte por nombre sin más sería peor que nada.
- No escribir nada desde Kayzen en `tt-data` (sigue siendo de solo lectura para
  los consumidores).
- No obligar a tenerlo todo: Fase 1 ya es útil sola.

## 1. Esquema propuesto

Dos tablas nuevas en [d1/schema.sql](../d1/schema.sql) + migración
`d1/alter_person_identity.sql` (misma mecánica que
`alter_encounter_venue_referee.sql` / `alter_team_games_for_against.sql`). El
`player` **no se toca**; `person_licence` cuelga de él.

```sql
CREATE TABLE IF NOT EXISTS person (
  id           TEXT NOT NULL,          -- 'p_<hash>' estable (ver §3)
  display_name TEXT,                   -- nombre con el que se le llama
  birth_year   INTEGER,                -- cuando se conozca (hoy solo RFETM)
  sex          TEXT,                   -- 'M' | 'F'
  updated_at   TEXT,
  PRIMARY KEY (id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS person_licence (
  person_id   TEXT NOT NULL,
  federation  TEXT NOT NULL,           -- 'FCTT' | 'RFETM'
  lic         TEXT NOT NULL,
  status      TEXT NOT NULL,           -- 'confirmed' | 'probable' | 'rejected'
  evidence    TEXT NOT NULL,           -- 'nat_league_match' | 'name_and_club'
                                       --  | 'birth_year' | 'manual' | 'singleton'
  confidence  REAL,                    -- cuando la evidencia sea estadística
  decided_at  TEXT,
  decided_by  TEXT,                    -- 'scraper' | 'admin:<quien>'
  PRIMARY KEY (federation, lic)        -- una licencia pertenece a UNA persona
) WITHOUT ROWID;
```

Notas de diseño frente al esquema que propone Kayzen (§3):

- **PK `(federation, lic)` en `person_licence`**, no `(person_id, …)`: una
  licencia pertenece a una sola persona, y así el `_emit` idempotente la mueve
  de persona sin dejar duplicados. El `person_id` va indexado aparte
  (`indexes.sql`).
- **`status` con tres valores**, tal cual pide Kayzen. `rejected` se guarda como
  fila de `person_licence` con ese estado (no se borra): así el builder sabe que
  ese par ya se descartó y no lo vuelve a proponer.
- **`evidence` siempre**, para poder retirar un criterio malo sin retirarlo
  todo. `singleton` = persona de una sola licencia (nadie con quien juntarla
  aún).
- **`decided_by`** distingue `scraper` de `admin:<quien>`: lo manual manda sobre
  lo deducido (§4, bucle de correcciones).

## 2. Fases de poblado

Cada fase es un builder derivado (estilo `build_player_link.py`) que lee de
`tt-data`, calcula y emite `output/sql/person_*.sql` cargable con
`load_chunks.py`. Todas idempotentes (`INSERT … ON CONFLICT DO UPDATE`).

### Fase 1 — MVP: solo lo CONFIRMADO (sin adivinar)

Esto es lo que Kayzen dice que "ya nos valdría como primer paso". Builder
`d1/build_person.py`:

1. Lee `player_link` (pares confirmados) y `player` (todas las licencias).
2. Componentes conexas sobre los pares de `player_link` → cada grupo de
   licencias equivalentes es **una persona**.
3. Cada `player` que no esté en ningún par → **persona de una sola licencia**
   (`evidence='singleton'`, `status='confirmed'`).
4. Emite:
   - `person(id, display_name, birth_year, sex)` — `display_name`/`sex`/
     `birth_year` tomados de la licencia con más datos (RFETM aporta
     `birth_year`).
   - `person_licence(…, status='confirmed', evidence='nat_league_match'|'singleton',
     confidence=<votos de player_link>, decided_by='scraper')`.

Resultado: toda licencia tiene su persona; los pares de liga nacional quedan
unidos. Cero heurística de nombre. **Esto desbloquea a Kayzen** (les da dónde
colgar lo demás y les quita el rodeo del código de club).

Limitación conocida: Gorka Neyra sigue siendo dos personas hasta la Fase 3,
porque no hay partido que las una. Es correcto: no inventamos.

### Fase 2 — Medir "mismo nombre y mismo club" ANTES de usarlo

Kayzen (§5) pide dos números y es lo que decide si la regla se queda, se afina o
se retira. Lo tenemos fácil porque tenemos la foto entera y `tt-data` es
nuestra (sin el coste por fila que les frena a ellos). Script
`d1/measure_name_club.py` (informe, no escribe):

- **Recall**: de los pares que `player_link` da por ciertos, ¿en cuántos
  coinciden además nombre exacto y club equivalente?
- **Precisión**: de los pares que "mismo nombre y mismo club" propondría,
  ¿cuántos **contradicen** a `player_link` (dicen A–B donde player_link dice
  A–C)?

Reutilizamos su lógica de club (`services/api/src/mismo_club.ts`, ~30 líneas):
quitar palabras no distintivas (`CTT`, `CLUB`, `TENNIS`, `TAULA`, la letra del
equipo) y exigir subconjunto. Grieta ya conocida a vigilar: dos clubes de la
misma ciudad con una palabra común. Entregamos los dos números a Kayzen.

**Gate**: si la precisión es mala, la Fase 3 por nombre+club no se activa (o se
restringe con `birth_year`).

### Fase 3 — Candidatos PROBABLES (una sola verdad, compartida)

Con el número de la Fase 2, el builder `build_person.py` añade, **sin tocar lo
confirmado ni lo rechazado**:

- Pares con **nombre exacto igual y club equivalente** no explicados por
  `player_link` → nueva fila `person_licence` con `status='probable'`,
  `evidence='name_and_club'`, `confidence` del número de la Fase 2.
- Cuando haya `birth_year` en ambos lados y **difiera** → no se propone (o
  `status='rejected'`, `evidence='birth_year'`): es el desempate más barato y
  parte del 28% de colisiones de nombre.

Así lo que hoy deduce cada consumidor por su cuenta pasa a estar en `tt-data`
una sola vez. Kayzen enseñaría "probable" con su forma de corregirlo y podría
borrar su `same_person_deducido`.

### Fase 4 — Bucle de correcciones (`rejected` / `manual`)

El punto delicado: **Kayzen/Kanto son de solo lectura en `tt-data`**, pero las
correcciones del admin ("estos dos NO son la misma persona", o "SÍ lo son")
tienen que sobrevivir al siguiente pase del builder. Opciones (requiere
decisión, ver §6):

- **(A) Fichero de decisiones versionado en tt-scraper** (`d1/identity_overrides.csv`
  o `.json`): el admin comunica las correcciones, se vuelcan ahí y el builder
  las aplica como `status='confirmed'|'rejected'`, `evidence='manual'`,
  `decided_by='admin:<quien>'`. Simple, auditable en git, sin canal nuevo.
- **(B) Tabla de entrada en `tt-data` que escribe Kanto** (no Kayzen) vía su
  API, y el builder la lee y la "promociona" a `person_licence`. Más vivo, pero
  abre escritura desde un consumidor.

Recomendación: empezar por **(A)** (respeta "nada que escriba Kayzen en
tt-data" y es suficiente para el volumen de correcciones), y pasar a (B) solo si
el volumen lo pide. En ambos casos, lo manual **gana** a lo deducido y el
builder **nunca** re-deduce un par marcado `rejected`.

## 3. Estabilidad del `id` (requisito duro de Kayzen §3)

Si el `person_id` cambia al recalcular, se rompe en silencio todo lo que alguien
colgó de él. Plan:

- El builder **lee primero** `person_licence` existente de `tt-data`. Toda
  licencia ya asignada **conserva su `person_id`**.
- `person_id` solo se **asigna** la primera vez que aparece una licencia o un
  cluster nuevo. Semilla determinista: `p_` + hash corto de la licencia
  canónica del cluster (la menor por `(federation, lic)` ordenado), pero una vez
  asignado queda **fijado por la lectura de vuelta**, no se recalcula.
- Caso raro: dos clusters confirmados que se fusionan (aparece un `player_link`
  que los une). Se elige un `person_id` superviviente (el del cluster mayor;
  empate → el menor) y se escribe una fila en `person_id_alias(old_id, new_id,
  merged_at)`. **Nunca se reescribe silenciosamente**: los consumidores resuelven
  alias. Tabla:

```sql
CREATE TABLE IF NOT EXISTS person_id_alias (
  old_id TEXT NOT NULL, new_id TEXT NOT NULL, merged_at TEXT,
  PRIMARY KEY (old_id)
) WITHOUT ROWID;
```

## 4. Mejoras de la fuente (§4 de Kayzen) — qué es factible

Por orden de valor y coste:

1. **`birth_year`** — el desempate más barato. Ya lo guardamos de **RFETM**.
   **VERIFICADO 2026-10-25: la FCTT NO publica año de nacimiento.** El ranking
   inicial tiene una columna `NAC` que resulta ser **nacionalidad** (`ESP`), no
   año; `EDAT` es la **categoría de edad** (SEN, V+40, JUV-1…), no un año. Así
   que el paso 4 "capturar birth_year de FCTT" **no es posible desde la fuente**.
   Consecuencias: (a) `person.birth_year` solo se puebla de RFETM; (b) el
   desempate cross-federación por año no existe — como mucho se puede usar la
   **categoría de edad FCTT** (ya en `player.cat`) para un chequeo de coherencia
   grosero (un RFETM nacido en 2010 no casa con un FCTT `V+40`).
2. **Código de club por federación** — hoy **no** lo capturamos (solo nombre
   libre). Investigar si FCTT (páginas de equipo/club, `team_name_id` resuelve a
   un club con código tipo `CTTBDN`) y RFETM exponen un código estable. Si sí:
   - tabla `club(federation, code, name, …)` y `club_link(federation_a, code_a,
     federation_b, code_b)` para equivalencias entre federaciones;
   - `player`/`person_licence` referencian el código → "mismo club" pasa de
     fuzzy a **igualdad**. Coste: medio (parser + tabla + enlace).
3. **Histórico de club por temporada** — hoy `player.club` ≈ final de la
   temporada anterior. Capturar el club **de la temporada en curso** desde las
   alineaciones/actas (ya scrapeamos equipos por encuentro) daría un
   `player_club_season(federation, lic, season, club)` que: (a) permite decir
   "cambió de club en verano" sin deducirlo; (b) usa el club como evidencia de
   identidad **con la temporada correcta**. Coste: medio.

## 5. Secuenciación y entregables

Las tablas nuevas son **derivadas** (las escribe `build_person.py` con SQL
crudo, como `build_player_link.py`), así que **no** van en `D1Client._TABLES`:
basta con que existan en `schema.sql` + migración.

| Paso | Entregable | Depende de | Esfuerzo |
|---|---|---|---|
| 1 | `person` + `person_licence` + `person_id_alias` en schema + `alter_person_identity.sql` | — | bajo |
| 2 | `d1/build_person.py` **Fase 1** (confirmado + singletons) + carga | 1 | bajo-medio |
| 3 | `d1/measure_name_club.py` (recall/precisión) → números a Kayzen | 2 | bajo |
| 4 | `birth_year` FCTT (si la fuente lo da) → desempate | — | bajo-medio |
| 5 | `build_person.py` **Fase 3** (probable name+club) — gate del paso 3 **y** con `birth_year` del paso 4 | 3, 4 | medio |
| 6 | Bucle de correcciones `manual`/`rejected` — **fichero versionado** (`d1/identity_overrides.csv`) | 2 | medio |
| 7 | Código de club + `club`/`club_link` → "mismo club" exacto | — | medio |
| 8 | Histórico de club por temporada | 7 | medio |
| 9 | Script periódico (`scripts/weekly_*` o uno nuevo) que recalcula identidad tras cargar rankings/actas | 2 | bajo |

Con los pasos **1–3** Kayzen ya puede borrar su rodeo del código de club y colgar
todo de `person_id`, y nosotros entregamos los dos números que piden. Los pasos
4–8 mejoran cobertura y calidad sin que ningún consumidor tenga que esperar a
tenerlo todo.

## 5bis. Estado y hallazgos (2026-10-25)

**Pasos 1–3 hechos.** Esquema aplicado a `tt-data` (`alter_person_identity.sql`),
`build_person.py` y `measure_name_club.py` implementados y ejecutados.

**Fase 1 cargada en producción:** 7033 personas, 7345 licencias
(624 enlazadas FCTT↔RFETM por `nat_league_match`, resto `singleton`), todo
`confirmed`. Kayzen ya tiene `person_id` donde colgar lo suyo.

**Medición "nombre+club" (los dos números de Kayzen §5):**

| Métrica | Valor | Lectura |
|---|---|---|
| Precisión (agree / (agree+contradict)) | **98.2 %** (56 vs 1 contradicción) | la regla casi no junta mal |
| Recall nombre+club | **19.6 %** (56/285) | casi no captura |
| Recall **nombre exacto solo** | **89.5 %** (255/285) | el club es lo que mata el recall |

**Causa del recall bajo (hallazgo importante):** en la FCTT `player.club` es un
**código** (`CTTBDN`, `RELIS`, `CALEL`, `S.CUGA`…) y en la RFETM es el **nombre**
(`CTT BADALONA`…). "Mismo club" por tokens casi nunca casa entre un código y un
nombre. Esto **confirma con números** el "fuzzy sobre fuzzy" de Kayzen (§2.1) y
reordena prioridades:

- **Sube el paso 7 (código de club canónico).** Si resolvemos el código FCTT a
  club y hacemos una tabla de equivalencias FCTT-código ↔ RFETM-nombre, el recall
  de "nombre+club" salta del 19.6 % hacia ~89.5 % **sin perder precisión**.
- **Confirma la decisión 3.** Pasar de "nombre+club" a "nombre casi solo" (89.5 %
  recall) es tentador, pero el nombre solo arrastra el 28 % de colisiones de
  Kayzen; `birth_year` es lo que lo hace seguro. De ahí que `probable` espere al
  `birth_year` de la FCTT (paso 4).

Números entregables a Kayzen tal cual.

**Paso 4 (birth_year FCTT): BLOQUEADO por la fuente** (ver §4.1) — la FCTT no
publica año de nacimiento. Esto obliga a **revisar la decisión 3**: no se puede
exigir birth_year de FCTT antes de proponer por nombre, porque no existe.
Alternativas para hacer seguro el "nombre+club": (a) resolver el club (paso 7)
para que "mismo club" vuelva a casar — es la vía principal; (b) usar la
**categoría de edad** FCTT vs el birth_year RFETM como coherencia grosera.

**Paso 7 (club canónico): primer resultado (`d1/build_club_link.py`).** Desde
los 285 pares confirmados con club en ambos lados se derivan **35 equivalencias**
código-FCTT → nombre-RFETM (2 ambiguas descartadas). Cobertura: 27 % de los 128
códigos distintos, pero **52.1 % de los jugadores FCTT** (1446/2776), porque los
clubes grandes concentran jugadores. Ejemplos correctos: `CTTBDN→CTT BADALONA`,
`S.CUGA→SANT CUGAT`, `HOSPIT→L´HOSPITALET`, `LLUÏSO→LLUISOS DE GRACIA`. El
diccionario crece según se confirman más pares de liga nacional.

**Persistido y medido el lift (opción 1, 2026-10-25).** `club_link` creada
(`alter_club_link.sql`) y cargada (35 filas). Con `measure_name_club.py
--use-club-map`, resolviendo el código FCTT antes de comparar:

| | sin mapa | con `club_link` |
|---|---|---|
| recall nombre+club | 19.6 % | **73.3 %** (209/285) |
| precisión | 98.2 % | **99.5 %** (209 ✓, 1 contradice) |

Techo = 89.5 % (nombre exacto); la diferencia son clubes aún no en el mapa de 35,
que crecerá con cada par nuevo. Aparecen **443 candidatos** nombre+club-resuelto
sin partido que los confirme: son los que `probable` (paso 5) propondría. La
única contradicción (GRANADOS Claudio, Tona) la **separa `player_link`**, así que
el builder de `probable` debe **saltar candidatos que choquen con un enlace
confirmado** → esa contradicción desaparece.

**Fase 5 (`probable`) HECHA y cargada (2026-10-25).** `build_person.py` propone
pares FCTT↔RFETM con nombre exacto + club resuelto (`club_link`) no explicados
por `player_link`, con las salvaguardas: salta licencias ya confirmadas-enlazadas
y descarta ambiguos (una licencia con >1 candidato). La licencia FCTT es el ancla
(confirmed) y la RFETM pasa a `status='probable'`, `evidence='name_and_club'`; no
se crea alias (reversible: el id 'probable' no ancla, así que retirarlo revierte).
Las personas que quedan sin licencias (singleton RFETM absorbido) se borran.
Resultado en tt-data: **6590 personas, 6902 confirmadas + 443 probables**, 0
huérfanas. Desactivable con `--no-probable`.

**Paso 6 (correcciones manuales) HECHO.** Fichero versionado
`d1/identity_overrides.csv` (decision = `confirmed` | `rejected`,
`decided_by=admin:<quien>`). `build_person.py` lo lee en cada pase: los
`confirmed` son aristas manuales (evidence=`manual`, pueden fusionar personas) y
los `rejected` impiden que un par se junte por `probable`. El emit deja al admin
pisar (CSV manda) pero **no** al scraper sobre una fila manual
(`WHERE decided_by='scraper' OR excluded.decided_by LIKE 'admin:%'`), así lo
manual persiste entre pases. (Para cambiar una decisión se edita su línea; el
borrado de una línea no auto-revierte una fila `manual` ya escrita — mejora
futura.)

**Paso 8 (histórico de club por temporada) HECHO.** Tabla `player_club_season`
(`alter_player_club_season.sql`) + `build_player_club_season.py`: deriva el club
de cada licencia POR TEMPORADA desde las alineaciones (`individual_match` →
`encounter`: el equipo con el que juega), normalizando el nombre de equipo a
club (quita la letra/número: «... "A"», «... 'A'», «... B»). Cargado: 7006 filas
(3175 FCTT + 3831 RFETM), 439 con >1 club esa temporada. Da el club **vigente y
legible** frente a `player.club` (que es un código desfasado): p. ej. licencia
FCTT 1000 → `player.club='CALEL'` pero `player_club_season.club='TERMOTUR
CALELLA'`. Permite "cambió de club" (comparar temporadas) y, a futuro, usar el
club con la temporada correcta como evidencia. Caveats: (a) `clubs>1` suele ser
variante de patrocinador entre competiciones (EUROCLIMA BADALONA ↔ CTT BADALONA),
no préstamo; (b) NO se enchufa aún a `build_person`, porque allí la comparación
usa el CÓDIGO FCTT + `club_link` y aquí salen NOMBRES de equipo — unificar las dos
representaciones de club es un diseño aparte.

**Paso 9 (script periódico) HECHO.** `scripts/weekly_identity.sh` encadena, en
orden y cargando cada paso antes del siguiente (porque el siguiente lee de D1 lo
del anterior): `build_player_link → build_club_link → build_person`, y al final
`build_player_club_season` (independiente). Flags `--no-probable` y `--dry-run`.
Pensado para pasar tras las cargas semanales.

---

**Estado global:** Fases 1, 2, 5 y pasos 6, 7, 8, 9 hechos y en producción. Paso
4 (birth_year FCTT) bloqueado por la fuente (no existe). Índice
`person_licence_by_person` sobre `person_licence(person_id)` creado en `tt-data`
y en `d1/indexes.sql` (pedido por Kayzen §8.3: sin él, "qué otras licencias tiene
esta persona" escaneaba las 7.345 filas; con él, dos). El plan queda cubierto.

Mejoras futuras (no bloqueantes) en `design/backlog_identidad.md`: automatizar el
encadenado periódico, canal para los `rejected` de Kayzen, unificar la
representación de club (código↔nombre), revertir `manual` al quitar del CSV, etc.

## 6. Decisiones (cerradas 2026-10-25)

1. **Canal de correcciones** (§4): **opción A — fichero versionado** en
   tt-scraper (`d1/identity_overrides.csv`). No se abre escritura a `tt-data`
   desde consumidores; lo manual queda auditable en git.
2. **Política de `id`** (§3): **`p_<hash de licencia canónica>` + tabla de
   alias** para fusiones. Reproducible y sin reescrituras silenciosas.
3. **`probable` por nombre+club**: se activa **después** de capturar
   `birth_year` de FCTT (paso 4), para que el desempate por año de nacimiento
   esté disponible antes de proponer equivalencias por nombre.

## 7. Lo que NO cambia

- `player` y `player_link` se quedan como están (`player_link` es la evidencia
  `nat_league_match` de la Fase 1).
- `tt-data` sigue siendo de solo lectura para Kayzen.
- Nada casa por nombre para `confirmed`: el nombre solo entra en `probable`, y
  siempre con `evidence` para poder retirarlo.
