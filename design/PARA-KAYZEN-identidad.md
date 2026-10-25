# Identidad de persona en `tt-data`: ya existe, y aquí están vuestros números

> Respuesta de tt-scraper a `PARA-TT-SCRAPER-identidad.md` (2026-10-24).
> Escrito el 2026-10-25.

## El resumen, por si no hay tiempo para más

Hecho el primer paso que pedíais: **`person` + `person_licence` ya están en
`tt-data`**, pobladas con lo seguro. Podéis colgar todo de `person_id` y retirar
`same_person_deducido`.

Y os traemos **los dos números** del §5, medidos sobre la foto entera:

- **Precisión de "mismo nombre y mismo club": 98.2 %** (de 57 candidatos que
  tocan un par ya conocido, 56 coinciden y **1** contradice).
- **Recall: 19.6 %** (56 de 285 pares ciertos de `player_link`).

El recall es bajo por una razón concreta y buena de saber: **en la FCTT el club
que publicamos es un CÓDIGO (`CTTBDN`, `RELIS`, `CALEL`…) y en la RFETM es el
nombre (`CTT BADALONA`…)**. Vuestra regla compara un código contra un nombre y
casi nunca casa. Con **nombre exacto solo** el recall sube al **89.5 %**. Es decir:
la regla acierta cuando dispara, pero dispara poco, y lo que la frena es
exactamente el rodeo del código de club que ya os molestaba (§2.1).

## 1. Qué hay ya en `tt-data`

Tablas nuevas (esquema en `d1/schema.sql`, migración `d1/alter_person_identity.sql`,
ambas aplicadas):

```
person(id, display_name, birth_year, sex, updated_at)
person_licence(person_id, federation, lic, status, evidence, confidence,
               decided_at, decided_by)   -- PK (federation, lic)
person_id_alias(old_id, new_id, merged_at)
```

Respeta lo que pedíais en §3:

- **`status` con tres valores**: hoy todo lo cargado es `confirmed`; `probable`
  llega en la siguiente fase (ver §3 de aquí) y `rejected` se guardará como fila,
  no se borra.
- **`evidence` siempre**: `nat_league_match` (de `player_link`) o `singleton`
  (persona de una sola licencia). Más adelante `name_and_club`, `birth_year`,
  `manual`.
- **`id` estable de verdad**: `p_<sha1(federación:licencia canónica)>`. Al
  recalcular, cada licencia **conserva** su `person_id` (lo leemos de vuelta
  antes de asignar). Si dos personas se fusionan, el id superviviente queda y el
  otro va a `person_id_alias` — **nunca reescribimos un id en silencio**.
- **PK por `(federation, lic)`**: una licencia pertenece a una sola persona, así
  moverla de persona no deja duplicados.

**Carga inicial (Fase 1, solo lo seguro):**

- **7033 personas**, **7345 licencias**.
- **624 licencias enlazadas** (los 312 pares de `player_link`, cada uno una
  persona con dos licencias).
- El resto, personas de una sola licencia (`singleton`).
- Todo `confirmed`, `decided_by = 'scraper'`.

Esto es lo que dijisteis que "ya os valdría como primer paso": os quita el rodeo
del código de club y os da dónde colgar lo demás. Gorka Neyra sigue siendo dos
personas —no hay partido que las una— y eso es a propósito: no adivinamos (§7).

## 2. Vuestros dos números, con detalle

Medidos con `d1/measure_name_club.py` (portando vuestra lógica de
`mismo_club.ts`: quitar `CTT`/`CLUB`/`TENNIS`/`TAULA`/… y la letra del equipo, y
exigir subconjunto):

**Recall — de los 285 pares de `player_link` con ficha en ambos lados:**

| | pares | % |
|---|---|---|
| nombre exacto igual | 255 | 89.5 % |
| nombre exacto **y** club equivalente | 56 | **19.6 %** |

Los 199 que pierden el club son casi todos código-vs-nombre. Ejemplos reales:

```
LLORET Pau        FCTT[CTTBDN] vs RFETM[CTT BADALONA]
KUMAR Aman        FCTT[CALEL]  vs RFETM[CTT CALELLA]
FIGAROLA Biel     FCTT[TTCAS]  vs RFETM[TT CASSA]
FOLCH Martí       FCTT[LLUÏSO] vs RFETM[LLUISOS DE GRACIA]
```

**Precisión — de lo que "nombre+club" propondría (187 candidatos):**

| | nº |
|---|---|
| coinciden con `player_link` | 56 |
| **contradicen** a `player_link` | **1** |
| sin dato en `player_link` (ni confirman ni contradicen) | 130 |

Precisión = 56 / (56 + 1) = **98.2 %**. La única contradicción es la grieta que
ya conocíais: **dos personas distintas del mismo pueblo** —"GRANADOS Claudio",
los dos de Tona—, que `player_link` separa bien y "nombre+club" junta mal.

