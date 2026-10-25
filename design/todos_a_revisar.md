# TODOs a revisar — tt-scraper

Hallazgos de la revisión de código hecha durante la migración a Firestore propio (13/08/2026).

Cada punto indica **dónde está**, **qué pasa** y **qué habría que hacer**.

> Estado: pendiente de priorización, salvo el punto 5 que ya está corregido.

---

## Resumen

| # | Punto | Severidad |
|---|-------|:---------:|
| 1 | `--dry-run` escribe en la caché de hashes: deja de ser una simulación sin efectos — **corregido** | — |
| 2 | `--force` no invalida los hashes de jornada, así que no sirve para repoblar — **corregido** | — |
| 3 | Un PDF ya descargado no se refresca nunca: las actas corregidas no se recogen | Media |
| 4 | RTBTT no publica clasificación en su web → pestaña Clasificación vacía en la liga de Barcelona (**decisión pendiente**) | Media |
| 5 | `LOG_LEVEL` se leía pero no se aplicaba — **corregido** | — |
| 6 | El `credentials_path` de `FirestoreClient` es decorativo | Baja |
| 7 | El CLI no permite elegir el fichero `.env` | Baja |
| 8 | Los hashes de ranking se guardan bajo la clave `matchdays` de la caché | Baja |

---

### 1 · `--dry-run` escribe en la caché de hashes — CORREGIDO 13/08/2026

**Dónde:** [`scrapers/rtbtt_scraper.py:215`](../scrapers/rtbtt_scraper.py) (`cache.set_matchday_hash`) y [`:223`](../scrapers/rtbtt_scraper.py) (`cache.set_group_hash`)

**Qué pasa:** el flag `dry_run` solo llega hasta `save_matchday_with_matches`, que se limita a loguear lo que habría escrito. Los hashes de la caché local se actualizan igual. Es decir, un `--dry-run` **no es una simulación sin efectos**: deja el `hashes.json` como si la carga se hubiera hecho.

**Por qué importa:** mordió durante la migración del 13/08/2026. Para repoblar un proyecto vacío hay que apartar `output/cache/hashes.json`; si después haces un `--dry-run` "para comprobar", te reconstruye la caché entera y la ejecución real siguiente se lo salta todo y escribe cero documentos.

**Qué se hizo:** en vez de repartir `if not dry_run:` por los puntos de escritura, `LocalCache` acepta ahora `readonly=True` y `_save()` no hace nada en ese modo. Los hashes se siguen actualizando **en memoria**, de forma que la simulación se comporta igual que una ejecución real, pero el `hashes.json` no se toca. `main.py` construye la caché con `readonly=cfg.dry_run` en `rtbtt run` y `rfetm run`. Los PDFs sí se siguen guardando: son copia del dato de origen, no estado que decida qué se escribe.

Verificado: `rtbtt run --dry-run --force --group PREF_G1` procesa las 22 jornadas y deja el `hashes.json` con el mismo md5 que antes de empezar.

---

### 2 · `--force` no invalida los hashes de jornada — CORREGIDO 13/08/2026

**Dónde:** [`main.py:130`](../main.py) — `cache.set_group_hash(grp.id, "")`

**Qué pasa:** `--force` invalida el hash **del grupo**, así que el grupo no se salta y se vuelven a descargar los PDFs. Pero los hashes **por jornada** siguen en la caché, y el scraper compara el hash del PDF antes de parsear ([`rtbtt_scraper.py:169`](../scrapers/rtbtt_scraper.py)): si coincide, hace `continue` sin escribir. Resultado: `--force` se descarga todo, tarda lo mismo que una carga completa y no escribe ni un documento.

**Qué se hizo:** nuevo `LocalCache.invalidate_group(group_id)`, que borra el hash del grupo y el de todas sus jornadas. Lo usan los dos comandos (`rtbtt run` y `rfetm run`), sustituyendo el `set_group_hash(id, "")` de uno y el bucle `range(1, 30)` con el prefijo `rfetm_` hardcodeado del otro. El grupo se ancla con `(^|_)` para no confundir `1A_G1` con `11A_G1`, que era un falso positivo real dado el esquema de ids de RTBTT.

Con esto, **`--force` ya sirve para repoblar un proyecto vacío** y deja de ser necesario apartar el `hashes.json` a mano.

---

### 3 · Un PDF ya descargado no se refresca nunca

