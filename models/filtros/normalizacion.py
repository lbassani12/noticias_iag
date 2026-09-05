# -*- coding: utf-8 -*-
import math

from odoo import models

ESTADOS_INCLUIDOS = {
    'incluir': {'valido', 'observado', 'no_sustentado'},
    'excluir': {'valido', 'observado'},
    'solo_validos': {'valido'},
}
TIPOS_CATEGORICOS = ('string', 'boolean')
SIN_ASIGNAR = '(sin asignar)'
#: Convención documentada (no es sintonizador): una clave cuya categoría dominante supera el 70 % se marca como
#: poco informativa; en sí/no, cuando la proporción de "true" supera el 90 % o no llega al 10 %.
UMBRAL_COLAPSO = 0.70
UMBRAL_COLAPSO_BOOL = 0.90
MAX_VALORES_CRUCE = 15
Z95 = 1.959964


def _texto(valor):
    """Forma canónica de un valor escalar del análisis para contar y comparar."""
    if isinstance(valor, bool):
        return 'true' if valor else 'false'
    return str(valor)


def wilson(k, n, z=Z95):
    """Intervalo de Wilson al 95 % para una proporción k/n; (p, inferior, superior) en [0, 1]."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    medio = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(p, 4), round(max(centro - medio, 0.0), 4), round(min(centro + medio, 1.0), 4))


def v_cramer(tabla):
    """V de Cramér de una tabla de contingencia {fila: {columna: n}}; None si no hay 2×2 o no hay datos."""
    filas = [f for f in tabla if sum(tabla[f].values())]
    columnas = sorted({c for f in filas for c, n in tabla[f].items() if n})
    if len(filas) < 2 or len(columnas) < 2:
        return None
    total = sum(sum(tabla[f].values()) for f in filas)
    chi2 = 0.0
    for f in filas:
        nf = sum(tabla[f].values())
        for c in columnas:
            nc = sum(tabla[g].get(c, 0) for g in filas)
            esperado = nf * nc / total
            if esperado:
                chi2 += (tabla[f].get(c, 0) - esperado) ** 2 / esperado
    return round(math.sqrt(chi2 / (total * (min(len(filas), len(columnas)) - 1))), 4)


class FiltroNormalizacion(models.AbstractModel):
    """Veredicto[M] -> Resultado. Consolidación determinística, sin modelo generativo.

    Aplica la política sobre los análisis observados y la condición de inclusión, resuelve la variable de
    comparación (una agrupación del corpus o la variable del experto) y produce, para cada clave consolidada
    del esquema, la distribución global y el cruce clave × grupo con porcentajes, concentración y V de Cramér.
    ``Resultado.veredictos`` referencia los M veredictos recibidos (procedencia completa); ``contenido.filas``
    dice, para cada uno, si se consolidó y en qué grupo; los criterios aplicados quedan materializados en
    ``contenido.politica_aplicada``.
    """
    _name = 'iag.filtro.normalizacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Normalización'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Resultado = self.env['iag.resultado']
        politica = sintonizador.get('politica_observados') or 'incluir'
        agrupar = sintonizador.get('agrupar_por') or 'ninguno'
        variable = sintonizador.get('variable') if agrupar == 'variable' else None
        condicion = sintonizador.get('condicion') or None

        propiedades, orden = {}, []
        for t in entrada.mapped('analisis_id.respuesta_id.prompt_id.template_id'):
            esquema = t.esquema_respuesta or {}
            propiedades.update(esquema.get('properties') or {})
            orden += [k for k in self.env['iag.template']._orden_claves(esquema) if k not in orden]
        claves = sintonizador.get('claves_comparacion')
        if claves is None:   # sin sintonizador: todas las comparables
            claves = [k for k, s in propiedades.items() if self._comparable(s)]
        claves = sorted(claves, key=lambda k: orden.index(k) if k in orden else len(orden))   # el orden del esquema

        # --- Filas: cada veredicto con su grupo y si entra en la consolidación ---------------------
        filas, incluidos = [], entrada.browse()
        for v in entrada:
            contenido = v.analisis_id.contenido or {}
            grupo = self._grupo(v.analisis_id, agrupar, variable)
            motivo = ''
            if v.estado not in ESTADOS_INCLUIDOS[politica]:
                motivo = 'politica'
            elif condicion and not self._cumple(contenido, condicion):
                motivo = 'condicion'
            filas.append({'veredicto': v.id, 'analisis': v.analisis_id.id, 'estado': v.estado,
                          'confianza': v.confianza, 'grupo': grupo, 'incluido': not motivo, 'motivo': motivo})
            if not motivo:
                incluidos |= v
        grupo_de = {f['veredicto']: f['grupo'] for f in filas}

        # --- Conteos: global y por grupo -------------------------------------------------------------
        distribuciones = {k: {} for k in claves}
        por_grupo, numericas, numericas_grupo = {}, {}, {}
        for v in incluidos:
            contenido = v.analisis_id.contenido or {}
            grupo = grupo_de[v.id]
            for k in claves:
                if k not in contenido:
                    continue
                valor = contenido[k]
                tipo = (propiedades.get(k) or {}).get('type')
                if tipo in ('number', 'integer') and isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    numericas.setdefault(k, []).append(float(valor))
                    numericas_grupo.setdefault(k, {}).setdefault(grupo, []).append(float(valor))
                    continue
                for x in (valor if isinstance(valor, list) else [valor]):
                    if isinstance(x, (dict, list)):
                        continue
                    etiqueta = _texto(x)
                    distribuciones[k][etiqueta] = distribuciones[k].get(etiqueta, 0) + 1
                    if agrupar != 'ninguno':
                        celda = por_grupo.setdefault(grupo, {}).setdefault(k, {})
                        celda[etiqueta] = celda.get(etiqueta, 0) + 1

        grupos = self._orden_grupos(list({f['grupo'] for f in filas}), agrupar, variable)
        cruces = {k: self._cruce(k, propiedades.get(k) or {}, distribuciones[k], por_grupo, grupos, agrupar)
                  for k in claves if distribuciones.get(k)}

        # --- Veredictos por grupo y resumen ----------------------------------------------------------------
        veredictos_grupo = {}
        for f in filas:
            g = veredictos_grupo.setdefault(f['grupo'], {'n': 0, 'incluidos': 0, 'por_estado': {}, '_conf': []})
            g['n'] += 1
            g['incluidos'] += 1 if f['incluido'] else 0
            g['por_estado'][f['estado']] = g['por_estado'].get(f['estado'], 0) + 1
            g['_conf'].append(f['confianza'])
        for g in veredictos_grupo.values():
            conf = g.pop('_conf')
            g['confianza_media'] = round(sum(conf) / len(conf), 4) if conf else None
        veredictos_grupo = {g: veredictos_grupo[g] for g in grupos if g in veredictos_grupo}

        por_estado = {}
        for v in entrada:
            por_estado[v.estado] = por_estado.get(v.estado, 0) + 1
        confianzas = [v.confianza for v in incluidos]
        prompts = entrada.mapped('analisis_id.respuesta_id.prompt_id')
        politica_aplicada = {'politica_observados': politica, 'agrupar_por': agrupar, 'claves_comparacion': claves}
        if variable:
            politica_aplicada['variable'] = {'nombre': variable.get('nombre') or 'grupo', 'dominio': variable.get('dominio'),
                                             'asignacion': dict(variable.get('asignacion') or {})}
        if condicion:
            politica_aplicada['condicion'] = {'clave': condicion['clave'], 'valor': condicion['valor']}
        contenido = {
            'resumen': {
                'n_noticias': len(prompts.mapped('noticia_ids')),
                'm_prompts': len(prompts),
                'm_analisis': len(entrada.mapped('analisis_id')),
                'm_veredictos': len(entrada),
                'incluidos': len(incluidos),
                'excluidos_por_politica': sum(1 for f in filas if f['motivo'] == 'politica'),
                'excluidos_por_condicion': sum(1 for f in filas if f['motivo'] == 'condicion'),
                'veredictos_por_estado': por_estado,
                'confianza_media': round(sum(confianzas) / len(confianzas), 4) if confianzas else None,
            },
            'politica_aplicada': politica_aplicada,
            'grupos': grupos,
            'distribuciones': {k: v for k, v in distribuciones.items() if v},
            'por_grupo': por_grupo,
            'cruces': cruces,
            'numericas': {k: self._estadisticas(xs) for k, xs in numericas.items()},
            'numericas_por_grupo': {k: {g: self._estadisticas(xs) for g, xs in porg.items()}
                                    for k, porg in numericas_grupo.items()},
            'veredictos_por_grupo': veredictos_grupo,
            'filas': filas,
        }
        return Resultado.create({'veredicto_ids': [(6, 0, entrada.ids)], 'contenido': contenido})

    # ------------------------------------------------------------------------------------------------
    @staticmethod
    def _comparable(sub):
        tipo = sub.get('type')
        if 'enum' in sub or tipo in TIPOS_CATEGORICOS + ('number', 'integer'):
            return True
        return tipo == 'array' and (sub.get('items') or {}).get('type') == 'string'

    @staticmethod
    def _estadisticas(xs):
        return {'n': len(xs), 'min': min(xs), 'media': round(sum(xs) / len(xs), 4), 'max': max(xs)}

    @staticmethod
    def _cumple(contenido, condicion):
        """Condición de inclusión: la clave toma el valor exigido (texto, sin distinguir mayúsculas; en listas,
        algún elemento)."""
        clave, valor = condicion.get('clave'), (condicion.get('valor') or '').strip().lower()
        if clave not in contenido:
            return False
        actual = contenido[clave]
        candidatos = actual if isinstance(actual, list) else [actual]
        return any(_texto(x).strip().lower() == valor for x in candidatos if not isinstance(x, (dict, list)))

    @staticmethod
    def _grupo(analisis, agrupar, variable=None):
        noticias = analisis.noticia_ids
        if agrupar == 'ninguno':
            return '(todos)'
        if len(noticias) != 1:
            return '(agregado: %d noticias)' % len(noticias)
        n = noticias[0]
        if agrupar == 'variable':
            tabla = (variable or {}).get('asignacion') or {}
            clave = (n.departamento or '').strip() if (variable or {}).get('dominio') == 'departamento' else (n.medio.name or '')
            return tabla.get(clave.strip(), SIN_ASIGNAR)
        if agrupar == 'medio':
            return n.medio.name or '(sin medio)'
        if agrupar == 'departamento':
            return n.departamento or '(sin departamento)'
        if agrupar in ('dia', 'semana'):
            fecha = n.fecha_hora or n.fecha_registro
            if not fecha:
                return '(sin fecha)'
            if agrupar == 'dia':
                return fecha.strftime('%Y-%m-%d')
            iso = fecha.isocalendar()
            return '%d-S%02d' % (iso[0], iso[1])
        return '(todos)'

    @staticmethod
    def _orden_grupos(grupos, agrupar, variable=None):
        """Orden de presentación: el de la tabla del experto para su variable (sin asignar al final); alfabético
        en el resto (las fechas y semanas ordenan solas)."""
        if agrupar == 'variable':
            orden = []
            for valor in ((variable or {}).get('asignacion') or {}).values():
                if valor not in orden:
                    orden.append(valor)
            presentes = [g for g in orden if g in grupos]
            resto = sorted(g for g in grupos if g not in orden and g != SIN_ASIGNAR)
            return presentes + resto + ([SIN_ASIGNAR] if SIN_ASIGNAR in grupos else [])
        return sorted(grupos)

    @classmethod
    def _cruce(cls, clave, sub, distribucion, por_grupo, grupos, agrupar):
        """Cruce clave × grupo: n por grupo, porcentaje por fila, concentración, colapso y V de Cramér."""
        es_bool = sub.get('type') == 'boolean'
        es_lista = sub.get('type') == 'array'
        if 'enum' in sub:
            valores = [str(x) for x in sub['enum'] if str(x) in distribucion]
            valores += [v for v in sorted(distribucion, key=lambda x: -distribucion[x]) if v not in valores]
        elif es_bool:
            valores = [v for v in ('true', 'false') if v in distribucion]
        else:
            valores = sorted(distribucion, key=lambda x: (-distribucion[x], x))
        otros = valores[MAX_VALORES_CRUCE:]
        valores = valores[:MAX_VALORES_CRUCE]
        if otros:
            valores.append('(otros)')

        def compactar(conteo):
            fila = {v: conteo.get(v, 0) for v in valores if v != '(otros)'}
            if otros:
                fila['(otros)'] = sum(conteo.get(v, 0) for v in otros)
            return fila

        total = sum(distribucion.values())
        dominante = max(distribucion.items(), key=lambda kv: kv[1])
        concentracion = round(dominante[1] / total, 4) if total else None
        if es_bool:
            p_true = distribucion.get('true', 0) / total if total else 0.0
            colapso = p_true > UMBRAL_COLAPSO_BOOL or p_true < 1 - UMBRAL_COLAPSO_BOOL
        else:
            colapso = bool(concentracion and concentracion > UMBRAL_COLAPSO)
        cruce = {
            'unidad': 'menciones' if es_lista else 'analisis',
            'valores': valores,
            'global': compactar(distribucion),
            'concentracion': concentracion,
            'valor_dominante': dominante[0],
            'colapso': colapso,
        }
        if agrupar == 'ninguno':
            return cruce
        tabla = {g: compactar(por_grupo.get(g, {}).get(clave, {})) for g in grupos}
        n_por_grupo = {g: sum(tabla[g].values()) for g in grupos}
        cruce.update({
            'tabla': tabla,
            'n_por_grupo': n_por_grupo,
            'pct': {g: {v: round(100.0 * n / n_por_grupo[g], 1) if n_por_grupo[g] else 0.0 for v, n in tabla[g].items()}
                    for g in grupos},
            'v_cramer': None if es_lista else v_cramer({g: tabla[g] for g in grupos if n_por_grupo[g]}),
        })
        if es_bool:
            cruce['proporcion_true'] = {g: wilson(tabla[g].get('true', 0), n_por_grupo[g]) for g in grupos if n_por_grupo[g]}
        return cruce