## 3. Qué hacemos con esto (y qué os pedimos confirmar)

El número dice que la regla **se queda, pero se afina**: es precisa, y su
problema es de cobertura, no de errores. Plan (detalle en
`design/plan_identidad_persona.md`):

1. **Resolver el código de club de la FCTT a club canónico** y una tabla de
   equivalencias FCTT-código ↔ RFETM-nombre. Eso convierte "mismo club" de fuzzy
   a igualdad y sube el recall del 19.6 % hacia ~89.5 % **sin tocar la
   precisión**. Es, con diferencia, lo que más mueve la aguja, y lo tenemos como
   siguiente paso.
2. **Capturar `birth_year` de la FCTT** si la fuente lo publica (de la RFETM ya
   lo tenemos). Con el año de nacimiento, "nombre casi solo" (89.5 %) pasa a ser
   seguro y parte del 28 % de colisiones de nombre desaparece.
3. Solo entonces activamos `person_licence` con `status = 'probable'` y
   `evidence = 'name_and_club'`, y lo consumís sabiendo que es probable.

Las **correcciones** (`rejected` / confirmaciones del admin) las guardaremos en
un **fichero versionado** nuestro (`d1/identity_overrides.csv`), no abriendo
escritura a `tt-data` desde fuera. Lo manual **manda** sobre lo deducido y el
builder nunca re-deduce un par marcado `rejected`.

## 4. Lo que NO cambia respecto a lo que pedíais

- Nada casa por nombre para `confirmed`. El nombre solo entrará en `probable`, y
  siempre con `evidence`.
- `tt-data` sigue siendo de **solo lectura** para vosotros.
- Lo que afirma un jugador sobre su propia libreta (`same_person`) es vuestro y
  manda; no lo tocamos.

## 5. Para reproducir los números

```
# desde tt-scraper/
NODE_OPTIONS=--dns-result-order=ipv4first python d1/measure_name_club.py --db tt-data
```

Si afináis la lista de palabras que no distinguen (`_CLUB_STOP` en ese script),
decídnoslo y lo igualamos, pero el cuello de botella real no es la lista: es el
código de club de la FCTT, y eso lo arreglamos en origen (punto 1 de §3).

## 6. El índice que pedíais (vuestro §8.3): hecho

`CREATE INDEX IF NOT EXISTS person_licence_by_person ON person_licence(person_id);`
está creado en `tt-data` y en `d1/indexes.sql`. Verificado con
`EXPLAIN QUERY PLAN`: `SEARCH person_licence USING INDEX person_licence_by_person`
(antes recorría las ~7.345 filas; ahora dos). **Ya podéis desplegar la lectura de
identidad.** El nombre es el que fijasteis, no lo renombraremos sin avisar.

Sobre lo demás de vuestro §8: cuando el encadenado periódico
(`weekly_identity.sh`, nuestro paso 9) corra automático, `person` estará siempre
al día y podréis leer solo `person_licence` y retirar vuestro escalón de
"nombre+club". Y los descartes (`rejected`) del admin que hoy guardáis en vuestra
casa: cuando nos paséis esos pares, entran en nuestro `d1/identity_overrides.csv`
y suben a `person_licence` con `status='rejected'`, `decided_by='admin:…'`, que
es donde valen para todos (el builder ya los aplica). Esas dos cosas están en
nuestro backlog (`design/backlog_identidad.md`).

## 7. Novedades · **2026-10-25 (tarde)**

### 7.1. El encadenado periódico ya existe → podéis simplificar

Hay un orquestador, `scripts/run_all.sh`, que lanza las cargas semanales (solo lo
pendiente) y, de paso, `weekly_identity.sh` y el report. En cuanto lo dejemos
programado para correr varias veces al día (nos queda el cron), `person` estará
al día casi en continuo. Es la condición que poníais en vuestro §8.2 para:

- **leer solo `person_licence`** y dejar de mirar `player_link` por delante;
- **retirar vuestro escalón propio** de "nombre+club".

Os avisamos cuando esté programado de verdad; hasta entonces, mantened vuestra
precedencia actual.

### 7.2. `probable` ahora usa el club de la TEMPORADA (no el del ranking)

La comparación de club de la fase `probable` ya no usa `player.club` (que es un
código del final de la temporada anterior), sino el club **vigente** derivado de
las alineaciones (`player_club_season`), que es un nombre en las dos
federaciones. Efecto: los `probable` pasan de 443 a **458** (más clubes resueltos
por nombre, con las mismas salvaguardas). Estado en `tt-data`: **6.575 personas,
6.887 `confirmed` + 458 `probable`**. El formato que consumís no cambia.

### 7.3. La grieta de §8.1, medida

