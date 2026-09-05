# -*- coding: utf-8 -*-
import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from ..services import esquema as esq
from ..services import render


def _json_texto(valor):
    return json.dumps(valor, ensure_ascii=False, indent=2) if valor not in (None, False) else ''


def _parsear_json(texto, etiqueta, tipo_esperado):
    if not (texto or '').strip():
        return None
    try:
        valor = json.loads(texto)
    except ValueError as e:
        raise ValidationError(_('%s: el texto no es JSON válido (%s).') % (etiqueta, e))
    if not isinstance(valor, tipo_esperado):
        raise ValidationError(_('%s: se esperaba un %s JSON.') % (
            etiqueta, 'objeto' if tipo_esperado is dict else 'arreglo'))
    return valor


class Template(models.Model):
    """Ancla de la definición del estudio (Apéndice, tipo ``Template``). No circula por los tubos."""
    _name = 'iag.template'
    _description = 'Template de prompt (definición del estudio)'
    _order = 'name'

    RE_VARIABLE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')

    @api.model
    def _orden_claves(self, esquema):
        """Orden de presentación de las claves del esquema de respuesta. El campo Json se persiste como jsonb, que
        reordena las claves de un objeto (por longitud y alfabeto); la lista ``required`` es un arreglo y conserva
        el orden del autor, así que manda ella y después van las claves que no figuran en ella."""
        esquema = esquema or {}
        props = list((esquema.get('properties') or {}).keys())
        req = [k for k in (esquema.get('required') or []) if k in props]
        return req + [k for k in props if k not in req]

    name = fields.Char('Nombre', required=True)
    objetivo = fields.Text('Objetivo del análisis', required=True,
                           help='Alcance, paradigma de análisis y referencias a tener en cuenta.')
    instrucciones = fields.Text('Instrucciones', required=True,
                                help='Cuerpo del prompt. Las variables de contexto se escriben {{nombre}}.')
    variables = fields.Json('Variables de contexto',
                            help='Lista de objetos {"nombre", "descripcion", "default"}.')
    esquema_respuesta = fields.Json('Esquema de respuesta',
                                    help='JSON Schema (subconjunto) contra el que valida Decodificación.')
    variables_texto = fields.Text('Variables (JSON)', compute='_compute_textos', inverse='_inverse_variables')
    esquema_texto = fields.Text('Esquema de respuesta (JSON Schema)', compute='_compute_textos',
                                inverse='_inverse_esquema')
    previsualizacion = fields.Text('Previsualización del prompt', compute='_compute_previsualizacion')
    active = fields.Boolean(default=True, help='Archivado: un template referenciado por prompts no se elimina, se archiva.')

    @api.depends('variables', 'esquema_respuesta')
    def _compute_textos(self):
        for rec in self:
            rec.variables_texto = _json_texto(rec.variables)
            rec.esquema_texto = _json_texto(rec.esquema_respuesta)

    def _inverse_variables(self):
        for rec in self:
            rec.variables = _parsear_json(rec.variables_texto, _('Variables'), list) or []

    def _inverse_esquema(self):
        for rec in self:
            rec.esquema_respuesta = _parsear_json(rec.esquema_texto, _('Esquema de respuesta'), dict)

    @api.depends('objetivo', 'instrucciones', 'variables', 'esquema_respuesta')
    def _compute_previsualizacion(self):
        """Aporte del template al prompt, en el orden real de Composición, con las variables en sus valores
        por defecto y el hueco del material empírico marcado (el material es del corpus, no del template)."""
        for rec in self:
            try:
                instrucciones = rec.renderizar_instrucciones({})
            except UserError as e:
                rec.previsualizacion = '(%s)' % e.args[0]
                continue
            rec.previsualizacion = render.previsualizacion(rec, instrucciones)

    @api.constrains('esquema_respuesta')
    def _check_esquema(self):
        for rec in self:
            if not rec.esquema_respuesta:
                raise ValidationError(_('El template "%s" requiere un esquema de respuesta.') % rec.name)
            errores = esq.validar_esquema(rec.esquema_respuesta)
            if errores:
                raise ValidationError(_('Esquema de respuesta inválido en "%s":\n- %s') % (
                    rec.name, '\n- '.join(errores)))
            if rec.esquema_respuesta.get('type', 'object') != 'object':
                raise ValidationError(_('El esquema raíz del template "%s" debe ser de tipo "object".') % rec.name)

    @api.constrains('variables')
    def _check_variables(self):
        for rec in self:
            variables = rec.variables or []
            if not isinstance(variables, list) or any(
                    not isinstance(v, dict) or not v.get('nombre') for v in variables):
                raise ValidationError(_('"Variables" debe ser una lista de objetos con la clave "nombre".'))

    def _valores_por_defecto(self):
        self.ensure_one()
        return {v['nombre']: v.get('default', '') for v in (self.variables or []) if v.get('nombre')}

    def renderizar_instrucciones(self, valores=None):
        """Sustituye las variables {{nombre}} de las instrucciones. Falla si alguna queda sin valor."""
        self.ensure_one()
        vals = self._valores_por_defecto()
        vals.update({k: v for k, v in (valores or {}).items() if v not in (None, '')})
        usadas = {m.group(1) for m in self.RE_VARIABLE.finditer(self.instrucciones or '')}
        faltan = sorted(usadas - set(vals))
        if faltan:
            raise UserError(_('Variables sin valor en el template "%s": %s') % (self.name, ', '.join(faltan)))
        return self.RE_VARIABLE.sub(lambda m: str(vals[m.group(1)]), self.instrucciones or '')
