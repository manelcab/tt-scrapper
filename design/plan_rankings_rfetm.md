# Plan — Rànquings de la RFETM

> Estado: **fase 1 implementada** el 14/08/2026. Decisiones tomadas: solo la
> temporada 11 (2025-2026), todas las categorías, y la fase 2 se decide después.
> Queda pendiente una recarga —ver §8— por dos fallos de modelo detectados al
> revisar el dato cargado.

La buena noticia de entrada: **esta fuente es mucho más barata de scrapear que la
de la FCTT**. Donde la FCTT obliga a 208 PDFs por temporada, aquí bastan
**20 peticiones HTML** para tener el ranking completo de los diez meses.

---

## 1. La fuente

Todo cuelga de `https://rfetm.es/ranking/?accion=…`, con cuatro parámetros:
`year`, `mes`, `sexo` (`M`/`F`) y `temporada`.

| `accion` | Qué devuelve |
|---|---|
| `0` | Índice: **todos los meses publicados**, de todas las temporadas |
| `2` | Ranking global con la posición en cada categoría |
| `3` | **Ranking global completo** — lo mismo que `2` más año de nacimiento, puntos anteriores, desglose por competición y PJ/PG/PP |
| `4` | Ranking de **una** categoría (`ranking=NAC`, `SEN`, `V40`…) |
| `5` | **Partidos de un jugador** en ese mes |
| `22` | Jugadores nacidos en un año |

### 1.1 `accion=3` lo tiene todo

Es el hallazgo que cambia el plan. Una sola petición por sexo y mes devuelve una
tabla de **31 columnas** con una fila por jugador:

```
LIC · NOMBRE · CLUB · FA · CAT · AÑO · ANT · ACT ·
LLNN · C.ESP · T.EST · TOP · SP.M · PJ · PG · PP · (CLAF) ·
NAC · SEN · S21 · JUV · CAD · INF · ALE · BEN · V40 · V50 · V60 · V65 · V70 · V75
```

Las **14 últimas son la posición del jugador en cada categoría**, así que las
URLs por categoría (`accion=4`) que me pasaste son redundantes: los rànquings de
`SEN-M`, `V40-M`, etc. se **derivan** de esta tabla ordenando por esa columna.
Se pasa de 28 peticiones por mes a **2**.

Volumen medido (juny 2026): **3.409 jugadores** masculinos y **941** femeninas.

### 1.2 Qué meses hay

`accion=0` los lista. Hoy hay dos temporadas:

| `temporada` | Temporada | Meses publicados |
|---|---|---|
| `10` | 2024-2025 | 11 — incluye un `mes=0`, que es el **ranking inicial** |
| `11` | 2025-2026 | 10 — de setembre 2025 a juny 2026, sin inicial |

El identificador de temporada es un contador: `temporada = año_inicio − 2014`.
Se calcula desde `SEASON` y se declara en `sources.py`, como el resto.

> `mes=0` no existe en la temporada 11. El cargador **no debe darlo por hecho**:
> se toma la lista de `accion=0` y se carga lo que haya.

---

## 2. Diferencias con la FCTT que condicionan el diseño

### 2.1 Aquí no hay que adivinar quién es el rival

En la página de partidos de un jugador (`accion=5`) vienen **las licencias de los
dos**:

```
FH PART · RK MES · EVENTO · CAT · J1 · NOMBRE J1 · J2 · NOMBRE J2 ·
RES 1 · RES 2 · RK 1 · RK 2 · P 1 · P2
```

```
2026-03-06 · MAR-26 · LLNN J15 · -SUM- · 19271 COLINA Aitor · 17948 LILLO Alberto
           · 1 · 3 · 1935.2 · 2606.8 · -0.3 · 0.3
```

Nada del emparejado por espejo que hizo falta en la FCTT, y ningún problema de
homónimos: el identificador está en la propia fila.

### 2.2 `NAC` no es "el ranking general"

El nº1 del ranking global masculino es FERREIRA Diogo (2838,2 puntos) y en la
columna `NAC` tiene `99999`. `NAC` es el ranking **nacional**, restringido a
jugadores con nacionalidad/elegibilidad española; el nº1 de `NAC` es LILLO
Alberto, con 2619,2. Son dos listas distintas y la app tiene que saberlo.

### 2.3 `99999` significa "no está en esta categoría"