**Dónde:** [`scrapers/rtbtt_scraper.py:238`](../scrapers/rtbtt_scraper.py) → [`storage/local_cache.py:41`](../storage/local_cache.py)

**Qué pasa:** `_download_pdf` devuelve el fichero local sin descargar nada si `is_pdf_cached(filename)` es cierto, y esa comprobación **solo mira que el fichero exista**. Como el nombre del PDF es determinista (`{group_id}_jornada{n}.pdf`), una vez descargada un acta ya no se vuelve a pedir jamás. Y como el hash de jornada se calcula sobre el PDF **local**, siempre coincide y la jornada se salta.

**Por qué importa:** si la federación publica un acta corregida (resultado mal transcrito, jugador equivocado), el scraper no se entera nunca. La única vía hoy es `--jornada N`, que sí borra el PDF local para forzar la re-descarga.

**Qué hacer:** comparar contra el origen en vez de fiarse de la existencia del fichero — un `HEAD` con `Last-Modified`/`ETag`, o descargar siempre y comparar hashes (cuesta 1 s por acta por el rate limit). Alternativa barata: caducar los PDFs de las últimas N jornadas, que son las que se corrigen.

---

### 4 · RTBTT no publica clasificación — decisión pendiente

**Dónde:** [`parsers/html_parser.py:234-304`](../parsers/html_parser.py) (`_parse_standings`), consumido en [`scrapers/rtbtt_scraper.py:96-115`](../scrapers/rtbtt_scraper.py)

**Qué pasa:** `parse_group_page` devuelve `(matchdays_info, teams)` y el scraper solo llama a `save_teams` `if teams:`. Para RTBTT esa lista sale siempre vacía, así que la subcolección `teams/` no se crea en ningún grupo. Verificado el 13/08/2026 en PREF_G1, 1A_G1 y 2A_G1: 22 jornadas y 0 equipos. El proyecto viejo (`prematch-cec7d`) está igual: no ha funcionado nunca.

**Diagnóstico (13/08/2026): no es un fallo del parser — la fuente no publica la clasificación.** La página de grupo (`.../PREF_G1/PREF_G1.html`) tiene exactamente dos tablas y las dos son de actas (`Jornada · Equip Local · Acta · Equip Visitant`). Ni en la página de grupo ni en el índice hay un solo enlace ni una sola aparición de la palabra "classificació"/"clasificación". `_parse_standings` busca cabeceras (`pts`, `pj`, `pg`…) que no existen en ninguna parte del sitio.

**Consecuencia en la app:** la pestaña **Clasificación** del Módulo 8 lee `{groupPath}/teams` y se queda vacía para toda la liga territorial de Barcelona. En RFETM sí funciona ([`scrapers/rfetm_scraper.py:81-87`](../scrapers/rfetm_scraper.py)), y en FCTT funcionará (publica la tabla en cada página).

**Opciones, pendiente de decidir:**

1. **Calcularla nosotros** a partir de los resultados, que sí tenemos todos (cada encuentro con su marcador). Salen jugados, ganados, perdidos y puntos a favor/en contra sin esfuerzo. El problema es el sistema de puntos: no hay ninguna tabla oficial publicada contra la que validar el cálculo, así que estaríamos mostrando una clasificación inventada con total seguridad aparente. Si la liga aplica descuentos por incomparecencia o un criterio de desempate propio, no hay forma de saberlo.
2. **Asumir que RTBTT no tiene clasificación** y ocultar la pestaña en la app cuando la competición seleccionada no tenga `teams/`. Honesto y barato; es lo que yo haría mientras no aparezca una fuente oficial.

Una tercera vía si aparece: la Federació Catalana (FCTT) publica clasificación de sus competiciones, pero de la territorial de Barcelona (RTBTT) no consta ninguna.

---

### 5 · `LOG_LEVEL` se leía pero no se aplicaba — CORREGIDO 13/08/2026

**Dónde:** [`utils/logger.py`](../utils/logger.py), [`config.py`](../config.py)

**Qué pasaba:** `Config.log_level` se rellenaba desde el `.env` y no lo usaba nadie. `get_logger` nunca llamaba a `setLevel`, así que los loggers quedaban en `NOTSET` y heredaban el nivel por defecto de la raíz (`WARNING`). Todos los mensajes de progreso del scraper son `logger.info`, de modo que una ejecución larga no imprimía absolutamente nada y parecía colgada.

