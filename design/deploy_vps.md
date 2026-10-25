# Despliegue de tt-scraper en un VPS (cargas periódicas + report)

> Cómo está montado el scraper en el VPS (`ssh vps`, Debian 13) para cargar
> tt-data varias veces al día y publicar un report HTML de estado. Pensado para
> poder reproducirlo o mantenerlo. Escrito 2026-10-25.

## Qué corre y dónde

- Ruta: **`/home/manu/tt-scrapper`** (código de `tt-scraper` sin `apps/api`).
- **Python**: `venv/` con las dependencias de D1 (sin `google-cloud-firestore`,
  que solo hace falta en backend Firestore).
- **Node + wrangler**: Node LTS en `.node/` y `wrangler` en `node_modules/`
  (instalados sin root). wrangler habla con la D1 `tt-data`.
- **Cron** (`crontab -l`): ejecuta `cron.sh` (ver abajo).
- **nginx en Docker** (`web/`): sirve el report en `127.0.0.1:8088/status/jobs/`.

El código es el mismo del repo; la única adaptación es que la ruta de wrangler
se toma de `TTS_WRANGLER_CWD` (commit "ruta de wrangler configurable"), porque en
el VPS no existe el monorepo `apps/api`.

## Ficheros propios del VPS (NO en el repo)

- **`env.sh`**: entorno para cron/ejecución. Exporta `PATH` (node), `TTS_WRANGLER_CWD`,
  `SEASON`, `NODE_OPTIONS`, `CLOUDFLARE_ACCOUNT_ID`, y sourcea el secreto.
- **`~/.tt-scrapper-secrets`** (chmod 600, fuera del repo): `export CLOUDFLARE_API_TOKEN="..."`.
  Token de API de Cloudflare con permiso **Account · D1 · Edit**.
- **`wrangler.toml`**: mínimo (la D1 `tt-data` se resuelve por nombre).
- **`cron.sh`**: wrapper de cron (entorno + `flock` anti-solape + log a
  `output/logs/cron.log`).
- **`output/cache/hashes.d1.json`**: caché de hashes copiada del PC de desarrollo
  para que la primera pasada no reprocese todo (incremental desde el minuto uno).

## Montaje desde cero (resumen reproducible)

```bash
# 1) Código (desde el PC de desarrollo, en tt-scraper/):
tar czf - --exclude=venv --exclude=output --exclude=__pycache__ \
  --exclude='*.pyc' --exclude=.pytest_cache --exclude=.env --exclude=credentials . \
  | ssh vps 'mkdir -p /home/manu/tt-scrapper && tar xzf - -C /home/manu/tt-scrapper'
#    (copiar aparte output/cache/ para arrancar con la caché de hashes)

# 2) Python (en el VPS):
cd /home/manu/tt-scrapper
python3 -m venv venv && venv/bin/pip install -U pip \
  && venv/bin/pip install httpx beautifulsoup4 lxml pdfplumber python-dotenv rich click

# 3) Node + wrangler (sin root):
VER=$(curl -s https://nodejs.org/dist/index.json | python3 -c 'import json,sys;print(next(r["version"] for r in json.load(sys.stdin) if r["lts"]))')
curl -fsSL "https://nodejs.org/dist/$VER/node-$VER-linux-x64.tar.xz" -o /tmp/node.tar.xz
mkdir .node && tar xf /tmp/node.tar.xz -C .node --strip-components=1 && rm /tmp/node.tar.xz
export PATH=/home/manu/tt-scrapper/.node/bin:$PATH && npm install wrangler

# 4) env.sh + wrangler.toml + secreto (ver "Ficheros propios"), y:
echo 'export CLOUDFLARE_API_TOKEN="<token>"' > ~/.tt-scrapper-secrets && chmod 600 ~/.tt-scrapper-secrets

# 5) Prueba:
. ./env.sh && ./node_modules/.bin/wrangler d1 execute tt-data --remote --command "select 1"
```

## Cron

`cron.sh` fija el entorno (`. ./env.sh`), toma un `flock` (no se solapan dos
ejecuciones) y lanza `scripts/run_all.sh`, registrando en `output/logs/cron.log`.

```cron
# Cargas diarias (solo pendiente) — 09:00, 13:00, 19:00 (Europe/Madrid)
0 9,13,19 * * * /home/manu/tt-scrapper/cron.sh
# Repaso completo por hash (detecta actas corregidas) — miércoles 15:00
0 15 * * 3 /home/manu/tt-scrapper/cron.sh --full
```

- **Diario**: `run_all.sh` con `--pending` → scrapea solo los grupos con
  encuentros vencidos sin resultado + rànquings (incremental por hash) +
  identidad. Rápido.
- **`--full` (miércoles)**: recorre TODOS los grupos (sin `--pending`),
  incremental por hash. Una **acta corregida** cambia el HTML → cambia el hash →
  se recarga; `--pending` no lo vería porque ese grupo ya tiene resultado.

## Report HTML

- Lo genera `scripts/run_all.sh` (paso final `build_report.py`) en
  `output/reports/latest.html` (+ `report_<stamp>.html` con histórico).
- Se publica con **nginx en Docker** (`web/docker-compose.yml` + `web/nginx.conf`),
  atado a **`127.0.0.1:8088`** (NO a 0.0.0.0: los puertos 80/443 del VPS ya están
  en uso y, sobre todo, no se expone a internet directamente).

```bash
cd /home/manu/tt-scrapper/web && docker compose up -d     # arrancar
# report en http://127.0.0.1:8088/status/jobs/  (solo localhost)
```

La publicación segura **a internet** la hace Cloudflare Tunnel; la parte de
Cloudflare está documentada para Kayzen en `design/PARA-KAYZEN-report-status.md`.

## Actualizar el código en el VPS

```bash
# desde el PC (tt-scraper/): recopiar el código (respeta venv/output/secretos)
tar czf - --exclude=venv --exclude=output --exclude=__pycache__ --exclude='*.pyc' \
  --exclude=.pytest_cache --exclude=.env --exclude=credentials . \
  | ssh vps 'tar xzf - -C /home/manu/tt-scrapper'
# si cambian dependencias: venv/bin/pip install -r requirements.txt (o el subconjunto)
```

## Operación / diagnóstico

- Log de cron: `tail -f output/logs/cron.log` · detalle por paso:
  `output/logs/run_<stamp>/`.
- Lanzar a mano: `. ./env.sh && bash scripts/run_all.sh` (o `--full`).
- Ver el report sin web: `output/reports/latest.html`.
- Estado de la D1 a mano: `. ./env.sh && venv/bin/python scripts/build_report.py --db tt-data --season 2026-2027 --out output/reports`.
