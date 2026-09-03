# -*- coding: utf-8 -*-
"""Los tipos de datos del pipeline (Apéndice de la tesina), un modelo por tipo.

Cada modelo lleva exactamente las claves del contrato más ``fecha_registro``. Los registros
son inmutables una vez creados (monotonía del repositorio): no se borran, y las referencias
de procedencia llevan ``ondelete='restrict'``.
"""
import json

from markupsafe import escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


def _json_texto(valor):
    return json.dumps(valor, ensure_ascii=False, indent=2) if valor not in (None, False) else ''


class TipoFlujo(models.AbstractModel):
    _name = 'iag.tipo.flujo'
    _description = 'Tipo de dato del flujo (mixin: procedencia y monotonía)'

    fecha_registro = fields.Datetime('Fecha de registro', default=fields.Datetime.now, readonly=True, index=True)

    def unlink(self):
        raise UserError(_('Los registros del flujo no se eliminan (monotonía del repositorio): %s.')
                        % self._description)

    def name_get(self):
        return [(rec.id, '%s #%d' % (rec._description, rec.id)) for rec in self]


class Embedding(models.Model):
    _name = 'iag.embedding'
    _inherit = 'iag.tipo.flujo'
    _description = 'Embedding'
    _order = 'id'

    noticia_id = fields.Many2one('wsf_noticias_resultados', 'Noticia', required=True, ondelete='restrict', index=True)
    vector = fields.Json('Vector')
    dimension = fields.Integer('Dimensión', compute='_compute_dimension')
    vector_texto = fields.Char('Vector (inicio)', compute='_compute_dimension')

    @api.depends('vector')
    def _compute_dimension(self):
        for rec in self:
            v = rec.vector or []
            rec.dimension = len(v)
            rec.vector_texto = ('[' + ', '.join('%.4f' % x for x in v[:8]) + (', …]' if len(v) > 8 else ']')) if v else ''


class Prompt(models.Model):
    _name = 'iag.prompt'
    _inherit = 'iag.tipo.flujo'
    _description = 'Prompt'
    _order = 'id'

    texto = fields.Text('Prompt completo', required=True)
    temperatura = fields.Float('Temperatura')
    longitud_salida = fields.Integer('Longitud de salida')
    noticia_ids = fields.Many2many('wsf_noticias_resultados', 'iag_prompt_noticia_rel', 'prompt_id', 'noticia_id',
                                   'Noticias del contexto', ondelete='restrict')
    template_id = fields.Many2one('iag.template', 'Template', required=True, ondelete='restrict', index=True)
    n_noticias = fields.Integer('Noticias', compute='_compute_n_noticias')

    @api.depends('noticia_ids')
    def _compute_n_noticias(self):
        for rec in self:
            rec.n_noticias = len(rec.noticia_ids)


class Respuesta(models.Model):
    _name = 'iag.respuesta'
    _inherit = 'iag.tipo.flujo'
    _description = 'Respuesta IAG'
    _order = 'id'

    prompt_id = fields.Many2one('iag.prompt', 'Prompt', required=True, ondelete='restrict', index=True)
    texto = fields.Text('Respuesta cruda', required=True)
    template_id = fields.Many2one(related='prompt_id.template_id')


class Analisis(models.Model):
    _name = 'iag.analisis'
    _inherit = 'iag.tipo.flujo'
    _description = 'Análisis'
    _order = 'id'

    respuesta_id = fields.Many2one('iag.respuesta', 'Respuesta', required=True, ondelete='restrict', index=True)
    contenido = fields.Json('Contenido')
    contenido_texto = fields.Text('Contenido (JSON)', compute='_compute_contenido_texto')
    # Navegación por la cadena de procedencia (no son columnas del contrato)
    prompt_id = fields.Many2one(related='respuesta_id.prompt_id')
    template_id = fields.Many2one(related='respuesta_id.prompt_id.template_id')
    noticia_ids = fields.Many2many(related='respuesta_id.prompt_id.noticia_ids')
    veredicto_ids = fields.One2many('iag.veredicto', 'analisis_id', 'Veredictos')

    @api.depends('contenido')
    def _compute_contenido_texto(self):
        for rec in self:
            rec.contenido_texto = _json_texto(rec.contenido)

    def action_emitir_veredicto(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Emitir veredicto'),
            'res_model': 'iag.veredicto',
            'view_mode': 'form',
            'view_id': self.env.ref('noticias_iag.view_iag_veredicto_form').id,
            'target': 'current',
            'context': {'default_analisis_id': self.id},
        }


