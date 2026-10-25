# tt-scraper — Funcionalidades y comandos

> **Nota (16/08/2026).** Este documento describe el modelo **actual, en
> Firestore**. Está decidido migrar el almacenamiento a **Cloudflare D1**: el
> esquema SQL, las cuotas y el plan están en
> [`handover_cloudflare_d1.md`](handover_cloudflare_d1.md).
>
> Todo lo que aquí se cuenta sobre **fuentes, parsers, trampas y verificaciones
> sigue vigente** — la migración solo sustituye la capa de escritura. Lo que sí
> es deuda de Firestore y desaparece con SQL (renombrado de categorías, claves
> con el sexo dentro, exenciones de índice, troceado de lotes por campos hoja)
> queda señalado como tal en el documento de traspaso.

Scraper en Python que descarga resultados de competiciones de tenis de mesa, los parsea y los guarda en una base de datos. Soporta tres fuentes:

- **RTBTT** — Liga Territorial Barcelona (formato HTML + actas en PDF)
- **RFETM** — Federación Española de Tenis de Mesa (HTML: liga nacional y rànquings)
- **FCTT** — Federació Catalana de Tennis Taula (HTML de liga, PDF de rànquings y de histórico de partidos)

Los datos alimentan el **Módulo 8 (Competición)** de la app prematch y, a partir
de ahora, la aplicación agéntica de compañero de tenis de mesa.

---

## 1. Resumen de fuentes

| Fuente | Organización | Formato resultados | Estado | Comando raíz |
|--------|--------------|--------------------|--------|--------------|
| RTBTT  | Liga Territorial Barcelona | HTML índice + actas PDF | Producción | `python main.py rtbtt run` |
| RFETM  | Federación Española | HTML (jornadas como query string) | Producción | `python main.py rfetm run` |
| FCTT   | Federació Catalana | HTML (jornadas como query string) | Producción | `python main.py fctt run` |

Cada competición es un subgrupo del CLI (`rtbtt`, `rfetm`, `fctt`). Todos los comandos van bajo su subgrupo correspondiente — añadir una nueva competición es declararla en `sources.py` y replicar el mismo patrón con un nuevo `@cli.group()`.

**Las URLs y los ids de competición viven solo en [`sources.py`](../sources.py)**, derivados de `SEASON` (del `.env`). No hay URLs en el `.env` ni repartidas por parsers y comandos.

Ambas comparten el mismo `FirestoreClient`, `LocalCache` y modelos (`Match`, `Matchday`, `IndividualMatch`, `Team`).

---

## 2. Flujo general

```
┌─────────────┐      ┌──────────────┐      ┌──────────────┐      ┌────────────┐
│   Índice    │  →   │   Grupo      │  →   │  Jornadas    │  →   │ Firestore  │
│ (categorías)│      │ (HTML/equipos)│      │ (PDF/HTML)   │      │   batches  │
└─────────────┘      └──────────────┘      └──────────────┘      └────────────┘
       │                    │                      │
       ▼                    ▼                      ▼
   Filtrar          Hash de grupo          Hash por jornada
 (--category,       (omite si no              (omite si no
   --group)          ha cambiado)              ha cambiado)
```

**Hashes MD5** en `output/cache/hashes.json`:
- `groups.{group_id}` → hash del HTML del grupo (RTBTT)
- `matchdays.{group_id}_jornada{n}` → hash del PDF (RTBTT) o del HTML (RFETM)

Se invalidan con `--force` (todo el grupo) o `--jornada N` (solo una jornada).

---

## 3. Comandos disponibles

### 3.1 Ejecución completa (RTBTT)

```bash
python main.py rtbtt run                                       # Toda la temporada
python main.py rtbtt run --category PREF                       # Solo Preferente
python main.py rtbtt run --category PREF --group PREF_G1       # PREF grupo 1
python main.py rtbtt run --category PREF,1A                    # Múltiples categorías
python main.py rtbtt run --dry-run                             # No escribe en Firestore
python main.py rtbtt run --force                               # Ignora todos los hashes
```

### 3.2 Actualizar una jornada concreta (RTBTT)

Caso típico: una jornada se publicó sin resultados y ahora ya están subidos.

```bash
python main.py rtbtt run --category PREF --group PREF_G1 --jornada 21
```

Comportamiento:
- Salta el hash de grupo (procesa aunque marque "sin cambios")
- Filtra `unique_matchdays` para procesar solo la jornada indicada
- Borra el PDF local (`output/pdfs/{group}_jornada{N}.pdf`) para forzar re-descarga
- Invalida el hash del PDF de esa jornada
- **No** actualiza el hash de grupo al final (queda válido para detectar cambios futuros)

### 3.3 Solo descargar PDFs (sin parsear)

