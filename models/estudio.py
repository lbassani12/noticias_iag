# -*- coding: utf-8 -*-
"""El Estudio: configuración del pipeline en el sentido de Garlan & Shaw.

Instancia la red de tubos (qué recordset viaja por cada tubo), expone los sintonizadores de
Composición, Verificación y Normalización, guarda el estado de avance y corre los filtros.
No es un tipo del flujo y no circula por ningún tubo.
"""
import json
import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError

ESTADOS = [
    ('borrador', 'Borrador'),
    ('corpus', 'Corpus seleccionado'),
    ('enriquecido', 'Enriquecido'),
    ('compuesto', 'Prompts compuestos'),
    ('invocado', 'Invocado'),
    ('decodificado', 'Decodificado'),
    ('en_verificacion', 'En verificación (revisor humano)'),
    ('verificado', 'Verificado'),
    ('consolidado', 'Consolidado'),
]

#: La red de tubos: (estado previo, filtro, tubo de entrada, tubo de salida, estado resultante)
ETAPAS = [
    ('corpus', 'enriquecimiento', 'noticia_ids', 'embedding_ids', 'enriquecido'),
    ('enriquecido', 'composicion', 'embedding_ids', 'prompt_ids', 'compuesto'),
    ('compuesto', 'invocacion', 'prompt_ids', 'respuesta_ids', 'invocado'),
    ('invocado', 'decodificacion', 'respuesta_ids', 'analisis_ids', 'decodificado'),
    ('decodificado', 'verificacion', 'analisis_ids', 'veredicto_ids', 'verificado'),
    ('verificado', 'normalizacion', 'veredicto_ids', 'resultado_id', 'consolidado'),
]

CRITERIO_VALIDEZ_DEFAULT = ('Cada afirmación del análisis debe estar sostenida por fragmentos literales de las '
                            'noticias suministradas como material empírico. Las categorías asignadas deben ser '
                            'consistentes con el contenido de la noticia.')


def _json_texto(valor):
    return json.dumps(valor, ensure_ascii=False, indent=2) if valor not in (None, False) else ''


