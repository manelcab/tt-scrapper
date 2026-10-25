# El report de cargas, publicado — lo que cambia para tt-scraper

> Respuesta a `PARA-KAYZEN-report-status.md`. Escrito desde Kayzen el
> 2026-10-25.
>
> **Resumen: no hace falta el túnel.** No os hace falta `cloudflared`, ni el
> perfil de Docker, ni esperar un token nuestro. El report ya se puede publicar
> con **una línea en vuestro cron**.

## 1. Por qué no el túnel

No es preferencia: **la zona `gravityplayers.net` no está en Cloudflare**, vive
en otro proveedor. Y eso cierra las dos piezas de vuestra propuesta:

- El **public hostname** de un túnel necesita que el dominio sea una zona activa
  en nuestra cuenta: el túnel crea un CNAME a `<uuid>.cfargotunnel.com`. Con la
  zona fuera, no hay dónde crearlo.
- **Cloudflare Access** (aplicación self-hosted) protege un hostname que pase
  por Cloudflare, o sea la misma condición.

La salida sin mover los nameservers es el *partial setup* (CNAME setup), y pide
plan **Business**. Mover la zona se ha planteado y por ahora es un no.

Y aunque se pudiera: Access traería **un segundo login** —un PIN al correo o un
SSO— para contestar una pregunta que Kayzen ya sabe contestar. Nuestra wiki
cerrada ya resolvió esto con el rol de administrador de Kayzen, y el report va
igual.

## 2. Qué se ha montado

El report **no se va a buscar: llega**. Son 29–42 KB de HTML autocontenido tres
veces al día; poner un túnel para eso es desproporcionado. Así que:

- Vosotros lo **empujáis** después de cada pasada del cron.
- Kayzen lo guarda y lo sirve detrás del rol de administrador, en
  `https://kaizen.gravityplayers.net/status/jobs`.
- Vuestro VPS **sigue sin exponer nada**, que es lo que queríais: ni puerto, ni
  IP pública, ni túnel.

De paso gana algo que un proxy no daba: con el VPS caído se sirve el último que
llegó, con su fecha dentro, en vez de un 502. Para un report de *estado de
cargas*, ese es justo el día que se mira.

## 3. Lo único que tenéis que hacer

Una línea después de generar `output/reports/latest.html`:

```bash
curl -sS --fail-with-body \
  -X PUT \
  -H "Authorization: Bearer $KAYZEN_REPORT_TOKEN" \
  -H "Content-Type: text/html" \
  --data-binary @output/reports/latest.html \
  https://kayzen-status.<subdominio>.workers.dev/ingest
```

La URL exacta y el token os los pasamos por el canal de siempre, **no van en
este fichero**.

Lo que contesta:

| | |
|---|---|
| `200 ok` | guardado; sustituye al anterior |
| `401` | el token no es ese |
| `400` | venía vacío — y eso no borra el último bueno |
| `413` | más de 2 MB |
| `405` | no era `PUT` ni `POST` |
| `503` | nos falta configurar el secreto; avisadnos |

Tres cosas que conviene saber:

- **Es idempotente y solo se guarda el último.** Repetir el empujón no rompe
  nada; empujar el mismo report dos veces tampoco.
- **El buzón nunca devuelve el report.** Es una puerta de escritura: contestar
  con el contenido convertiría vuestro token en un token de lectura, y ese token
  vive en un VPS.
- **No falléis el cron por esto.** Si el `curl` no sale, que la pasada siga: el
  siguiente empujón lo arregla y lo que se sirve mientras tanto es el anterior.
  `-sS --fail-with-body` para que el error se vea en vuestro log sin ensuciar la
  salida cuando va bien.

## 4. Qué podéis quitar

- El contenedor `cloudflared` comentado en `web/docker-compose.yml` y el perfil
  `tunnel`. No hace falta.
- La espera del **token del túnel**: no hay túnel.
- El nginx en `127.0.0.1:8088` **se puede quedar** si os sirve para mirarlo en
  local, pero ya no es la fuente de nada nuestro. Nosotros solo leemos lo que
  empujéis.

Lo que **no** cambia: el report lo seguís generando vosotros, con el formato que
queráis. Nosotros no lo tocamos, no lo reescribimos y no le añadimos nada; se
sirve tal cual llega. Si cambiáis su aspecto, se ve cambiado y no hay que
desplegar nada por nuestra parte.

## 5. Una cosa que os pedimos

Que el HTML siga llevando **su fecha de generación visible en la cabecera**, como
ahora («2026-10-25 12:29»). Es lo que nos permite no inventarnos un aviso de
frescura por nuestro lado: si el empujón se parara, esa fecha deja de moverse y
se ve al abrirlo. Si algún día desaparece de la cabecera, decídnoslo, porque
entonces sí tendríamos que guardar la hora de llegada y enseñarla.
