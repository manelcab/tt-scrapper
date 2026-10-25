# Plan — `Partits_Jugats`: el histórico de partidos por jugador (FCTT)

> Estado: **implementado** el 13/08/2026 (queda la carga histórica y la exención
> de índice). Redactado después de descargar e inspeccionar las ediciones 02,
> 06, 09 y 10 de la temporada 2025-2026.

Es la pieza que faltaba de `/ranquings/`. El ranking dice **dónde está** un
jugador; `Partits_Jugats` dice **cómo ha llegado ahí**: partido a partido, con
rival, marcador, rating de los dos en ese momento y los puntos que se movieron.

---

## 1. Qué es la fuente (verificado, no supuesto)

Un PDF por revisión mensual en el mismo índice `https://fctt.cat/ranquings/`,
con el nombre `RK{temporada}_{edición}_Partits_Jugats.pdf`. Hay 9 (ediciones
`02`…`10`); el ranking inicial no tiene equivalente.

Tabla única de **14 columnas**, que `extract_tables()` devuelve limpia:

```
DATA · EVENT · CATEGORIA · LLIC 1 · JUG 1 (NOM) · JUG 2 (NOM) ·
TOT JUG1 · TOT JUG2 · rk 1 · rk 2 · ΔRK1 · ΔRK2 · RD'1 · RD'2
```

Ejemplo real (edición 10):

```
02/06/2026 · Lliga RTB J4 · Veterans 3a "A" · 68 · MARTINEZ Felipe ·
GIMENEZ Antonio · 3 · 0 · 1704,0 · 1366,9 · 2,4 · -2,5 · 57,3 · 59,2
```

Volumen medido sobre cuatro ediciones (cada partido aparece **dos veces**, una
por jugador, así que los partidos son la mitad de las filas):

| Edición | Págs | Filas | Periodo | Jugadores |
|---|---:|---:|---|---:|
| `02` | 173 | 17.245 | 06/09/2025 → 27/10/2025 | 2.280 |
| `06` | 120 | 10.503 | 10/01/2026 → 01/03/2026 | 2.069 |
| `09` | 110 | 9.656 | 01/05/2026 → 31/05/2026 | 1.776 |
| `10` | 24 | 2.084 | 01/06/2026 → 13/06/2026 | 462 |

**Las ediciones no se solapan**: la intersección entre ediciones consecutivas es
exactamente **0 filas**, y los periodos son contiguos (la `09` acaba el 31/05 y
la `10` empieza el 01/06). Son particiones complementarias de la temporada, así
que la unión de las 9 es la temporada entera y la ejecución mensual solo trae lo
nuevo. Extrapolando, la temporada completa ronda las **80.000 filas ≈ 40.000
partidos**.

Los eventos cubren mucho más que la FCTT:

| Edición | Eventos más frecuentes |
|---|---|
| `02` | `Lliga RTB J1..J4` (~1.950 c/u) · `Circuit Promeses ZonaTT` 1.734 · `Open Catalunya 1a Fase` 1.498 · `Open Internacional de Vic` 1.186 |
| `06` | `Circuit Promeses ZonaTT 5a` 2.000 · `Lliga RTB J14/J15` ~1.185 · `CC Màsters Ind&Db` 928 · `ESTATAL` 925 · `LLNN J15` 344 |
| `09` | `Lliga RTB Play-Off` 3.036 · `Open Catalunya 3a Fase` 1.198 · `Lliga RTB J21/J22` 1.188 · `CC Absolut i Adaptat` 376 |
| `10` | `Top Català` 878 · `Lliga RTB J4` 516 · `TOP OPEN BCN` 426 · `Open Kids-Girona` 180 |

Aparecen las **dos ligas que ya scrapeamos** (`Lliga RTB` → RTBTT, `LLNN` y
`ESTATAL` → RFETM) y una decena de torneos que no cubre ninguna otra fuente.

> Ojo: `event` no es único. La `06` trae `Lliga RTB J1` y `J2` con fechas de
> enero, que son de la segunda vuelta. La fecha forma parte de la identidad del
> partido.

---

## 2. Por qué merece la pena

1. **Incluye torneos.** `Top Català`, `TOP OPEN BCN`, `Open Kids-Girona`… hoy no
   scrapeamos ninguno. Para muchos jugadores es la mitad de su actividad, y es
   la única fuente que la publica.