Vuestra grieta —dos clubes de la misma ciudad con una palabra común— ya la
medimos aparte: de los 653 candidatos de "nombre+club", **31 (4,7 %)** casan por
una sola palabra significativa, y casi todos son abreviaturas legítimas del mismo
club (PARETS ≈ TT PARETS, PREMIA ≈ PREMIA DE MAR). La precisión se mantiene en
99,5 %. Confirmado: no es urgente.

### 7.4. Lo único que necesitamos de vosotros

Para cerrar el canal de descartes (§8.2): **pasadnos vuestros pares `descartado`**
en el formato de `d1/identity_overrides.csv` (una línea
`federation_a,lic_a,federation_b,lic_b,rejected,admin:kayzen,<nota>` por par). En
cuanto los tengamos, suben a `person_licence` como `rejected` y valen para todos.
El mecanismo ya está probado; solo falta vuestro export.

## 8. Respuesta a vuestro §9 · **2026-10-25 (tarde)**

### 8.1. `decided_by = kayzen:jugador`: perfecto, y cómo lo tratamos (vuestro §9.1)

Nada que cambiéis. Nuestro builder ya no mira `admin:` en concreto: trata como
manual **cualquier origen que no sea `scraper`**, así que `kayzen:jugador` entra
igual y manda sobre lo deducido. Una línea por par y siempre la misma dirección
es justo como lo queremos.

Dos matices para que no os sorprenda nada:

- **Un descarte NO crea una fila `rejected` en `person_licence`.** Una fila es de
  UNA licencia (PK `(federation, lic)`), y un descarte es sobre un PAR; meterlo
  como fila de una licencia sería media relación. Lo que hacemos es **suprimir
  ese par** en la deducción: el `probable` no llega a existir. El efecto que
  queréis ("no se vuelve a juntar") se cumple —leeréis `person_licence` y ese
  enlace sencillamente no estará—, solo que no hay un `rejected` literal que
  excluir. Guardamos el descarte por el **par exacto**, igual que vosotros.
- **La revisión que ofrecéis ya existe, y es barata**: los descartes entran por
  un fichero **versionado en git** (`identity_overrides.csv`), así que alguien
  los commitea antes de que se apliquen. Ese commit es el filtro. Y el riesgo es
  bajo en cualquier caso: un descarte solo IMPIDE una fusión; nunca fuerza una
  mala. Lo peor que puede pasar es dejar sin proponer un par que quizá era el
  mismo. Nos vale aplicarlos directos; si algún día uno chirría, se ve en el
  propio commit.

### 8.2. El alias y el censo 6.590 → 6.575 (vuestro §9.3)

Buena pregunta, y la respuesta corta es la que temíais a medias: **`person_id_alias`
se rellena SOLO en las fusiones confirmadas explícitas, no en cada
reconstrucción.** Pero el id **no se pierde en silencio** para lo que os importa.
Desglose:

- **Los `person_id` confirmados son estables.** El builder lee los
  `person_licence` `confirmed` que ya hay y **reutiliza** su `person_id`; no se
  recalcula. Si dos personas confirmadas se fusionan (aparece un `player_link`
  nuevo entre ellas), el superviviente se queda y el otro id va a
  `person_id_alias`. Eso sí viaja.
- **El −15 de hoy NO fue una fusión.** Fueron 15 singletons de la RFETM que
  pasaron a `probable` (absorbidos por una persona de la FCTT al resolverse mejor
  el club, §7.2). Cuando una licencia pasa a `probable`, su persona-de-una-sola
  deja de tener licencias y **se borra, sin alias**. Y es **a propósito**: un
  `probable` es reversible (si se retira, la licencia vuelve a su persona propia),
  así que escribir un alias sería convertir una conjetura en equivalencia
  permanente. Un alias de algo probable mentiría.
- **Qué colgar, entonces.** La clave estable de verdad es la **licencia**:
  `person_licence(federation, lic)` es PK y no cambia nunca. El `person_id` es
  **derivado**: estable mientras la persona tenga una licencia `confirmed`
  (ancla), pero puede cambiar por el vaivén de los `probable`. Recomendación para
  cuando colguéis cosas: **ancladlas a la licencia y resolved el `person_id` al
  leer**, o colgadlas solo de personas con una licencia `confirmed`. Las fusiones
  confirmadas (las permanentes) las cubre el alias; el vaivén de `probable`, por
  diseño, no —ni debe—.

Si os sirviera un alias también para las absorciones `probable`, decídnoslo, pero
creemos que no: rompería la reversibilidad que hace que `probable` sea seguro de
enseñar.

### 8.3. Lo demás de vuestro §9

- **§9.2** (mantener `player_link` por delante hasta el cron): de acuerdo, os
  avisamos cuando esté programado de verdad.
- **§9.4**: recibido, gracias.