class Veredicto(models.Model):
    _name = 'iag.veredicto'
    _inherit = 'iag.tipo.flujo'
    _description = 'Veredicto'
    _order = 'id'

    ESTADOS = [('valido', 'Válido'), ('observado', 'Observado'), ('no_sustentado', 'No sustentado')]

    analisis_id = fields.Many2one('iag.analisis', 'Análisis', required=True, ondelete='restrict', index=True)
    estado = fields.Selection(ESTADOS, 'Estado', required=True)
    confianza = fields.Float('Confianza', required=True, help='Fidelidad del análisis a la evidencia, en [0, 1].')
    # El contrato dice `sustento: objeto` con esquema según el criterio de validez del estudio. En esta
    # implementación (criterio v1: anclaje literal de cada afirmación en la evidencia) el esquema es una lista
    # de {afirmacion, relacion, fragmento, link}, cargada como filas y materializada en el Json del contrato.
    sustento_ids = fields.One2many('iag.veredicto.sustento', 'veredicto_id', 'Sustento')
    sustento = fields.Json('Sustento', compute='_compute_sustento', store=True)
    sustento_texto = fields.Text('Sustento (JSON)', compute='_compute_sustento_texto')
    # Contexto para el revisor (navegación, no contrato)
    estudio_id = fields.Many2one('iag.estudio', 'Estudio', compute='_compute_estudio')
    criterio_validez = fields.Text('Criterio de validez del estudio', compute='_compute_estudio')
    analisis_contenido_texto = fields.Text(related='analisis_id.contenido_texto')
    noticia_ids = fields.Many2many(related='analisis_id.noticia_ids')
    evidencia_texto = fields.Text('Evidencia', compute='_compute_evidencia_texto')

    @api.depends('sustento_ids', 'sustento_ids.afirmacion', 'sustento_ids.relacion', 'sustento_ids.fragmento', 'sustento_ids.link')
    def _compute_sustento(self):
        for rec in self:
            rec.sustento = [{'afirmacion': l.afirmacion or '', 'relacion': l.relacion or '',
                             'fragmento': l.fragmento or '', 'link': l.link or ''} for l in rec.sustento_ids]

    @api.depends('sustento')
    def _compute_sustento_texto(self):
        for rec in self:
            rec.sustento_texto = _json_texto(rec.sustento)

    @api.depends('analisis_id')
    def _compute_estudio(self):
        Estudio = self.env['iag.estudio']
        for rec in self:
            estudio = Estudio.search([('analisis_ids', 'in', rec.analisis_id.ids)], limit=1) if rec.analisis_id else Estudio
            rec.estudio_id = estudio
            rec.criterio_validez = estudio.criterio_validez or ''

    @api.depends('analisis_id')
    def _compute_evidencia_texto(self):
        for rec in self:
            partes = []
            for i, n in enumerate(rec.noticia_ids, 1):
                partes.append('[%d] (%s, %s) %s\n%s\n\n%s' % (
                    i, n.medio.name or '-', n.fecha_hora or n.fecha_registro or '-', n.titulo or '',
                    n.link or '', n.texto or ''))
            rec.evidencia_texto = '\n\n' + ('-' * 60 + '\n\n').join(partes) if partes else ''

    @api.constrains('confianza')
    def _check_confianza(self):
        for rec in self:
            if not 0.0 <= rec.confianza <= 1.0:
                raise ValidationError(_('La confianza debe estar en [0, 1].'))

    @api.model
    def _sustento_sugerido(self, analisis):
        """Filas iniciales para el revisor humano: las afirmaciones que el propio análisis declara (cualquier
        lista de objetos con clave 'afirmacion' en su contenido), con el fragmento que citó y el link de la
        noticia. La relación queda vacía a propósito: la decide el revisor fila por fila."""
        contenido = analisis.contenido or {}
        link = analisis.noticia_ids[0].link if len(analisis.noticia_ids) == 1 else ''
        filas = []
        for valor in contenido.values():
            if not isinstance(valor, list):
                continue
            for item in valor:
                if isinstance(item, dict) and item.get('afirmacion'):
                    filas.append({'sequence': len(filas), 'afirmacion': str(item['afirmacion']),
                                  'fragmento': str(item.get('fragmento') or ''), 'link': link or ''})
        return filas

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        analisis_id = res.get('analisis_id') or self.env.context.get('default_analisis_id')
        if analisis_id and 'sustento_ids' in fields_list and not res.get('sustento_ids'):
            analisis = self.env['iag.analisis'].browse(analisis_id).exists()
            if analisis:
                res['sustento_ids'] = [(0, 0, fila) for fila in self._sustento_sugerido(analisis)]
        return res

    def action_emitir(self):
        """El formulario ya se guardó (Odoo guarda antes de ejecutar un botón). Sigue con el próximo análisis
        pendiente del mismo estudio o vuelve al estudio si no queda ninguno."""
        self.ensure_one()
        estudio = self.estudio_id
        pendientes = estudio._analisis_pendientes() if estudio else self.env['iag.analisis']
        if pendientes:
            return {
                'type': 'ir.actions.act_window', 'res_model': 'iag.veredicto', 'view_mode': 'form',
                'view_id': self.env.ref('noticias_iag.view_iag_veredicto_form').id, 'target': 'current',
                'name': _('Emitir veredicto (%d pendientes)') % len(pendientes),
                'context': {'default_analisis_id': pendientes[0].id},
            }
        if estudio:
            return {'type': 'ir.actions.act_window', 'res_model': 'iag.estudio', 'res_id': estudio.id,
                    'view_mode': 'form', 'target': 'current'}
        return {'type': 'ir.actions.act_window_close'}

    @api.model_create_multi
    def create(self, vals_list):
        # El sustento puede llegar como filas (formulario del revisor), como lista de dicts (juez generativo)
        # o como texto JSON (scripts); todo se normaliza a filas y el Json del contrato se computa de ellas.
        for vals in vals_list:
            items = vals.pop('sustento', None)
            texto = vals.pop('sustento_texto', None)
            if items is None and texto:
                try:
                    items = json.loads(texto)
                except ValueError as e:
                    raise ValidationError(_('Sustento: el texto no es JSON válido (%s).') % e)
            if items and not vals.get('sustento_ids'):
                if not isinstance(items, list):
                    raise ValidationError(_('Sustento: se esperaba una lista de afirmaciones.'))
                vals['sustento_ids'] = [(0, 0, {
                    'sequence': i, 'afirmacion': str(it.get('afirmacion') or ''), 'relacion': it.get('relacion') or 'sin_evidencia',
                    'fragmento': str(it.get('fragmento') or ''), 'link': str(it.get('link') or '')})
                    for i, it in enumerate(items) if isinstance(it, dict)]
        return super().create(vals_list)