2. **Da el rating del rival en el momento del partido.** Permite decir "le
   ganaste a alguien 300 puntos por encima" en lugar de "ganaste 3-1".
3. **Explica el ranking.** El ΔRK del ranking mensual es un número sin
   contexto; aquí se descompone en los partidos que lo produjeron.
4. **Es el material del dossier del rival** que pide el concepto UX: forma
   reciente, contra quién juega, si compite en torneos, historial directo.

---

## 3. Las tres trampas de esta fuente

### 3.1 Solo hay licencia del jugador 1 — el espejo la recupera

`JUG 2` viene **solo con nombre**. Sin código, el rival no enlaza con
`rankings/players/{code}` ni con los partidos de liga, y se pierde justo lo que
hace valiosa la fuente.

La solución está en el propio PDF: **cada partido aparece dos veces**, una desde
cada jugador, y en la fila espejo el rival es `JUG 1` y trae su licencia. La
cobertura medida:

| Edición | Filas | Con espejo | Huérfanas |
|---|---:|---:|---:|
| `02` | 17.245 | 96,1 % | 673 |
| `06` | 10.503 | 88,6 % | 1.197 |
| `09` | 9.656 | 97,2 % | 270 |
| `10` | 2.084 | 100 % | 0 |

O sea: el espejo resuelve el 96 % de los casos, pero **no es universal**.

Las huérfanas tienen una explicación limpia, comprobada sobre la edición 06:
**las 1.197 son de competición estatal**, sin una sola excepción.

```
769  ESTATAL     139  CE ABS      94  LLNN J16     84  LLNN J15
 70  COPAS        25  LLNN J13    14  LLNN J14      2  LLNN J12
```

Son partidos contra jugadores de otras federaciones, que no tienen fila propia
porque la FCTT no les calcula ranking. No es un fallo del parseo ni una laguna
de la fuente: **ese código no existe**. De los 1.197 rivales huérfanos, solo 13
aparecen como `JUG 1` en alguna otra fila, y rescatarlos exigiría casar por
nombre — el 1 % de los casos a cambio del riesgo del §3.2. No se hace.

Por tanto: rival huérfano → se guarda con `name` y sin `code`, marcado como
externo. La consecuencia de producto es que en un partido de liga nacional el
dossier del rival no se podrá abrir, lo cual es correcto: ese rival no está en
el ranking catalán.

El emparejado se hace por `(fecha, evento, categoría, nombre1↔nombre2)` y se
**valida** exigiendo que en el espejo el marcador esté invertido
(`TOT JUG1 ↔ TOT JUG2`) y los ratings cruzados coincidan (`rk 1` de uno =
`rk 2` del otro). Con esa validación, 2.066 filas casan a la primera y quedan 18
ambiguas: son 13 parejas que se enfrentaron **dos veces el mismo día en el mismo
evento** (grupo + eliminatoria). Se desempatan por marcador y ΔRK; si aun así
quedara alguna, se guarda sin código de rival antes que emparejarla mal.

### 3.2 Hay homónimos — nunca casar por nombre

En la edición 10, `GIMENEZ Angel` son **dos jugadores distintos**, licencias
`947` y `8328`. Y no es una rareza: hay **19 nombres duplicados en la edición
02, 12 en la 06 y 9 en la 09**. Cualquier atajo del tipo "mapa nombre → código
construido desde el ranking" produce partidos atribuidos al jugador equivocado,
en silencio. Por eso el emparejado del punto anterior valida también los
ratings: son los que distinguen a los dos GIMENEZ Angel.

### 3.3 Las mismas de siempre en esta web

- El sufijo `-1` de WordPress también aparece aquí (`RK2526_06_Partits_Jugats-1.pdf`).
  Ya lo deduplica `parse_index`, pero hay que dejar de filtrar `partits_jugats`
  en `_SKIP_CATEGORIES` de forma controlada, no borrando la constante.
- Los PDFs son gordos (la edición 02 pesa 6,9 MB y son 173 páginas). Caché en
  disco obligatorio y parseo por página.

Y una que **no** es trampa, comprobada por si acaso: aunque la edición 06 incluya
un `CC Màsters Ind&Db`, **no hay ni una fila de dobles** en ninguna de las cuatro
ediciones inspeccionadas. Solo individuales, un jugador por columna.

---

## 4. Modelo de datos

