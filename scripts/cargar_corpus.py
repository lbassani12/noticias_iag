# -*- coding: utf-8 -*-
"""Carga el corpus de noticias (export institucional de ``wsf_noticias_resultados``) en una base local.

Se ejecuta por stdin de ``odoo shell`` dentro del contenedor, con la ruta del archivo en CORPUS:

    cd docker
    docker compose exec -T odoo sh -c \\
      "CORPUS=/mnt/extra-addons/noticias_iag/corpus/wsfnoticias_resultados_2024-03-29_2024-05-30.xlsx \\
       odoo shell -d tesina --no-http" < ../scripts/cargar_corpus.py

Qué hace:
- lee el archivo (xlsx con xlrd, que trae la imagen de Odoo; o csv UTF-8) con los encabezados del export de
  Odoo (etiquetas en español) o con los nombres técnicos de los campos;
- crea una noticia por fila en ``wsf_noticias_resultados``; el medio se resuelve por nombre de contacto
  (``res.partner``) y se crea si no existe; ``tipo`` acepta las etiquetas del export (Pos / Neg / ???);
- es idempotente: las filas cuyo ``link`` ya existe se saltean (o se comparan con CORPUS_VERIFICAR=1);
- registra en ``wsf_noticias_reglas`` (en estado OFF, para que el scraper no las use) las reglas que aparecen
  en "Reglas Aplicadas", así el filtro por regla del Estudio las ofrece;
- graba de a CORPUS_LOTE filas (500) con commit entre lotes: si se corta, se relanza y sigue.

Las fechas del export están en la hora local del usuario que exportó (Argentina, UTC-3) y Odoo las guarda
en UTC: CORPUS_TZ (default America/Argentina/Buenos_Aires) es la zona con que se interpretan; es lo mismo
que hace el asistente de importación de Odoo con la zona horaria del usuario.

Variables de entorno: CORPUS (ruta, obligatoria), CORPUS_TZ, CORPUS_LOTE (500), CORPUS_LIMITE (0 = todas),
CORPUS_VERIFICAR (1 = compara las filas ya cargadas con el archivo y no crea nada).
"""
import csv
import datetime
import os
import time

import pytz

RUTA = os.environ.get('CORPUS')
TZ = pytz.timezone(os.environ.get('CORPUS_TZ', 'America/Argentina/Buenos_Aires'))
LOTE = int(os.environ.get('CORPUS_LOTE', '500'))
LIMITE = int(os.environ.get('CORPUS_LIMITE', '0'))
VERIFICAR = os.environ.get('CORPUS_VERIFICAR') == '1'
assert RUTA and os.path.exists(RUTA), 'Falta CORPUS=<ruta del archivo> o el archivo no existe: %s' % RUTA

#: Encabezado del archivo -> campo de wsf_noticias_resultados (se aceptan las etiquetas del export de Odoo y los
#: nombres técnicos). "Categorias" (many2many) y las columnas vacías del export se ignoran.
MAPA = {
    'Título': 'titulo', 'Copete': 'copete', 'Texto': 'texto', 'Link': 'link', 'Medio': 'medio',
    'Departamento': 'departamento', 'Fecha registro': 'fecha_registro', 'Fecha y Hora': 'fecha_hora',
    'Reglas Aplicadas': 'regla2', 'Tipo': 'tipo', 'Nube': 'nube', 'Valorar': 'valorar',
    'Clasificacion': 'clasificacion', 'Entidades': 'entidades', 'Localidad': 'localidad',
}
MAPA.update({v: v for v in list(MAPA.values())})
TIPOS = {'Pos': 'positiva', 'Neg': 'negativa', '???': 'neutra', 'positiva': 'positiva', 'negativa': 'negativa', 'neutra': 'neutra'}
FECHAS = ('fecha_registro', 'fecha_hora')


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


def _fecha(valor):
    """Hora local del export -> UTC (texto), como lo guarda Odoo."""
    if valor in (None, '', False):
        return False
    if isinstance(valor, datetime.datetime):
        local = valor
    else:
        texto = str(valor).strip()
        local = None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
            try:
                local = datetime.datetime.strptime(texto, fmt)
                break
            except ValueError:
                continue
        if local is None:
            raise ValueError('fecha no reconocida: %r' % valor)
    return TZ.localize(local).astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')


def filas_xlsx(ruta):
    import xlrd
    libro = xlrd.open_workbook(ruta)
    hoja = libro.sheet_by_index(0)
    encabezado = [str(c.value).strip() for c in hoja.row(0)]
    for r in range(1, hoja.nrows):
        fila = {}
        for c, nombre in enumerate(encabezado):
            celda = hoja.cell(r, c)
            if celda.ctype == xlrd.XL_CELL_DATE:
                fila[nombre] = xlrd.xldate.xldate_as_datetime(celda.value, libro.datemode)
            elif celda.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                fila[nombre] = ''
            else:
                fila[nombre] = celda.value
        yield fila


