# -*- coding: utf-8 -*-
from odoo import models


class Noticia(models.Model):
    """Extensión mínima del tipo ``Noticia`` de la infraestructura preexistente.

    No agrega columnas ni cambia comportamiento: sólo fija el campo de presentación
    (``titulo``) para que las referencias desde los tipos del pipeline sean legibles.
    """
    _inherit = 'wsf_noticias_resultados'
    _rec_name = 'titulo'