class VeredictoSustento(models.Model):
    """Una afirmación del análisis y su relación con la evidencia (esquema v1 del `sustento`)."""
    _name = 'iag.veredicto.sustento'
    _description = 'Sustento del veredicto'
    _order = 'sequence, id'

    RELACIONES = [('respalda', 'La evidencia la respalda'), ('contradice', 'La evidencia la contradice'),
                  ('sin_evidencia', 'Sin evidencia')]

    veredicto_id = fields.Many2one('iag.veredicto', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=0)
    afirmacion = fields.Text('Afirmación del análisis', required=True)
    relacion = fields.Selection(RELACIONES, 'Relación con la evidencia', required=True)
    fragmento = fields.Text('Fragmento literal de la evidencia')
    link = fields.Char('Noticia (link)')

    def unlink(self):
        raise UserError(_('El sustento de un veredicto no se elimina (monotonía del repositorio).'))


class Resultado(models.Model):
    _name = 'iag.resultado'
    _inherit = 'iag.tipo.flujo'
    _description = 'Resultado'
    _order = 'id desc'

    veredicto_ids = fields.Many2many('iag.veredicto', 'iag_resultado_veredicto_rel', 'resultado_id', 'veredicto_id',
                                     'Veredictos consolidados', ondelete='restrict')
    contenido = fields.Json('Contenido consolidado')
    contenido_texto = fields.Text('Contenido (JSON)', compute='_compute_contenido_texto')
    html = fields.Html('Presentación', compute='_compute_html', store=True, sanitize=False)
    n_veredictos = fields.Integer('Veredictos', compute='_compute_n_veredictos')

    @api.depends('contenido')
    def _compute_contenido_texto(self):
        for rec in self:
            rec.contenido_texto = _json_texto(rec.contenido)

    @api.depends('veredicto_ids')
    def _compute_n_veredictos(self):
        for rec in self:
            rec.n_veredictos = len(rec.veredicto_ids)

    @api.depends('contenido')
    def _compute_html(self):
        for rec in self:
            rec.html = _render_html(rec.contenido or {})


