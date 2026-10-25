# Lo que Kayzen le pide a `tt-scraper`

Este documento no describe cómo funciona Kayzen: describe **lo que Kayzen
necesita de `tt-data`** para leerla sin arruinarse. Está escrito para quien
trabaja en el scraper, no para quien trabaja en la aplicación.

Hay **una petición** y **dos avisos**. La petición es una línea de código.

---

## 1. La petición: `ANALYZE` al final de cada carga

Después de escribir los datos de una temporada —y antes de dar la carga por
terminada— hay que ejecutar contra `tt-data`:

```sql
ANALYZE;
```

Sin parámetros: analiza todas las tablas y todos los índices de la base.

**Coste medido en producción el 2026-09-16**: 624 ms, 1.682.040 filas leídas, 28
escritas. Es una vez por carga del scraper; no por usuario ni por consulta.

---

## 2. Por qué: SQLite elige mal sin estadísticas

`tt-data` no tiene `sqlite_stat1`. Nunca se le ha pasado un `ANALYZE`, así que el
planificador de consultas **decide a ojo**, y en esta base se equivoca de una
forma concreta y cara.

El caso es `player_match`. Es una tabla `WITHOUT ROWID`, con esta clave primaria:

```sql
PRIMARY KEY (federation, season, edition_id, lic, seq)
```

`lic` está en **cuarto** lugar. Una consulta que filtra por `federation` y `lic`
—que es prácticamente todo lo que Kayzen pregunta— solo puede aprovechar la
primera columna de esa clave. Existe el índice bueno:

```sql
CREATE INDEX idx_pm_player ON player_match(federation, lic, date)
```

…pero **sin estadísticas SQLite prefiere la clave primaria**, porque en una tabla
`WITHOUT ROWID` la clave *es* la tabla y usar el índice obliga a volver a ella a
buscar el resto de columnas. Sin datos para comparar, supone que es peor. No lo
es: por 160 filas de diferencia se lleva por delante cien mil.

Le pasa sobre todo a las consultas que **además filtran por columnas sin índice**
—`rk`, `opp_rk`, `won`—. Esas condiciones extra le hacen creer que el índice no
le sirve de mucho. Las que solo miran `federation`, `lic` y `date` eligen bien
solas, y son la mayoría: por eso el problema estuvo meses sin verse.

---

## 3. Lo que se midió, antes y después

Dieciséis consultas reales de Kayzen, con `EXPLAIN QUERY PLAN` y midiendo
`rows_read`. Cuatro estaban mal:

| Qué hace | Plan antes | Plan después | Filas leídas |
|---|---|---|---|
| Resumen de un federado | clave primaria, solo `federation` | `idx_pm_player` | 100.152 → **181** |
| Su nombre sacado de las actas | clave primaria, solo `federation` | `idx_pm_opponent` | 100.328 → **353** |
| «¿A quién gana de verdad?» (agente) | clave primaria, solo `federation` | `idx_pm_player` | 100.152 → **189** |
| Su ranking vigente | clave primaria, solo `federation` | `idx_snap_player` | ~40.000 → **11** |

Las dos primeras corren **cada vez que alguien abre la ficha de un rival**.

**Ninguna consulta empeoró con el `ANALYZE`.** Doce quedaron con el mismo plan y
una mejoró de paso: la que saca los partidos de las actas pasó a usar también
`competition_id` dentro del índice (71 filas leídas).

### Cómo se descubrió

El 2026-09-16 una consulta de diagnóstico sobre `player.name` —columna sin
índice— leyó **12,27 millones de filas** de una sentada y agotó el límite diario
del plan gratuito de D1. Con la base sin lecturas dejó de funcionar la entrada
con Google en la aplicación. Al revisar todas las consultas por ese motivo
aparecieron estas cuatro.

D1 factura **filas escaneadas, no devueltas**. Ese es el detalle que convierte un
plan malo en una factura o en una caída.

---

## 4. Por qué tiene que ir en cada carga, y no vale con haberlo lanzado una vez

El `ANALYZE` ya se lanzó a mano el 2026-09-16, así que **ahora mismo la base está
bien**. El problema es lo que pasa en la carga siguiente.

`DROP TABLE` **borra las filas de esa tabla en `sqlite_stat1`**. Comprobado. Si la
carga de la temporada nueva recrea alguna tabla en vez de insertar sobre la que
hay, las estadísticas de esa tabla desaparecen y los planes vuelven a los malos.

