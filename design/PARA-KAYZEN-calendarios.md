# Calendarios y rankings FCTT, RFETM y RTBTT 2026-2027 en `tt-data` (para Kayzen)

Ya están cargados en la D1 `tt-data` los **calendarios completos** de tres ligas de
la temporada 2026-2027, con los **partidos aún no jugados**, para que el agente
pueda ver los próximos partidos y preparar el siguiente:

- **RFETM** (liga nacional) → `competition_id = 'rfetm_2627'` — cargado 2026-09-18.
- **RTBTT** (territorial de Barcelona, licencia FCTT) → `competition_id = 'rtbtt_2627'`
  — cargado 2026-09-21.
- **FCTT** (Tercera Divisió Masculina, "estatals" catalanes) → `competition_id = 'fctt_2627'`
  — cargado 2026-09-21 (solo TDM de momento; Copa Femenina aún sin publicar).

Además del calendario, está cargado el **ranking FCTT 2026-2027 y el historial de
partidos por jugador** (edición 01, "Setembre"): ver **§11**.

La mayor parte de este documento (§2–§6) describe el calendario de RFETM. **RTBTT
funciona igual con dos diferencias (§8); FCTT igual, ver §10; rankings, §11.**

---

## 1. Qué hay cargado

Competición nueva `competition_id = 'rfetm_2627'` (la 2025-2026 sigue intacta en
`rfetm_2526`; conviven, no se pisan):

| Tabla | Filas (rfetm_2627) | Qué |
|---|---|---|
| `competition` | 1 | la liga 2026-2027 |
| `comp_group` | 32 | grupos (Superdiv M/F, Div. Honor M/F, 1ª M/F, 2ª M) |
| `team` | 366 | equipos con su clasificación (a 0: la liga no ha empezado) |
| `matchday` | 564 | jornadas |
| `encounter` | 3.046 | **todos los partidos del calendario** |
| `individual_match` | 0 | *aún ninguno: no se ha jugado nada* |

Como la temporada **no ha arrancado**, hoy los 3.046 `encounter` están en estado
`scheduled` y no hay ni un `individual_match`. A medida que se juegue, cada
encuentro pasará a `completed` y aparecerán sus partidos individuales (ver §3).

---

## 2. Cómo leer los próximos partidos

Todo vive en `encounter`. Las columnas relevantes:

- `status`: **`'scheduled'`** (por jugar) · `'completed'` (jugado) · `'in_progress'`
  (jornada a medias). Filtra por `'scheduled'` para el calendario futuro.
- `date`: fecha **con hora de saque** en ISO, p. ej. `'2026-10-02T17:30:00'`.
  ⚠️ **Puede ser `NULL`** si la RFETM aún no ha publicado día/hora de ese partido.
- `team_local_id` / `team_visitor_id`: id numérico del equipo (el `equipo=` de la web).
- `team_local_name` / `team_visitor_name`: nombre del equipo.
- `matchday_number`, `group_id`, `acta_url` (vacío hasta que se juega).

**Próximos partidos de la liga (globales):**
```sql
SELECT date, group_id, team_local_name, team_visitor_name
FROM encounter
WHERE competition_id = 'rfetm_2627'
  AND status = 'scheduled'
  AND date IS NOT NULL
  AND date >= :ahora            -- ISO 'YYYY-MM-DDTHH:MM:SS'
ORDER BY date
LIMIT 20;
```

**Próximo partido de un equipo concreto (por id de equipo):**
```sql
SELECT date, team_local_name, team_visitor_name, matchday_number
FROM encounter
WHERE competition_id = 'rfetm_2627'
  AND status = 'scheduled'
  AND (team_local_id = :team OR team_visitor_id = :team)
  AND date IS NOT NULL
ORDER BY date
LIMIT 1;
```

> Ordenar por `date` en SQLite pone los `NULL` primero en `ASC`; por eso los
> ejemplos filtran `date IS NOT NULL`. Si quieres también los sin fecha, quítalo.

---