```bash
python main.py rtbtt download --group PREF_G1
```

Útil para inspeccionar el acta antes de procesarla.

### 3.4 Parsear PDFs ya descargados (sin tocar Firestore)

```bash
python main.py rtbtt parse --group PREF_G1                     # Todas las jornadas
python main.py rtbtt parse --group PREF_G1 --jornada 21        # Una jornada
python main.py rtbtt parse --group PREF_G1 --debug-pdf         # Texto raw del PDF
```

Pinta una tabla por consola con el resultado del encuentro y los partidos individuales (incluido el de dobles si lo hay). No escribe nada — solo lectura local.

### 3.5 Listar categorías y grupos

```bash
python main.py rtbtt list      # RTBTT
python main.py rfetm list      # RFETM
```

### 3.6 Validar credenciales Firestore

Comando genérico, no asociado a ninguna competición concreta:

```bash
python main.py validate
```

### 3.7 Ejecución completa (RFETM)

```bash
python main.py rfetm run                                       # Todas las ligas
python main.py rfetm run --category liga1_M                    # Liga 1 masc.
python main.py rfetm run --group liga1_g0_M                    # Un grupo
python main.py rfetm run --group liga1_g0_M --jornada 18       # Una jornada
python main.py rfetm run --dry-run
python main.py rfetm run --force
```

`--jornada N` invalida el hash de esa jornada y limita el loop a esa única URL.

### 3.8 Ejecución completa (FCTT)

```bash
python main.py fctt run                                        # TDM + Copa Catalana Femenina
python main.py fctt run --category TDM                         # Solo TDM
python main.py fctt run --group TDM_G1 --jornada 3             # Una jornada
python main.py fctt run --dry-run
python main.py fctt run --force
python main.py fctt list
```

Cubre **TDM** (Tercera Divisió Masculina: TDM_G1, TDM_G2, TDM_G3) y la **Copa Catalana Femenina** (COPA_F_1A, COPA_F_2A). La sección "Lligues estatals (grups catalans)" del índice se excluye a propósito: son grupos de la liga estatal que ya están en `competitions/rfetm_*`, y scrapearlos aquí duplicaría los mismos encuentros.

### 3.9 Rànquings (FCTT)

```bash
python main.py fctt rankings                                   # todas las ediciones
python main.py fctt rankings --edition 10                      # solo Juliol
python main.py fctt rankings --edition ini                      # solo el inicial
python main.py fctt rankings --category SEM                    # solo sènior masculí
python main.py fctt rankings --dry-run
python main.py fctt rankings --force
```

Escribe en la colección raíz `rankings`, separada de `competitions` (ver §6bis).

### 3.11 Rànquings (RFETM)

```bash
python main.py rfetm rankings                                  # los 10 meses
python main.py rfetm rankings --edition 2026-06                # solo un mes
python main.py rfetm rankings --dry-run
python main.py rfetm rankings --force
```

Escribe en `rankings/rfetm_2526`, con el mismo esqueleto que la FCTT (ver
§5quinquies para las diferencias de la fuente).

### 3.10 Partidos jugados (FCTT)

```bash
python main.py fctt matches                                    # las 9 ediciones
python main.py fctt matches --edition 10                       # solo Juliol
python main.py fctt matches --dry-run
python main.py fctt matches --force
```

Cuelga de `rankings/{competition_id}/players/{código}/match_log/` (ver §6ter).

> Con `--edition` **no se recalcula el `record`** de temporada del jugador: se
> resume la temporada entera y con una sola edición delante saldría un resumen
> falso. El comando lo avisa por pantalla.
>
> Por eso una carga por tandas (`--edition 02`, `--edition 03`…) tiene que
> **acabar en una ejecución sin filtros**, que es la que escribe los resúmenes.
> Esa ejecución los reescribe todos, no solo los de quien jugó en la última
> edición: si no, los jugadores de las primeras tandas se quedarían sin
> resumen para siempre.

---

## 4. Detalles del parser RTBTT

Las actas siguen un formato fijo. El parser detecta la presencia de `Acta {n}` en la página y aplica `_parse_rtbtt_page`. Si no, cae al fallback genérico (`_parse_generic_page`) que usa heurísticas sobre tablas y texto.

### 4.1 Estructura del acta

```
Acta 123 03/05/26 EQUIPO_LOCAL EQUIPO_VISITANTE
ABC EQUIPO_LOCAL XYZ EQUIPO_VISITANTE
A 15266 SUBIRANA Biel Y 7971 BOSCH Miquel 3 0
B 5399 CASTRO Ismael X 14174 RAMÍREZ Rafael 0 3
...
D1 15266 SUBIRANA Biel D1 14174 RAMÍREZ Rafael 0 3
D2 200 LUCEA Javier D2 7713 FARELL Jordi
EQUIPO_LOCAL EQUIPO_VISITANTE Jocs 11 12
3 4
```

