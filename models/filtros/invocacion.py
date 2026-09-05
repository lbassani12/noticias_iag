# -*- coding: utf-8 -*-
import logging

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FiltroInvocacion(models.AbstractModel):
    """Prompt[M] -> RespuestaIAG[M]. Aísla la dependencia del servicio generativo.

    Si el proveedor falla para un prompt pese a los reintentos, la respuesta se persiste vacía y el
    motivo queda en el informe de la corrida (``fallidas``): la etapa no se pierde entera por una
    llamada, y la compuerta de Decodificación descarta la respuesta vacía y la cuenta.
    """
    _name = 'iag.filtro.invocacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Invocación generativa'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Respuesta = self.env['iag.respuesta']
        if not entrada:
            return Respuesta
        proveedor = self._proveedor()
        vals, fallidas = [], []
        total = len(entrada)
        for i, prompt in enumerate(entrada, 1):
            if i == 1 or i % 10 == 0 or i == total:
                _logger.info('Invocación: prompt %d/%d', i, total)
            try:
                texto = proveedor.completar(prompt.texto, prompt.temperatura, prompt.longitud_salida,
                                            esquema=prompt.template_id.esquema_respuesta or None)
            except UserError as e:
                motivo = str(e.args[0] if e.args else e)[:300]
                _logger.error('Invocación: prompt %d sin respuesta del proveedor (%s)', prompt.id, motivo)
                fallidas.append({'prompt_id': prompt.id, 'motivo': motivo})
                texto = ''
            vals.append({'prompt_id': prompt.id, 'texto': texto or ''})
        if informe is not None and fallidas:
            informe['fallidas'] = fallidas
        return Respuesta.create(vals)
