# -*- coding: utf-8 -*-
from odoo import api, models


class Filtro(models.AbstractModel):
    """Interfaz común de los filtros del pipeline.

    ``ejecutar(entrada, sintonizador, informe)`` recibe el recordset del tubo de entrada y un
    ``dict`` con la configuración del sintonizador (vacío para los filtros sin sintonizador),
    persiste sus registros y devuelve el recordset del tubo de salida. ``informe`` es un
    ``dict`` opcional donde el filtro deja diagnósticos de la corrida (descartes, similitudes);
    no es un tubo ni un sintonizador. Un filtro no importa ni invoca a otro; la sustitución
    se hace por ``_inherit`` sobre el filtro concreto.
    """
    _name = 'iag.filtro'
    _description = 'Filtro del pipeline (interfaz)'

    def ejecutar(self, entrada, sintonizador, informe=None):
        raise NotImplementedError

    @api.model
    def _proveedor(self):
        return self.env['iag.proveedor']._obtener()

    @api.model
    def _config(self):
        return self.env['iag.proveedor']._config()