### 4.2 Tipos de línea

| Patrón | Regex | Descripción |
|--------|-------|-------------|
| Individual | `_IND_MATCH_RE` | `[ABC] lic nombre [XYZ] lic nombre sets_local sets_visitor` |
| Dobles D1 | `_DOUBLES_D1_RE` | `D1 lic nombre D1 lic nombre sets_local sets_visitor` |
| Dobles D2 | `_DOUBLES_D2_RE` | `D2 lic nombre D2 lic nombre` (sin score) |

### 4.3 Partido de dobles (encuentros 4-3 / 3-4)

Cuando un encuentro va 3-3 después de los 6 individuales se juega un dobles que decide el resultado.

- La línea **D1** contiene los primeros jugadores de cada lado y el score del partido completo
- La línea **D2** contiene los segundos jugadores (sin score, ya está en D1)
- Ambas se combinan en un único `IndividualMatch` con `is_doubles=True` y los campos `*_2` rellenos

Antes del fix, las líneas D1/D2 se ignoraban silenciosamente y los encuentros 4-3 se guardaban como 3-3. Comprobado con la jornada 21 PREF_G1 (CARDEDEU 3 - 4 FALCONS).

---

## 5. Detalles del parser RFETM

A diferencia de RTBTT, no hay PDFs. Cada jornada es una URL distinta (`?jornada=N`) con HTML estructurado en tablas.

### 5.1 Flujo

1. `parse_index` extrae la lista de ligas y grupos del índice principal
2. Por cada grupo, se descarga la URL con `jornada=0` (clasificación)
3. `parse_max_jornada` lee del desplegable cuántas jornadas hay disponibles
4. Loop sobre jornadas 1..N (o solo `--jornada N` si se filtra)
5. `parse_matches` extrae los encuentros de cada página de jornada
6. Solo se guardan los encuentros con `status="completed"` (resultados publicados)

### 5.2 Sin acta — sin individuales detallados

RFETM publica encuentros con resultado global (ej. 4-2) pero el HTML público no incluye los partidos individuales en todos los formatos. El parser extrae lo que está disponible.

---

## 5bis. Detalles del parser FCTT

### 5bis.1 Forma de la fuente

| Página | Contenido |
|--------|-----------|
| `/competicions-estatals/` | `h2` = categoría, `h3` = grupo, y el primer enlace a `/lligues/<slug>/` que sigue al `h3` (la web es Elementor: título y botón son widgets hermanos, sin contenedor común) |
| `/lligues/<slug>/` | `table.br-apic-resultats` con la clasificación + enlaces `?jornada=N` |
| `/lligues/<slug>/?jornada=N` | La misma clasificación + un bloque por encuentro: `div.match-header` seguido de `table.match-results-table` |

Un encuentro son hasta 7 partidos: individuales (A/B/C contra X/Y/Z) y el doble (fila «Db»), que solo se juega si el marcador lo pide. En el doble, la celda de jugador lleva **dos** enlaces, uno por jugador, cada uno con su `codi_jugador`.

### 5bis.2 Tres trampas de esta fuente

1. **Encuentros sin acta.** Las incomparecencias se publican como 5-0 o 0-5 con `div.match-header` y **sin** tabla. Cuentan para la clasificación, así que el parser recorre las cabeceras, no las tablas; recorriendo tablas se perderían.
2. **Actas giradas.** En algunos encuentros la tabla lista los equipos al revés que la cabecera (aplazados, cambio de pista). Los nombres y el marcador se toman **siempre de la cabecera**, que es donde van juntos, y cuando el acta viene girada se voltean sus partidos (jugadores, sets y ganador) para expresarlos en términos del local del encuentro. Cruzar nombres del acta con el marcador de la cabecera invertía resultados.
3. **Throttling silencioso, y con dos grados.** fctt.cat responde HTTP 200 con contenido incompleto cuando se le encadenan peticiones a 1/s. Como no es un error HTTP, el reintento del `HttpClient` no salta. Hay dos niveles observados:

   | Grado | Síntoma | Consecuencia si no se detecta |
   |-------|---------|-------------------------------|
   | Fuerte | Página de ~157KB sin tabla de clasificación ni cabecera de temporada | El scraper concluye "sin jornadas disponibles" y se salta el grupo entero |
   | Suave | Página de tamaño normal, con las actas, **pero sin los enlaces `codi_jugador`** | Se parsea sin error y los partidos se guardan sin licencia — que es justo lo que necesita el filtro de "Mi temporada" |

   Mitigación: 2,5 s entre peticiones para esta fuente y `_fetch_valid()`, que reintenta con esperas de 5/15/30 s mientras falte contenido. Ojo con la comprobación de enlaces: **solo aplica a las páginas de jornada**. La página de grupo lista los encuentros de toda la temporada con su marcador pero sin detalle de jugadores, así que ahí la ausencia de `codi_jugador` es normal.

   El grado suave es especialmente traicionero porque la invariante de coherencia (marcador = partidos ganados) **sigue cumpliéndose**: no se pierden filas, solo los identificadores. Se detecta contando licencias vacías, no validando resultados.

