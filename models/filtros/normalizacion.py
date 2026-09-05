# -*- coding: utf-8 -*-
from odoo import models

ESTADOS_INCLUIDOS = {
    'incluir': {'valido', 'observado', 'no_sustentado'},
    'excluir': {'valido', 'observado'},
    'solo_validos': {'valido'},
}
TIPOS_CATEGORICOS = ('string', 'boolean')


class FiltroNormalizacion(models.AbstractModel):
    """Veredicto[M] -> Resultado. Consolidación determinística, sin modelo generativo.

    Aplica la política sobre los análisis observados, cuenta distribuciones de las claves de
    comparación del esquema del estudio (global y por grupo) y estadísticas de las numéricas.
    ``Resultado.veredictos`` referencia los M veredictos recibidos (procedencia completa);
    los efectivamente consolidados se listan en ``contenido.incluidos``.
    """
    _name = 'iag.filtro.normalizacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Normalización'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Resultado = self.env['iag.resultado']
        politica = sintonizador.get('politica_observados') or 'incluir'
        agrupar = sintonizador.get('agrupar_por') or 'ninguno'
        incluidos = entrada.filtered(lambda v: v.estado in ESTADOS_INCLUIDOS[politica])

        propiedades = {}
        for t in entrada.mapped('analisis_id.respuesta_id.prompt_id.template_id'):
            propiedades.update((t.esquema_respuesta or {}).get('properties') or {})
        claves = sintonizador.get('claves_comparacion')
        if claves is None:   # sin sintonizador: todas las comparables
            claves = [k for k, s in propiedades.items() if self._comparable(s)]

        distribuciones = {k: {} for k in claves}
        por_grupo, numericas = {}, {}
        for v in incluidos:
            contenido = v.analisis_id.contenido or {}
            grupo = self._grupo(v.analisis_id, agrupar)
            for k in claves:
                if k not in contenido:
                    continue
                valor = contenido[k]
                tipo = (propiedades.get(k) or {}).get('type')
                if tipo in ('number', 'integer') and isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    numericas.setdefault(k, []).append(float(valor))
                    continue
                for x in (valor if isinstance(valor, list) else [valor]):
                    if isinstance(x, (dict, list)):
                        continue
                    etiqueta = str(x)
                    distribuciones[k][etiqueta] = distribuciones[k].get(etiqueta, 0) + 1
                    if agrupar != 'ninguno':
                        celda = por_grupo.setdefault(grupo, {}).setdefault(k, {})
                        celda[etiqueta] = celda.get(etiqueta, 0) + 1

        por_estado = {}
        for v in entrada:
            por_estado[v.estado] = por_estado.get(v.estado, 0) + 1
        confianzas = [v.confianza for v in incluidos]
        prompts = entrada.mapped('analisis_id.respuesta_id.prompt_id')
        contenido = {
            'resumen': {
                'n_noticias': len(prompts.mapped('noticia_ids')),
                'm_prompts': len(prompts),
                'm_analisis': len(entrada.mapped('analisis_id')),
                'm_veredictos': len(entrada),
                'incluidos': len(incluidos),
                'veredictos_por_estado': por_estado,
                'confianza_media': round(sum(confianzas) / len(confianzas), 4) if confianzas else None,
            },
            'politica_aplicada': {'politica_observados': politica, 'agrupar_por': agrupar,
                                  'claves_comparacion': claves},
            'distribuciones': {k: v for k, v in distribuciones.items() if v},
            'por_grupo': por_grupo,
            'numericas': {k: {'n': len(xs), 'min': min(xs), 'media': round(sum(xs) / len(xs), 4), 'max': max(xs)}
                          for k, xs in numericas.items()},
            'incluidos': [{'veredicto': v.id, 'analisis': v.analisis_id.id, 'estado': v.estado,
                           'confianza': v.confianza} for v in incluidos],
        }
        return Resultado.create({'veredicto_ids': [(6, 0, entrada.ids)], 'contenido': contenido})

    @staticmethod
    def _comparable(sub):
        tipo = sub.get('type')
        if 'enum' in sub or tipo in TIPOS_CATEGORICOS + ('number', 'integer'):
            return True
        return tipo == 'array' and (sub.get('items') or {}).get('type') == 'string'

    @staticmethod
    def _grupo(analisis, agrupar):
        noticias = analisis.noticia_ids
        if len(noticias) != 1:
            return '(agregado: %d noticias)' % len(noticias)
        n = noticias[0]
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
