# -*- coding: utf-8 -*-
"""El sumidero del pipeline: la proyección aplanada del Resultado para tableros.

No es un filtro, ni un tubo, ni un tipo del flujo: es una vista SQL de sólo lectura sobre el
``contenido.filas`` de cada Resultado enlazado a un Estudio, con una fila por veredicto consolidado
y por clave escalar del análisis (las listas de textos aportan un elemento por fila; los objetos
anidados, como las afirmaciones, quedan afuera). Cada fila lleva el estado y la confianza del veredicto
(responsabilidad D) y las referencias al análisis y a la noticia (responsabilidad E). Sobre este modelo
operan las vistas pivot y graph nativas de Odoo, que son la integración con los tableros de la
plataforma que promete el OE1. Los Resultados anteriores a la clave ``filas`` se leen desde ``incluidos``
(sin grupo).
"""
from odoo import api, fields, models, tools, _


class ResultadoFila(models.Model):
    _name = 'iag.resultado.fila'
    _description = 'Tablero: análisis consolidados, una fila por clave'
    _auto = False
    _order = 'id'
    _rec_name = 'clave'

    resultado_id = fields.Many2one('iag.resultado', 'Resultado', readonly=True)
    estudio_id = fields.Many2one('iag.estudio', 'Estudio', readonly=True)
    veredicto_id = fields.Many2one('iag.veredicto', 'Veredicto', readonly=True)
    analisis_id = fields.Many2one('iag.analisis', 'Análisis', readonly=True)
    noticia_id = fields.Many2one('wsf_noticias_resultados', 'Noticia', readonly=True)
    medio_id = fields.Many2one('res.partner', 'Medio', readonly=True)
    departamento = fields.Char('Departamento', readonly=True)
    fecha = fields.Date('Fecha', readonly=True, help='Fecha de publicación declarada o, si falta, de registro.')
    grupo = fields.Char('Grupo', readonly=True, help='Valor de la variable de comparación con que consolidó Normalización.')
    incluido = fields.Boolean('Consolidado', readonly=True,
                              help='Entró en la consolidación según la política sobre observados y la condición de inclusión.')
    veredicto_estado = fields.Selection([('valido', 'Válido'), ('observado', 'Observado'), ('no_sustentado', 'No sustentado')],
                                        'Estado del veredicto', readonly=True)
    confianza = fields.Float('Confianza', readonly=True, group_operator='avg')
    clave = fields.Char('Clave', readonly=True)
    valor = fields.Char('Valor', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute("""
            CREATE OR REPLACE VIEW %s AS
            WITH filas AS (
                SELECT r.id AS resultado_id, e.id AS estudio_id, f.ord AS orden,
                       (f.fila->>'veredicto')::int AS veredicto_id,
                       (f.fila->>'analisis')::int AS analisis_id,
                       f.fila->>'estado' AS veredicto_estado,
                       (f.fila->>'confianza')::float AS confianza,
                       f.fila->>'grupo' AS grupo,
                       COALESCE((f.fila->>'incluido')::boolean, true) AS incluido
                FROM iag_resultado r
                JOIN iag_estudio e ON e.resultado_id = r.id
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(r.contenido->'filas', r.contenido->'incluidos', '[]'::jsonb)
                ) WITH ORDINALITY AS f(fila, ord)
            ),
            noticia_unica AS (
                SELECT prompt_id, MIN(noticia_id) AS noticia_id
                FROM iag_prompt_noticia_rel GROUP BY prompt_id HAVING COUNT(*) = 1
            ),
            valores AS (
                SELECT a.id AS analisis_id, kv.clave, kv.valor, kv.orden_clave
                FROM iag_analisis a
                CROSS JOIN LATERAL (
                    SELECT e.key AS clave, e.value #>> '{}' AS valor, e.ord AS orden_clave
                    FROM jsonb_each(a.contenido) WITH ORDINALITY AS e(key, value, ord)
                    WHERE jsonb_typeof(e.value) IN ('string', 'number', 'boolean')
                    UNION ALL
                    SELECT e.key, el.value #>> '{}', e.ord
                    FROM jsonb_each(a.contenido) WITH ORDINALITY AS e(key, value, ord)
                    CROSS JOIN LATERAL jsonb_array_elements(e.value) AS el(value)
                    WHERE jsonb_typeof(e.value) = 'array'
                      AND jsonb_typeof(el.value) IN ('string', 'number', 'boolean')
                ) kv
                WHERE jsonb_typeof(a.contenido) = 'object'
            )
            SELECT row_number() OVER (ORDER BY f.resultado_id, f.orden, v.orden_clave, v.valor) AS id,
                   f.resultado_id, f.estudio_id, f.veredicto_id, f.analisis_id,
                   f.veredicto_estado, f.confianza, f.grupo, f.incluido,
                   n.id AS noticia_id, n.medio AS medio_id, n.departamento,
                   COALESCE(n.fecha_hora, n.fecha_registro)::date AS fecha,
                   v.clave, v.valor
            FROM filas f
            JOIN valores v ON v.analisis_id = f.analisis_id
            JOIN iag_analisis a ON a.id = f.analisis_id
            JOIN iag_respuesta rs ON rs.id = a.respuesta_id
            LEFT JOIN noticia_unica nu ON nu.prompt_id = rs.prompt_id
            LEFT JOIN wsf_noticias_resultados n ON n.id = nu.noticia_id
        """ % self._table)

    @api.model
    def accion_tablero(self, dominio, nombre, clave=None):
        """Acción del tablero sobre un dominio: pivot (filas = grupo, columnas = valor) y gráfico de barras
        apiladas, con el filtro "consolidados" activo y, si se indica, una clave del esquema preseleccionada."""
        contexto = {
            'search_default_incluido': 1,
            'pivot_row_groupby': ['grupo'], 'pivot_column_groupby': ['valor'], 'pivot_measures': ['__count'],
            'graph_mode': 'bar', 'graph_groupbys': ['grupo', 'valor'], 'graph_measure': '__count',
        }
        if clave:
            contexto['search_default_clave'] = clave
        return {
            'type': 'ir.actions.act_window', 'name': _('Tablero · %s') % nombre, 'res_model': self._name,
            'view_mode': 'pivot,graph,tree', 'domain': dominio, 'context': contexto,
            'views': [(self.env.ref('noticias_iag.view_iag_resultado_fila_pivot').id, 'pivot'),
                      (self.env.ref('noticias_iag.view_iag_resultado_fila_graph').id, 'graph'),
                      (self.env.ref('noticias_iag.view_iag_resultado_fila_tree').id, 'tree')],
            'search_view_id': self.env.ref('noticias_iag.view_iag_resultado_fila_search').id,
        }