## 3. Ciclo de vida de un partido (importante)

El **id de un `encounter` es estable**: `'{jornada}_{idLocal}_{idVisitante}'`
(p. ej. `'1_73_811'`). El **mismo id** se usa cuando el partido está en el
calendario y cuando ya se ha jugado, así que al jugarse el `INSERT ... ON CONFLICT`
**actualiza la fila en su sitio**:

- `status`: `scheduled` → `completed`
- se rellenan `score_local` / `score_visitor` y `acta_url`
- aparecen sus filas en `individual_match` (con licencias de jugador)

No hay filas duplicadas ni "fantasma": el partido de calendario y el jugado son la
misma fila. Si consultas por `status='scheduled'`, nunca verás partidos ya jugados.

---

## 4. Aviso: el calendario es a nivel de EQUIPO, no de jugador

Los `encounter` enlazan **equipos**, no licencias. Para "el próximo partido de un
jugador" hace falta saber **en qué equipo juega**, y esa relación **no está en el
calendario** (solo aparece cuando el jugador dispute un partido → `individual_match`).

Al principio de temporada, para deducir el equipo de un jugador tienes dos vías:

1. **Temporada anterior** (`rfetm_2526`): el equipo con el que jugó en
   `individual_match` (por `player_local_lic` / `player_visitor_lic`). Mismo club
   la mayoría de las veces, pero puede cambiar de equipo/categoría.
2. **En cuanto juegue la 1ª jornada** de 2026-2027: su `individual_match` ya te da
   el equipo real de esta temporada; a partir de ahí, sus próximos partidos son los
   `scheduled` de ese equipo.

No hay forma fiable de mapear jugador→equipo por nombre; usa las licencias.

---

## 5. Índices para las consultas por equipo (✅ ya creados)

Las consultas de §2 filtran por `team_local_id` / `team_visitor_id`. Para que no
escaneen todos los encuentros de la liga (D1 factura **filas escaneadas**), están
creados en `tt-data` estos dos índices, ya con `ANALYZE` pasado:

```sql
CREATE INDEX idx_enc_local   ON encounter(competition_id, team_local_id);
CREATE INDEX idx_enc_visitor ON encounter(competition_id, team_visitor_id);
```

Comprobado con `EXPLAIN QUERY PLAN`: la consulta "próximo partido de un equipo"
usa `MULTI-INDEX OR` sobre `idx_enc_local` + `idx_enc_visitor` (nada de scan).

Están también en `d1/indexes.sql`, así que se recrean en futuras cargas. **Kayzen
depende de estos dos nombres**: igual que con `idx_pm_player`/`idx_pm_opponent`, si
algún día hay que renombrarlos avisaremos para no romperos las consultas.

---

## 6. Cadencia de actualización

Todas las ligas (RFETM + las 4 catalanas) se **recargan semanalmente** con dos
scripts incrementales (`tt-scraper/scripts/weekly_rfetm.sh` y `weekly_fctt.sh`):

- Las **jornadas jugadas** pasan a `status='completed'` con su marcador y sus
  `individual_match`; el resto sigue como `scheduled`. Los cambios de fecha/hora del
  calendario se actualizan en sitio. Todo por upsert sobre el mismo `encounter_id`.
- Tras cada tanda se lanza `ANALYZE;`, así que los planes de consulta se mantienen.
- Es **incremental**: entre semana solo cambia lo nuevo. Una acta puntual puede tardar
  una pasada extra si la web devolvió la página incompleta (se reintenta sola).
- Ojo: la primera jornada de cada división se publica en fechas distintas (p. ej. en
  RFETM las divisiones inferiores empiezan antes que Superdivisión), así que verás
  `completed` apareciendo de forma escalonada por categoría.
- **Detección de resultados pendientes**: al final, cada script lista los encuentros
  cuya **fecha ya pasó pero siguen `scheduled`** (la federación aún no ha publicado el
  acta, o el partido está aplazado). Es normal: la FCTT a veces publica un resultado uno
  o dos días tarde. Sirve para saber qué grupos volver a cargar más adelante; la
  siguiente pasada los recoge sola. Consulta equivalente:
  `status='scheduled' AND date(date) < date('now')`.

