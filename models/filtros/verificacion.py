# -*- coding: utf-8 -*-
import json
import logging

from odoo import models
from odoo.exceptions import UserError

from ...services import esquema as esq
from ...services.proveedor import MARCADOR_ESQUEMA

_logger = logging.getLogger(__name__)

#: Esquema del juicio que emite el juez generativo (contrato Veredicto: estado, confianza, sustento).
ESQUEMA_VEREDICTO = {
    'type': 'object',
    'required': ['estado', 'confianza', 'sustento'],
    'properties': {
        'estado': {'type': 'string', 'enum': ['valido', 'observado', 'no_sustentado']},
        'confianza': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'sustento': {'type': 'array', 'items': {
            'type': 'object',
            'required': ['afirmacion', 'relacion'],
            'properties': {
                'afirmacion': {'type': 'string'},
                'relacion': {'type': 'string', 'enum': ['respalda', 'contradice', 'sin_evidencia']},
                'link': {'type': 'string'},
                'fragmento': {'type': 'string'},
            }}},
    },
}


class FiltroVerificacion(models.AbstractModel):
    """Análisis[M] -> Veredicto[M]. Compuerta blanda: juzga, cuantifica y señala sin suprimir.

    Ejecutor ``llm_judge``: un prompt de juez por análisis con la evidencia (las noticias que
    su prompt incorporó) y el criterio de validez. Ejecutor ``humano``: el filtro no crea
    registros; el revisor los emite desde la vista y el Estudio queda ``en_verificacion``.
    Si el juez no produce un veredicto conforme (o el proveedor no responde pese a los
    reintentos), se registra ``observado`` con confianza 0 y el motivo en el sustento: ningún
    análisis queda sin veredicto y la etapa no se pierde por una llamada fallida.
    """
    _name = 'iag.filtro.verificacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Verificación'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Veredicto = self.env['iag.veredicto']
        if sintonizador.get('ejecutor') == 'humano' or not entrada:
            return Veredicto
        cfg = self._config()
        proveedor = self._proveedor()
        vals, no_conformes, sin_respuesta = [], 0, 0
        for analisis in entrada:
            prompt_juez = self._prompt_juez(analisis, sintonizador.get('criterio_validez') or '',
                                            cfg['max_chars_noticia'])
            try:
                crudo = proveedor.completar(prompt_juez, 0.0, 2048, esquema=ESQUEMA_VEREDICTO)
                obj, error = esq.aislar_json(crudo or '')
            except UserError as e:
                sin_respuesta += 1
                crudo, obj, error = '', None, 'el proveedor no respondió: %s' % (e.args[0] if e.args else e)
                _logger.error('Verificación: análisis %d sin juicio del proveedor (%s)', analisis.id, error)
            canon, errores = (None, [error]) if error else esq.canonicalizar(obj, ESQUEMA_VEREDICTO)
            if errores:
                no_conformes += 1
                canon = {'estado': 'observado', 'confianza': 0.0, 'sustento': [{
                    'afirmacion': '(el juez no produjo un veredicto conforme al esquema: %s)' % '; '.join(errores[:3]),
                    'relacion': 'sin_evidencia', 'fragmento': (crudo or '')[:500]}]}
            vals.append({'analisis_id': analisis.id, 'estado': canon['estado'],
                         'confianza': canon['confianza'], 'sustento': canon['sustento']})
        if informe is not None:
            informe['juez_no_conforme'] = no_conformes
            informe['juez_sin_respuesta'] = sin_respuesta
        return Veredicto.create(vals)

    @staticmethod
    def _prompt_juez(analisis, criterio, max_chars):
        partes = ['Sos un verificador. Evaluá si el siguiente análisis está sostenido por la evidencia suministrada.',
                  '', '# Criterio de validez', criterio or 'Cada afirmación debe estar sostenida por fragmentos literales de la evidencia.',
                  '', '# Análisis a verificar', json.dumps(analisis.contenido or {}, ensure_ascii=False, indent=2),
                  '', '# Evidencia (noticias que el prompt incorporó)']
        for i, n in enumerate(analisis.noticia_ids, 1):
            fecha = n.fecha_hora or n.fecha_registro
            partes.append('[%d] (%s, %s) %s\n%s\n%s' % (
                i, n.medio.name or 'medio desconocido', fecha.strftime('%Y-%m-%d') if fecha else 's/f',
                n.titulo or '', n.link or '', (n.texto or '')[:max_chars]))
            partes.append('')
        partes += ['# Instrucciones',
                   'Para cada afirmación del análisis indicá si la evidencia la respalda, la contradice o no hay evidencia, '
                   'citando el fragmento literal y el link. Estado: "valido" si todas las afirmaciones están respaldadas; '
                   '"observado" si alguna carece de evidencia; "no_sustentado" si la mayoría carece de evidencia o hay '
                   'contradicciones. La confianza es un número en [0, 1].',
                   '', MARCADOR_ESQUEMA,
                   'Respondé únicamente con un objeto JSON válido que cumpla el siguiente esquema, sin texto adicional:',
                   json.dumps(ESQUEMA_VEREDICTO, ensure_ascii=False, indent=2)]
        return '\n'.join(partes)