Y esto es lo importante: **no falla nada**. No hay error, no hay aviso, no hay
nada en un log. La aplicación sigue funcionando exactamente igual y pasa a costar
quinientas veces más por consulta. La única señal sería la factura, o volver a
quedarse sin lecturas.

Si la carga **solo inserta** filas nuevas sin recrear tablas, las estadísticas
sobreviven y se quedan algo desfasadas — lo cual aquí da igual, porque seguirían
diciendo «`lic` es selectivo», que es cierto. Pero el modo que rompe es el
silencioso, así que la recomendación no depende de cuál de los dos sea:
**`ANALYZE` al final, siempre.**

---

## 5. Aviso 1: hay nombres de índice de los que Kayzen depende

Tres consultas de Kayzen llevan `INDEXED BY` porque se escribieron **antes** de
saber que el `ANALYZE` lo arreglaba, y se quedan como red de seguridad para la
ventana entre una recarga y el `ANALYZE` siguiente.

Eso convierte estos dos nombres en una **dependencia real**:

```
idx_pm_player     ON player_match(federation, lic, date)
idx_pm_opponent   ON player_match(federation, opp_lic)
```

Si desaparecen o cambian de nombre, esas consultas fallan con
`no such index` — en alto, no en silencio, que es a propósito. Los tests de
Kayzen crean esos dos índices en sus fixtures justo para que un cambio de nombre
salga en rojo.

**No hace falta hacer nada**, solo saberlo antes de tocar la definición de los
índices. Si algún día el scraper necesita renombrarlos, avisad y se quitan los
`INDEXED BY` de la aplicación en el mismo momento.

El resto de índices que Kayzen usa —y que no están atados por nombre— son:

```
idx_enc_group     ON encounter(competition_id, group_id, matchday_number)
idx_im_local      ON individual_match(player_local_lic)
idx_im_visitor    ON individual_match(player_visitor_lic)
idx_snap_player   ON ranking_snapshot(federation, lic, edition_id)
idx_pos_top       ON ranking_position(federation, season, edition_id, category, sex, pos)
idx_link_b        ON player_link(federation_b, lic_b)
```

---

## 6. Aviso 2: `player.name` no tiene índice, y está bien así

Kayzen **no busca nunca por `player.name`**: para eso usa `player_fts`, que es lo
correcto. No hace falta añadir ningún índice por ahí.

Se menciona solo porque es la columna que causó el incidente: un `JOIN` de
`player` consigo misma por `name` es un producto cartesiano de 2.770 × 4.351
filas. Si desde el scraper se hacen consultas de diagnóstico o de deducción sobre
nombres, conviene tenerlo presente.

---

## 7. Cómo comprobar que ha funcionado

Después de la primera carga con `ANALYZE`, dos comprobaciones de un minuto:

**Que las estadísticas están:**

```sql
SELECT count(*) FROM sqlite_stat1;
```

Tiene que devolver algo distinto de cero, y volver a hacerlo después de cada
carga.

**Que el plan es el bueno** (esto no lee filas, no cuesta nada):

```sql
EXPLAIN QUERY PLAN
SELECT count(*) FROM player_match
 WHERE federation = 'FCTT' AND lic = '18196'
   AND rk IS NOT NULL AND opp_rk IS NOT NULL;
```

- Bien: `SEARCH player_match USING INDEX idx_pm_player (federation=? AND lic=?)`
- Mal: `SEARCH player_match USING PRIMARY KEY (federation=?)`

Si sale lo segundo, el `ANALYZE` no se ha ejecutado o la carga borró las
estadísticas después.

---

## 8. Resumen

| | |
|---|---|
| **Qué hay que hacer** | `ANALYZE;` al final de cada carga de `tt-data` |
| **Qué cuesta** | 624 ms, 1,68 M filas leídas, una vez por carga |
| **Qué arregla** | Cuatro consultas que leían tablas enteras; de ~100.000 filas a ~200 |
| **Qué pasa si no se hace** | Nada visible. Solo cuesta 500× más, y se descubre por la factura |
| **Urgencia** | Antes de cargar los calendarios de la temporada nueva |
| **Qué NO hay que tocar** | Los nombres `idx_pm_player` e `idx_pm_opponent` |

Medido en producción el 2026-09-16. El detalle completo, con los planes de las
dieciséis consultas, está en `kayzen-agent/docs/GRAFICA-Y-RIVALES.md` §18 y §19.