---

## 7. Resumen (RFETM)

| | |
|---|---|
| **Competición** | `rfetm_2627` (convive con `rfetm_2526`) |
| **Dónde** | tabla `encounter`, `status='scheduled'` = calendario futuro |
| **Fecha** | `date` ISO con hora; puede ser `NULL` si no publicada |
| **Id estable** | `'{jornada}_{idLocal}_{idVisitante}'`; scheduled→completed en la misma fila |
| **Nivel** | equipo (no jugador): mapea jugador→equipo por licencia vía `individual_match` |
| **Índices** | ✅ creados `idx_enc_local` y `idx_enc_visitor` (por si dependéis de los nombres) |

---

## 8. RTBTT (`rtbtt_2627`) — igual que RFETM, con dos diferencias

> ⚠️ **DESACTUALIZADO (2026-09-27).** Barcelona ya **no** se carga de rtbtt.com: la FCTT
> integró las territoriales en fctt.cat y `rtbtt_2627` se recargó desde ahí. **Léelo en
> §12** (ids de equipo numéricos, y ahora con actas individuales cuando se jueguen). Esta
> sección describe el flujo antiguo (rtbtt.com) y queda como histórico.


RTBTT vive en las **mismas tablas** (`encounter`, `matchday`, `team`, `comp_group`)
y se lee **igual** que RFETM (§2): filtra por `competition_id = 'rtbtt_2627'` y
`status`. Cargado: 40 grupos, 359 equipos, 652 jornadas, **2.900 encounters** (hoy
todos `scheduled`; la liga empieza el 26/09/2026). Los índices `idx_enc_local` /
`idx_enc_visitor` sirven igual.

**Diferencia 1 — sin partidos individuales (de momento).** La fuente de RTBTT que
está publicada es el **calendario en PDF**, que da el **resultado por EQUIPO**
(`score_local` / `score_visitor` = encuentros ganados por cada lado, sobre 6-7)
pero **NO** los partidos individuales. Por eso para `rtbtt_2627` **no habrá filas en
`individual_match`** (ni licencias de jugador) hasta que la RTBTT publique las actas
detalladas (`actes_2627.html`, aún no disponible). En cuanto se jueguen jornadas, los
`encounter` pasarán a `completed` **con el marcador por equipo**; el detalle jugador
a jugador llegará después. (RFETM sí trae `individual_match` al jugarse.)

**Diferencia 2 — ids de equipo y de encuentro son de texto.** RTBTT no tiene id
numérico de equipo: `team_local_id` / `team_visitor_id` son **slugs del nombre**
(p. ej. `'ctt_vilafranca'`). Y el id del encuentro **incluye el grupo** para ser
único entre categorías (un mismo club juega en varias ligas de veteranos):
`'{group_id}_{jornada}_{slugLocal}_{slugVisitor}'` (p. ej.
`'PREF_G1_1_ctt_vilafranca_ctt_collbato'`). Sigue siendo estable scheduled→completed.

`date` con hora (puede ser `NULL`); `acta_url` vacío. Se recargará **semanalmente**
desde el mismo PDF, que la RTBTT va rellenando con los resultados.

**Ojo, el formato de `team_local_id` / `team_visitor_id` cambia entre temporadas.**
En `rtbtt_2627` son slugs "pelados" del nombre (`'cn_mataro'`). En temporadas
anteriores llevan el grupo delante, porque se cargaron con otro flujo (el de actas):

| Competición | Ejemplo |
|---|---|
| `rtbtt_2627` | `cn_mataro` |
| `rtbtt_2526` | `PREF_G1_cn_mataro` |
| `fctt_2526` | `TDM_G3_ue_sant_cugat` |