### 5bis.3 Temporada

Las URLs de FCTT **no llevan la temporada**: `/lligues/grup-1/` sirve siempre la vigente. Como los números de jornada se repiten cada temporada, arrancar en septiembre sin haber cambiado `SEASON` escribiría la temporada nueva sobre la anterior en las mismas rutas. Por eso `SourceDef.season_label` declara cómo escribe la web la temporada (`2025/2026`) y el scraper aborta si la página publica otra distinta.

### 5bis.4 Identificador de jugador

FCTT identifica a los jugadores con `codi_jugador`, su código interno, **que no es el número de licencia de la RFETM**. Se guarda igualmente en los campos `player_*_license` porque es lo que identifica al jugador en esta fuente, pero un jugador que compita en las dos tendrá identificadores distintos en `fctt_*` y en `rfetm_*`.

---

## 5ter. Rànquings de la FCTT

### 5ter.1 La fuente

`/ranquings/` publica un PDF por categoría y revisión: `RK{temporada}_{edición}_{CATEGORIA}.pdf`. Hay 9 revisiones mensuales (`02`=Novembre … `10`=Juliol), 23 categorías cada una, más el ranking inicial de temporada.

La cabecera del PDF (`REVISIÓ 10 - JULIOL. TEMPORADA 2025-2026. v1.`) da edición, mes, temporada **y versión**, que es lo que permite detectar una revisión republicada.

**Las revisiones mensuales** comparten una tabla de 17 columnas, idéntica en todas las categorías (verificado en sènior, adaptat, benjamí, veterans i sub-21):

```
NP · jug · SEN · EDAT FCTT · CLUB · RK anterior · ΔRK · _ · RK · RD · _ · PJ · PG · PP · _ · P-any · CLASS
```

`extract_tables()` de pdfplumber las devuelve limpias — no hace falta parsear texto a mano como en las actas de RTBTT.

**El ranking inicial** (`Rk2526_ini.pdf`) es otro documento: una lista global de 8 columnas (POS, NP, JUGADOR/A, GEN, NAC, EDAT, CLUB, RANK), sin partidos ni desviación. Se carga como edición `ini`, categoría `GEN`.

### 5ter.2 `NP` es el `codi_jugador` de las ligas

Comprobado cruzando jugadores ya cargados en `competitions/fctt_2526`: el número de la primera columna del ranking es el mismo identificador que aparece en los enlaces de jugador de las páginas de liga. **El ranking enlaza con los partidos sin casar nombres**, que es lo que permite al agente hablar del rival concreto de la próxima jornada.

### 5ter.3 Qué se guarda y qué no

| Columna | Se guarda | Motivo |
|---------|:---------:|--------|
| `NP` | ✅ | Clave de todo |
| `jug`, `CLUB`, `EDAT FCTT` | ✅ nivel jugador | No se repiten por edición |
| `SEN` (posición), `RK` | ✅ | El ranking en sí |
| `ΔRK` | ✅ | Evita ambigüedades cuando el jugador se salta una edición |
| `RK anterior` | ❌ | Es `rk - d_rk`, y ya está el histórico |
| `RD` | ✅ | Desviación tipo Glicko: mide cuán fiable es el rating |
| `PJ/PG/PP` | ✅ | Partidos del periodo |
| `P-any` | ✅ | Partidos **acumulados en el año** (comprobado: P-any de una edición = P-any anterior + PJ intermedios) |
| `CLASS` | ✅ booleano | No es cosmético: un jugador con 2.857 puntos estaba 1.592º en març por no estar clasificado y 2º en juliol |
| Las 3 columnas vacías | ❌ | Artefactos de maquetación |

### 5ter.4 Duplicados y documentos ajenos

- `RK2526_06_SEM-1.pdf` es el mismo fichero resubido (WordPress añade `-1`): mismo contenido y misma versión `v2`. Se deduplica por (edición, categoría).
- `Partits_Jugats` **no es un ranking**: es la relación de partidos por deportista (fecha, evento, categoría, los dos jugadores) e **incluye torneos**, que hoy no cubrimos. Se ignora en esta fase; es la fuente natural para la siguiente.

---