**Qué se hizo:** `utils/logger.py` guarda ahora el nivel (de la variable de entorno o `INFO` por defecto), lo aplica en `get_logger` y expone `set_level()`; `Config.from_env()` lo llama tras `load_dotenv`, porque los loggers de cada módulo se crean al importar, antes de que el `.env` esté cargado.

---

### 6 · El `credentials_path` de `FirestoreClient` es decorativo

**Dónde:** [`storage/firestore_client.py:24-32`](../storage/firestore_client.py)

**Qué pasa:** `_get_db` hace `os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", self._credentials_path)`. Como `Config.from_env` ya ha metido esa variable en `os.environ` vía `load_dotenv`, el `setdefault` es siempre un no-op y el argumento del constructor no se usa jamás: las credenciales salen de la variable de entorno del proceso.

**Por qué importa:** hoy funciona por casualidad y es inofensivo con un solo proyecto. Pero engaña al leer el código, y hace imposible tener dos clientes contra proyectos distintos en el mismo proceso (escritura dual durante una migración, por ejemplo).

**Qué hacer:** credenciales explícitas por instancia:

```python
from google.oauth2 import service_account
creds = service_account.Credentials.from_service_account_file(self._credentials_path)
self._db = firestore.Client(project=self._project_id, credentials=creds)
```

Y mantener siempre juntos el par (JSON, `project_id`): si las credenciales son de un proyecto y el `project_id` de otro, el cliente se construye sin quejarse y falla mucho después con un 403 confuso.

---

### 8 · `LocalCache` guarda los hashes de ranking bajo la clave `matchdays`

**Dónde:** [`scrapers/fctt_rankings_scraper.py`](../scrapers/fctt_rankings_scraper.py) usa `cache.set_matchday_hash("fctt_rk_{edición}_{categoría}", …)`

**Qué pasa:** los PDFs de ranking no son jornadas, pero reutilizan el almacén de hashes de jornada porque es el que existe. Funciona —las claves llevan prefijo `fctt_rk_` y no colisionan— pero el `hashes.json` mezcla dos cosas distintas bajo el mismo epígrafe, y `invalidate_group()` no las alcanza (su patrón exige `_jornadaN`).

**Qué hacer:** un `get_hash(key)` / `set_hash(key, value)` genérico en `LocalCache`, con `get_matchday_hash` delegando en él por compatibilidad. Y de paso, un `invalidate_rankings()` para no tener que purgar el JSON a mano.

---

### 7 · El CLI no permite elegir el fichero `.env`

**Dónde:** [`config.py:27`](../config.py) y todos los comandos de [`main.py`](../main.py)

**Qué pasa:** `Config.from_env(env_file=".env")` acepta la ruta, pero ningún comando la expone: todos llaman a `Config.from_env()` a secas. Para apuntar a otro proyecto o a otra temporada hay que editar el `.env` a mano.

**Qué hacer:** una opción global `--env-file` en el grupo `cli()` que se propague por el contexto de click. Útil para separar destinos (`.env.pro`, `.env.poc`) sin editar ficheros.

---

### 9 · Sobras del renombrado de categorías en `output/pdfs/rankings/`

**Dónde:** [`output/pdfs/rankings/`](../output/pdfs/rankings/)

**Qué pasa:** los PDFs se guardan con el id de categoría ya normalizado (`10_V40M.pdf`), pero los descargados antes del renombrado siguen en disco con el nombre viejo (`10_40M.pdf`). Son ~90 ficheros duplicados: ocupan sitio y obligan a filtrar por "empieza por dígito" a cualquier código que recorra la carpeta — lo hace ya `_ranking_por_jugador` en [`main.py`](../main.py).

**Qué hacer:** borrarlos (`rm output/pdfs/rankings/*_[0-9][0-9][MF].pdf`) y quitar el filtro. No corre prisa: son solo caché local, se vuelven a descargar solos.

---

### 10 · El marcador de `Partits_Jugats` no siempre es fiable

**Dónde:** [`parsers/fctt_partits_parser.py`](../parsers/fctt_partits_parser.py), `_row_from`

**Qué pasa:** en unas 264 filas de 99.584 (0,27 %) el marcador contradice a los puntos: el jugador gana 2-0 y pierde 14,4 puntos, o pierde 0-2 y gana 34. La lectura más plausible es que la fuente, al escribir el partido desde el otro jugador, cambia nombre y licencia pero no gira el marcador. El ΔRK siempre es correcto — la suma cuadra con el ranking en las nueve ediciones, al 100 %.

