# -*- coding: utf-8 -*-
from odoo import models

from ...services import esquema as esq


class FiltroDecodificacion(models.AbstractModel):
    """RespuestaIAG[M] -> Análisis[M']. Compuerta: lo no conforme no se persiste.

    Alcanza el esquema esperado por la cadena respuesta -> prompt -> template, sin sintonizador.
    Política ante no conformidad (config. técnica ``politica_no_conformidad``):
    ``descartar`` (descarta y cuenta) o ``reparar`` (si la respuesta es un JSON truncado, cierra
    lo que quedó abierto al final, valida igual contra el esquema y deja constancia de la
    reparación en el informe de la corrida; lo que sigue sin conformar se descarta).
    """
    _name = 'iag.filtro.decodificacion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Decodificación'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Analisis = self.env['iag.analisis']
        politica = self._config()['politica_no_conformidad']
        vals, descartes, reparados = [], [], []
        for respuesta in entrada:
            esquema = respuesta.prompt_id.template_id.esquema_respuesta or {}
            texto = respuesta.texto or ''
            obj, error = esq.aislar_json(texto)
            reparacion = None
            if error and politica == 'reparar':
                obj, reparacion = esq.reparar_json(texto)
                if obj is not None:
                    error = None
            if error:
                descartes.append({'respuesta_id': respuesta.id, 'motivo': error})
                continue
            canon, errores = esq.canonicalizar(obj, esquema)
            if errores:
                motivo = '; '.join(errores[:5])
                if reparacion:
                    motivo = '(reparado: %s) %s' % (reparacion, motivo)
                descartes.append({'respuesta_id': respuesta.id, 'motivo': motivo})
                continue
            if reparacion:
                reparados.append({'respuesta_id': respuesta.id, 'reparacion': reparacion})
            vals.append({'respuesta_id': respuesta.id, 'contenido': canon})
        if informe is not None:
            informe['politica'] = politica
            informe['descartes'] = descartes
            informe['reparados'] = reparados
        return Analisis.create(vals)