## 5quater. `Partits_Jugats` de la FCTT

### 5quater.1 La fuente

El mismo `/ranquings/` publica, junto a cada revisión mensual,
`RK{temporada}_{edición}_Partits_Jugats.pdf`: el histórico partido a partido de
todos los jugadores con licencia. Tabla única de 14 columnas:

```
DATA · EVENT · CATEGORIA · LLIC 1 · JUG 1 (NOM) · JUG 2 (NOM) ·
TOT JUG1 · TOT JUG2 · rk 1 · rk 2 · ΔRK1 · ΔRK2 · RD'1 · RD'2
```

**Es incremental**: cada edición cubre solo el periodo desde la anterior y las
ediciones no se solapan —la intersección entre consecutivas es exactamente 0
filas, comprobado en 02/06/09/10—, así que la unión de las 9 es la temporada.
Volumen: ~80.000 filas ≈ 40.000 partidos, con picos de 17.245 filas (edición 02,
173 páginas).

Cubre **más competición que ninguna otra fuente**: las dos ligas que ya
scrapeamos (`Lliga RTB` → RTBTT, `LLNN`/`ESTATAL` → RFETM) y una treintena de
torneos que no cubre nadie más (`Top Català`, `Open Internacional de Vic`,
`Circuit Promeses ZonaTT`…). 64 eventos distintos en la temporada.

### 5quater.2 Solo hay licencia del jugador 1 — el espejo la recupera

`JUG 2` viene **solo con nombre**. La licencia del rival se recupera del propio
PDF: cada partido aparece **dos veces**, una desde cada jugador, y en la fila
espejo el rival es `JUG 1`. El emparejado exige que el marcador esté invertido y
los ratings cruzados; sin esa validación, los homónimos —19 nombres repetidos en
la edición 02— atribuirían partidos al jugador equivocado en silencio.

Cobertura del espejo: 100 % en la edición 10, 97,2 % en la 09, 96,1 % en la 02,
88,6 % en la 06. **Las filas sin espejo no son un fallo del parseo**: en la
edición 06 las 1.197 son *todas* de competición estatal (`ESTATAL`, `LLNN`,
`CE ABS`, `COPAS`), donde el rival es de otra federación y la FCTT no le calcula
ranking, así que no tiene fila propia. Se guardan con `opponent.external = true`
y sin código.

### 5quater.3 Lo que **no** hace falta deduplicar

Las dos caras del partido se conservan, una en el `match_log` de cada jugador:
el modelo es por jugador, no por partido. El espejo solo sirve para averiguar la
licencia del rival. Eso elimina de raíz la pregunta de qué hacer cuando dos
jugadores se enfrentan dos veces el mismo día en el mismo evento (grupo y
eliminatoria), que en la edición 10 pasa 13 veces.

### 5quater.4 El marcador miente en el 0,27 % de las filas

El ΔRK de la fuente es de fiar —su suma cuadra con el ranking en las nueve
ediciones, sin una sola discrepancia— pero **el marcador no siempre**: hay 264
filas donde el jugador gana 2-0 y pierde puntos, o pierde y los gana. Encaja con
que la fuente escriba el partido desde el otro jugador sin girar el resultado.

Se corrige con una heurística documentada en `_row_from`, que baja los jugadores
con el balance mal de 70 a 27 pero rompe 11 que estaban bien. **No es la
solución definitiva**: está en `todos_a_revisar.md` §10, y pasa por reconciliar
con el ranking del mes en vez de adivinar. Lo que importa aquí es que la
comprobación cruzada del §5 es lo que lo destapó; sin ella, 70 jugadores tendrían
las victorias y las derrotas cambiadas y nadie lo habría notado.

### 5quater.5 Detalles menores

- No hay **ni una fila de dobles** en las cuatro ediciones inspeccionadas, ni
  siquiera en el `CC Màsters Ind&Db`. Un jugador por columna.
- `event` **no es único**: la edición 06 trae `Lliga RTB J1` con fecha de enero,
  que es de la segunda vuelta. La fecha forma parte de la identidad del partido.
- El sufijo `-1` de WordPress también aparece aquí.

---

## 6ter. Partidos jugados en Firestore

```
rankings/{competition_id}/players/{codi_jugador}
    record: { played, won, lost, tournaments, last_played, best_win{…} }
  /match_log/{edición}                            ← "02".."10"
      edition, label, from, to, played, won, lost, d_rk_total
      matches: [ { date, event, event_type, category,
                   opponent{code|external, name},
                   sets, opp_sets, won, rk, opp_rk, d_rk, rd } … ]
```

