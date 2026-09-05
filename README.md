# noticias_iag

Addon de Odoo 16 que implementa el pipeline de Tubos y Filtros de la tesina
*Una infraestructura técnica y procedimental para el análisis de noticias de portales digitales a través de
inteligencia artificial generativa* (L. Bassani, UNR) sobre el addon institucional
[`wsfnoticias`](../wsfnoticias) (CAETI/CIM), que aporta el filtro de Extracción.

Plan de trabajo: `Tesina_Bassani_Lucio/tmp/plan-addon-noticias-iag.md`.

## Qué hay

| Pieza | Dónde |
|---|---|
| Tipos del Apéndice, un modelo por tipo (`iag.embedding/prompt/respuesta/analisis/veredicto/resultado`) | `models/tipos.py` |
| Template (definición del estudio, panel con validación de JSON Schema) | `models/template.py` |
| Estudio: configuración del pipeline, sintonizadores, tubos como recordsets y *runner* | `models/estudio.py` |
| Los seis filtros, `AbstractModel` con firma `ejecutar(entrada, sintonizador, informe)` | `models/filtros/` |
| Proveedor generativo/embedding (interfaz + `fake` determinístico + `openai_compat`) | `services/proveedor.py` |
| Validador y canonicalizador de JSON Schema (subconjunto) | `services/esquema.py` |
| El sumidero: proyección aplanada de los Resultados (`iag.resultado.fila`, vista SQL de sólo lectura) para las vistas pivot y graph nativas | `models/resultado_fila.py`, `views/tablero.xml` |
| Tests de cardinalidades, procedencia, compuerta, ejecutor humano, monotonía, sintonizador de Normalización, tablero y ficha (27) | `tests/test_pipeline.py` |
| Corpus del caso de aplicación (12.072 noticias, 64 portales, 29/03 → 30/05/2024) y su documentación | `corpus/` |
| Cargador del corpus para `odoo shell` (idempotente, con verificación) | `scripts/cargar_corpus.py` |

Menú: **Noticias ▸ Análisis IAG** (Estudios, Templates, Tablero, Auditoría, Configuración).
Grupos: reutiliza `wsfnoticias.noticias_analista` (CRUD) y `wsfnoticias.noticias_operadores` (lectura).

## Entorno de desarrollo (Docker)

Requiere Docker con el repo `wsfnoticias` clonado como carpeta hermana (`../wsfnoticias`).

```bash
cd docker
docker compose build                                   # odoo:16.0 + deps de wsfnoticias
docker compose run --rm odoo odoo -d tesina -i wsfnoticias --stop-after-init --without-demo=all   # solo la primera vez
docker compose run --rm odoo odoo -d tesina -i noticias_iag --stop-after-init                     # instalar el addon
docker compose up -d                                   # http://localhost:8069  (base: tesina, admin/admin)
docker compose logs -f odoo
```

Actualizar y testear:

```bash
docker compose run --rm odoo odoo -d tesina -u noticias_iag --stop-after-init
docker compose run --rm odoo odoo -d tesina -u noticias_iag --test-enable --test-tags /noticias_iag --stop-after-init --log-level=test
```

Los tests usan el proveedor `fake` y hacen *rollback*: no dejan datos en la base.

**Cuidado con `--dev=reload`.** El servidor de desarrollo recarga su registro cuando cambia un `.py`. Si eso coincide con
un `-u` o una corrida de tests desde otro contenedor sobre la misma base, los dos procesos actualizan `ir_module_module` a
la vez, el servidor pierde con `could not serialize access due to concurrent update` y queda colgado (el contenedor sigue
"Up" pero no responde). Protocolo: editar, y recién después `docker compose stop odoo` → `-u`/tests → `docker compose start odoo`.
Si pasó igual: `docker compose restart odoo`.

**Quitar un campo borra su columna.** Al actualizar con `-u`, Odoo 16 elimina la columna de todo campo que ya no esté
en el modelo, con sus datos. Antes de quitar un campo con datos, migrarlos (script en `odoo shell`) y recién después actualizar.

