# Corpus del caso de aplicación

`wsfnoticias_resultados_2024-03-29_2024-05-30.xlsx` es el export del modelo `wsf_noticias_resultados` de la
instancia institucional del scraper `wsfnoticias` (CAETI/CIM, UNR), recibido el 2026-09-03. Es el archivo
tal como se recibió: sólo se le cambió el nombre (SHA-256 que empieza en `eac13ef1d67c7e7c`). Es el corpus
sobre el que corre el caso de aplicación de la tesina (Cap. 6) y sirve para reproducirlo en una base local.

## Qué contiene

| | |
|---|---|
| Noticias | 12.072 (una por fila; `link` único) |
| Portales | 64 |
| Registro en el corpus (`fecha_registro`) | 2024-03-29 → 2024-05-30 |
| Reglas de extracción (`Reglas Aplicadas`) | Narco 6.911 · Pullaro 3.015 · Inseguridad 2.762 · Lavado 1.044 · Corrupción 849 (una noticia puede cumplir varias) |
| Texto completo | 12.021 (51 vacíos; 253 con menos de 200 caracteres) |
| Copete | 7.755 (64 %) |
| Fecha de publicación declarada (`Fecha y Hora`) | 7.024 (58 %), con precisión de día |

Las 15 columnas son las etiquetas del export de Odoo. Su correspondencia con el contrato `Noticia` del
Apéndice de la tesina:

| Columna | Campo | Notas |
|---|---|---|
| Título | `titulo` | |
| Copete | `copete` | opcional en el contrato |
| Texto | `texto` | cuerpo completo, normalizado por el scraper |
| Link | `link` | URL canónica, clave única |
| Medio | `medio` | portal de origen; al cargar se resuelve por nombre de contacto (`res.partner`) |
| Departamento | `departamento` | adscripción geográfica **del portal**, tal como está configurada en el feed; no describe el contenido de la noticia. 14 valores; "Z-Exterior a Santa FE" son los portales nacionales |
| Fecha y Hora | `fecha_hora` | fecha de publicación declarada por el portal, opcional |
| Fecha registro | `fecha_registro` | ingreso al corpus; siempre presente |
| Reglas Aplicadas | `regla2` | reglas del scraper que seleccionaron la noticia, separadas por coma |
| Tipo | `tipo` | sentimiento léxico de TextBlob calculado por el scraper (Pos / Neg / ???); no es un dato del contrato |
| Nube | `nube` | términos para la nube de palabras del scraper |
| Valorar, Categorias, Clasificacion, Entidades | | vacías en este export |

Las fechas están en hora de Argentina (la del usuario que exportó); al cargarlas Odoo las guarda en UTC.

## Cómo cargarlo

Con el entorno Docker levantado (ver el README principal), desde la carpeta `docker/`:

```bash
docker compose exec -T odoo sh -c \
  "CORPUS=/mnt/extra-addons/noticias_iag/corpus/wsfnoticias_resultados_2024-03-29_2024-05-30.xlsx \
   odoo shell -d tesina --no-http" < ../scripts/cargar_corpus.py
```

El script crea las noticias y los medios que falten, registra las cinco reglas en `wsf_noticias_reglas` (en
estado OFF, para que el scraper no las use) y es idempotente: si se relanza, saltea los links ya cargados.
Con `CORPUS_VERIFICAR=1` no crea nada y compara el archivo con lo que hay en la base. Tarda menos de un
minuto (probado el 2026-09-05 en una base recién instalada: 12.072 noticias, 64 medios, 5 reglas). La alternativa sin script es el asistente de importación de Odoo (Noticias ▸ Resultados ▸
Favoritos ▸ Importar registros) con el mismo archivo, resolviendo `Medio` por nombre.

Cargado el corpus, un Estudio lo recorta con los criterios de la solapa Corpus (reglas, fechas, medios).

## Advertencias para el análisis

- Las reglas son términos por subcadena unidos por OR. "Narco" engancha también "anarcocapitalista", la
  serie *Narcos* y barras laterales de notas relacionadas: en el total del período sólo el 25 % de sus
  noticias contiene "narco" en título, copete o texto; entre el 1 y el 14 de abril de 2024 la precisión es
  del 80 % y desde el 16 de abril cae al 15 %. Pullaro (99 %) y Corrupción (97 %) son limpias; Lavado 46 %;
  Inseguridad 22 %.
- Hay republicaciones: 1.142 títulos repetidos con links distintos (portales del interior que reproducen
  gacetillas y cables; portales scrapeados por dos medios). Las unidades no son independientes.
- `Departamento` es una etiqueta del portal; cinco medios no son prensa o están duplicados (agregadores de
  búsqueda, la web oficial provincial, una newsletter, un diario registrado en dos departamentos). La tabla
  medio → grupo usada en la tesina se declara en cada Estudio como variable del experto.
- `Tipo` (TextBlob sobre texto traducido) no distingue la valencia de una noticia de violencia; en el caso
  de aplicación su acuerdo con la codificación del modelo fue nulo.