No es una posición. Guardarlo sería mentir en cada consulta de "¿en qué
categorías compite este jugador?". Se descarta al parsear.

### 2.4 La cabecera de la tabla viene rota

Cada `<th>` contiene la concatenación de todos los encabezados que quedan a su
derecha (`'NAC LIC. NOMBRE CLUB CAT …'`). **No se puede parsear por nombre de
columna**: hay que ir por posición y afirmar el número de columnas (31 en
`accion=3`), abortando si cambia. Es la misma disciplina que con los PDFs.

### 2.5 Las licencias de RFETM y FCTT son espacios distintos

Comprobado contra lo que ya está cargado:

| Jugador | Licencia RFETM | Código FCTT |
|---|---:|---:|
| YEH Chih-Wei (L'Escala) | 33851 | 13209 |
| PROVOST Damien Roland (Olot) | 27561 | 10218 |

Un catalán que juega la liga nacional **tiene dos identidades** y no hay ningún
campo que las una. Volveré sobre esto en §5, porque tiene una solución elegante.

---

## 3. Modelo de datos

```
rankings/rfetm_2526
    organization, season, source_url, last_edition, player_count, updated_at
  /editions/{2025-09 … 2026-06}
      id, year, month, label, player_count, categories[]
    /categories/{NAC-M, SEN-M, … V75-F}          ← 28 por edición
        id, sex, player_count
        top: [ {pos, lic, name, club, fa, pts} … 100 ]
  /players/{licencia}
      lic, name, club, fa, sex, birth_year, cat, updated_at
      current: { NAC: {pos, pts}, SEN: {pos, pts}, V40: {…}, edition }
      history: { "2026-06": { pts, pts_prev, pj, pg, pp,
                              by_event: {llnn, c_esp, t_est, top, sp_m},
                              pos: {NAC: 1, SEN: 3} } }
```

Mismo esqueleto que `rankings/fctt_2526` —la app aprende un solo modelo— con dos
diferencias que vienen de la fuente:

- **`history` se indexa por edición y dentro por categoría**, al revés que en la
  FCTT. Allí cada PDF era una categoría; aquí una fila trae las 14 posiciones a
  la vez y los puntos son únicos, así que la edición es la clave natural y no se
  repite el dato.
- **`birth_year` y `fa`** (federación autonómica) no tienen equivalente en la
  FCTT y son útiles: el año de nacimiento da la categoría de edad real y la FA
  permite filtrar "rivales de mi federación".

Los ids de categoría (`NAC`, `SEN`, `V40`…) empiezan todos por letra, así que
**no reaparece la trampa** de las claves numéricas que obligó a renombrar las de
la FCTT. Las claves de `history` sí son fechas (`2026-06`), pero `history` va
exento de indexación y no se consulta nunca.

---

## 4. Coste

| | Peticiones | Escrituras |
|---|---:|---:|
| Carga histórica (10 meses × 2 sexos) | **20** | ~6.100 |
| Actualización mensual | 2 | ~5.000 |
| Temporada anterior (`temporada=10`) | 22 | ~6.100 |

Las escrituras son ~5.500 jugadores + 280 documentos de categoría + 10 ediciones.
Cabe de sobra en la cuota diaria, sin tandas. Y frente a las 20 peticiones, la
FCTT necesitaba descargar 208 PDFs de 117 MB.

La actualización mensual reescribe casi todos los jugadores porque el payload
trae el histórico fundido, igual que en la FCTT.

---

## 5. Fase 2 — los partidos (`accion=5`), y el puente entre federaciones

La página de partidos por jugador y mes es el equivalente de `Partits_Jugats`,
con la ventaja de traer las dos licencias. Su problema es el coste: **una
petición por jugador y mes**.

- Bruto: 4.350 jugadores × 10 meses ≈ **43.500 peticiones**.
- Filtrando por quien realmente jugó ese mes —el propio ranking lo dice en `PJ`,
  y en març solo 760 de 3.022 tenían partidos— baja a **~10.000**.

A 1,5 s por petición son unas 4 horas, viable como carga puntual pero no como
algo que se repita a la ligera. Propongo dejarlo para una fase aparte y decidirlo
con los rànquings ya cargados.

**Y aquí está lo interesante.** Los partidos de liga nacional aparecen en las dos
fuentes: en `Partits_Jugats` de la FCTT con códigos catalanes, y en `accion=5` de
la RFETM con licencias españolas. El mismo partido, el mismo día, el mismo
marcador, los mismos ratings. Cruzándolos por `(fecha, evento, marcador)` sale
**la equivalencia licencia RFETM ↔ código FCTT deducida de los datos**, sin casar
nombres ni clubes. Es la misma técnica del espejo que resolvió el rival en la
FCTT, aplicada entre federaciones.

Eso permitiría que la ficha de un rival catalán que juega la nacional muestre sus
dos rànquings y su historial completo, en vez de dos jugadores inconexos. No lo
incluyo en la fase 1, pero condiciona que **no se invente** ningún emparejado por
nombre mientras tanto: sería irreversible y contaminaría el dato bueno.

---

## 6. Fases

| # | Qué |
|---|---|
| 1 | `sources.py`: `RFETM_RANKINGS` con las URLs y el cálculo de `temporada` |
| 2 | `parsers/rfetm_ranking_parser.py`: índice (`accion=0`) + tabla de 31 columnas + derivación de los tops por categoría |
| 3 | `models/`: reutilizar `RankingEntry` donde encaje, ampliar con `fa` y `birth_year` |
| 4 | `scrapers/rfetm_rankings_scraper.py` + escrituras (reutiliza `save_ranking_*`) |
| 5 | `python main.py rfetm rankings [--edition] [--sex] [--dry-run] [--force]` |
| 6 | Carga de la temporada 11, y después la 10 como histórico |
| 7 | Documentación y exención de índice para `history` en `rankings/rfetm_*` |

Fase 2 es el único trabajo real; el resto reutiliza lo que ya existe para la
FCTT.

---

## 8. Dos fallos de modelo, encontrados mirando el dato cargado

La primera carga entró sin errores —10 meses, 38.344 filas, 4.351 jugadores, 78
segundos— y el resultado parecía bueno hasta que se consultó:

```
— top 3 del ranking NACIONAL masculino —
   1  PEREZ Juan      INDEPENDIENTE-RFETM
   1  LILLO Alberto   ALICANTE TM
   1  ROBLES Alvaro   INDEPENDIENTE-RFETM

— top 3 de V40 masculino —
   1  BILENKO Tetyana      1983
   1  ERDELYI Annamaria    1985
```

Tres primeros en `NAC` y mujeres en el top masculino. El parseo era correcto
—comprobado contra la fuente: en juny hay exactamente un `NAC`=1 por sexo—, así
que los dos fallos estaban en cómo montaba los documentos.

**1. El sexo no formaba parte de la clave.** Las categorías de la RFETM (`NAC`,
`SEN`, `V40`…) son las mismas para hombres y mujeres, al contrario que en la
FCTT, donde el sexo va dentro del propio id (`SEM`/`SEF`). Con `current.NAC` se
mezclaban las dos listas. Ahora la clave es `NAC_M` / `NAC_F`. El separador es
`_` y no `-` porque un guion en una ruta de campo hay que escaparlo con acentos
graves — la misma trampa de las categorías numéricas de la FCTT.

**2. `current` no era "actual" sino "la última vez que apareció".** Se
sobreescribía edición a edición, así que un jugador que dejó de estar en el
ranking conservaba su última posición para siempre: de ahí los dos primeros
fantasma, que fueron nº1 en algún mes y luego desaparecieron. Ahora `current`
se llena **solo** desde la última edición, y quien no está en el ranking del mes
se queda sin `current`, que es exactamente lo que significa.

Los dos casos están fijados con tests. **Falta rehacer la carga** (~4.900
escrituras) y borrar los 262 documentos de categoría con el id viejo (`NAC-M`).

---

## 7. Decisiones que hay que confirmar

1. **¿Cargamos también la temporada 2024-2025** (`temporada=10`)? Son 22
   peticiones y ~6.100 escrituras más, y da un año de histórico de golpe,
   incluido el ranking inicial que la temporada actual no tiene. Yo lo haría.
2. **¿Guardamos las 28 categorías o solo las que interesan?** Guardar todas
   cuesta 280 documentos y evita tener que volver atrás. Yo las guardaría todas.
3. **Fase 2 (partidos)**: ~10.000 peticiones y ~4 horas. Se puede decidir después
   de ver los rànquings cargados.