## Cargar el corpus del caso de aplicación

El repo trae el corpus real de la tesina en `corpus/` (el export institucional de `wsf_noticias_resultados`,
documentado en [`corpus/README.md`](corpus/README.md)). Con el entorno levantado, desde `docker/`:

```bash
docker compose exec -T odoo sh -c \
  "CORPUS=/mnt/extra-addons/noticias_iag/corpus/wsfnoticias_resultados_2024-03-29_2024-05-30.xlsx \
   odoo shell -d tesina --no-http" < ../scripts/cargar_corpus.py
```

Crea las 12.072 noticias, los 64 medios y las cinco reglas de extracción; se puede relanzar (saltea lo ya
cargado) y con `CORPUS_VERIFICAR=1` sólo compara el archivo con la base. Después, Noticias ▸ Resultados
muestra el corpus y cualquier Estudio lo recorta por reglas, fechas y medios.

## Correr un estudio

1. **Corpus.** Lo más directo es cargar el corpus del repo (sección anterior). La otra vía es la Extracción
   de `wsfnoticias`: cargar un medio (partner + `wsf_noticias_medios` con RSS) y una regla
   (`wsf_noticias_reglas` con términos OR) y disparar el scraping a mano desde `odoo shell`
   (`env['wsf_noticias_medios'].scrap_importancia_<importancia>()`) o esperar el cron (`cat1`, cada 80').
2. **Template.** Noticias ▸ Análisis IAG ▸ Templates. Vienen dos de ejemplo: *Agenda-setting de segundo nivel
   (por noticia)* y *Resumen agregado del corpus*.
3. **Estudio.** Noticias ▸ Análisis IAG ▸ Estudios ▸ Nuevo: filtros del corpus, template, estrategia
   (`por_noticia` → M = N, `agregado` → M = 1), ejecutor de Verificación (`llm_judge` o `humano`), política de
   Normalización. Botones *Seleccionar corpus*, *Ejecutar siguiente filtro*, *Ejecutar todo*.
   Los *smart buttons* abren el recordset de cada tubo; la solapa *Resultado* muestra la consolidación;
   *Corrida* guarda la configuración técnica, tiempos e informes de los filtros; *Ficha* documenta el recorrido
   (cada sintonizador con su valor, configuración técnica, cardinalidades y tiempos) a partir de lo persistido.
5. **Normalización.** Además de la política sobre los observados y las claves a consolidar, el sintonizador admite
   una **condición de inclusión** (una clave del esquema y el valor que debe tomar, p. ej. `es_sobre_narco = true`;
   nada se suprime: el Resultado referencia los M veredictos y `contenido.filas` dice cuáles se tabularon) y, como
   variable de comparación, la **variable del experto**: una tabla medio → valor (o departamento → valor) que el
   corpus no trae y el experto define (*Poblar desde el corpus* crea una fila por medio). El Resultado trae, por
   clave, el cruce contra esa variable con n y % por fila, la categoría dominante y la marca de clave concentrada
   (> 70 %, o > 90 % / < 10 % en sí/no), V de Cramér, IC de Wilson para las claves sí/no y los veredictos por grupo.
   Es determinístico: no interviene el modelo generativo; la lectura es del experto.
6. **Tablero.** Botón *Tablero* en el Estudio o en el Resultado (o menú Análisis IAG ▸ Tablero): pivot con filas =
   grupo y columnas = valor, y gráfico de barras apiladas, sobre una fila por análisis consolidado y por clave del
   esquema, con el estado y la confianza del veredicto y las referencias al análisis y a la noticia. Se elige la
   clave en el buscador; cualquier otro cruce (medio, semana, estado del veredicto) se arma desde *Agrupar por*.
   Sólo existe para estudios consolidados: el sumidero cuelga del Resultado (puerto 14).
4. **Ejecutor humano.** El Estudio queda `en_verificacion`; desde el *smart button* "Sin veredicto" se abre cada
   análisis y con *Emitir veredicto* se carga estado, confianza y sustento con la evidencia al lado. *Continuar* recoge
   los veredictos y sigue a Normalización.

Proveedor: Noticias ▸ Análisis IAG ▸ Configuración. `fake` (por defecto) no necesita red; `openai_compat` apunta a
cualquier API compatible con OpenAI (OpenAI, Ollama como servicio del compose, `http://ollama:11434/v1`, Groq, OpenRouter).

## Notas

- `wsfnoticias` escribe `models/medio.log` y `models/tools/telegram_norep.db` dentro de su carpeta; el contenedor
  corre como uid 101, así que esas rutas necesitan `chmod o+w` (no cambia nada que git rastree).
- El cron de scraping de `wsfnoticias` (cada 80') corre también en dev; solo toma medios con `importancia = cat1`.
- Al grabar noticias `wsfnoticias` envía cada una a Telegram con el token hardcodeado del repo base, **aunque la
  regla no tenga `telegram` cargado** (usa el chat por defecto). El `docker-compose.yml` resuelve `api.telegram.org`
  a loopback dentro del contenedor para que esos envíos fallen sin salir a Internet. No quitar esa línea.
- `TextBlob.translate()` (usado por `sentimiento()`) funciona con `textblob==0.17.1` (verificado 2026-09-03): el scraper
  corre completo en local. El modo `prueba` no graba ni envía Telegram.
- Dos motivos por los que el scraping de `wsfnoticias` podía no grabar nada, ambos verificados el 2026-09-03:
  (1) en la vía RSS escribía `fecha_registro` como `"'2026/09/03 16:30:11'"` (comillas y barras), formato que el
  `Datetime` de Odoo 16 rechaza, y `grabar_resultados()` tragaba la excepción: **parcheado en `wsfnoticias/models/medios.py`**
  (formato `%Y-%m-%d %H:%M:%S` y la excepción se registra en `medio.log`); pendiente de acordar con el director.
  (2) `scrap_importancia_prueba()` no llama a `grabar_resultados()`: los medios con `importancia = prueba` nunca se graban,
  por diseño. Para corpus local usar otra importancia que el cron no toque (`nuevo`) y disparar
  `env['wsf_noticias_medios'].scrap_importancia_nuevo()` desde `odoo shell`.
- Portales verificados con RSS + descarga por newspaper: El Ciudadano (`/feed/`), Rosarioplus (`/rss/`). Conclusión
  publica RSS pero devuelve 403 a newspaper. La Capital, Rosario3, Aire de Santa Fe y Uno no exponen RSS en las rutas usuales.
- Ollama: servicio `ollama` del compose (CPU; en este WSL Docker no ve GPU). Modelos: `nomic-embed-text` (768 dim) y
  `qwen2.5:3b` (1,9 GB; con ~5 GB de RAM libre un 7B no entra). Bajarlos con
  `docker compose exec ollama ollama pull <modelo>`. Con `max_chars_noticia = 4000` el prompt entra en el contexto del 3B.
- Respuestas no conformes: el adaptador `openai_compat` pide salida estructurada al proveedor (`response_format`
  `json_schema` por defecto, con degradación a `json_object` y a texto libre ante un 400) y Decodificación aplica la política
  `reparar` (cierra un JSON truncado al final, valida contra el esquema y anota la reparación en el informe de la corrida).
  Ambas son configuración técnica en Noticias ▸ Análisis IAG ▸ Configuración. Medido con `qwen2.5:3b`: sin mitigación
  4/13 descartes (3 truncados por fin de secuencia prematuro, 1 `maxItems`); con `json_schema` 0/8 en la repetición.
- Los registros de los siete tipos no se borran (`unlink` bloqueado, `ondelete='restrict'` en la procedencia):
  monotonía del repositorio. *Volver a borrador* en un Estudio sólo desvincula los recordsets.