No lo unificamos (cambiar ids de una temporada ya cargada es peor). Y para cruzar el
equipo de un jugador entre temporadas **no basta con quitar el prefijo**: el grupo
cambia (CN MATARÓ era `PREF_G1` y ahora es `PRIM_G1`). Cruzad por **nombre de equipo**
(o por licencia del jugador vía `individual_match`), no por id.

**`team` no tiene filas de `rtbtt_2526`.** Esa temporada tiene `encounter` (907) e
`individual_match` (5.607) pero la clasificación de equipos no se cargó (hueco del
flujo de actas).

> **Decisión**: **no lo backfilleamos**. El flujo de calendario generaría ids de
> equipo con otro formato (slug pelado) que **no casarían** con los `encounter` ya
> cargados de `rtbtt_2526` (que los llevan con el grupo delante), así que rellenar
> `team` ahí crearía equipos huérfanos: peor el remedio. Para el club/clasificación
> del año pasado en territorial, `team` de `rtbtt_2526` **no está**; usad
> `fctt_2526`/`rfetm_2526`. Si resulta imprescindible, avisad y montamos un backfill
> a medida (con los ids del flujo de actas).

**La "vía 2" del §4 no aplica a RTBTT esta temporada.** Como `rtbtt_2627` no tendrá
`individual_match` hasta que salgan las actas, no podréis deducir el equipo de un
jugador "en cuanto juegue la 1ª jornada". Para RTBTT, deducidlo del año pasado o
preguntándole al jugador.

---

## 9. Fechas: año derivado del mes (robusto ante erratas de la fuente)

Las federaciones publican a veces el **año mal** en el calendario. Encontrados y
**corregidos** (2026-09-21):

- **RTBTT**, última jornada de veteranos (J14): la fuente la fechaba en **febrero de
  2026** (un año antes); son 68 encuentros. Corregido a **2027-02-15…18**.
- **RFETM**, 2 encuentros de la J5: la fuente publica `26/11/2031` y `31/10/2027`.
  El de octubre (GRANÁZUBIA VSPORT – GRUPO ÁREA DON BENITO) queda **2026-10-31**,
  correcto. El de noviembre (CTM VEGAS DEL GENIL – INSPRO DON BENITO) queda
  **2026-11-26**.

  > **Decisión sobre VEGAS DEL GENIL** (`id = '5_15_20191280'`): en la fuente el
  > **día** también está mal, no solo el año. Es un encuentro de la **J5**, cuyo
  > fin de semana es **31/10–01/11/2026**, y lo más probable —por los horarios de
  > las 11:00 de ese grupo, que juegan en domingo— es **2026-11-01 11:00**. Pero la
  > derivación por mes solo puede arreglar el año (→ 2026-11-26); **no inventamos el
  > día**. Se queda en `2026-11-26` a la espera de que la RFETM corrija su fuente.
  > Ya no rompe nada (cae dentro de temporada y ordena bien); solo está ~3 semanas
  > desplazado. Si a Kayzen le viene mejor el dato visible correcto, avisad y lo
  > fijamos a mano a `2026-11-01`.

Cómo: el año de una fecha de calendario se **deriva del mes + la temporada**
(sep–dic → año de inicio; ene–ago → siguiente), en vez de fiarse del que imprime la
fuente. Además, la carga **avisa** de cualquier fecha que caiga fuera de la ventana
de la temporada (posible errata de mes que la derivación no salva). Por eso las dos
consultas de sanidad del §5 de vuestro documento ya devuelven **cero filas**.

---

## 10. FCTT (`fctt_2627`) — igual que RFETM, con dos diferencias

FCTT vive en las **mismas tablas** y se lee **igual** (§2), filtrando
`competition_id = 'fctt_2627'`. Cargado: **Tercera Divisió Masculina** (TDM), 3
grupos, 36 equipos, 66 jornadas, **396 encounters** (hoy todos `scheduled`; empieza
el 26/09/2026, con `date` a hora local). Fecha con hora, año derivado del mes.

