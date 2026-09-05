# -*- coding: utf-8 -*-
"""Fase 2 del plan: el pipeline completo con el proveedor simulado.

Verifica cardinalidades N→N→M→M→M′→M′→1, la cadena de procedencia, la compuerta de
Decodificación, el ejecutor humano, la monotonía y la política de Normalización.
"""
from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from ..models.estudio import ETAPAS
from ..services import esquema as esq

TITULOS = [
    'El intendente anunció obras en la zona sur de Rosario',
    'Aumentó el boleto de colectivo en Rosario',
    'Detuvieron a tres personas por narcomenudeo en Santa Fe',
    'Rosario Central ganó el clásico',
    'Lanzan un programa de becas para estudiantes secundarios',
    'Alerta por crecida del río Paraná',
]


@tagged('post_install', '-at_install', 'noticias_iag')
class TestPipeline(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('noticias_iag.proveedor', 'fake')
        Partner = cls.env['res.partner']
        cls.medio_a = Partner.create({'name': 'Portal A (test)'})
        cls.medio_b = Partner.create({'name': 'Portal B (test)'})
        ahora = datetime.now()
        vals = []
        for i, titulo in enumerate(TITULOS):
            medio = cls.medio_a if i % 2 == 0 else cls.medio_b
            vals.append({
                'titulo': titulo, 'copete': 'Copete %d' % i, 'texto': ('%s. ' % titulo) * 5,
                'link': 'https://test.local/noticia/%d' % i, 'medio': medio.id,
                'departamento': 'Rosario' if medio == cls.medio_a else 'La Capital',
                'fecha_hora': ahora - timedelta(days=i), 'fecha_registro': ahora - timedelta(hours=i),
                'regla2': 'test',
            })
        cls.regla_test = cls.env['wsf_noticias_reglas'].create({'nombre_regla': 'test', 'estado': 'on'})
        cls.regla_otra = cls.env['wsf_noticias_reglas'].create({'nombre_regla': 'otra-regla', 'estado': 'on'})
        cls.regla_tes = cls.env['wsf_noticias_reglas'].create({'nombre_regla': 'tes', 'estado': 'on'})
        cls.noticias = cls.env['wsf_noticias_resultados'].create(vals)
        cls.N = len(cls.noticias)
        cls.template = cls.env.ref('noticias_iag.template_agenda_setting')

    def _estudio(self, **extra):
        vals = {'name': 'Estudio de prueba', 'medio_ids': [(6, 0, (self.medio_a | self.medio_b).ids)],
                'template_id': self.template.id, 'estrategia': 'por_noticia', 'ejecutor': 'llm_judge'}
        vals.update(extra)
        return self.env['iag.estudio'].create(vals)

    # --- Fase 1: contratos --------------------------------------------------------------------
    def test_01_template_rechaza_esquemas_invalidos(self):
        Template = self.env['iag.template']
        with self.assertRaises(ValidationError):
            Template.create({'name': 'inválido', 'objetivo': 'x', 'instrucciones': 'x',
                             'esquema_respuesta': {'type': 'object', 'properties': {'a': {'type': 'foo'}}}})
        with self.assertRaises(ValidationError):
            Template.create({'name': 'inválido 2', 'objetivo': 'x', 'instrucciones': 'x',
                             'esquema_texto': '{no es json'})
        with self.assertRaises(ValidationError):
            Template.create({'name': 'inválido 3', 'objetivo': 'x', 'instrucciones': 'x',
                             'esquema_respuesta': {'type': 'object', 'required': ['b'], 'properties': {'a': {'type': 'string'}}}})
        valido = Template.create({'name': 'válido', 'objetivo': 'x', 'instrucciones': 'Analizá {{tema}}',
                                  'esquema_texto': '{"type": "object", "required": ["r"], "properties": {"r": {"type": "string"}}}'})
        self.assertEqual(valido.esquema_respuesta['required'], ['r'])
        with self.assertRaises(UserError):
            valido.renderizar_instrucciones({})
        self.assertEqual(valido.renderizar_instrucciones({'tema': 'seguridad'}), 'Analizá seguridad')

    def test_02_validador_de_esquema(self):
        esquema = {'type': 'object', 'required': ['tono', 'n'], 'properties': {
            'tono': {'type': 'string', 'enum': ['Positivo', 'Negativo']},
            'n': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'ok': {'type': 'boolean'}}}
        canon, errores = esq.canonicalizar({'tono': ' positivo ', 'n': '0,5', 'ok': 'sí'}, esquema)
        self.assertEqual(errores, [])
        self.assertEqual(canon, {'tono': 'Positivo', 'n': 0.5, 'ok': True})
        _canon, errores = esq.canonicalizar({'tono': 'neutro', 'n': 3}, esquema)
        self.assertEqual(len(errores), 2)
        obj, error = esq.aislar_json('Claro:\n```json\n{"a": 1}\n```\nListo.')
        self.assertIsNone(error)
        self.assertEqual(obj, {'a': 1})
        self.assertIsNotNone(esq.aislar_json('sin json')[1])

    # --- Fase 2: pipeline --------------------------------------------------------------------
    def test_03_cardinalidades_por_noticia(self):
        est = self._estudio()
        est.action_ejecutar_todo()
        self.assertEqual(est.state, 'consolidado')
        for tubo in ('noticia_ids', 'embedding_ids', 'prompt_ids', 'respuesta_ids', 'analisis_ids', 'veredicto_ids'):
            self.assertEqual(len(est[tubo]), self.N, tubo)
        self.assertTrue(est.resultado_id)
        self.assertEqual(est.descartados_decodificacion, 0)
        self.assertTrue(all(len(p.noticia_ids) == 1 for p in est.prompt_ids))
        # el juez generativo también deja su sustento como filas y como Json
        v = est.veredicto_ids[0]
        self.assertTrue(v.sustento_ids)
        self.assertEqual(len(v.sustento), len(v.sustento_ids))
        self.assertIn(v.sustento_ids[0].relacion, ('respalda', 'contradice', 'sin_evidencia'))
        requeridas = set(self.template.esquema_respuesta['required'])
        for a in est.analisis_ids:
            self.assertTrue(requeridas <= set(a.contenido), a.contenido)
        self.assertEqual(set(est.resultado_id.veredicto_ids.ids), set(est.veredicto_ids.ids))
        self.assertEqual(est.config_tecnica_snapshot['proveedor'], 'fake')
        self.assertNotIn('api_key', est.config_tecnica_snapshot)
        self.assertEqual(set(est.tiempos), {e[1] for e in ETAPAS})

    def test_04_estrategia_agregado(self):
        est = self._estudio(estrategia='agregado', template_id=self.env.ref('noticias_iag.template_resumen_agregado').id)
        est.action_ejecutar_todo()
        self.assertEqual(est.state, 'consolidado')
        self.assertEqual(len(est.embedding_ids), self.N)
        self.assertEqual(len(est.prompt_ids), 1)
        self.assertEqual(set(est.prompt_ids.noticia_ids.ids), set(self.noticias.ids))
        self.assertEqual(len(est.analisis_ids), 1)
        self.assertIn('temas', est.analisis_ids.contenido)

    def test_05_recuperacion_top_k(self):
        est = self._estudio(top_k=2)
        est.action_ejecutar_todo()
        self.assertEqual(len(est.embedding_ids), self.N)
        self.assertEqual(len(est.prompt_ids), 2)
        self.assertEqual(len(est.informe['composicion']['similitudes']), 2)

    def test_06_procedencia_resultado_a_noticia(self):
        est = self._estudio()
        est.action_ejecutar_todo()
        res = est.resultado_id
        cadena = res.veredicto_ids.mapped('analisis_id.respuesta_id.prompt_id')
        self.assertEqual(set(cadena.mapped('noticia_ids').ids), set(self.noticias.ids))
        self.assertEqual(cadena.mapped('template_id'), self.template)
        for v in res.veredicto_ids:
            self.assertEqual(v.analisis_id.respuesta_id.prompt_id.template_id, self.template)
            self.assertEqual(len(v.analisis_id.noticia_ids), 1)

    def test_07_compuerta_decodificacion(self):
        est = self._estudio().with_context(iag_fake_basura_si_contiene='Rosario Central')
        est.action_ejecutar_todo()
        self.assertEqual(len(est.respuesta_ids), self.N)
        self.assertEqual(len(est.analisis_ids), self.N - 1)
        self.assertEqual(est.descartados_decodificacion, 1)
        self.assertEqual(len(est.veredicto_ids), self.N - 1)
        descartes = est.informe['decodificacion']['descartes']
        self.assertEqual(len(descartes), 1)
        respuesta = self.env['iag.respuesta'].browse(descartes[0]['respuesta_id'])
        self.assertIn('Rosario Central', respuesta.prompt_id.texto)
        self.assertEqual(est.state, 'consolidado')
        self.assertEqual(est.resultado_id.contenido['resumen']['m_analisis'], self.N - 1)

    def test_08_ejecutor_humano(self):
        est = self._estudio(ejecutor='humano')
        est.action_ejecutar_todo()
        self.assertEqual(est.state, 'en_verificacion')
        self.assertEqual(len(est.veredicto_ids), 0)
        self.assertEqual(est.analisis_pendientes, self.N)
        with self.assertRaises(UserError):
            est.action_continuar_verificacion()
        for i, a in enumerate(est.analisis_ids):
            if i == 0:
                self.env['iag.veredicto'].create({
                    'analisis_id': a.id, 'estado': 'valido', 'confianza': 0.9,
                    'sustento_ids': [(0, 0, {'afirmacion': 'x', 'relacion': 'respalda', 'fragmento': 'x'})]})
            else:
                self.env['iag.veredicto'].create({
                    'analisis_id': a.id, 'estado': 'valido', 'confianza': 0.9,
                    'sustento_texto': '[{"afirmacion": "x", "relacion": "respalda", "fragmento": "x"}]'})
        self.assertEqual(est.analisis_pendientes, 0)
        est.action_continuar_verificacion()
        self.assertEqual(est.state, 'verificado')
        self.assertEqual(len(est.veredicto_ids), self.N)
        v = est.veredicto_ids[0]
        self.assertEqual(v.sustento[0]['relacion'], 'respalda')          # Json del contrato, computado de las filas
        self.assertEqual(v.sustento_ids.mapped('afirmacion'), ['x'])
        self.assertEqual(v.estudio_id, est)
        self.assertEqual(v.criterio_validez, est.criterio_validez)       # el revisor ve el criterio del estudio
        with self.assertRaises(UserError):
            v.sustento_ids.unlink()
        est.action_ejecutar_siguiente()
        self.assertEqual(est.state, 'consolidado')
        self.assertEqual(est.resultado_id.contenido['resumen']['veredictos_por_estado'], {'valido': self.N})

    def test_09_monotonia_del_repositorio(self):
        est = self._estudio()
        est.action_ejecutar_todo()
        with self.assertRaises(UserError):
            est.embedding_ids[0].unlink()
        with self.assertRaises(UserError):
            est.resultado_id.unlink()
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.noticias[0].unlink()
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.template.unlink()

    def test_10_normalizacion_politica(self):
        est = self._estudio(politica_observados='solo_validos', agrupar_por='medio')
        est.action_ejecutar_todo()
        validos = est.veredicto_ids.filtered(lambda v: v.estado == 'valido')
        contenido = est.resultado_id.contenido
        self.assertEqual(contenido['resumen']['incluidos'], len(validos))
        self.assertEqual(contenido['resumen']['m_veredictos'], self.N)
        self.assertEqual(len(est.resultado_id.veredicto_ids), self.N)
        self.assertIn('tema', contenido['politica_aplicada']['claves_comparacion'])
        if validos:
            self.assertEqual(sum(contenido['distribuciones']['tono'].values()), len(validos))
            self.assertTrue(set(contenido['por_grupo']) <= {'Portal A (test)', 'Portal B (test)'})
        self.assertIn('<h3>', est.resultado_id.html)

    def test_11_nueva_iteracion(self):
        est = self._estudio()
        est.action_ejecutar_todo()
        accion = est.action_nueva_iteracion()
        nuevo = self.env['iag.estudio'].browse(accion['res_id'])
        self.assertEqual(nuevo.state, 'borrador')
        self.assertEqual(nuevo.iteracion_de_id, est)
        self.assertEqual(nuevo.template_id, est.template_id)
        self.assertEqual(set(nuevo.medio_ids.ids), set(est.medio_ids.ids))
        self.assertEqual(nuevo.variable_ids.mapped('nombre'), est.variable_ids.mapped('nombre'))
        self.assertFalse(nuevo.noticia_ids)
        self.assertFalse(nuevo.resultado_id)
        nuevo.action_ejecutar_todo()
        self.assertEqual(nuevo.state, 'consolidado')
        self.assertNotEqual(nuevo.resultado_id, est.resultado_id)

    # --- Mitigación de respuestas truncadas ---------------------------------------------------
    TRUNCADO = ('```json\n{"tema": "política", "tono": "neutro", "actores": ["A"], "encuadre": "ninguno", '
                '"resumen": "x", "afirmaciones": [{"afirmacion": "a", "fragmento": "b"')

    def test_12_reparar_json_truncado(self):
        obj, desc = esq.reparar_json(self.TRUNCADO)
        self.assertEqual(obj['afirmaciones'], [{'afirmacion': 'a', 'fragmento': 'b'}])
        self.assertIn("se agregó", desc)
        # sin la llave final (el caso observado con qwen2.5:3b)
        obj, _d = esq.reparar_json('{"a": 1, "b": [1, 2]\n')
        self.assertEqual(obj, {'a': 1, 'b': [1, 2]})
        # string y clave cortadas: se quita el miembro incompleto
        obj, desc = esq.reparar_json('{"a": 1, "b": "texto cor')
        self.assertEqual(obj, {'a': 1})
        self.assertIn('incompleto', desc)
        obj, desc = esq.reparar_json('{"a": [1, 2], "afirm')
        self.assertEqual(obj, {'a': [1, 2]})
        # número cortado y coma colgante
        self.assertEqual(esq.reparar_json('{"a": 1, "n": 0.')[0], {'a': 1})
        self.assertEqual(esq.reparar_json('[1, 2,')[0], [1, 2])
        self.assertEqual(esq.reparar_json('sin json'), (None, None))
        # lo que ya es válido no se toca por la vía normal
        self.assertIsNone(esq.aislar_json('{"a": 1}')[1])

    def _hasta(self, est, estado):
        while est.state != estado:
            est.action_ejecutar_siguiente()

    def test_13_politica_reparar_vs_descartar(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('noticias_iag.politica_no_conformidad', 'reparar')
        est = self._estudio().with_context(iag_fake_respuesta=self.TRUNCADO)
        self._hasta(est, 'decodificado')
        self.assertEqual(len(est.analisis_ids), self.N)
        self.assertEqual(est.descartados_decodificacion, 0)
        self.assertEqual(len(est.informe['decodificacion']['reparados']), self.N)
        self.assertEqual(est.analisis_ids[0].contenido['tema'], 'política')
        icp.set_param('noticias_iag.politica_no_conformidad', 'descartar')
        est2 = self._estudio().with_context(iag_fake_respuesta=self.TRUNCADO)
        self._hasta(est2, 'decodificado')
        self.assertEqual(len(est2.analisis_ids), 0)
        self.assertEqual(est2.descartados_decodificacion, self.N)
        self.assertEqual(est2.informe['decodificacion']['politica'], 'descartar')

    def test_14_reparacion_no_salva_lo_no_conforme(self):
        self.env['ir.config_parameter'].sudo().set_param('noticias_iag.politica_no_conformidad', 'reparar')
        truncado_invalido = '{"tema": "política", "tono": "neutro", "actores": ["A"], "encuadre": "ninguno"'  # falta resumen
        est = self._estudio().with_context(iag_fake_respuesta=truncado_invalido)
        self._hasta(est, 'decodificado')
        self.assertEqual(len(est.analisis_ids), 0)
        descartes = est.informe['decodificacion']['descartes']
        self.assertEqual(len(descartes), self.N)
        self.assertIn('reparado', descartes[0]['motivo'])
        self.assertIn('resumen', descartes[0]['motivo'])

    def test_15_previsualizacion_del_template(self):
        prev = self.template.previsualizacion
        self.assertIn('# Objetivo del análisis', prev)
        self.assertIn(self.template.objetivo[:40], prev)
        self.assertIn('agenda mediática regional', prev)          # variable {{estudio}} con su default
        self.assertNotIn('{{', prev)
        self.assertIn('# Material empírico', prev)
        self.assertIn('[Aquí Composición de contexto inserta', prev)
        self.assertIn('### Esquema de respuesta (JSON Schema)', prev)
        self.assertIn('"relevancia_regional"', prev)
        # el prompt real de Composición comparte encabezado y formato con la previsualización
        est = self._estudio(limite=1)
        self._hasta(est, 'compuesto')
        texto = est.prompt_ids.texto
        cabecera = prev.split('# Material empírico')[0]
        self.assertTrue(texto.startswith(cabecera))
        self.assertEqual(texto.split('### Esquema de respuesta')[1], prev.split('### Esquema de respuesta')[1])
        self.assertNotIn('[Aquí Composición', texto)
        self.assertIn(est.noticia_ids.titulo, texto)

    def test_16_variables_del_template_en_el_estudio(self):
        est = self._estudio()
        # al crear con template, las filas nacen de la declaración del template, sin valor asignado
        self.assertEqual(est.variable_ids.mapped('nombre'), ['estudio', 'ambito'])
        self.assertEqual(est.variable_ids[0].valor_default, 'agenda mediática regional')
        self.assertFalse(est.variable_ids[0].valor)
        self.assertEqual(est._valores_variables(), {})
        # el experto asigna un valor; el otro queda en su default
        est.variable_ids.filtered(lambda l: l.nombre == 'estudio').valor = 'elecciones 2027'
        self.assertEqual(est._valores_variables(), {'estudio': 'elecciones 2027'})
        self._hasta(est, 'compuesto')
        texto = est.prompt_ids[0].texto
        self.assertIn('"elecciones 2027"', texto)
        self.assertIn('Rosario y la provincia de Santa Fe', texto)
        # cambiar de template reconstruye las filas conservando por nombre lo asignado
        otro = self.env['iag.template'].create({
            'name': 'otro', 'objetivo': 'x', 'instrucciones': 'Estudio {{estudio}} sobre {{tema}}',
            'variables': [{'nombre': 'estudio', 'default': 'e'}, {'nombre': 'tema', 'default': 't'}],
            'esquema_respuesta': {'type': 'object', 'required': ['r'], 'properties': {'r': {'type': 'string'}}}})
        est2 = self._estudio()
        est2.variable_ids.filtered(lambda l: l.nombre == 'estudio').valor = 'conservado'
        est2.write({'template_id': otro.id})
        self.assertEqual(est2.variable_ids.mapped('nombre'), ['estudio', 'tema'])
        self.assertEqual(est2._valores_variables(), {'estudio': 'conservado'})
        # compatibilidad: valores_variables en create() se vuelca a las filas
        est3 = self._estudio(valores_variables={'ambito': 'Santa Fe capital'})
        self.assertEqual(est3._valores_variables(), {'ambito': 'Santa Fe capital'})

    def test_17_vista_previa_y_curacion_del_corpus(self):
        Estudio = self.env['iag.estudio']
        # vista previa: Buscar llena la tabla con lo que cumple los criterios
        est = Estudio.new({'name': 'preview', 'medio_ids': [(6, 0, [self.medio_a.id])], 'template_id': self.template.id})
        est.action_buscar_corpus()
        self.assertEqual(set(est.noticia_ids.ids), set(self.noticias.filtered(lambda n: n.medio == self.medio_a).ids))
        est.medio_ids = self.medio_a | self.medio_b
        est.action_buscar_corpus()
        self.assertEqual(len(est.noticia_ids), self.N)
        self.assertEqual(est.n_candidatas, self.N)
        self.assertIn('Vista previa', est.corpus_estado)
        # curación a mano: el experto quita una; al confirmar, queda registrada como excluida
        est = self._estudio()
        self.assertIn('Buscá', est.corpus_estado)
        est.action_buscar_corpus()
        quitada = est.noticia_ids[0]
        est.noticia_ids = [(3, quitada.id, 0)]
        est.action_seleccionar_corpus()
        self.assertEqual(est.state, 'corpus')
        self.assertEqual(len(est.noticia_ids), self.N - 1)
        self.assertEqual(est.noticia_excluida_ids, quitada)
        self.assertTrue(est.fecha_corpus)
        self.assertIn('Corpus confirmado', est.corpus_estado)
        est.action_ejecutar_todo()
        self.assertEqual(len(est.veredicto_ids), self.N - 1)
        self.assertNotIn(quitada, est.prompt_ids.mapped('noticia_ids'))
        # volver a borrador desvincula todo y rehace la vista previa
        est.action_volver_a_borrador()
        self.assertEqual(est.state, 'borrador')
        self.assertFalse(est.noticia_excluida_ids)
        self.assertEqual(len(est.noticia_ids), self.N)
        # límite: la vista previa y el corpus respetan el tope
        est2 = self._estudio(limite=2)
        est2.action_buscar_corpus()
        self.assertEqual(len(est2.noticia_ids), 2)
        est2.action_seleccionar_corpus()
        self.assertEqual(len(est2.noticia_ids), 2)
        self.assertFalse(est2.noticia_excluida_ids)

    def test_18_filtro_por_regla_de_extraccion(self):
        est = self._estudio(regla_ids=[(6, 0, [self.regla_test.id])])
        est.action_buscar_corpus()
        self.assertEqual(len(est.noticia_ids), self.N)
        # una regla que ninguna noticia lista no trae nada
        est.write({'regla_ids': [(6, 0, [self.regla_otra.id])]})
        est.action_buscar_corpus()
        self.assertEqual(len(est.noticia_ids), 0)
        # coincidencia exacta por nombre: 'tes' no se lleva a 'test'
        est.write({'regla_ids': [(6, 0, [self.regla_tes.id])]})
        est.action_buscar_corpus()
        self.assertEqual(len(est.noticia_ids), 0)
        self.assertEqual(est.n_candidatas, 0)
        # varias reglas: alcanza con una
        est.write({'regla_ids': [(6, 0, [self.regla_tes.id, self.regla_test.id])]})
        est.action_buscar_corpus()
        self.assertEqual(len(est.noticia_ids), self.N)

    def test_19_formulario_del_revisor(self):
        est = self._estudio(ejecutor='humano')
        est.action_ejecutar_todo()
        Veredicto = self.env['iag.veredicto']
        a = est.analisis_ids[0]
        # el formulario abre con el sustento precargado desde las afirmaciones del análisis, sin relación decidida
        defaults = Veredicto.with_context(default_analisis_id=a.id).default_get(['analisis_id', 'sustento_ids'])
        self.assertEqual(defaults['analisis_id'], a.id)
        filas = [c[2] for c in defaults['sustento_ids']]
        self.assertEqual(len(filas), len(a.contenido['afirmaciones']))
        self.assertEqual(filas[0]['afirmacion'], a.contenido['afirmaciones'][0]['afirmacion'])
        self.assertEqual(filas[0]['link'], a.noticia_ids.link)
        self.assertNotIn('relacion', filas[0])
        # emitir lleva al siguiente pendiente; el último vuelve al estudio
        for i, an in enumerate(est.analisis_ids):
            v = Veredicto.create({'analisis_id': an.id, 'estado': 'valido', 'confianza': 1.0,
                                  'sustento_ids': [(0, 0, {'afirmacion': 'a', 'relacion': 'respalda'})]})
            accion = v.action_emitir()
            if i < self.N - 1:
                self.assertEqual(accion['res_model'], 'iag.veredicto')
                self.assertIn(accion['context']['default_analisis_id'], est._analisis_pendientes().ids)
            else:
                self.assertEqual(accion['res_model'], 'iag.estudio')
                self.assertEqual(accion['res_id'], est.id)
        self.assertEqual(est.analisis_pendientes, 0)

    def test_20_claves_a_consolidar_como_filas(self):
        est = self._estudio()
        # filas desde el esquema del template, en el orden del autor (el de `required`; jsonb no conserva el de
        # `properties`); por defecto se tildan opciones cerradas y numéricas
        esquema = self.template.esquema_respuesta
        self.assertEqual(est.clave_ids.mapped('nombre'), self.env['iag.template']._orden_claves(esquema))
        self.assertEqual(est.clave_ids.mapped('nombre')[:len(esquema['required'])], esquema['required'])
        self.assertEqual(set(est.clave_ids.mapped('nombre')), set(esquema['properties']))
        tildadas = est.clave_ids.filtered('consolidar').mapped('nombre')
        self.assertEqual(set(tildadas), {'tema', 'tono', 'encuadre', 'relevancia_regional'})
        self.assertIn('opciones:', est.clave_ids.filtered(lambda c: c.nombre == 'tema').tipo)
        self.assertEqual(est.clave_ids.filtered(lambda c: c.nombre == 'relevancia_regional').tipo, 'número')
        # destildar una clave la saca del Resultado; tildar una lista la mete
        est.clave_ids.filtered(lambda c: c.nombre == 'tono').consolidar = False
        est.clave_ids.filtered(lambda c: c.nombre == 'actores').consolidar = True
        est.action_ejecutar_todo()
        contenido = est.resultado_id.contenido
        self.assertEqual(set(contenido['politica_aplicada']['claves_comparacion']), {'tema', 'encuadre', 'relevancia_regional', 'actores'})
        self.assertIn('tema', contenido['distribuciones'])
        self.assertNotIn('tono', contenido['distribuciones'])
        self.assertIn('actores', contenido['distribuciones'])
        self.assertIn('relevancia_regional', contenido['numericas'])
        # la iteración copia las filas con sus tildes
        nuevo = self.env['iag.estudio'].browse(est.action_nueva_iteracion()['res_id'])
        self.assertEqual(nuevo.clave_ids.filtered('consolidar').mapped('nombre'), est.clave_ids.filtered('consolidar').mapped('nombre'))

    # --- Semana en Normalización; tolerancia a fallas del proveedor ---------------------------
    def test_21_agrupar_por_semana(self):
        est = self._estudio(agrupar_por='semana')
        est.action_ejecutar_todo()
        grupos = set(est.resultado_id.contenido['por_grupo'])
        self.assertTrue(grupos)
        for g in grupos:
            self.assertRegex(g, r'^\d{4}-S\d{2}$')
        semanas = {'%d-S%02d' % (n.fecha_hora.isocalendar()[0], n.fecha_hora.isocalendar()[1]) for n in self.noticias}
        self.assertEqual(grupos, semanas)

    def test_22_falla_del_proveedor_no_pierde_la_etapa(self):
        from unittest.mock import patch
        Fake = self.registry['iag.proveedor.fake']
        original = Fake.completar

        def completar_fallando(proveedor, texto, temperatura, longitud, esquema=None):
            if 'El intendente anunció obras' in texto:      # una sola noticia falla, el resto responde
                raise UserError('Error del proveedor generativo: simulado')
            return original(proveedor, texto, temperatura, longitud, esquema=esquema)

        est = self._estudio()
        with patch.object(Fake, 'completar', completar_fallando), mute_logger('odoo.addons.noticias_iag.models.filtros.invocacion'):
            self._hasta(est, 'decodificado')
        self.assertEqual(len(est.respuesta_ids), self.N)                       # M respuestas, una vacía
        vacias = est.respuesta_ids.filtered(lambda r: not r.texto)
        self.assertEqual(len(vacias), 1)
        self.assertEqual(len(est.informe['invocacion']['fallidas']), 1)
        self.assertEqual(est.informe['invocacion']['fallidas'][0]['prompt_id'], vacias.prompt_id.id)
        self.assertIn('simulado', est.informe['invocacion']['fallidas'][0]['motivo'])
        self.assertEqual(len(est.analisis_ids), self.N - 1)                    # la compuerta descarta la vacía
        self.assertEqual(est.informe['decodificacion']['descartes'][0]['motivo'], 'respuesta vacía')
        # el juez tampoco pierde la etapa: el análisis sin juicio queda observado con confianza 0
        with patch.object(Fake, 'completar', completar_fallando), mute_logger('odoo.addons.noticias_iag.models.filtros.verificacion'):
            self._hasta(est, 'consolidado')
        self.assertEqual(len(est.veredicto_ids), self.N - 1)
        self.assertEqual(est.informe['verificacion']['juez_sin_respuesta'], 0)   # esa noticia ya no llega al juez
        est2 = self._estudio(limite=2)
        with patch.object(Fake, 'completar', lambda *a, **k: (_ for _ in ()).throw(UserError('caído'))), \
                mute_logger('odoo.addons.noticias_iag.models.filtros.invocacion', 'odoo.addons.noticias_iag.models.filtros.verificacion'):
            self._hasta(est2, 'decodificado')
        self.assertEqual(est2.state, 'decodificado')
        self.assertEqual(len(est2.analisis_ids), 0)
        self.assertEqual(len(est2.informe['invocacion']['fallidas']), 2)

    # --- Resultado y sumidero (2026-09-04): variable del experto, condición, cruces, tablero, ficha ---
    def test_23_variable_del_experto(self):
        est = self._estudio(agrupar_por='variable', variable_nombre='tipo de prensa', variable_dominio='medio')
        est.action_seleccionar_corpus()
        est.action_poblar_asignaciones()                       # una fila por medio del corpus, sin valor
        self.assertEqual(set(est.asignacion_ids.mapped('medio_id')), set(self.medio_a | self.medio_b))
        self.assertEqual(est.n_sin_asignar, self.N)
        est.asignacion_ids.filtered(lambda a: a.medio_id == self.medio_a).valor = 'nacional'
        self.assertEqual(est.n_sin_asignar, len(self.noticias.filtered(lambda n: n.medio == self.medio_b)))
        self.assertEqual(est.asignacion_ids.filtered(lambda a: a.medio_id == self.medio_a).n_noticias,
                         len(self.noticias.filtered(lambda n: n.medio == self.medio_a)))
        est.action_poblar_asignaciones()                       # idempotente: conserva el valor cargado
        self.assertEqual(len(est.asignacion_ids), 2)
        self.assertEqual(est._sintonizador('normalizacion')['variable'],
                         {'nombre': 'tipo de prensa', 'dominio': 'medio', 'asignacion': {'Portal A (test)': 'nacional'}})
        est.action_ejecutar_todo()
        c = est.resultado_id.contenido
        self.assertEqual(c['grupos'], ['nacional', '(sin asignar)'])
        self.assertEqual(c['politica_aplicada']['variable']['asignacion'], {'Portal A (test)': 'nacional'})
        grupos = {f['grupo'] for f in c['filas']}
        self.assertEqual(grupos, {'nacional', '(sin asignar)'})
        self.assertEqual(len(c['filas']), self.N)
        self.assertTrue(all(f['incluido'] for f in c['filas']))
        # el cruce de una clave tiene n por grupo, porcentajes por fila que suman 100 y V de Cramér
        cruce = c['cruces']['tema']
        self.assertEqual(sum(cruce['n_por_grupo'].values()), self.N)
        for g in c['grupos']:
            self.assertAlmostEqual(sum(cruce['pct'][g].values()), 100.0, delta=0.3)
        self.assertIn('v_cramer', cruce)
        self.assertIn('tipo de prensa', est.resultado_id.html)
        html = est.resultado_id.html
        self.assertLess(html.index('<th>nacional</th>'), html.index('<th>(sin asignar)</th>'))   # el orden del experto, no el de jsonb
        # la iteración hereda la tabla; la ficha documenta la variable
        nuevo = self.env['iag.estudio'].browse(est.action_nueva_iteracion()['res_id'])
        self.assertEqual(nuevo._tabla_asignacion(), {'Portal A (test)': 'nacional'})
        self.assertIn('nacional', est.ficha_html)
        # por departamento: la misma mecánica sobre la otra clave de procedencia
        est2 = self._estudio(agrupar_por='variable', variable_dominio='departamento')
        est2.action_seleccionar_corpus()
        est2.action_poblar_asignaciones()
        self.assertEqual(set(est2.asignacion_ids.mapped('departamento')), {'Rosario', 'La Capital'})
        for a in est2.asignacion_ids:
            a.valor = 'rosario' if a.departamento == 'Rosario' else 'interior'
        est2.action_ejecutar_todo()
        self.assertEqual(est2.resultado_id.contenido['grupos'], ['interior', 'rosario'])   # orden de la tabla del experto

    def test_24_condicion_de_inclusion(self):
        est = self._estudio(agrupar_por='medio')
        est.condicion_clave_id = est.clave_ids.filtered(lambda c: c.nombre == 'tono')
        est.condicion_valor = 'Negativo'                        # sin distinguir mayúsculas
        self.assertEqual(est._sintonizador('normalizacion')['condicion'], {'clave': 'tono', 'valor': 'Negativo'})
        est.action_ejecutar_todo()
        c = est.resultado_id.contenido
        negativos = est.analisis_ids.filtered(lambda a: a.contenido.get('tono') == 'negativo')
        self.assertEqual(c['resumen']['incluidos'], len(negativos))
        self.assertEqual(c['resumen']['excluidos_por_condicion'], self.N - len(negativos))
        self.assertEqual(c['politica_aplicada']['condicion'], {'clave': 'tono', 'valor': 'Negativo'})
        self.assertEqual(len(est.resultado_id.veredicto_ids), self.N)          # compuerta blanda: nada se suprime
        self.assertEqual(sum(1 for f in c['filas'] if f['motivo'] == 'condicion'), self.N - len(negativos))
        if negativos:
            self.assertEqual(sum(c['distribuciones']['tema'].values()), len(negativos))
            self.assertEqual(set(c['distribuciones']['tono']), {'negativo'})
            self.assertTrue(c['cruces']['tono']['colapso'])                    # una sola categoría: concentrada
        self.assertIn('Condición de inclusión', est.resultado_id.html)
        # sobre una lista alcanza con que un elemento coincida
        est2 = self._estudio()
        est2.condicion_clave_id = est2.clave_ids.filtered(lambda c: c.nombre == 'actores')
        est2.action_ejecutar_todo()                                             # sin valor: la condición no aplica
        self.assertNotIn('condicion', est2.resultado_id.contenido['politica_aplicada'])
        self.assertEqual(est2.resultado_id.contenido['resumen']['incluidos'], self.N)

    def test_25_cruces_y_colapso(self):
        from ..models.filtros.normalizacion import v_cramer, wilson
        # V de Cramér: independencia perfecta → 0; asociación perfecta 2×2 → 1
        self.assertEqual(v_cramer({'a': {'x': 10, 'y': 10}, 'b': {'x': 10, 'y': 10}}), 0.0)
        self.assertEqual(v_cramer({'a': {'x': 10, 'y': 0}, 'b': {'x': 0, 'y': 10}}), 1.0)
        self.assertIsNone(v_cramer({'a': {'x': 10}}))
        p, lo, hi = wilson(5, 10)
        self.assertEqual(p, 0.5)
        self.assertTrue(lo < 0.5 < hi and 0.23 < lo < 0.24 and 0.76 < hi < 0.77)
        # respuestas idénticas para todas las noticias: todas las claves colapsan y los booleanos traen IC
        template = self.env['iag.template'].create({
            'name': 'Booleanos (test)', 'objetivo': 'Prueba', 'instrucciones': 'Respondé.',
            'esquema_respuesta': {'type': 'object', 'required': ['es_local', 'tema'],
                                  'properties': {'es_local': {'type': 'boolean'},
                                                 'tema': {'type': 'string', 'enum': ['a', 'b']}}}})
        est = self._estudio(template_id=template.id, agrupar_por='medio').with_context(
            iag_fake_respuesta='{"es_local": true, "tema": "a"}')
        est.action_ejecutar_todo()
        c = est.resultado_id.contenido
        self.assertTrue(c['cruces']['tema']['colapso'])
        self.assertEqual(c['cruces']['tema']['concentracion'], 1.0)
        self.assertEqual(c['cruces']['tema']['valor_dominante'], 'a')
        self.assertTrue(c['cruces']['es_local']['colapso'])
        for g, ic in c['cruces']['es_local']['proporcion_true'].items():
            self.assertEqual(ic[0], 1.0)
            self.assertLess(ic[1], 1.0)
        self.assertIn('poco informativa', est.resultado_id.html)
        self.assertIn('IC 95', est.resultado_id.html)

    def test_26_tablero_proyeccion_del_resultado(self):
        Fila = self.env['iag.resultado.fila']
        est = self._estudio(agrupar_por='variable')
        est.action_seleccionar_corpus()
        est.action_poblar_asignaciones()
        est.asignacion_ids.filtered(lambda a: a.medio_id == self.medio_a).valor = 'nacional'
        est.asignacion_ids.filtered(lambda a: a.medio_id == self.medio_b).valor = 'interior'
        est.condicion_clave_id = est.clave_ids.filtered(lambda c: c.nombre == 'tono')
        est.condicion_valor = 'negativo'
        self.env.flush_all()
        self.assertFalse(Fila.search([('estudio_id', '=', est.id)]))          # sin Resultado no hay tablero
        with self.assertRaises(UserError):
            est.action_ver_tablero()
        est.action_ejecutar_todo()
        self.env.flush_all()                                                   # la vista SQL lee lo persistido
        filas = Fila.search([('resultado_id', '=', est.resultado_id.id)])
        self.assertTrue(filas)
        # una fila por veredicto y por clave escalar (más un elemento por actor); nada de las afirmaciones
        esperadas = 0
        for a in est.analisis_ids:
            for k, v in a.contenido.items():
                if isinstance(v, list):
                    esperadas += sum(1 for x in v if not isinstance(x, (dict, list)))
                elif not isinstance(v, dict):
                    esperadas += 1
        self.assertEqual(len(filas), esperadas)
        self.assertEqual(set(filas.mapped('clave')) & {'afirmaciones'}, set())
        self.assertEqual(set(filas.mapped('grupo')), {'nacional', 'interior'})
        self.assertEqual(set(filas.mapped('estudio_id')), set(est))
        # cada fila lleva su veredicto (D) y su análisis y noticia (E)
        for f in filas[:10]:
            self.assertEqual(f.veredicto_id.analisis_id, f.analisis_id)
            self.assertEqual(f.veredicto_estado, f.veredicto_id.estado)
            self.assertEqual(f.noticia_id, f.analisis_id.noticia_ids)
            self.assertEqual(f.medio_id, f.noticia_id.medio)
        # el flag de consolidación sigue a la condición
        negativos = est.analisis_ids.filtered(lambda a: a.contenido.get('tono') == 'negativo')
        self.assertEqual(set(filas.filtered('incluido').mapped('analisis_id')), set(negativos))
        # el tablero se abre desde el Estudio y desde el Resultado, con la primera clave y "consolidados" preseleccionados
        accion = est.action_ver_tablero()
        self.assertEqual(accion['res_model'], 'iag.resultado.fila')
        self.assertEqual(accion['context']['search_default_clave'], 'tema')
        self.assertEqual(accion['context']['search_default_incluido'], 1)
        self.assertEqual(accion['domain'], [('resultado_id', '=', est.resultado_id.id)])
        # el pivot del tablero reproduce el cruce del Resultado: tema × grupo sobre los consolidados
        pivot = Fila.read_group([('resultado_id', '=', est.resultado_id.id), ('incluido', '=', True), ('clave', '=', 'tema')],
                                ['grupo', 'valor'], ['grupo', 'valor'], lazy=False)
        cruce = est.resultado_id.contenido['cruces'].get('tema', {}).get('tabla', {})
        for celda in pivot:
            self.assertEqual(celda['__count'], cruce[celda['grupo']][celda['valor']])
        # es una vista SQL de sólo lectura: el sumidero no escribe en el repositorio
        self.assertFalse(Fila._auto)
        self.env.cr.execute("SELECT relkind FROM pg_class WHERE relname = %s", (Fila._table,))
        self.assertEqual(self.env.cr.fetchone()[0], 'v')

    def test_27_ficha_del_estudio(self):
        est = self._estudio(notas='Umbral fijado por presupuesto.')
        self.assertIn('Selección del corpus', est.ficha_html)
        self.assertIn(self.template.name, est.ficha_html)
        self.assertIn('Sin corrida todavía', est.ficha_html)
        est.action_ejecutar_todo()
        ficha = est.ficha_html
        self.assertIn('N = %d' % self.N, ficha)
        self.assertIn('fake', ficha)                                            # configuración técnica de la corrida
        self.assertIn('Umbral fijado por presupuesto', ficha)
        self.assertIn('Veredicto[M′]', ficha)
        self.assertNotIn('api_key', ficha)