def filas_csv(ruta):
    with open(ruta, encoding='utf-8-sig', newline='') as f:
        for fila in csv.DictReader(f):
            yield {k.strip(): v for k, v in fila.items()}


def vals_de(fila, partners, crear_partner):
    vals = {}
    for encabezado, valor in fila.items():
        campo = MAPA.get(encabezado)
        if not campo:
            continue
        if isinstance(valor, float) and valor != valor:     # NaN
            valor = ''
        if campo == 'medio':
            nombre = str(valor).strip()
            if not nombre:
                continue
            if nombre not in partners:
                partners[nombre] = crear_partner(nombre)
            vals['medio'] = partners[nombre]
        elif campo in FECHAS:
            vals[campo] = _fecha(valor)
        elif campo == 'tipo':
            vals['tipo'] = TIPOS.get(str(valor).strip(), False)
        else:
            texto = valor if isinstance(valor, str) else ('' if valor in (None, '') else str(valor))
            vals[campo] = texto if texto.strip() else False       # tal cual el export (regla2 termina en ", ")
    return vals


Noticia = env['wsf_noticias_resultados']
Partner = env['res.partner']
Regla = env['wsf_noticias_reglas']

filas = filas_csv(RUTA) if RUTA.lower().endswith('.csv') else filas_xlsx(RUTA)
existentes = {}
for r in Noticia.search_read([('link', '!=', False)], ['link']):
    existentes[r['link']] = r['id']
partners = {p.name: p.id for p in Partner.search([('name', '!=', False)])}
reglas = {r.nombre_regla.strip() for r in Regla.search([]) if r.nombre_regla}


def crear_partner(nombre):
    p = Partner.create({'name': nombre, 'is_company': True})
    log('medio nuevo: %s' % nombre)
    return p.id


log('archivo %s; noticias en la base: %d; contactos: %d; modo %s' % (
    os.path.basename(RUTA), len(existentes), len(partners), 'VERIFICAR' if VERIFICAR else 'CARGA'))
t0 = time.time()
leidas = creadas = salteadas = 0
diferencias = []
lote = []
reglas_nuevas = set()
for fila in filas:
    leidas += 1
    if LIMITE and leidas > LIMITE:
        break
    vals = vals_de(fila, partners, crear_partner)
    link = vals.get('link')
    if not link:
        log('fila %d sin link: se saltea' % leidas)
        salteadas += 1
        continue
    for nombre in (vals.get('regla2') or '').split(','):
        nombre = nombre.strip()
        if nombre and nombre not in reglas:
            reglas_nuevas.add(nombre)
    if link in existentes:
        salteadas += 1
        if VERIFICAR:
            n = Noticia.browse(existentes[link])
            for campo in ('titulo', 'texto', 'departamento', 'regla2', 'fecha_registro', 'tipo'):
                esperado = vals.get(campo) or False
                actual = n[campo] or False
                if campo == 'fecha_registro' and actual:
                    actual = actual.strftime('%Y-%m-%d %H:%M:%S')
                if (esperado or '') != (actual or ''):
                    diferencias.append((link, campo, str(esperado)[:60], str(actual)[:60]))
            if vals.get('medio') and n.medio.id != vals['medio']:
                diferencias.append((link, 'medio', vals['medio'], n.medio.id))
        continue
    if VERIFICAR:
        diferencias.append((link, 'ausente', '', ''))
        continue
    lote.append(vals)
    if len(lote) >= LOTE:
        Noticia.create(lote)
        creadas += len(lote)
        env.cr.commit()
        log('%d leídas, %d creadas, %d salteadas (%.0f s)' % (leidas, creadas, salteadas, time.time() - t0))
        lote = []
if lote:
    Noticia.create(lote)
    creadas += len(lote)
if not VERIFICAR:
    for nombre in sorted(reglas_nuevas):
        Regla.create({'nombre_regla': nombre, 'estado': 'off'})
        log('regla registrada (OFF): %s' % nombre)
    env.cr.commit()
log('FIN: %d leídas, %d creadas, %d salteadas, %d contactos (medios), %.0f s' % (
    leidas, creadas, salteadas, len(partners), time.time() - t0))
if VERIFICAR:
    log('VERIFICACIÓN: %d diferencias%s' % (len(diferencias), '' if not diferencias else '; primeras: %s' % diferencias[:5]))
