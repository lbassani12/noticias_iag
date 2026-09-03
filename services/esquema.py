# -*- coding: utf-8 -*-
"""Validación y canonicalización de estructuras contra un subconjunto de JSON Schema.

Subconjunto soportado: ``type`` (string, number, integer, boolean, array, object, null),
``properties``, ``required``, ``enum``, ``items``, ``minimum``, ``maximum``, ``minItems``,
``maxItems``. Sin dependencias externas. Lo usan el panel de templates (validación del
esquema declarado), la compuerta de Decodificación y el juez de Verificación.
"""
import json
import re

TIPOS = ('string', 'number', 'integer', 'boolean', 'array', 'object', 'null')
_RE_FENCE = re.compile(r'```[a-zA-Z0-9_-]*\s*(.*?)```', re.S)
VERDADEROS = {'true', 'si', 'sí', 'verdadero', 'yes', '1'}
FALSOS = {'false', 'no', 'falso', '0'}


def validar_esquema(esquema, ruta='$'):
    """Verifica que `esquema` pertenezca al subconjunto soportado. Devuelve la lista de errores."""
    errores = []
    if not isinstance(esquema, dict):
        return ['%s: el esquema debe ser un objeto JSON' % ruta]
    tipo = esquema.get('type')
    if tipo is None and 'enum' not in esquema and 'properties' not in esquema:
        errores.append('%s: falta "type"' % ruta)
    if tipo is not None and tipo not in TIPOS:
        errores.append('%s: tipo no soportado "%s"' % (ruta, tipo))
    if 'enum' in esquema and (not isinstance(esquema['enum'], list) or not esquema['enum']):
        errores.append('%s: "enum" debe ser una lista no vacía' % ruta)
    props = esquema.get('properties')
    if props is not None:
        if not isinstance(props, dict):
            errores.append('%s: "properties" debe ser un objeto' % ruta)
        else:
            for clave, sub in props.items():
                errores.extend(validar_esquema(sub, '%s.%s' % (ruta, clave)))
    requeridas = esquema.get('required')
    if requeridas is not None:
        if not isinstance(requeridas, list):
            errores.append('%s: "required" debe ser una lista' % ruta)
        elif isinstance(props, dict):
            for clave in requeridas:
                if clave not in props:
                    errores.append('%s: clave requerida "%s" no declarada en "properties"' % (ruta, clave))
    if 'items' in esquema:
        errores.extend(validar_esquema(esquema['items'], ruta + '[]'))
    return errores


def aislar_json(texto):
    """Extrae el primer objeto o lista JSON de un texto (quita fences y prosa). Devuelve (obj, error)."""
    if not texto or not texto.strip():
        return None, 'respuesta vacía'
    candidatos = _RE_FENCE.findall(texto) + [texto]
    ultimo_error = None
    for cand in candidatos:
        inicios = [i for i in (cand.find('{'), cand.find('[')) if i >= 0]
        if not inicios:
            ultimo_error = 'no se encontró un objeto JSON'
            continue
        ini = min(inicios)
        fin = max(cand.rfind('}'), cand.rfind(']'))
        try:
            return json.loads(cand[ini:fin + 1]), None
        except ValueError as e:
            ultimo_error = 'JSON inválido: %s' % e
    return None, ultimo_error or 'no se encontró un objeto JSON'


def canonicalizar(valor, esquema, ruta='$'):
    """Valida `valor` contra `esquema` y devuelve (valor_canonico, errores)."""
    errores = []
    tipo = esquema.get('type')
    if tipo is None and 'properties' in esquema:
        tipo = 'object'

    if tipo == 'string':
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            valor = str(valor)
        if not isinstance(valor, str):
            return valor, ['%s: se esperaba string' % ruta]
        valor = valor.strip()
    elif tipo == 'number':
        if isinstance(valor, str):
            try:
                valor = float(valor.strip().replace(',', '.'))
            except ValueError:
                return valor, ['%s: se esperaba number' % ruta]
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return valor, ['%s: se esperaba number' % ruta]
        valor = float(valor)
        errores.extend(_rango(valor, esquema, ruta))
    elif tipo == 'integer':
        if isinstance(valor, str):
            try:
                valor = int(valor.strip())
            except ValueError:
                return valor, ['%s: se esperaba integer' % ruta]
        if isinstance(valor, float) and valor.is_integer():
            valor = int(valor)
        if isinstance(valor, bool) or not isinstance(valor, int):
            return valor, ['%s: se esperaba integer' % ruta]
        errores.extend(_rango(valor, esquema, ruta))
    elif tipo == 'boolean':
        if isinstance(valor, str):
            v = valor.strip().lower()
            valor = True if v in VERDADEROS else False if v in FALSOS else valor
        if not isinstance(valor, bool):
            return valor, ['%s: se esperaba boolean' % ruta]
    elif tipo == 'null':
        if valor is not None:
            return valor, ['%s: se esperaba null' % ruta]
    elif tipo == 'array':
        if not isinstance(valor, list):
            return valor, ['%s: se esperaba array' % ruta]
        if 'minItems' in esquema and len(valor) < esquema['minItems']:
            errores.append('%s: mínimo %d elementos' % (ruta, esquema['minItems']))
        if 'maxItems' in esquema and len(valor) > esquema['maxItems']:
            errores.append('%s: máximo %d elementos' % (ruta, esquema['maxItems']))
        if 'items' in esquema:
            nuevos = []
            for i, elem in enumerate(valor):
                canon, errs = canonicalizar(elem, esquema['items'], '%s[%d]' % (ruta, i))
                nuevos.append(canon)
                errores.extend(errs)
            valor = nuevos
    elif tipo == 'object':
        if not isinstance(valor, dict):
            return valor, ['%s: se esperaba object' % ruta]
        props = esquema.get('properties') or {}
        for clave in esquema.get('required') or []:
            if clave not in valor:
                errores.append('%s: falta la clave requerida "%s"' % (ruta, clave))
        nuevo = dict(valor)
        for clave, sub in props.items():
            if clave in valor:
                canon, errs = canonicalizar(valor[clave], sub, '%s.%s' % (ruta, clave))
                nuevo[clave] = canon
                errores.extend(errs)
        valor = nuevo

    if 'enum' in esquema and not errores:
        opciones = esquema['enum']
        if valor not in opciones:
            coincidencia = None
            if isinstance(valor, str):
                for op in opciones:
                    if isinstance(op, str) and op.strip().lower() == valor.strip().lower():
                        coincidencia = op
                        break
            if coincidencia is None:
                errores.append('%s: "%s" no está entre %s' % (ruta, valor, opciones))
            else:
                valor = coincidencia
    return valor, errores