```
rankings/fctt_2526/players/{codi_jugador}
    … (lo que ya hay: current, history, club, gender …)
    record: { played, won, lost, tournaments, last_played, best_win: {…} }   ← nuevo

  /match_log/{edición}          ← "10", "09", … un documento por revisión
      edition, label, from, to
      played, won, lost, d_rk_total
      matches: [
        { date, event, event_type, category,
          opponent: { code, name, external },   ← `external: true` = sin licencia FCTT
          sets, opp_sets, won,
          rk, opp_rk, d_rk, rd }, …
      ]
```

**Un documento por jugador y edición, con los partidos dentro de un array.** Las
alternativas y por qué no:

| Opción | Escrituras temporada | Problema |
|---|---:|---|
| Un documento por partido | ~80.000 | Cuatro días de cuota y hay que inventar un id estable |
| Todo dentro del documento de jugador | ~3.000 | El documento crece sin techo y se relee entero cada mes |
| **Un documento por jugador y edición** | **~15.000** | Justo por debajo de la cuota diaria: la carga histórica se parte por ediciones |

Encaja además con cómo publica la fuente: una edición = exactamente un documento
por jugador activo, idempotente y re-ejecutable.

El array no se descontrola: la media es de **5 a 8 partidos por jugador y
edición** y el máximo medido es **36** (edición 02). Muy lejos del límite de
1 MiB por documento.

### ¿Y si solo se guardara la última edición?

Se planteó pisar cada mes los documentos del anterior, para contener el volumen.
Medido: **285 bytes por partido, 27,2 MB la temporada entera** — el 2,7 % del GiB
del plan gratuito; cinco temporadas serían el 13 %. Y **no ahorra ni una
escritura**: la ejecución mensual escribe los mismos documentos, solo que encima
de los viejos. Ahorra el recurso que sobra y no toca el que aprieta.

Lo que costaría, medido sobre la temporada 2025-2026:

| Se conserva | Jugadores con ficha | Cobertura |
|---|---:|---:|
| Solo la última edición | 462 | 15,5 % |
| Últimas 2 | 1.831 | 61,5 % |
| Últimas 3 | 2.363 | 79,4 % |
| Todas | 2.975 | 100 % |

La actividad es muy estacional (2.315 jugadores en desembre, 462 en juliol), así
que guardar solo el último mes deja sin dossier a 5 de cada 6 jugadores. Ni
siquiera una ventana móvil de tres ediciones —que ahorraría 18 MB— evita dejar
fuera a uno de cada cinco. Se guarda todo.

Además, **somos el único archivo**: la FCTT republica estos PDFs (la revisión 06
va por la v2) y nada garantiza que las temporadas viejas sigan colgadas.

**Lecturas que resuelve:**

- *"Forma reciente de mi rival"* → 1 lectura (la última edición) o 2-3 para
  cubrir tres meses.
- *"¿Cómo voy contra este jugador?"* → leer mis ≤9 documentos de `match_log` y
  filtrar por `opponent.code`. No hace falta guardar un `head-to-head`
  precalculado, que sería un mapa sin techo y con coste de indexación.
- *"Titular del dossier"* → `record` en el documento de jugador, 1 lectura, ya
  disponible junto a `current`.

**`match_log` tiene que quedar exento de indexación**, igual que `history`: un
array de mapas genera una entrada de índice por campo hoja (~12 × 20 partidos
por documento) y nunca se consulta —se lee entero—. Es exactamente lo que
provocó el `Transaction too big` de los rànquings.

### `event_type`

`event` se guarda tal cual viene (`Lliga RTB J4`), pero se añade un
`event_type` normalizado (`lliga` | `torneig`) porque es lo que la app va a
querer filtrar. **No se intenta enlazar con `competitions/`**: los eventos de
liga que aparecen aquí son de RTBTT, cuyas actas en PDF no traen `codi_jugador`,
así que el enlace tendría que hacerse por nombre — justo lo que el punto 3.2
prohíbe. Se deja como texto y se revisa más adelante.

---

## 5. Verificación: un invariante que cruza las dos fuentes

El ranking mensual de un jugador trae `ΔRK`, `PJ`, `PG` y `PP` de ese mismo
periodo. Por tanto, para cada jugador y edición:

```
Σ ΔRK1 de sus partidos  ==  ΔRK de su fila en el ranking
nº de partidos          ==  PJ          (ganados == PG, perdidos == PP)
```