**Un documento por jugador y edición.** Un documento por partido serían ~80.000
escrituras por temporada (cuatro días de cuota) y meterlo todo en el documento
del jugador lo haría crecer sin techo y obligaría a reescribirlo entero cada
mes. El array no se descontrola: 5-8 partidos de media por jugador y edición,
máximo medido 36.

| Pregunta de la app | Coste |
|---|---|
| Forma reciente del rival | 1 lectura (última edición), 2-3 para tres meses |
| ¿Cómo voy contra este jugador? | ≤9 lecturas propias, filtrando por `opponent.code` |
| Titular del dossier del rival | 0 — `record` viaja en el documento de jugador |

`match_log` **debe estar exento de indexación**, por lo mismo que `history`: un
array de mapas genera una entrada de índice por campo hoja y nunca se consulta,
se lee entero.

### 6ter.1 Cuota

| Operación | Escrituras |
|---|---:|
| Carga histórica (9 ediciones) | ~15.000 |
| `record` de temporada | ~2.800 |
| Actualización mensual | ~2.500 |
| Límite diario del plan Spark | 20.000 |

La histórica cabe en un día pero sin margen, así que se hace en dos tandas con
`--edition`. La caché ahorra descargas y escrituras, **no parseo**: los partidos
de todas las ediciones hacen falta en memoria para recalcular el `record`, y una
edición sin cambios se parsea igual pero no se reescribe.

> **El día de cuota no es el día natural.** Las cuotas del plan gratuito se
> reinician a medianoche del Pacífico, que en verano son **las 09:00 CEST**.
> Lanzar una tanda a las 00:30 de la madrugada la mete en la cuota del día
> *anterior*, junto con todo lo que se haya cargado esa tarde. Comprobado el
> 14/08/2026: la carga de la edición 05 murió con
> `RESOURCE_EXHAUSTED: Quota exceeded` porque compartía día de cuota con la
> recarga de rànquings de la tarde previa.
>
> Cuando eso pasa, **no hay que limpiar nada**: los hashes se confirman solo tras
> una escritura correcta, así que la edición a medias se reescribe entera en el
> siguiente intento y las anteriores se saltan.

---

## 5quinquies. Rànquings de la RFETM

### 5quinquies.1 Una petición por sexo y mes

`https://rfetm.es/ranking/?accion=…` con `year`, `mes`, `sexo` y `temporada`.
De las acciones disponibles solo hacen falta dos:

- **`accion=0`** — índice de todos los meses publicados. La temporada 2025-2026
  es la `temporada=11` (contador propio de la RFETM: `año_inicio − 2014`) y tiene
  10 meses, de setembre a juny. La 2024-2025 además incluye un `mes=0`, que es el
  ranking inicial.
- **`accion=3`** — ranking global del mes, 31 columnas, una fila por jugador:

```
LIC · NOMBRE · CLUB · FA · CAT · AÑO · ANT · ACT ·
LLNN · C.ESP · T.EST · TOP · SP.M · PJ · PG · PP · CLAF ·
NAC · SEN · S21 · JUV · CAD · INF · ALE · BEN · V40 · V50 · V60 · V65 · V70 · V75
```

Las 14 últimas son **la posición en cada categoría**, así que los rànquings por
categoría (`accion=4`) no se descargan: se derivan ordenando. La temporada
entera son **20 peticiones** y 78 segundos, frente a los 208 PDFs y 117 MB de la
FCTT.

### 5quinquies.2 Cuatro trampas de esta fuente

1. **La cabecera viene rota.** Cada `<th>` contiene todos los encabezados que
   quedan a su derecha concatenados. No se puede parsear por nombre: se va por
   posición y se **aborta** si el ancho no es 31 (`RfetmFormatError`).
2. **`99999` no es una posición**, es "no compite en esa categoría". Se descarta.
3. **`NAC` no es el ranking general.** Es el nacional, restringido por
   elegibilidad: el nº1 global masculino (2.838 puntos) no aparece en `NAC`, y el
   nº1 de `NAC` tiene 2.619.
4. **Las categorías no llevan el sexo dentro.** `NAC` es la misma para ambos, al
   revés que en la FCTT (`SEM`/`SEF`). Por eso las claves se guardan como
   `NAC_M` / `NAC_F`, con `_` y no con `-`, que en una ruta de campo habría que
   escapar.

### 5quinquies.3 Las licencias no son las de la FCTT

Espacios de identificadores distintos: YEH Chih-Wei es el `33851` de la RFETM y
el `13209` de la FCTT; PROVOST Damien, `27561` y `10218`. Un catalán que juega la
liga nacional tiene **dos identidades** sin ningún campo que las una.

La unión se puede deducir de los datos —los partidos de liga nacional aparecen en
las dos fuentes con el mismo día, marcador y ratings— pero **no se debe casar por
nombre** mientras tanto: sería irreversible y contaminaría el dato bueno.