class Estudio(models.Model):
    _name = 'iag.estudio'
    _description = 'Estudio (configuración del pipeline y corrida)'
    _order = 'id desc'

    name = fields.Char('Nombre', required=True)
    state = fields.Selection(ESTADOS, 'Estado', default='borrador', required=True, copy=False, index=True)
    iteracion_de_id = fields.Many2one('iag.estudio', 'Iteración de', readonly=True, copy=False, ondelete='set null')
    iteracion_ids = fields.One2many('iag.estudio', 'iteracion_de_id', 'Iteraciones')
    notas = fields.Text('Notas')

    # --- Selección del corpus (tubo 2->3; la Extracción es wsfnoticias) ---------------------
    campo_fecha = fields.Selection([('fecha_registro', 'Fecha de registro'), ('fecha_hora', 'Fecha de publicación')],
                                   'Filtrar fechas por', default='fecha_registro', required=True,
                                   help='Fecha de registro: cuándo el scraper incorporó la noticia al corpus (siempre existe). '
                                        'Fecha de publicación: la que declara el portal (puede faltar; en la vía RSS suele faltar).')
    fecha_desde = fields.Datetime('Desde', help='Vacío: sin cota inferior.')
    fecha_hasta = fields.Datetime('Hasta', help='Vacío: sin cota superior.')
    medio_ids = fields.Many2many('res.partner', 'iag_estudio_medio_rel', 'estudio_id', 'partner_id', 'Medios',
                                 help='Portales de origen (el contacto asociado a cada medio en wsfnoticias). Vacío: todos.')
    regla_ids = fields.Many2many('wsf_noticias_reglas', 'iag_estudio_regla_rel', 'estudio_id', 'regla_id',
                                 'Reglas de extracción',
                                 help='Reglas de extracción de wsfnoticias que seleccionaron la noticia (columna "Reglas '
                                      'aplicadas" de Noticias ▸ Resultados). Con varias, alcanza con que una coincida. '
                                      'Vacío: cualquier regla.')
    excluir_archivadas = fields.Boolean('Excluir archivadas', default=True,
                                        help='Deja afuera las noticias que un operador marcó "archivar" en wsfnoticias.')
    limite = fields.Integer('Límite de noticias', default=0,
                            help='Cantidad máxima, tomando las más recientes por fecha de registro. 0 = sin límite.')
    n_candidatas = fields.Integer('Cumplen los criterios ahora', compute='_compute_corpus_estado')
    corpus_estado = fields.Char('Estado del corpus', compute='_compute_corpus_estado')
    fecha_corpus = fields.Datetime('Corpus confirmado el', readonly=True, copy=False)
    noticia_excluida_ids = fields.Many2many('wsf_noticias_resultados', 'iag_estudio_noticia_excluida_rel', 'estudio_id',
                                            'noticia_id', 'Excluidas a mano', copy=False, readonly=True,
                                            help='Cumplían los criterios al confirmar pero el experto las quitó de la vista previa.')
    n_excluidas = fields.Integer('Excluidas a mano', compute='_compute_contadores')

    # --- Sintonizador de Composición de contexto -------------------------------------------
    template_id = fields.Many2one('iag.template', 'Template', ondelete='restrict')
    variable_ids = fields.One2many('iag.estudio.variable', 'estudio_id', 'Variables de contexto', copy=True,
                                   help='Una fila por variable declarada en el template; vacío = valor por defecto.')
    texto_libre = fields.Text('Aclaraciones (texto libre)',
                              help='Instrucciones adicionales propias de este estudio. Van al prompt después de las '
                                   'instrucciones del template y también integran la consulta con la que se recuperan las noticias.')
    temperatura = fields.Float('Temperatura (temperature)', default=0.2,
                               help='Aleatoriedad del modelo generativo: 0 = respuestas casi determinísticas; valores '
                                    'más altos, respuestas más variadas. Se envía con cada prompt.')
    longitud_salida = fields.Integer('Longitud máxima de la respuesta (max_tokens)', default=1024,
                                     help='Cantidad máxima de tokens que el modelo puede generar por respuesta.')
    estrategia = fields.Selection([('por_noticia', 'Un prompt por noticia (M = N)'),
                                   ('agregado', 'Un prompt agregado (M = 1)')],
                                  'Estrategia de composición', default='por_noticia', required=True,
                                  help='Por noticia: cada noticia recuperada se analiza sola, en su propio prompt. '
                                       'Agregado: todas las recuperadas van juntas en un único prompt.')
    top_k = fields.Integer('Noticias a recuperar (top-k)', default=0,
                           help='Cuántas noticias del corpus entran al análisis: las k más parecidas a la consulta '
                                '(el objetivo del template más las variables y las aclaraciones). Con "un prompt por '
                                'noticia" es la cantidad de prompts; con "agregado", cuántas noticias van en el único '
                                'prompt. 0 = todas las del corpus que superen el umbral.')
    umbral_similitud = fields.Float('Umbral de similitud (similarity threshold)', default=0.0,
                                    help='Similitud coseno mínima entre cada noticia y la consulta, entre 0 y 1. '
                                         'Las que no llegan quedan afuera del análisis. 0 = sin umbral.')

    # --- Sintonizador de Verificación ---------------------------------------------------------
    criterio_validez = fields.Text('Criterio de validez', default=CRITERIO_VALIDEZ_DEFAULT)
    ejecutor = fields.Selection([('llm_judge', 'Juez generativo (LLM-as-a-judge)'), ('humano', 'Revisor humano')],
                                'Ejecutor', default='llm_judge', required=True)

    # --- Sintonizador de Normalización --------------------------------------------------------
    politica_observados = fields.Selection([('incluir', 'Incluir todos los análisis'),
                                            ('excluir', 'Excluir los no sustentados'),
                                            ('solo_validos', 'Sólo los válidos')],
                                           'Política sobre los observados', default='incluir', required=True)
    agrupar_por = fields.Selection([('ninguno', 'Sin agrupar'), ('medio', 'Medio'),
                                    ('departamento', 'Departamento'), ('dia', 'Día')],
                                   'Agrupar por', default='medio', required=True)
    clave_ids = fields.One2many('iag.estudio.clave', 'estudio_id', 'Claves del esquema a consolidar', copy=True,
                                help='Una fila por clave del esquema de respuesta del template; tildá las que Normalización '
                                     'debe tabular. Las de opciones cerradas dan distribuciones; las numéricas, mínimo, media y máximo.')

    # --- Tubos (recordsets) -------------------------------------------------------------------
    noticia_ids = fields.Many2many('wsf_noticias_resultados', 'iag_estudio_noticia_rel', 'estudio_id', 'noticia_id',
                                   'Noticias (tubo 2→3)', copy=False)
    embedding_ids = fields.Many2many('iag.embedding', 'iag_estudio_embedding_rel', 'estudio_id', 'embedding_id',
                                     'Embeddings (tubo 4→5)', copy=False)
    prompt_ids = fields.Many2many('iag.prompt', 'iag_estudio_prompt_rel', 'estudio_id', 'prompt_id',
                                  'Prompts (tubo 6→7)', copy=False)
    respuesta_ids = fields.Many2many('iag.respuesta', 'iag_estudio_respuesta_rel', 'estudio_id', 'respuesta_id',
                                     'Respuestas (tubo 8→9)', copy=False)
    analisis_ids = fields.Many2many('iag.analisis', 'iag_estudio_analisis_rel', 'estudio_id', 'analisis_id',
                                    'Análisis (tubo 10→11)', copy=False)
    veredicto_ids = fields.Many2many('iag.veredicto', 'iag_estudio_veredicto_rel', 'estudio_id', 'veredicto_id',
                                     'Veredictos (tubo 12→13)', copy=False)
    resultado_id = fields.Many2one('iag.resultado', 'Resultado (puerto 14)', copy=False, ondelete='restrict')
    resultado_html = fields.Html(related='resultado_id.html')

    n_noticias = fields.Integer('N noticias', compute='_compute_contadores')
    n_embeddings = fields.Integer('N embeddings', compute='_compute_contadores')
    m_prompts = fields.Integer('M prompts', compute='_compute_contadores')
    m_respuestas = fields.Integer('M respuestas', compute='_compute_contadores')
    m_analisis = fields.Integer("M' análisis", compute='_compute_contadores')
    m_veredictos = fields.Integer("M' veredictos", compute='_compute_contadores')
    descartados_decodificacion = fields.Integer('Descartados por la compuerta', compute='_compute_contadores')
    analisis_pendientes = fields.Integer('Análisis sin veredicto', compute='_compute_contadores')

    # --- Corrida ------------------------------------------------------------------------------
    config_tecnica_snapshot = fields.Json('Configuración técnica de la corrida', copy=False)
    tiempos = fields.Json('Tiempos por filtro (s)', copy=False)
    informe = fields.Json('Informe de los filtros', copy=False)
    config_tecnica_texto = fields.Text('Configuración técnica', compute='_compute_textos')
    tiempos_texto = fields.Text('Tiempos', compute='_compute_textos')
    informe_texto = fields.Text('Informe', compute='_compute_textos')

    # ------------------------------------------------------------------------------------------
    @api.depends('noticia_ids', 'embedding_ids', 'prompt_ids', 'respuesta_ids', 'analisis_ids',
                 'veredicto_ids', 'analisis_ids.veredicto_ids')
    def _compute_contadores(self):
        for rec in self:
            rec.n_noticias = len(rec.noticia_ids)
            rec.n_embeddings = len(rec.embedding_ids)
            rec.m_prompts = len(rec.prompt_ids)
            rec.m_respuestas = len(rec.respuesta_ids)
            rec.m_analisis = len(rec.analisis_ids)
            rec.m_veredictos = len(rec.veredicto_ids)
            rec.descartados_decodificacion = max(len(rec.respuesta_ids) - len(rec.analisis_ids), 0)
            rec.analisis_pendientes = len(rec._analisis_pendientes())
            rec.n_excluidas = len(rec.noticia_excluida_ids)

    CRITERIOS_CORPUS = ('campo_fecha', 'fecha_desde', 'fecha_hasta', 'medio_ids', 'regla_ids', 'excluir_archivadas', 'limite')


    TOPE_VISTA_PREVIA = 500

    @api.depends(*(CRITERIOS_CORPUS + ('state', 'noticia_ids', 'noticia_excluida_ids', 'fecha_corpus')))
    def _compute_corpus_estado(self):
        for rec in self:
            if rec.state == 'borrador':
                candidatas = rec._buscar_candidatas(tope=self.TOPE_VISTA_PREVIA + 1)
                n = len(candidatas)
                rec.n_candidatas = n
                en_tabla = len(rec.noticia_ids)
                cuantas = _('más de %d') % self.TOPE_VISTA_PREVIA if n > self.TOPE_VISTA_PREVIA else str(n)
                if not n:
                    rec.corpus_estado = _('Ninguna noticia cumple los criterios ahora.')
                elif not en_tabla:
                    rec.corpus_estado = _('%s noticias cumplen los criterios ahora. Buscá para verlas en la tabla.') % cuantas
                elif not rec.limite and n > self.TOPE_VISTA_PREVIA:
                    rec.corpus_estado = _('Vista previa: %d noticias en la tabla (tope de %d); cumplen más. Ajustá los criterios o fijá un límite.') % (en_tabla, self.TOPE_VISTA_PREVIA)
                else:
                    rec.corpus_estado = _('Vista previa: %d noticias en la tabla, de %s que cumplen los criterios. Quitá las que no quieras y confirmá.') % (en_tabla, cuantas)
            else:
                rec.n_candidatas = len(rec.noticia_ids)
                fecha = fields.Datetime.context_timestamp(rec, rec.fecha_corpus).strftime('%d/%m/%Y %H:%M') if rec.fecha_corpus else '-'
                rec.corpus_estado = _('Corpus confirmado: %d noticias, el %s. Excluidas a mano: %d.') % (
                    len(rec.noticia_ids), fecha, len(rec.noticia_excluida_ids))

    def action_buscar_corpus(self):
        """Vista previa: llena la tabla con lo que cumple los criterios. Después el experto puede quitar o agregar
        noticias a mano antes de confirmar. Volver a buscar rehace la tabla desde los criterios."""
        for rec in self:
            if rec.state != 'borrador':
                raise UserError(_('La vista previa del corpus sólo se rehace en borrador.'))
            rec.noticia_ids = [(6, 0, rec._buscar_candidatas().ids)]
        return True

    @api.depends('config_tecnica_snapshot', 'tiempos', 'informe')
    def _compute_textos(self):
        for rec in self:
            rec.config_tecnica_texto = _json_texto(rec.config_tecnica_snapshot)
            rec.tiempos_texto = _json_texto(rec.tiempos)
            rec.informe_texto = _json_texto(rec.informe)

    # --- Variables de contexto: el template las declara, el Estudio les asigna valor -----------
    def _valores_variables(self):
        self.ensure_one()
        return {l.nombre: l.valor for l in self.variable_ids if l.nombre and (l.valor or '').strip()}

    def _sincronizar_variables(self, valores=None):
        """Reconstruye las filas a partir de las variables declaradas por el template, conservando
        por nombre los valores ya asignados (y aplicando `valores`, si se pasan)."""
        for rec in self:
            asignados = {l.nombre: l.valor for l in rec.variable_ids if l.valor}
            asignados.update({k: v for k, v in (valores or {}).items() if v not in (None, '')})
            comandos = [(5, 0, 0)]
            for i, v in enumerate(rec.template_id.variables or []):
                nombre = v.get('nombre')
                if not nombre:
                    continue
                comandos.append((0, 0, {
                    'sequence': i, 'nombre': nombre, 'descripcion': v.get('descripcion') or '',
                    'valor_default': str(v.get('default', '') or ''), 'valor': asignados.get(nombre) or False}))
            rec.variable_ids = comandos

    @staticmethod
    def _describir_clave(sub):
        tipo = sub.get('type')
        if 'enum' in sub:
            return _('opciones: %s') % ' | '.join(str(x) for x in sub['enum'])
        if tipo in ('number', 'integer'):
            return _('número')
        if tipo == 'boolean':
            return _('sí / no')
        if tipo == 'array':
            item = (sub.get('items') or {}).get('type')
            return _('lista de textos') if item == 'string' else _('lista de objetos')
        if tipo == 'object':
            return _('objeto')
        return _('texto libre')

    @staticmethod
    def _consolidable_por_defecto(sub):
        return 'enum' in sub or sub.get('type') in ('number', 'integer', 'boolean')

    def _sincronizar_claves(self, marcadas=None):
        """Reconstruye las filas de claves desde el esquema del template, conservando por nombre lo tildado
        (o aplicando `marcadas`). Por defecto se tildan las de opciones cerradas, numéricas y sí/no."""
        for rec in self:
            previas = {c.nombre: c.consolidar for c in rec.clave_ids}
            comandos = [(5, 0, 0)]
            props = (rec.template_id.esquema_respuesta or {}).get('properties') or {}
            for i, (nombre, sub) in enumerate(props.items()):
                sub = sub if isinstance(sub, dict) else {}
                if marcadas is not None:
                    consolidar = nombre in marcadas
                elif nombre in previas:
                    consolidar = previas[nombre]
                else:
                    consolidar = self._consolidable_por_defecto(sub)
                comandos.append((0, 0, {'sequence': i, 'nombre': nombre, 'tipo': self._describir_clave(sub),
                                        'consolidar': consolidar}))
            rec.clave_ids = comandos

    def _claves_a_consolidar(self):
        self.ensure_one()
        return [c.nombre for c in self.clave_ids if c.consolidar]

    @api.onchange('template_id')
    def _onchange_template_id(self):
        self._sincronizar_variables()
        self._sincronizar_claves()

    @api.model_create_multi
    def create(self, vals_list):
        valores = [vals.pop('valores_variables', None) for vals in vals_list]   # compatibilidad con scripts
        registros = super().create(vals_list)
        for rec, vals, val in zip(registros, vals_list, valores):
            if rec.template_id and (val or not rec.variable_ids):
                rec._sincronizar_variables(val)
            if rec.template_id and not rec.clave_ids:
                rec._sincronizar_claves()
        return registros

    def write(self, vals):
        valores = vals.pop('valores_variables', None)
        res = super().write(vals)
        if 'template_id' in vals or valores:
            self._sincronizar_variables(valores)
        if 'template_id' in vals:
            self._sincronizar_claves()
        return res

    # --- Sintonizadores: cada filtro recibe sólo sus claves -------------------------------------
    def _sintonizador(self, filtro):
        self.ensure_one()
        if filtro == 'composicion':
            return {
                'template': self.template_id,
                'valores_variables': self._valores_variables(),
                'texto_libre': self.texto_libre or '',
                'temperatura': self.temperatura,
                'longitud_salida': self.longitud_salida,
                'estrategia': self.estrategia,
                'top_k': self.top_k,
                'umbral_similitud': self.umbral_similitud,
            }
        if filtro == 'verificacion':
            return {'criterio_validez': self.criterio_validez or '', 'ejecutor': self.ejecutor}
        if filtro == 'normalizacion':
            return {
                'politica_observados': self.politica_observados,
                'agrupar_por': self.agrupar_por,
                'claves_comparacion': self._claves_a_consolidar(),
            }
        return {}

    # --- Corpus (sintonizador de Extracción: selección sobre lo que wsfnoticias extrajo) ------
    def _dominio_corpus(self):
        self.ensure_one()
        dominio = []
        if self.fecha_desde:
            dominio.append((self.campo_fecha, '>=', self.fecha_desde))
        if self.fecha_hasta:
            dominio.append((self.campo_fecha, '<=', self.fecha_hasta))
        if self.medio_ids:
            dominio.append(('medio', 'in', self.medio_ids.ids))
        nombres = [r.nombre_regla for r in self.regla_ids if r.nombre_regla]
        if nombres:
            dominio += ['|'] * (len(nombres) - 1) + [('regla2', 'ilike', n) for n in nombres]
        if self.excluir_archivadas:
            dominio.append(('valorar', '!=', 'archivar'))
        return dominio

    def _buscar_candidatas(self, tope=None):
        """Noticias que cumplen los criterios, más recientes primero, hasta `limite` (o el tope de vista previa).
        Con reglas elegidas la coincidencia es exacta por nombre dentro de "Reglas aplicadas" (el dominio sólo
        preselecciona por substring)."""
        self.ensure_one()
        tope = self.limite or tope or self.TOPE_VISTA_PREVIA
        Noticia = self.env['wsf_noticias_resultados']
        nombres = {r.nombre_regla.strip().lower() for r in self.regla_ids if r.nombre_regla}
        if not nombres:
            return Noticia.search(self._dominio_corpus(), order='fecha_registro desc, id desc', limit=tope)
        candidatas = Noticia.search(self._dominio_corpus(), order='fecha_registro desc, id desc')
        exactas = candidatas.filtered(
            lambda n: nombres & {x.strip().lower() for x in (n.regla2 or '').split(',')})
        return exactas[:tope]

    def action_seleccionar_corpus(self):
        """Confirma el corpus: congela lo que está en la tabla (vista previa curada a mano) como el recordset
        Noticia[N] del tubo 2→3. Si la tabla está vacía, la llena con los criterios. Lo que cumplía los criterios
        y no quedó en la tabla se registra como excluido a mano."""
        for rec in self:
            if rec.state != 'borrador':
                raise UserError(_('El corpus sólo se confirma en borrador.'))
            candidatas = rec._buscar_candidatas()
            noticias = rec.noticia_ids or candidatas
            if not noticias:
                raise UserError(_('Ninguna noticia de wsf_noticias_resultados cumple los criterios del corpus.'))
            rec.write({
                'noticia_ids': [(6, 0, noticias.ids)],
                'noticia_excluida_ids': [(6, 0, (candidatas - noticias).ids)],
                'fecha_corpus': fields.Datetime.now(),
                'state': 'corpus',
            })
        return True

    # --- Runner ---------------------------------------------------------------------------------
    def _proxima_etapa(self):
        self.ensure_one()
        for etapa in ETAPAS:
            if etapa[0] == self.state:
                return etapa
        return None

    def action_ejecutar_siguiente(self):
        for rec in self:
            if rec.state == 'borrador':
                rec.action_seleccionar_corpus()
                continue
            etapa = rec._proxima_etapa()
            if not etapa:
                raise UserError(_('No hay una etapa siguiente en el estado "%s".') % dict(ESTADOS)[rec.state])
            rec._ejecutar_etapa(etapa)
        return True

    def action_ejecutar_todo(self):
        for rec in self:
            guarda = 0
            while rec.state not in ('consolidado', 'en_verificacion') and guarda < len(ETAPAS) + 2:
                rec.action_ejecutar_siguiente()
                guarda += 1
        return True

    def _ejecutar_etapa(self, etapa):
        self.ensure_one()
        _estado_previo, filtro, entrada, salida, estado_final = etapa
        if not self.config_tecnica_snapshot:
            self.config_tecnica_snapshot = self.env['iag.proveedor']._snapshot()
        servicio = self.env['iag.filtro.%s' % filtro]
        informe = {}
        inicio = time.time()
        emitido = servicio.ejecutar(self[entrada], self._sintonizador(filtro), informe)
        tiempos = dict(self.tiempos or {})
        tiempos[filtro] = round(time.time() - inicio, 3)
        informes = dict(self.informe or {})
        if informe:
            informes[filtro] = informe
        vals = {'tiempos': tiempos, 'informe': informes}
        if filtro == 'verificacion' and not emitido and self.ejecutor == 'humano':
            vals['state'] = 'en_verificacion'
        else:
            es_m2o = self._fields[salida].type == 'many2one'
            vals[salida] = (emitido.id if emitido else False) if es_m2o else [(6, 0, emitido.ids)]
            vals['state'] = estado_final
        self.write(vals)

    # --- Verificación con revisor humano ---------------------------------------------------------
    def _analisis_pendientes(self):
        self.ensure_one()
        return self.analisis_ids.filtered(lambda a: not a.veredicto_ids)

    def action_continuar_verificacion(self):
        for rec in self:
            if rec.state != 'en_verificacion':
                raise UserError(_('El estudio no está en verificación.'))
            pendientes = rec._analisis_pendientes()
            if pendientes:
                raise UserError(_('Faltan veredictos para %d análisis.') % len(pendientes))
            Veredicto = self.env['iag.veredicto']
            veredictos = Veredicto
            for analisis in rec.analisis_ids:
                veredictos |= Veredicto.search([('analisis_id', '=', analisis.id)], order='id desc', limit=1)
            rec.write({'veredicto_ids': [(6, 0, veredictos.ids)], 'state': 'verificado'})
        return True

    # --- Iteración y reinicio -----------------------------------------------------------------------
    def action_nueva_iteracion(self):
        self.ensure_one()
        nuevo = self.copy({'name': _('%s (iteración %d)') % (self.name, len(self.iteracion_ids) + 1),
                           'iteracion_de_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'iag.estudio', 'res_id': nuevo.id,
                'view_mode': 'form', 'target': 'current'}

    def action_volver_a_borrador(self):
        """Desvincula los recordsets de la corrida. Los registros permanecen en el repositorio."""
        self.write({
            'noticia_ids': [(5, 0, 0)], 'noticia_excluida_ids': [(5, 0, 0)], 'fecha_corpus': False,
            'embedding_ids': [(5, 0, 0)], 'prompt_ids': [(5, 0, 0)],
            'respuesta_ids': [(5, 0, 0)], 'analisis_ids': [(5, 0, 0)], 'veredicto_ids': [(5, 0, 0)],
            'resultado_id': False, 'config_tecnica_snapshot': False, 'tiempos': False, 'informe': False,
            'state': 'borrador',
        })
        for rec in self:   # vuelve a mostrar la vista previa según los criterios vigentes
            rec.noticia_ids = [(6, 0, rec._buscar_candidatas().ids)]
        return True

    def action_ver_excluidas(self):
        return self._abrir_noticias(self.noticia_excluida_ids.ids, _('Excluidas a mano'))

    # --- Navegación (smart buttons) -------------------------------------------------------------------
    def _abrir(self, modelo, ids, nombre):
        return {'type': 'ir.actions.act_window', 'name': nombre, 'res_model': modelo, 'view_mode': 'tree,form',
                'domain': [('id', 'in', ids)], 'context': {'create': False}}

    def _abrir_noticias(self, ids, nombre):
        """Lista simple de noticias (sin la barra de valoración de wsfnoticias)."""
        return {'type': 'ir.actions.act_window', 'name': nombre, 'res_model': 'wsf_noticias_resultados',
                'domain': [('id', 'in', ids)], 'context': {'create': False},
                'views': [(self.env.ref('noticias_iag.view_noticia_tree_simple').id, 'tree'), (False, 'form')]}

    def action_ver_noticias(self):
        return self._abrir_noticias(self.noticia_ids.ids, _('Noticias del corpus'))

    def action_ver_embeddings(self):
        return self._abrir('iag.embedding', self.embedding_ids.ids, _('Embeddings'))

    def action_ver_prompts(self):
        return self._abrir('iag.prompt', self.prompt_ids.ids, _('Prompts'))

    def action_ver_respuestas(self):
        return self._abrir('iag.respuesta', self.respuesta_ids.ids, _('Respuestas'))

    def action_ver_analisis(self):
        return self._abrir('iag.analisis', self.analisis_ids.ids, _('Análisis'))

    def action_ver_analisis_pendientes(self):
        return self._abrir('iag.analisis', self._analisis_pendientes().ids, _('Análisis sin veredicto'))

    def action_ver_veredictos(self):
        return self._abrir('iag.veredicto', self.veredicto_ids.ids, _('Veredictos'))

    def action_ver_resultado(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Resultado'), 'res_model': 'iag.resultado',
                'res_id': self.resultado_id.id, 'view_mode': 'form', 'target': 'current'}


class EstudioVariable(models.Model):
    """Una fila por variable de contexto declarada por el template del Estudio. La declaración
    (nombre, descripción, default) es del template; el valor es del Estudio."""
    _name = 'iag.estudio.variable'
    _description = 'Variable de contexto del estudio'
    _order = 'sequence, id'
    _rec_name = 'nombre'

    estudio_id = fields.Many2one('iag.estudio', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=0)
    nombre = fields.Char('Variable', required=True, readonly=True)
    descripcion = fields.Char('Descripción', readonly=True)
    valor_default = fields.Char('Valor por defecto', readonly=True)
    valor = fields.Char('Valor en este estudio', help='Vacío: se usa el valor por defecto del template.')
    valor_efectivo = fields.Char('Valor efectivo', compute='_compute_valor_efectivo')

    @api.depends('valor', 'valor_default')
    def _compute_valor_efectivo(self):
        for rec in self:
            rec.valor_efectivo = (rec.valor or '').strip() or rec.valor_default or ''

    @api.model_create_multi
    def create(self, vals_list):
        # Red de seguridad: si un cliente manda la fila sin la declaración (nombre/descripción/default),
        # se completa desde el template del estudio por posición.
        for vals in vals_list:
            if vals.get('nombre') or not vals.get('estudio_id'):
                continue
            estudio = self.env['iag.estudio'].browse(vals['estudio_id'])
            declaradas = [v for v in (estudio.template_id.variables or []) if v.get('nombre')]
            idx = vals.get('sequence', 0) or 0
            if 0 <= idx < len(declaradas):
                v = declaradas[idx]
                vals.update({'nombre': v['nombre'], 'descripcion': v.get('descripcion') or '',
                             'valor_default': str(v.get('default', '') or '')})
        return super().create(vals_list)


class EstudioClave(models.Model):
    """Una fila por clave del esquema de respuesta del template; el analista tilda cuáles consolidar."""
    _name = 'iag.estudio.clave'
    _description = 'Clave del esquema a consolidar'
    _order = 'sequence, id'
    _rec_name = 'nombre'

    estudio_id = fields.Many2one('iag.estudio', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=0)
    nombre = fields.Char('Clave', required=True, readonly=True)
    tipo = fields.Char('Valores', readonly=True)
    consolidar = fields.Boolean('Consolidar', default=True)

    @api.model_create_multi
    def create(self, vals_list):
        # Red de seguridad: si el cliente manda la fila sin la declaración, se completa desde el template por posición.
        for vals in vals_list:
            if vals.get('nombre') or not vals.get('estudio_id'):
                continue
            estudio = self.env['iag.estudio'].browse(vals['estudio_id'])
            props = list(((estudio.template_id.esquema_respuesta or {}).get('properties') or {}).items())
            idx = vals.get('sequence', 0) or 0
            if 0 <= idx < len(props):
                nombre, sub = props[idx]
                vals.update({'nombre': nombre, 'tipo': estudio._describir_clave(sub if isinstance(sub, dict) else {})})
        return super().create(vals_list)
