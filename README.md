# wsfnoticias_iag

Addon de Odoo 16 que implementa el pipeline de Tubos y Filtros de la tesina
*Framework para el análisis de noticias de portales digitales mediante IAG* (L. Bassani, UNR)
sobre el addon institucional [`wsfnoticias`](../wsfnoticias) (CAETI/CIM), que aporta el filtro de Extracción.

Plan de trabajo: `Tesina_Bassani_Lucio/tmp/plan-addon-wsfnoticias-iag.md`.

## Entorno de desarrollo (Docker)

Requiere Docker con el repo `wsfnoticias` clonado como carpeta hermana (`../wsfnoticias`).

```bash
cd docker
docker compose build                                   # odoo:16.0 + deps de wsfnoticias
docker compose run --rm odoo odoo -d tesina -i wsfnoticias --stop-after-init --without-demo=all   # solo la primera vez
docker compose up -d                                   # http://localhost:8069  (base: tesina, admin/admin)
docker compose logs -f odoo
```

Instalar/actualizar el addon de la tesina cuando exista:

```bash
docker compose run --rm odoo odoo -d tesina -i wsfnoticias_iag --stop-after-init
docker compose run --rm odoo odoo -d tesina -u wsfnoticias_iag --stop-after-init
docker compose run --rm odoo odoo -d tesina -u wsfnoticias_iag --test-enable --stop-after-init
```

Notas:
- `wsfnoticias` escribe `models/medio.log` y `models/tools/telegram_norep.db` dentro de su carpeta; el contenedor
  corre como uid 101, así que esas rutas necesitan `chmod o+w` (no cambia nada que git rastree).
- El cron de scraping de `wsfnoticias` (cada 80') corre también en dev. Sin medios cargados no hace nada.
  Al grabar noticias envía mensajes a Telegram con el token del repo base: en dev no cargar `telegram` en las reglas.
- Ollama en el host queda accesible desde el contenedor como `http://host.docker.internal:11434`.