Hoy se corrige con una heurística (girar cuando el marcador contradice al ΔRK) que **mejora pero no resuelve**: los jugadores con el balance de victorias mal bajan de 70 a 27, pero 11 que estaban bien pasan a estar mal. No hay regla local que lo resuelva, porque en este sistema se puede ganar y perder puntos, y probar por evento tampoco separa: en `Open Ciutat d'Olot` girar ayuda a 4 y estropea a 10; en `Piramidal de Veterans RTL` ayuda a 26 y estropea a 3.

**Qué hacer:** reconciliar contra el ranking del mismo mes, que es la fuente autoritativa y ya está descargado. Para cada jugador y edición, elegir la interpretación —cruda o girada— cuyo PG/PP coincida con su fila del ranking, y dejar la cruda cuando ninguna cuadre, contándolo. Eso da el 100 % por construcción en vez de un 99,5 % heurístico. Cuesta que el cargador de partidos dependa de los PDFs de ranking de la misma edición; ya los tiene `fctt verify`, así que la lógica está medio escrita.

---

---

## Trabajo acordado, pendiente de hacer

### A · Recargar los rànquings de la RFETM — **lo primero**

Los dos fallos de modelo (sexo en la clave, `current` obsoleto) están corregidos en el código pero **el dato cargado es el viejo**. Hace falta `python main.py rfetm rankings --force` (~4.900 escrituras) y borrar los 262 documentos de categoría con el id antiguo (`NAC-M` en lugar de `NAC_M`). Detalle en [`plan_rankings_rfetm.md`](plan_rankings_rfetm.md) §8.

### B · Documento de índice de búsqueda

Firestore no tiene búsqueda de texto: ni subcadena, ni prefijo dentro de una palabra, ni erratas. Los 7.358 jugadores con licencia, nombre y club ocupan **224 KB** (75 KB la FCTT, 149 KB la RFETM), así que caben en un solo documento —el límite es 1 MiB— y la app puede descargarlo una vez y buscar en local: subcadena en cualquier posición, sin acentos, tolerante a erratas, instantánea, sin conexión y por **2 lecturas**.

**Qué hacer:** al final de cada carga de rànquings, generar `rankings/{competition_id}/search/players` con la lista compacta. Dos escrituras al mes. Revisar el tamaño cuando se acerque a 1 MiB (harían falta ~30.000 jugadores) y entonces partirlo por federación o inicial.

### C · Diccionario de alias de club por voto mayoritario

El mismo club se escribe distinto en cada fuente: `VILCA` en el ranking de la FCTT, `CTT VILANOVA DEL CAMI` en las actas, `BURGOS FÉMINAS TM` en la RFETM. Ninguna normalización de texto une esas cadenas.

**Qué hacer:** deducirlo del dato. Para cada equipo de una competición, mirar el club que el ranking asigna a sus jugadores y quedarse con el mayoritario. Verificado en 7 equipos de la RTBTT con **100 % de acuerdo** en todos (entre 5 y 12 jugadores por equipo). Sirve para buscar por club (punto B) y para ir del "próximo rival" a su plantilla.

### D · Calendario futuro de la RTBTT

Para predecir alineaciones hace falta saber contra quién se juega. La FCTT marca `scheduled` los encuentros sin resultado y la RFETM `pending`, así que entrarán solos; **la RTBTT no**, porque su fuente son las actas en PDF de lo ya jugado y el scraper fija `status="completed"` a mano. Habría que localizar dónde publica la RTBTT el calendario.

---

## Contexto de la migración (13/08/2026)

- Proyecto Firestore propio: **`gravity-scrapper-pro`** (nombre para mostrar `gravity-scrapper-pro`, mismo ID). Reglas verificadas: lectura pública de `competitions/**`, escritura denegada; el scraper escribe con service account y las reglas no le aplican.
- Ojo: existió un proyecto huérfano `gravity-scrapper-pro-505407`, creado sin querer desde la consola de Google Cloud, que fue el origen de un 403 al validar. Si sigue vivo, borrarlo.
- Para repoblar un proyecto vacío: apartar `output/cache/hashes.json` y ejecutar **sin** `--dry-run` (ver puntos 1 y 2). Los PDFs de `output/pdfs/` se reutilizan.
