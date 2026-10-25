# `player.club`, y el `year`/`month` de las ediciones — respuesta

> Para el equipo de Kayzen, contestando a vuestro `PARA-TT-SCRAPER-licencias.md`
> (2026-09-28). Todo comprobado contra el código de `tt-scraper` y la prod
> `tt-data`.

## Resumen

- **Vuestro diagnóstico es correcto**: `player.club` es el club de la **última
  edición del ranking**, no el club vigente. Para quien se cambió de club este
  verano, va con retraso. No es un fallo del scraper: es la fuente (el fichero del
  ranking) la que va por detrás de los traspasos.
- **`ranking_edition.year`/`month` de la FCTT: corregido** — se derivan del label
  en la carga y se ha hecho backfill de las 11 ediciones ya cargadas. Ya podéis
  ordenar por `(year, month)` sin depender del desempate por `edition_id`.

## Pregunta 1 — ¿De dónde sale hoy `player.club`?

Del **fichero del ranking**, confirmado en código. La tabla `player` se escribe en
**un solo sitio**, `D1Client.save_ranking_players`, con el club que trae cada fila
del ranking. Las actas (`individual_match`) **no** escriben nunca en `player` —
solo insertan en `individual_match`. Por eso todas las filas FCTT comparten el
`updated_at` de la carga del ranking, como medisteis.

Conclusión, escrita para que la tratéis como contrato:

> **`player.club` es el club de la última edición del ranking cargada.** En la
> edición 01 («Setembre 2026/2027»), que se publica al arrancar la temporada, ese
> club es el del **final de la temporada anterior**, antes de tramitar altas y
> bajas del verano.

## Pregunta 2 — ¿Hay otra fuente con el club vigente?

Hoy, **de lo que scrapeamos, no** hay un club vigente por licencia:

- La FCTT **no** publica (que ingiramos) una lista de «licencias inscritas por
  club» esta temporada. Las páginas de grupo dan el nombre de los **equipos**, no
  el roster por licencia.
- La señal más fresca de «con qué club juega alguien ahora» es la que **ya usáis**:
  `individual_match` + `encounter` → equipo. Es un dato publicado por la federación
  y más reciente que el ranking. Nos parece la decisión correcta preferirlo.
- Queda el hueco que señaláis: quien se cambió de club y **aún no ha jugado** no
  aparece en actas, y ahí no hay nada que deducir. Para esos, `player.club` (club
  del ranking) es lo único que tenemos.

Opción a futuro (no hecho, avisadnos si os cambia el trabajo): scrapear la **fitxa
del jugador** en fctt.cat si publica el club de la licencia en vigor. Serían ~2.775
páginas nuevas por edición; solo tiene sentido si de verdad trae el club vigente y
lo vais a consumir. Si en algún momento la FCTT publica las inscripciones por
licencia, esa sería la fuente preferida.

## Pregunta 3 — ¿Separar `club` y `club_ranking`?

De acuerdo con el principio: **no sobreescribir una cosa con otra**. Hoy `player.club`
ya ES «el club del ranking» (no hay otra fuente que lo pise), así que el contrato de
arriba os vale para reproducir un ranking. Si en el futuro añadimos una fuente de
club vigente (fitxa o inscripciones), la meteremos en una **columna aparte** (p. ej.
`club_current`) sin tocar `club`, para que quien reproduce el ranking siga teniendo
el club de esa edición. No cambiamos el esquema ahora para no mover algo que hoy no
tiene segunda fuente.

## `ranking_edition.year` / `month` — corregido

Era esto: `RankingEdition.to_dict()` de la FCTT emite `month` como **nombre catalán**
(«Setembre») y **no** emite `year`, así que la carga guardaba ambos a NULL. La RFETM
sí los trae como enteros y por eso funcionaba.

Arreglado derivándolos **del propio label** en la carga (`sources.parse_ranking_label`,
usado en `save_ranking_edition`): «Setembre 2026/2027» → `year=2026, month=9`. El mes
sale del nombre; el año, del año de temporada del label aplicando la regla mes ≥ agosto
→ primer año. La RFETM no se toca (solo se deriva cuando no vienen ya como int).

**Backfill hecho** sobre las 11 ediciones FCTT ya cargadas:

```
2025-2026 02 Novembre  → year=2025 month=11      2025-2026 08 Maig     → year=2026 month=5
2025-2026 03 Desembre  → year=2025 month=12      2025-2026 09 Juny     → year=2026 month=6
2025-2026 04 Gener     → year=2026 month=1       2025-2026 10 Juliol   → year=2026 month=7
2025-2026 05 Febrer    → year=2026 month=2       2026-2027 01 Setembre → year=2026 month=9
2025-2026 06 Març      → year=2026 month=3
2025-2026 07 Abril     → year=2026 month=4       2025-2026 ini Inicial → year=NULL month=NULL
```

Nota: la edición **«Inicial»** se queda con `year`/`month` a NULL a propósito — no
tiene mes (es la pre-temporada). Ordenad con `NULLS FIRST` o dad por hecho que la
inicial va antes que la 02; el `label` la identifica igualmente.
