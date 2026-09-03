# -*- coding: utf-8 -*-
from odoo import models


class FiltroInvocacion(models.AbstractModel):
    """Prompt[M] -> RespuestaIAG[M]. Aísla la dependencia del servicio generativo."""
    _name = 'iag.filtro.invocacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Invocación generativa'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Respuesta = self.env['iag.respuesta']
        if not entrada:
            return Respuesta
        proveedor = self._proveedor()
        vals = []
        for prompt in entrada:
            texto = proveedor.completar(prompt.texto, prompt.temperatura, prompt.longitud_salida,
                                        esquema=prompt.template_id.esquema_respuesta or None)
            vals.append({'prompt_id': prompt.id, 'texto': texto or ''})
        return Respuesta.create(vals)