def _rango(valor, esquema, ruta):
    errs = []
    if 'minimum' in esquema and valor < esquema['minimum']:
        errs.append('%s: %s es menor que el mínimo %s' % (ruta, valor, esquema['minimum']))
    if 'maximum' in esquema and valor > esquema['maximum']:
        errs.append('%s: %s es mayor que el máximo %s' % (ruta, valor, esquema['maximum']))
    return errs


# --- Reparación de JSON truncado -------------------------------------------------------------
# Los modelos chicos a veces emiten el fin de secuencia antes de cerrar el último objeto. La
# reparación sólo agrega lo que falta al final (comillas, corchetes, llaves) y, si no alcanza,
# quita de a uno los últimos miembros incompletos. El resultado se valida igual contra el esquema.

def _escanear(s):
    pila, en_str, esc = [], False, False
    for ch in s:
        if en_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                en_str = False
        elif ch == '"':
            en_str = True
        elif ch in '{[':
            pila.append(ch)
        elif ch in '}]':
            if pila:
                pila.pop()
    return pila, en_str


def _ultima_coma(s):
    en_str, esc, idx = False, False, -1
    for i, ch in enumerate(s):
        if en_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                en_str = False
        elif ch == '"':
            en_str = True
        elif ch == ',':
            idx = i
    return idx


def _cerrar(s):
    pila, en_str = _escanear(s)
    if en_str:
        s += '"'
    s = s.rstrip()
    while s.endswith(','):
        s = s[:-1].rstrip()
    if s.endswith(':'):
        s += ' null'
    return s + ''.join('}' if c == '{' else ']' for c in reversed(pila))


def reparar_cierre(texto, max_recortes=3):
    """Cierra lo que quedó abierto al final de un JSON truncado. Devuelve (obj, descripcion) o (None, None).

    Un string abierto al final es un valor (o una clave) incompleto: no se cierra como si estuviera
    entero, se descarta ese miembro. Sólo se agregan cierres; nunca se inventa contenido."""
    cand = (texto or '').rstrip()
    if not cand:
        return None, None
    recortes = 0
    if _escanear(cand)[1]:
        idx = _ultima_coma(cand)
        if idx >= 0:
            cand, recortes = cand[:idx], 1
    for _ in range(max_recortes + 1):
        cerrado = _cerrar(cand)
        try:
            obj = json.loads(cerrado)
        except ValueError:
            idx = _ultima_coma(cand)
            if idx < 0:
                break
            cand, recortes = cand[:idx], recortes + 1
            continue
        descripcion = 'se agregó %r' % cerrado[len(cand):]
        if recortes:
            descripcion += ' tras quitar %d miembro(s) incompleto(s)' % recortes
        return obj, descripcion
    return None, None


def reparar_json(texto):
    """Como aislar_json, para respuestas truncadas: toma desde el primer '{' o '[' hasta el final
    (aunque el fence no se haya cerrado) y repara el cierre. Devuelve (obj, descripcion) o (None, None)."""
    if not texto:
        return None, None
    fences = _RE_FENCE.findall(texto)
    if fences:
        cand = fences[0]
    elif '```' in texto:
        cand = re.sub(r'^[a-zA-Z0-9_-]*\s*', '', texto.split('```', 1)[1], count=1)
    else:
        cand = texto
    inicios = [i for i in (cand.find('{'), cand.find('[')) if i >= 0]
    if not inicios:
        return None, None
    return reparar_cierre(cand[min(inicios):])