**Diferencia 1 — sin partidos individuales, y no solo "de momento".** La FCTT
**rediseñó** las páginas de grupo: el calendario vive ahora en bloques
`div.match-container` que dan **fixtures + resultado por EQUIPO**, pero el detalle
jugador a jugador (con `codi_jugador`) ya **no** aparece con el markup antiguo. Así
que `fctt_2627` **no tendrá `individual_match`** hasta que adaptemos el parseo del
acta al formato nuevo, lo cual solo se puede hacer **cuando se juegue la 1ª jornada**
y podamos ver cómo renderiza un encuentro disputado. Hasta entonces: fixtures y, al
jugarse, marcador por equipo (`scheduled → completed`).

**Diferencia 2 — ids de equipo numéricos (como RFETM, no como RTBTT).** En FCTT
`team_local_id` / `team_visitor_id` son el **`team_name_id`** de fctt.cat (numérico,
p. ej. `'130'`), estable y único. El id de encuentro es
`'{jornada}_{idLocal}_{idVisitante}'` (p. ej. `'1_130_149'`), sin prefijo de grupo
(no hace falta: los ids son únicos). Ojo: este `team_name_id` es de fctt.cat y **no**
coincide con los ids de equipo de `fctt_2526` (que se cargaron con otro flujo, con
slug+grupo, ver §8); cruzad por **nombre** entre temporadas.

Solo está **TDM** publicada. Cuando la FCTT publique Copa Femenina y demás, se
cargan igual (`fctt calendar`).

---

## 11. Ranking FCTT 2026-2027 y partidos por jugador

Aparte de los calendarios, está cargada la **edición 01 (Setembre) del ranking
catalán FCTT** y su **historial partido a partido**. Esto es independiente de las
ligas: no vive en `encounter`/`competition`, sino en las tablas de ranking y en
`player_match`, con `federation = 'FCTT'` y `season = '2026-2027'`.

Qué hay cargado (2026-09-25):

| Tabla | Filas (FCTT, 2026-2027) | Qué |
|---|---|---|
| `ranking_edition` | 1 | la edición 01 "Setembre 2026/2027" |
| `ranking_snapshot` | 2.775 | una fila por jugador: puntos, RD, PJ/PG/PP… |
| `ranking_position` | 9.729 | posición del jugador en cada categoría (SEM, V40M, S21M…) |
| `player` | 2.775 | identidad (upsert: comparte `(federation, lic)` con temporadas previas) |
| `player_match` | 3.043 | historial de esta edición (441 jugadores) |

Notas para el agente:
- La **licencia** (`lic`) es el `codi_jugador` de la FCTT, el mismo que enlaza con
  `player_match` y con los `individual_match` de las ligas FCTT. **No** es la
  licencia RFETM (para cruzar FCTT↔RFETM está `player_link`).
- **Top de una categoría**: `ranking_position` filtrando `federation='FCTT'`,
  `season='2026-2027'`, `category=?`, `ORDER BY pos` (índice `idx_pos_top`).
- **Ranking/estado de un jugador**: `ranking_snapshot` por `(federation, lic,
  edition_id)` (índice `idx_snap_player`).
- **Sus partidos**: `player_match` por `(federation, lic, date)` (índice
  `idx_pm_player`); head-to-head por `opp_lic` (`idx_pm_opponent`). Ver
  `PETICION-A-TT-SCRAPER.md`.
- **Fechas de la ed. 01**: los partidos son de **jun–ago 2026** (torneos de verano
  que alimentan el ranking de septiembre); es correcto, no es un error.
- Se cargará **una edición nueva al mes** según la publique la FCTT. Ojo interno:
  el índice de `/ranquings/` lista varias temporadas a la vez, pero la carga filtra
  por la temporada configurada (no se mezclan años).

---

## 12. Ligas territoriales catalanas en fctt.cat (RTB · RTG · RTT)

La FCTT ha integrado en su web los calendarios de las **tres representaciones
territoriales**, con el mismo sistema que TDM (§10). Cargadas 2026-09-27. Se leen
**igual** (tabla `encounter`, `status='scheduled'|'completed'`), filtrando por su
`competition_id`:

