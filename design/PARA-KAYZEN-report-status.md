# Publicar el report de estado de cargas (para Kayzen)

> Para el equipo de Kayzen / quien gestiona Cloudflare y el dominio
> gravityplayers.net. Escrito desde tt-scraper el 2026-10-25.
>
> **Qué queremos**: ver por internet, de forma segura, un report HTML con el
> estado de las cargas de tt-data (qué hay cargado, qué falta, errores, % por
> liga y semana). Hoy se genera en un VPS y se sirve en local; falta exponerlo.

## 1. Qué hay ya montado (lado VPS, hecho)

- Un VPS (Debian) genera el report cada vez que corre el cron (09/13/19h y un
  repaso completo los miércoles) en `output/reports/latest.html`.
- Un **nginx en Docker** sirve ese report, atado **solo a `127.0.0.1:8088`**
  (no a una IP pública), en la ruta **`/status/jobs/`**:
  - `http://127.0.0.1:8088/status/jobs/` → el último report.
  - `http://127.0.0.1:8088/healthz` → `ok` (sonda).
- El puerto **no está abierto a internet** y no hay IP pública expuesta: es a
  propósito, para que la única puerta de entrada la ponga Cloudflare.

Es un HTML estático y autocontenido (sin recursos externos), solo-lectura.

## 2. Lo que pedimos a Kayzen: publicarlo seguro con Cloudflare

La vía recomendada es **Cloudflare Tunnel + Cloudflare Access (Zero Trust)**:

- **Cloudflare Tunnel (cloudflared)**: una conexión **saliente** desde el VPS a
  Cloudflare. No hace falta abrir puertos de entrada ni IP pública; funciona
  detrás de NAT/firewall. Cloudflare publica un hostname y lo enruta al nginx
  local. Es la forma segura de exponer el `127.0.0.1:8088`.
- **Cloudflare Access**: delante del hostname, una política que **solo deja
  entrar a quien autoricéis** (correo con OTP, Google SSO, lista de emails…).
  Gratis para equipos pequeños (Zero Trust free). Esto es el "de forma segura".

Con eso, el report queda detrás de login de Cloudflare y el VPS no expone nada.

### Cómo encaja el túnel con nuestro Docker

Lo más limpio: añadir un contenedor `cloudflared` al mismo `docker-compose` del
VPS, que apunta al nginx por la red interna (`http://report:80`). Ya lo dejamos
preparado (comentado) en `web/docker-compose.yml`; solo necesita el **token del
túnel** que generáis vosotros:

1. Vosotros creáis el túnel en el panel de Cloudflare (Zero Trust → Networks →
   Tunnels), tipo **remotely-managed**, y en su configuración:
   - **Public hostname** → Service `http://report:80` (o `http://localhost:8088`
     si preferís cloudflared fuera de Docker).
   - Ruta/host: ver §3.
   - Añadís la **política de Access** sobre ese hostname/ruta.
2. Nos pasáis el **token** del túnel. Nosotros creamos `web/.cloudflared.env` con
   `TUNNEL_TOKEN=...` y levantamos el servicio (`docker compose --profile tunnel up -d`).
   El DNS lo crea Cloudflare solo al asociar el public hostname.

(Si preferís gestionar vosotros cloudflared en el VPS en vez de en Docker,
también vale; decídnoslo y os dejamos el `127.0.0.1:8088` como origen.)

## 3. La URL: subdominio propio (recomendado) vs ruta bajo el dominio de Kaizen

Planteabais algo como `https://kaizen.gravityplayers.net/status/jobs`. Dos
opciones, de más simple a más acoplada:

- **(A, recomendada) Subdominio dedicado** — p. ej. `status.gravityplayers.net`
  (o `jobs.gravityplayers.net`). El public hostname del túnel es ese subdominio
  y la ruta `/status/jobs/` la sirve nuestro nginx. No toca para nada el Worker
  de Kaizen. Access se aplica al subdominio. Es lo más limpio y aislado.

- **(B) Ruta bajo el host de Kaizen** — `https://kaizen.gravityplayers.net/status/jobs`.
  Ese host lo sirve hoy vuestro **Worker** de Kaizen, así que una ruta nueva ahí
  implica que el Worker enrute `/status/jobs*` al túnel (o excluir esa ruta del
  Worker y mandarla al túnel por configuración de Cloudflare). Funciona, pero
  acopla el report al despliegue del Worker y es más trabajo vuestro. Solo si
  os interesa que viva bajo esa misma URL.

Nuestro nginx ya responde en `/status/jobs` en los dos casos; la elección es
vuestra (subdominio vs Worker). Nuestra recomendación: **(A)**.

## 4. Alternativas (por si no queréis túnel)

- **Proxy naranja + puerto abierto + firewall a IPs de Cloudflare + Access**:
  abriría un puerto de entrada en el VPS (peor que el túnel, que no abre nada).
  No lo recomendamos salvo que ya tengáis ese patrón montado.
- **Sin Cloudflare**: nginx con Basic Auth y un puerto público. Más simple pero
  sin la capa de Access ni el filtrado de Cloudflare. No es lo que buscáis
  ("de forma segura" + aprovechar Cloudflare).

## 5. Resumen de quién hace qué

| | Lado VPS (nosotros) | Lado Cloudflare (Kayzen) |
|---|---|---|
| Report | ✅ generado + nginx en `127.0.0.1:8088/status/jobs` | — |
| Túnel | levantamos `cloudflared` con vuestro token | creáis el túnel + public hostname + DNS |
| Protección | — | política de **Access** (quién entra) |
| URL | nginx responde en `/status/jobs` | subdominio (A) o ruta en el Worker (B) |

**Lo único que necesitamos de vosotros para arrancar**: el **token del túnel**
(y decidir subdominio vs ruta de Kaizen, y la política de Access). Con el token,
en el VPS es un `docker compose up` y queda publicado.