Es el equivalente al `marcador == suma de individuales` que validó las actas de
FCTT, pero **cruzando dos PDFs distintos**: si el emparejado del espejo o el
parseo fallan, salta aquí. Implementado como `python main.py fctt verify`, que
reporta el número de jugadores incoherentes, no un OK/KO.

**Resultado en la edición 10** (462 jugadores): PJ/PG/PP coincide en **el
100 %**, sin una sola discrepancia. Los ΔRK cuadran en 452 y en los 10 restantes
difieren entre 0,2 y 0,8 puntos — la fuente publica cada ΔRK redondeado a un
decimal, así que sumar 13 partidos arrastra hasta ~0,7 de error frente al total.
La comprobación lo tolera en proporción al número de partidos.

Una duda que había al escribir el plan queda resuelta: el ΔRK de un jugador es
**idéntico en todas las categorías** en las que aparece (comprobado en los 2.769
jugadores de la edición 10), así que no hay que elegir categoría principal ni
tolerar nada por ese lado.

Ocho jugadores compitieron sin tener fila en el ranking de ese mes (sin
clasificar, baja de licencia); se cuentan aparte y no se consideran error.

---

## 6. Fases

| # | Qué | Estado |
|---|---|---|
| 1 | `parsers/fctt_partits_parser.py`: índice + tabla de 14 columnas | ✅ |
| 2 | Emparejado espejo con validación de marcador y ratings | ✅ 16 tests |
| 3 | `models/player_match.py` + `save_player_match_logs()` / `save_player_records()` | ✅ |
| 4 | Comando `python main.py fctt matches [--edition N] [--dry-run] [--force]` | ✅ |
| 5 | `python main.py fctt verify` — invariante cruzado con el ranking | ✅ ed.10 al 100 % en PJ/PG/PP |
| 6 | Exención de índice para `match_log` + documentación | Falta la exención (consola) |
| 7 | Carga histórica de las 9 ediciones, en dos tandas | Pendiente |

---

## 7. Cuota de escrituras

Un documento por jugador **activo en esa edición**, y las ediciones de temporada
alta mueven ~2.000 jugadores cada una:

| Operación | Escrituras |
|---|---:|
| Carga histórica (9 ediciones, ~1.700 jugadores de media) | ~15.000 |
| `record` de jugador al final de la carga | ~2.800 |
| **Total histórica** | **~17.800** |
| Actualización mensual (edición + records tocados) | ~2.500 |
| Límite diario del plan Spark | 20.000 |

La histórica cabe en un día, pero **sin margen**: 17.800 de 20.000 y el resto de
la cuota ya la consumen los rànquings si se recargan el mismo día. Por eso el
comando acepta `--edition` y la carga se hace **en dos tandas** (5 + 4
ediciones), que además es la unidad natural de reintento. Nada de esto es
esfuerzo perdido: en régimen mensual son ~2.500 escrituras.

Cifras reales del dry-run completo (13/08/2026): 100.152 filas ≈ 50.000
partidos, **17.070 documentos** y 2.975 jugadores, o sea 20.045 escrituras con
los resúmenes — cuarenta y cinco por encima del límite diario. Dos tandas no era
una precaución, era obligatorio.

**Y el día de cuota empieza a las 09:00 CEST** (medianoche del Pacífico), no a
medianoche local. Lanzar una tanda de madrugada la suma a la cuota del día
anterior; así murió la edición 05 en el primer intento.

---

## 8. Decisiones tomadas (13/08/2026)

1. **`record` en el documento de jugador**: sí. Evita una lectura extra en el
   caso más frecuente (abrir la ficha de un rival), a cambio de ~2.800
   escrituras más en la carga histórica. Se calcula siempre con la temporada
   entera; por eso `--edition` no lo toca.
2. **Torneos como competición navegable**: no. El torneo queda como texto dentro
   del partido, con `event_type` para poder filtrar. Convertirlos en
   `competitions/{torneo}` es un proyecto aparte y la fuente no da ni cuadros ni
   rondas.
3. **Rivales sin licencia FCTT** (el 4 % de las filas, todas de competición
   estatal): se guardan con `opponent.name`, sin `code` y con `external: true`,
   y se cuentan en el resumen de cada ejecución. Perder el partido sería peor
   que perder el enlace, y el partido cuenta igual para la forma del jugador.
