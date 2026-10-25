# TT Agent Scraper

Herramienta local en Python que descarga resultados de competiciones de tenis de mesa de [rtbtt.com](https://www.rtbtt.com), los parsea y los guarda en Firestore. Primera fase del módulo de seguimiento de competiciones del sistema **TT Agent**.

## Requisitos

- Python 3.11+
- Cuenta en Google Firebase / Firestore
- Conexión a internet (para descargar desde rtbtt.com)

## Setup

### 1. Crear entorno virtual

```bash
cd tt-scraper
python3 -m venv venv
source venv/bin/activate
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Crear proyecto Firebase

1. Ve a [console.firebase.google.com](https://console.firebase.google.com)
2. Crea un nuevo proyecto (o usa uno existente)
3. Habilita **Cloud Firestore** en modo producción o prueba

### 4. Descargar Service Account

1. En Firebase → Configuración del proyecto → Cuentas de servicio
2. Genera una nueva clave privada (JSON)
3. Guárdala en `credentials/firebase-service-account.json`

> ⚠️ Este fichero **nunca debe commitearse** (está en `.gitignore`)

### 5. Configurar .env

```bash
cp .env.example .env
```

Edita `.env` con tus valores:

```bash
FIRESTORE_PROJECT_ID=tu-proyecto-firebase
GOOGLE_APPLICATION_CREDENTIALS=./credentials/firebase-service-account.json
SEASON=2025-2026
```

**Las URLs no van en el `.env`**: viven en [`sources.py`](sources.py), que es la única
fuente de verdad de dominios, índices y ids de competición. El `.env` solo lleva
la temporada, las credenciales, los directorios locales y los filtros.

Cambiar `SEASON` mueve a la vez las URLs y el `competitions/{id}` de Firestore, de
forma que la temporada anterior queda archivada intacta bajo su propio id
(`rtbtt_2526`, `rfetm_2526`, `fctt_2526` → `*_2627`).

## Uso

Cada competición es un subgrupo del CLI: `rtbtt`, `rfetm` o `fctt`. Para añadir otra: declararla en `sources.py`, escribir su parser y su scraper, y añadir el subgrupo aquí.

```bash
python main.py --help          # Lista las competiciones disponibles
python main.py rtbtt --help    # Comandos de la RTBTT
python main.py rfetm --help    # Comandos de la RFETM
```

### RTBTT — Liga Territorial Barcelona

```bash
python main.py rtbtt run                                      # Toda la temporada
python main.py rtbtt run --category PREF                      # Solo Preferente
python main.py rtbtt run --category PREF --group PREF_G1      # Un grupo concreto
python main.py rtbtt run --category PREF,1A                   # Varias categorías
python main.py rtbtt run --jornada 21 --group PREF_G1         # Solo una jornada
python main.py rtbtt run --dry-run                            # Sin escribir en Firestore
python main.py rtbtt run --force                              # Ignorar todos los hashes

python main.py rtbtt download --group PREF_G1                 # Solo descargar PDFs
python main.py rtbtt parse --group PREF_G1                    # Parsear PDFs locales
python main.py rtbtt parse --group PREF_G1 --jornada 21       # Una jornada
python main.py rtbtt parse --group PREF_G1 --debug-pdf        # Texto raw del PDF

python main.py rtbtt list                                     # Categorías y grupos
```

### RFETM — Federación Española

```bash
python main.py rfetm run                                      # Todas las ligas
python main.py rfetm run --category liga1_M                   # Una liga
python main.py rfetm run --group liga1_g0_M --jornada 18      # Una jornada
python main.py rfetm run --dry-run
python main.py rfetm run --force

python main.py rfetm list                                     # Ligas disponibles
```

### FCTT — Federació Catalana de Tennis Taula

Cubre **TDM** (Tercera Divisió Masculina, 3 grupos) y la **Copa Catalana Femenina**
(1a y 2a Divisió). La sección "Lligues estatals (grups catalans)" del índice queda
deliberadamente fuera: son grupos de la liga estatal y ya están en `rfetm_*`.

```bash
python main.py fctt run                                       # TDM + Copa
python main.py fctt run --category TDM                        # Solo TDM
python main.py fctt run --group TDM_G1 --jornada 3            # Una jornada
python main.py fctt run --dry-run
python main.py fctt run --force

python main.py fctt list                                      # Categorías y grupos
```

**Rànquings** (colección `rankings`, separada de `competitions`):

```bash
python main.py fctt rankings                                  # Todas las ediciones
python main.py fctt rankings --edition 10                     # Solo una revisión
python main.py fctt rankings --category SEM                   # Solo una categoría
python main.py fctt rankings --dry-run
```

23 categorías × 9 revisiones mensuales + el ranking inicial, en PDF. El `NP` de
estos PDFs es el mismo `codi_jugador` que usan las páginas de liga, así que el
ranking enlaza con los partidos ya cargados sin casar nombres.

Dos particularidades de esta fuente:

- **Va más despacio a propósito** (2,5 s entre peticiones). fctt.cat empieza a
  responder páginas vacías con HTTP 200 si se le encadenan peticiones a 1/s. El
  scraper detecta esas páginas —una página válida siempre trae la tabla de
  clasificación— y reintenta con esperas crecientes antes de darse por vencido.
- **Comprueba la temporada.** Sus URLs no la llevan (`/lligues/grup-1/` sirve
  siempre la vigente), así que el scraper compara la temporada publicada en la
  página con `SEASON` y aborta si no coinciden, en vez de escribir la temporada
  nueva encima de la archivada.

**Partidos jugados** (`Partits_Jugats`, el histórico partido a partido):

```bash
python main.py fctt matches                                   # Las 9 ediciones
python main.py fctt matches --edition 10                      # Solo una revisión
python main.py fctt matches --dry-run
```

El mismo `/ranquings/` publica, junto a cada revisión, el histórico de partidos
de todos los jugadores con licencia: fecha, evento, rival, marcador, el rating
de los dos **en ese momento** y los puntos que se movieron. Es la única fuente
que cubre los torneos (`Top Català`, `Open Internacional de Vic`, `Circuit
Promeses`…), además de las dos ligas que ya scrapeamos.

Es incremental —cada edición trae solo el periodo desde la anterior, sin
solaparse— y tiene una peculiaridad que condiciona todo el parser: **solo se
publica la licencia del jugador 1**. La del rival se recupera de la fila espejo,
porque cada partido aparece dos veces, una desde cada jugador. El emparejado
valida marcador y ratings cruzados: casar por nombre atribuiría partidos al
jugador equivocado, y hay hasta 19 nombres repetidos en una sola edición. Los
rivales de otras federaciones no tienen fila propia y se guardan con
`opponent.external = true`.

### Validar credenciales Firestore

Comando genérico, no asociado a ninguna competición:

```bash
python main.py validate
```

## Estructura de datos en Firestore

```
competitions/{competition_id}
  ├─ categories/{category_id}
  │   └─ groups/{group_id}
  │       ├─ teams/{team_id}
  │       ├─ matchdays/{matchday_number}
  │       └─ matchdays/{n}/matches/{match_id}
  │               └─ individual_matches/{im_id}
```

Ejemplo de ruta completa:
```
competitions/rtbtt_2526/categories/PREF/groups/PREF_G1/matchdays/3/matches/3_ctt_barcelona_a_vs_ctt_granollers
```

Los rànquings viven en una colección raíz aparte, para no mezclar
clasificaciones con partidos:

```
rankings/{competition_id}
  ├─ editions/{ini,02..10}
  │   └─ categories/{SEM,SEF,V40M,S21F…}      ← con su `top` de 100
  └─ players/{codi_jugador}
      ├─ current, history, record
      └─ match_log/{edición}                  ← los partidos de ese periodo
```

Los ids de categoría llevan prefijo (`V40M`, `S21F`) **a propósito**: como
acaban siendo claves de mapa dentro de `current`, una que empezase por dígito
obligaría a escapar la ruta del campo y, sin escapar, la consulta devolvería
cero resultados sin dar error.

## Detección de cambios

El scraper usa hashes MD5 para evitar reprocesar contenido ya analizado:

- **Página HTML de grupo**: si el hash coincide con el guardado en `output/cache/hashes.json`, se salta el grupo
- **PDF de jornada** (RTBTT) / **página de jornada** (RFETM, FCTT): si el hash ya está en caché, se salta la jornada
- Segunda ejecución → todos los grupos sin cambios → "Sin cambios desde última ejecución"

Para forzar reprocesamiento completo: `--force`, que invalida el hash del grupo
**y el de todas sus jornadas** (si solo invalidase el del grupo, se volvería a
descargar todo pero no se escribiría nada).

`--dry-run` no toca `hashes.json`: la caché se abre en modo solo lectura, los
hashes se actualizan en memoria para que la simulación se comporte como una
ejecución real, y el fichero queda intacto.

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Los tests del PDF parser se saltarán automáticamente si no hay un fichero `tests/fixtures/sample_acta.pdf`. Para activarlos, copia un acta real de rtbtt.com ahí.

## Estructura del proyecto

```
tt-scraper/
├── main.py              # CLI (Click)
├── sources.py           # ÚNICA fuente de verdad de URLs e ids de competición
├── config.py            # Configuración desde .env
├── scrapers/
│   ├── base.py          # Clase abstracta CompetitionScraper
│   ├── rtbtt_scraper.py # Implementación para rtbtt.com (actas en PDF)
│   ├── rfetm_scraper.py # Implementación para rfetm.es (HTML)
│   └── fctt_scraper.py  # Implementación para fctt.cat (HTML)
├── parsers/
│   ├── html_parser.py       # RTBTT
│   ├── rfetm_html_parser.py # RFETM
│   ├── fctt_html_parser.py  # FCTT
│   └── pdf_parser.py        # Parsing de actas PDF con pdfplumber
├── storage/
│   ├── firestore_client.py  # Escrituras en Firestore
│   └── local_cache.py       # Caché local de hashes
├── models/
│   ├── competition.py   # CompetitionIndex, Category, Group
│   ├── match.py         # Match, IndividualMatch, Matchday, SetScore
│   └── team.py          # Team
├── utils/
│   ├── logger.py        # Logging con Rich (nivel desde LOG_LEVEL)
│   ├── hashing.py       # MD5 helpers
│   ├── text.py          # slugify compartido
│   └── http_client.py   # HTTP con reintentos y rate limit por fuente
├── credentials/         # Aquí va firebase-service-account.json (no commitear)
└── output/
    ├── cache/           # hashes.json
    ├── pdfs/            # PDFs descargados
    └── logs/            # Logs de ejecución
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'google.cloud'`**
```bash
pip install google-cloud-firestore
```

**`FileNotFoundError: credentials/firebase-service-account.json`**
Asegúrate de haber descargado el JSON de la cuenta de servicio y colocarlo en `credentials/`.

**`DefaultCredentialsError`**
Revisa que `GOOGLE_APPLICATION_CREDENTIALS` en `.env` apunta al fichero correcto y que el proyecto en `FIRESTORE_PROJECT_ID` coincide.

**El parser no extrae datos del PDF**
Usa `--debug-pdf` para ver el contenido raw:
```bash
python main.py parse --group PREF_G1 --debug-pdf
```
El formato de las actas de rtbtt.com puede variar. El parser usa estrategias heurísticas; si un acta tiene un formato muy diferente, puede necesitar ajustes en `parsers/pdf_parser.py`.

**Rate limiting / bloqueos**
El cliente HTTP espera 1 segundo entre peticiones. Si rtbtt.com bloquea las peticiones, aumenta `_RATE_LIMIT_SLEEP` en `utils/http_client.py`.

## Próximos pasos: migración a Cloud Run

Para migrar de ejecución local a Cloud Run:

1. Crear un `Dockerfile` con la imagen Python 3.11
2. Mover las credenciales a **Secret Manager** de Google Cloud
3. Crear un **Cloud Scheduler** job que dispare el contenedor periódicamente
4. Eliminar la escritura local de PDFs (usar Cloud Storage o procesar en memoria)
5. Adaptar `config.py` para leer desde variables de entorno del servicio Cloud Run

La arquitectura del código ya está preparada: la separación entre scrapers, parsers y storage facilita esta migración sin cambios estructurales.