---

## 6bis. Rànquings en Firestore

Colección raíz `rankings`, separada de `competitions` para no mezclar partidos con clasificaciones:

```
rankings/{competition_id}                       ← fctt_2526
    organization, season, source_url, last_edition, player_count, updated_at
  /editions/{ini,02..10}
      number, month, season, version, label, categories[], player_count
    /categories/{SEM,SEF,40M,…}
        id, title, source_url, player_count
        top: [ {pos, code, name, club, rk} … 100 ]
  /players/{codi_jugador}
      code, name, club, edat_fctt, gender, updated_at
      current: { SEM: {pos, rk, edition}, … }
      history: { SEM: { "10": {pos, rk, d_rk, rd, pj, pg, pp, p_any, classified}, … }, … }
```

**Por qué este reparto:**

- **`players/{código}`** resuelve de **una sola lectura** "quién es mi rival y cómo ha evolucionado", que es como llega la app: siempre tiene el código del jugador desde el partido.
- **`current`** es un mapa de campos fijos, así que Firestore lo indexa automáticamente y permite `orderBy('current.SEM.pos').limit(20)` para sacar un top sin índices compuestos. Los jugadores que no están en esa categoría quedan fuera solos porque les falta el campo.
- **`top` por edición y categoría** cubre lo que `current` no puede: la clasificación **de un mes pasado**. Ordenar por una posición histórica exigiría un índice por cada categoría×edición (230), así que se guarda ya calculada.

> **Ojo al consultar categorías que empiezan por dígito** (`21F`, `40M`, `50F`, `60M`, `65M`, `70M`…): en una ruta de campo escrita como texto hay que escaparlas con acentos graves —`current.\`40M\`.pos`— o la consulta no devuelve nada, **sin dar error**. En Flutter lo limpio es evitar el texto y usar `FieldPath(['current', '40M', 'pos'])`, que no pasa por el parseo de rutas. Verificado el 13/08/2026: `current.70M.pos` devuelve 0 resultados y `current.\`70M\`.pos` devuelve la clasificación correcta.

### 6bis.2 Índices

`history` **debe estar exento de indexación** (Consola → Firestore → Índices → Exenciones de un solo campo → grupo de colecciones `players`, campo `history`, los tres modos desactivados). No se consulta nunca —se lee entero con el documento— y en cambio genera del orden de 1,5 millones de entradas de índice: encarece las escrituras, infla el almacenamiento y es lo que provoca el error `Transaction too big` al escribir en lotes grandes.

`current` **sí tiene que seguir indexado**: es lo que hace posible el `orderBy` de los tops sin índices compuestos.

### 6bis.1 Cuota de escrituras

| Operación | Escrituras |
|-----------|-----------:|
| Carga histórica completa (10 ediciones) | ~3.000 |
| Actualización mensual | ~2.800 |
| Límite diario del plan Spark | 20.000 |

Esto **depende de una decisión de diseño**: las ediciones se acumulan en memoria y cada jugador se escribe **una sola vez**, con `set(merge=True)`. Escribiendo edición a edición serían ~28.000 escrituras y la carga inicial no cabría en un día. El `merge=True` es además lo que permite ejecutar mes a mes sin releer nada: la edición nueva se añade dentro de `history` sin pisar las anteriores.

---

## 6. Estructura en Firestore

```
competitions/{competition_id}                 ← rtbtt_2526 | rfetm_2526
  ├─ name, organization, season, source_url
  ├─ categories/{category_id}                 ← PREF, 1A, liga1_M...
  │   └─ groups/{group_id}                    ← PREF_G1, liga1_g0_M...
  │       ├─ id, name, phase, source_url
  │       ├─ teams/{team_id}                  ← clasificación
  │       └─ matchdays/{N}                    ← jornada
  │           ├─ number, date, pdf_url, pdf_hash, status
  │           └─ matches/{match_id}           ← encuentro
  │               ├─ team_local_*, team_visitor_*
  │               ├─ score_local, score_visitor
  │               └─ individual_matches[]     ← embebido en el doc
  │                   ├─ player_*_name, player_*_license
  │                   ├─ sets[] = [{local, visitor}, ...]
  │                   ├─ winner = "local" | "visitor"
  │                   └─ is_doubles + player_*_name_2/_license_2
```

Los `individual_matches` van **embebidos** en el documento del encuentro, no como subcollection. Esto reduce reads en la app (un solo `get` trae todo el encuentro).

### 6.1 IDs