def _celda(v):
    if isinstance(v, (dict, list)):
        return escape(json.dumps(v, ensure_ascii=False))
    return escape('' if v is None else v)


def _tabla_distribucion(valores):
    filas = ''.join('<tr><td>%s</td><td class="text-end">%s</td></tr>' % (_celda(k), n)
                    for k, n in sorted(valores.items(), key=lambda kv: (-kv[1], str(kv[0]))))
    return ('<table class="table table-sm table-bordered"><thead><tr><th>Valor</th><th class="text-end">n</th></tr></thead>'
            '<tbody>%s</tbody></table>' % filas)


def _render_html(c):
    partes = []
    resumen = c.get('resumen') or {}
    partes.append('<h3>Resumen de la corrida</h3><table class="table table-sm"><tbody>')
    for k, v in resumen.items():
        partes.append('<tr><th style="width:40%%">%s</th><td>%s</td></tr>' % (_celda(k), _celda(v)))
    partes.append('</tbody></table>')
    politica = c.get('politica_aplicada') or {}
    if politica:
        partes.append('<p><small>Política aplicada: %s</small></p>' % _celda(politica))
    dist = c.get('distribuciones') or {}
    if dist:
        partes.append('<h3>Distribuciones</h3>')
        for clave, valores in dist.items():
            partes.append('<h4>%s</h4>%s' % (_celda(clave), _tabla_distribucion(valores)))
    num = c.get('numericas') or {}
    if num:
        partes.append('<h3>Variables numéricas</h3><table class="table table-sm table-bordered"><thead><tr>'
                      '<th>Clave</th><th>n</th><th>mín</th><th>media</th><th>máx</th></tr></thead><tbody>')
        for clave, st in num.items():
            partes.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _celda(clave), st.get('n'), st.get('min'), st.get('media'), st.get('max')))
        partes.append('</tbody></table>')
    por_grupo = c.get('por_grupo') or {}
    if por_grupo:
        partes.append('<h3>Por grupo</h3>')
        for grupo, claves in por_grupo.items():
            partes.append('<h4>%s</h4>' % _celda(grupo))
            for clave, valores in claves.items():
                partes.append('<h5>%s</h5>%s' % (_celda(clave), _tabla_distribucion(valores)))
    return ''.join(partes)