| Competición | Territorial | Grupos | Equipos | Encuentros |
|---|---|---|---|---|
| **`rtbtt_2627`** | Barcelona (RTB) | 40 | 358 | 2.882 |
| **`rtg_2627`** | Girona (RTG) | 4 | 50 | 25 *(calendario parcial; irá creciendo)* |
| **`rtt_2627`** | Tarragona (RTT) | 5 | 50 | 450 |

Puntos clave:
- **Barcelona (`rtbtt_2627`) cambió de fuente**: antes venía de rtbtt.com (ids de
  equipo tipo slug+grupo, sin partidos individuales); **ahora viene de fctt.cat** con
  **`team_name_id` numérico** (como TDM/RFETM) e id de encuentro
  `'{jornada}_{idLocal}_{idVisitante}'`. Si teníais cacheados los ids antiguos de
  Barcelona, **han cambiado** (se reemplazó el `rtbtt_2627` entero).
- `team_local_id`/`team_visitor_id` son numéricos (`team_name_id` de fctt.cat), como en
  TDM (§10); **no** coinciden con los de `rtbtt_2526`. Cruzad por nombre entre temporadas.
- **Partidos individuales**: aún **no** (igual que TDM). Cuando se jueguen, el detalle
  jugador-a-jugador (con `codi_jugador` + sets) está en la **página de equipo**
  (`?team_name_id=`) y se cargará entonces en `individual_match` para todas estas ligas.
- Fecha con hora; año derivado del mes (§9). Índices `idx_enc_local`/`idx_enc_visitor`
  sirven igual para "próximo partido de un equipo".

---

## 13. Partidos individuales (actas) de las ligas fctt.cat — ✅ ya cargados

Actualiza las notas de §10 y §12 que decían "aún no hay partidos individuales": **ya
los hay**. La FCTT publica el acta (jugador a jugador, con `codi_jugador` y sets) en la
**página de cada equipo**; se cargan en `individual_match` a medida que se juegan
(2026-09-28, primeras jornadas):

| Competición | Encuentros con acta | `individual_match` |
|---|---|---|
| `fctt_2627` (TDM) | 16 | 99 |
| `rtbtt_2627` (Barcelona) | 95 | 582 |
| `rtg_2627` (Girona) | 14 | 86 |
| `rtt_2627` (Tarragona) | 0 | 0 *(aún sin jugar)* |

Cómo leerlo (igual que RFETM/FCTT históricos):
- `individual_match` por `(competition_id, encounter_id)`; `encounter_id` es el del
  calendario (`'{jornada}_{idLocal}_{idVisitante}'`), así que se une directo al
  `encounter` (con su fecha, equipos y marcador por equipo).
- `player_local_lic` / `player_visitor_lic` son el **`codi_jugador`** de la FCTT (el
  mismo que en `player_match` y en el ranking). `winner` = `'local'|'visitor'` (local =
  equipo de casa). `sets` es JSON `[{local, visitor}, …]`. Dobles: `is_doubles=1` +
  `player_*_lic_2`.
- Índices `idx_im_local`/`idx_im_visitor` (por `player_*_lic`) para el head-to-head y el
  historial de un jugador en estas ligas.
- Se actualiza con cada jornada nueva; una acta puntual puede tardar una pasada extra si
  la web devolvió la página incompleta (reintento incremental).

**RFETM igual.** `rfetm_2627` también ha empezado a llenarse de resultados: los
encuentros jugados pasan a `completed` con marcador e `individual_match` en la misma
pasada (`rfetm run`). A 2026-09-28: **116 encuentros jugados, 696 `individual_match`**
(las divisiones inferiores; Superdivisión/D.Honor empiezan más tarde). Se lee igual que
las catalanas (misma tabla, mismo `encounter_id`, `player_*_lic` = licencia RFETM). En
RFETM la licencia **sí** es la de la RFETM; para cruzar con FCTT está `player_link`.