| Entidad | Patrón |
|---------|--------|
| Competition | `rtbtt_2526`, `rfetm_2526` |
| Category | `PREF`, `1A`, `liga1_M` |
| Group | `PREF_G1`, `liga1_g0_M` |
| Team | `{group_id}_{slug}` |
| Match | `{matchday_n}_a{acta_num}_{local_slug}_vs_{visitor_slug}` |
| IndividualMatch | `{match_id}_p{order}` |

---

## 7. Configuración y caché local

### 7.1 `.env`

```bash
SEASON=2025-2026
FIRESTORE_PROJECT_ID=tu-proyecto-firebase
GOOGLE_APPLICATION_CREDENTIALS=./credentials/firebase-service-account.json
OUTPUT_DIR=./output
CACHE_DIR=./output/cache
PDFS_DIR=./output/pdfs
LOG_LEVEL=INFO
```

> Las URLs y los ids de competición **ya no están aquí**: se derivan de `SEASON` en `sources.py`. `LOG_LEVEL` sí se aplica de verdad desde el 13/08/2026 (antes se leía y no lo usaba nadie, con lo que las ejecuciones largas no imprimían nada y parecían colgadas).

### 7.2 Estructura `output/`

```
output/
├── cache/
│   └── hashes.json     ← {"groups": {...}, "matchdays": {...}}
├── pdfs/
│   └── PREF_G1_jornada21.pdf
└── logs/
    └── ejecuciones
```

### 7.3 Invalidaciones del caché

| Acción | Hash de grupo | Hashes de jornadas | PDFs locales |
|--------|---------------|---------------------|--------------|
| `run` (sin flags) | Comparado, salta si igual | Comparado por jornada | Reusados si existen |
| `run --force` | Borrado al inicio | Borrados al inicio (RFETM) | Reusados si existen |
| `run --jornada N` | Ignorado, no actualiza al final | Solo el de la jornada N | El de la jornada N se borra |
| `parse` | No tocado | No tocado | Solo lectura |

---

## 8. Casos de uso habituales

### 8.1 Cron diario

```bash
# Toda la temporada en ambas competiciones
python main.py rtbtt run
python main.py rfetm run
```

Los hashes hacen que la mayoría de grupos se salten en segundos.

### 8.2 Actualizar la última jornada después de que se publiquen los resultados

```bash
python main.py rtbtt run --category PREF --group PREF_G1 --jornada 21
python main.py rfetm run --group liga1_g0_M --jornada 18
```

### 8.3 Inspeccionar un acta antes de subirla

```bash
python main.py rtbtt download --group PREF_G1
python main.py rtbtt parse --group PREF_G1 --jornada 21
python main.py rtbtt parse --group PREF_G1 --jornada 21 --debug-pdf   # si algo no cuadra
```

### 8.4 Recompilar todo (cambio en parser, schema, etc.)

```bash
python main.py rtbtt run --force
python main.py rfetm run --force
```

---

## 9. Estructura del proyecto

```
tt-scraper/
├── main.py                        # CLI (Click): rtbtt {run,parse,download,list}, rfetm {run,list}, validate
├── config.py                      # Carga de .env
├── design/
│   └── functionalities_overview.md (este documento)
├── scrapers/
│   ├── base.py                    # CompetitionScraper (ABC)
│   ├── rtbtt_scraper.py           # Implementación RTBTT
│   └── rfetm_scraper.py           # Implementación RFETM
├── parsers/
│   ├── html_parser.py             # HTML RTBTT (índice + página de grupo)
│   ├── pdf_parser.py              # PDFs de actas RTBTT
│   └── rfetm_html_parser.py       # HTML RFETM (índice, clasificación, jornadas)
├── storage/
│   ├── firestore_client.py        # Escrituras batch en Firestore
│   └── local_cache.py             # hashes.json + PDFs locales
├── models/
│   ├── competition.py             # CompetitionIndex, Category, Group
│   ├── match.py                   # Match, IndividualMatch, Matchday, SetScore
│   └── team.py                    # Team
├── utils/
│   ├── http_client.py             # HTTP con reintentos y rate-limit
│   ├── hashing.py                 # md5_str, md5_bytes
│   └── logger.py                  # Logging con Rich
├── credentials/                   # firebase-service-account.json (gitignored)
├── output/                        # cache, pdfs, logs (gitignored)
└── tests/
```

---

## 10. Migración futura a Cloud Run

La separación entre `scrapers` / `parsers` / `storage` permite mover el scraper a Cloud Run sin cambios estructurales:

1. Dockerfile con Python 3.11 y dependencias
2. Mover credenciales a Secret Manager
3. Cloud Scheduler dispara el contenedor cada N horas
4. PDFs en Cloud Storage en vez de disco local (o en memoria)
5. `config.py` lee desde variables de entorno del servicio

El caché de hashes pasaría a Firestore o Memorystore para sobrevivir entre ejecuciones del contenedor efímero.
