# -*- coding: utf-8 -*-
"""Configuración técnica del implementador (no es sintonizador: no la fija el experto)."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    iag_proveedor = fields.Selection(
        [('fake', 'Simulado (desarrollo y tests)'), ('openai_compat', 'API compatible con OpenAI')],
        string='Proveedor', config_parameter='noticias_iag.proveedor', default='fake')
    iag_base_url = fields.Char('URL base', config_parameter='noticias_iag.base_url',
                               default='http://ollama:11434/v1')
    iag_api_key = fields.Char('Clave de API', config_parameter='noticias_iag.api_key')
    iag_modelo_generativo = fields.Char('Modelo generativo', config_parameter='noticias_iag.modelo_generativo',
                                        default='qwen2.5')
    iag_modelo_embedding = fields.Char('Modelo de embedding', config_parameter='noticias_iag.modelo_embedding',
                                       default='nomic-embed-text')
    iag_timeout = fields.Integer('Timeout (s)', config_parameter='noticias_iag.timeout', default=120)
    iag_max_chars_embedding = fields.Integer('Máx. caracteres por noticia (embedding)',
                                             config_parameter='noticias_iag.max_chars_embedding', default=8000)
    iag_max_chars_noticia = fields.Integer('Máx. caracteres por noticia (prompt)',
                                           config_parameter='noticias_iag.max_chars_noticia', default=6000)
    iag_politica_no_conformidad = fields.Selection(
        [('reparar', 'Reparar el cierre de un JSON truncado, validar y contar'),
         ('descartar', 'Descartar y contar')],
        string='Política ante respuestas no conformes',
        config_parameter='noticias_iag.politica_no_conformidad', default='reparar')
    iag_salida_estructurada = fields.Selection(
        [('ninguna', 'Texto libre (sin restricción)'),
         ('json_object', 'JSON válido (response_format json_object)'),
         ('json_schema', 'JSON conforme al esquema (response_format json_schema)')],
        string='Salida estructurada del proveedor',
        config_parameter='noticias_iag.salida_estructurada', default='json_schema')
