# -*- coding: utf-8 -*-
"""Servicio técnico de acceso a modelos generativos y de *embedding*.

No es un filtro: es infraestructura compartida (como el ORM) que usan Enriquecimiento,
Composición, Invocación y Verificación sin que por ello un filtro conozca a otro.
La implementación concreta se elige por ``ir.config_parameter`` (``noticias_iag.proveedor``);
la interfaz es la misma para todas: ``embeber(textos)`` y ``completar(texto, temperatura, longitud)``.
"""
import hashlib
import json
import logging
import math
import re
import time

import requests

from odoo import api, models, _
from odoo.exceptions import UserError

from . import esquema as esq

_logger = logging.getLogger(__name__)

#: Marcador que precede al esquema de respuesta dentro de todo prompt compuesto por el
#: framework. Lo usan Composición y el juez de Verificación al componer, y el proveedor
#: simulado para generar respuestas conformes.
MARCADOR_ESQUEMA = '### Esquema de respuesta (JSON Schema)'


class Proveedor(models.AbstractModel):
    _name = 'iag.proveedor'
    _description = 'Proveedor de modelos generativos y de embedding (interfaz)'

    @api.model
    def _config(self):
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'proveedor': icp.get_param('noticias_iag.proveedor', 'fake'),
            'base_url': icp.get_param('noticias_iag.base_url', 'http://ollama:11434/v1'),
            'api_key': icp.get_param('noticias_iag.api_key', ''),
            'modelo_generativo': icp.get_param('noticias_iag.modelo_generativo', 'qwen2.5'),
            'modelo_embedding': icp.get_param('noticias_iag.modelo_embedding', 'nomic-embed-text'),
            'timeout': int(icp.get_param('noticias_iag.timeout', '120')),
            'max_chars_embedding': int(icp.get_param('noticias_iag.max_chars_embedding', '8000')),
            'max_chars_noticia': int(icp.get_param('noticias_iag.max_chars_noticia', '6000')),
            'politica_no_conformidad': icp.get_param('noticias_iag.politica_no_conformidad', 'reparar'),
            'salida_estructurada': icp.get_param('noticias_iag.salida_estructurada', 'json_schema'),
        }

    @api.model
    def _obtener(self):
        """Devuelve el proveedor concreto configurado."""
        nombre = self._config()['proveedor']
        modelo = 'iag.proveedor.%s' % nombre
        if modelo not in self.env:
            raise UserError(_('El proveedor "%s" no está disponible.') % nombre)
        return self.env[modelo]

    @api.model
    def _snapshot(self):
        """Configuración técnica de la corrida, para el registro del Estudio (sin credenciales)."""
        cfg = self._config()
        cfg.pop('api_key', None)
        return cfg

    def embeber(self, textos):
        raise NotImplementedError

    def completar(self, texto, temperatura, longitud_salida, esquema=None):
        """Devuelve la respuesta cruda. ``esquema`` (JSON Schema esperado) es opcional: los proveedores
        que ofrecen salida estructurada pueden usarlo como restricción de generación."""
        raise NotImplementedError


class ProveedorFake(models.AbstractModel):
    """Proveedor simulado y determinístico, para desarrollo y tests.

    *Embeddings*: bolsa de palabras con *hashing* a 64 dimensiones (textos parecidos dan
    vectores parecidos). *Completado*: lee el esquema de respuesta que el prompt declara
    tras ``MARCADOR_ESQUEMA`` y devuelve una instancia válida; los valores se eligen a
    partir del *hash* del prompt, así dos corridas iguales dan la misma salida.
    Con la clave de contexto ``iag_fake_basura_si_contiene`` responde texto no conforme
    para los prompts que contengan ese fragmento (sirve para probar la compuerta).
    """
    _name = 'iag.proveedor.fake'
    _inherit = 'iag.proveedor'
    _description = 'Proveedor simulado (desarrollo y tests)'

    DIM = 64

    def embeber(self, textos):
        return [self._vector(t) for t in textos]

    @classmethod
    def _vector(cls, texto):
        v = [0.0] * cls.DIM
        for token in re.findall(r'\w{4,}', (texto or '').lower()):
            h = int(hashlib.md5(token.encode('utf-8')).hexdigest(), 16)
            v[h % cls.DIM] += 1.0 if (h >> 8) & 1 else -1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def completar(self, texto, temperatura, longitud_salida, esquema=None):
        ctx = self.env.context
        basura = ctx.get('iag_fake_basura_si_contiene')
        if basura and basura in texto:
            return 'Lo siento, no puedo responder en el formato pedido.'
        if ctx.get('iag_fake_respuesta') is not None:
            return ctx['iag_fake_respuesta']
        esquema = self._esquema_del_prompt(texto)
        if esquema is None:
            return '{"respuesta": "[fake] el prompt no declara esquema"}'
        semilla = int(hashlib.sha256(texto.encode('utf-8')).hexdigest(), 16)
        instancia = self._instancia(esquema, semilla, '')
        return 'Respuesta simulada del proveedor fake:\n```json\n%s\n```' % json.dumps(
            instancia, ensure_ascii=False, indent=2)

    @staticmethod
    def _esquema_del_prompt(texto):
        pos = texto.rfind(MARCADOR_ESQUEMA)
        if pos < 0:
            return None
        obj, _err = esq.aislar_json(texto[pos + len(MARCADOR_ESQUEMA):])
        return obj if isinstance(obj, dict) else None

    def _instancia(self, esquema, semilla, ruta):
        s = int(hashlib.sha256(('%d|%s' % (semilla, ruta)).encode('utf-8')).hexdigest(), 16)
        tipo = esquema.get('type') or ('object' if 'properties' in esquema else 'string')
        if 'enum' in esquema:
            return esquema['enum'][s % len(esquema['enum'])]
        if tipo == 'object':
            return {k: self._instancia(sub, semilla, ruta + '.' + k)
                    for k, sub in (esquema.get('properties') or {}).items()}
        if tipo == 'array':
            n = max(int(esquema.get('minItems', 1)), 1)
            sub = esquema.get('items') or {'type': 'string'}
            return [self._instancia(sub, semilla, '%s[%d]' % (ruta, i)) for i in range(n)]
        if tipo == 'number':
            lo, hi = float(esquema.get('minimum', 0.0)), float(esquema.get('maximum', 1.0))
            return round(lo + (hi - lo) * ((s % 1000) / 1000.0), 3)
        if tipo == 'integer':
            lo, hi = int(esquema.get('minimum', 0)), int(esquema.get('maximum', 10))
            return lo + (s % max(hi - lo + 1, 1))
        if tipo == 'boolean':
            return bool(s % 2)
        if tipo == 'null':
            return None
        return '[fake] %s' % (ruta.strip('.') or 'texto')


class _ErrorHTTP(Exception):
    def __init__(self, status, detalle):
        super().__init__(detalle)
        self.status = status
        self.detalle = detalle


class ProveedorOpenAICompat(models.AbstractModel):
    """Proveedor por API compatible con OpenAI: OpenAI, Ollama, Groq, OpenRouter, vLLM, etc.

    Verificado contra Ollama (2026-09-03). Salida estructurada por ``response_format``: con
    ``json_schema`` el proveedor restringe la generación al esquema del template (en Ollama,
    gramática de llama.cpp: garantiza sintaxis y restricciones como ``maxItems``); si el
    proveedor rechaza el modo configurado (HTTP 400) se degrada a ``json_object`` y luego a
    texto libre, dejando constancia en el log.
    """
    _name = 'iag.proveedor.openai_compat'
    _inherit = 'iag.proveedor'
    _description = 'Proveedor por API compatible con OpenAI'

    #: Reintentos ante fallas transitorias (red, timeout, HTTP 5xx). Los 4xx no se reintentan.
    REINTENTOS = 3

    def _post(self, ruta, payload):
        cfg = self._config()
        headers = {'Content-Type': 'application/json'}
        if cfg['api_key']:
            headers['Authorization'] = 'Bearer %s' % cfg['api_key']
        url = cfg['base_url'].rstrip('/') + ruta
        ultimo = None
        for intento in range(1, self.REINTENTOS + 1):
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=cfg['timeout'])
            except requests.RequestException as e:
                ultimo = _ErrorHTTP(None, '%s: %s' % (url, e))
            else:
                if r.status_code < 500:
                    if r.status_code >= 400:
                        raise _ErrorHTTP(r.status_code, '%s -> HTTP %d: %s' % (url, r.status_code, r.text[:300]))
                    try:
                        return r.json()
                    except ValueError as e:
                        raise _ErrorHTTP(r.status_code, '%s: respuesta no JSON (%s)' % (url, e))
                ultimo = _ErrorHTTP(r.status_code, '%s -> HTTP %d: %s' % (url, r.status_code, r.text[:300]))
            if intento < self.REINTENTOS:
                _logger.warning('Proveedor: intento %d/%d falló (%s); se reintenta.', intento, self.REINTENTOS, ultimo.detalle)
                time.sleep(2 * intento)
        raise ultimo

    def embeber(self, textos):
        cfg = self._config()
        try:
            datos = self._post('/embeddings', {'model': cfg['modelo_embedding'], 'input': textos})
        except _ErrorHTTP as e:
            raise UserError(_('Error del proveedor de embeddings: %s') % e.detalle)
        filas = sorted(datos.get('data') or [], key=lambda d: d.get('index', 0))
        vectores = [f.get('embedding') for f in filas]
        if len(vectores) != len(textos) or any(not v for v in vectores):
            raise UserError(_('El proveedor devolvió %d embeddings para %d textos.') % (len(vectores), len(textos)))
        return vectores

    @staticmethod
    def _response_format(modo, esquema):
        if modo == 'json_schema' and esquema:
            return {'type': 'json_schema', 'json_schema': {'name': 'respuesta', 'schema': esquema}}
        if modo in ('json_object', 'json_schema'):
            return {'type': 'json_object'}
        return None

    def completar(self, texto, temperatura, longitud_salida, esquema=None):
        cfg = self._config()
        base = {'model': cfg['modelo_generativo'], 'messages': [{'role': 'user', 'content': texto}]}
        if temperatura is not None:
            base['temperature'] = temperatura
        if longitud_salida:
            base['max_tokens'] = longitud_salida
        modo = cfg.get('salida_estructurada') or 'ninguna'
        # Degradación: json_schema -> json_object -> texto libre, sólo ante un 400 del proveedor.
        intentos = {'json_schema': ['json_schema', 'json_object', 'ninguna'],
                    'json_object': ['json_object', 'ninguna']}.get(modo, ['ninguna'])
        for m in intentos:
            payload = dict(base)
            rf = self._response_format(m, esquema)
            if rf:
                payload['response_format'] = rf
            try:
                datos = self._post('/chat/completions', payload)
            except _ErrorHTTP as e:
                if e.status == 400 and m != 'ninguna':
                    _logger.warning('response_format %s rechazado por el proveedor, se reintenta con menos restricción: %s', m, e.detalle)
                    continue
                raise UserError(_('Error del proveedor generativo: %s') % e.detalle)
            try:
                return datos['choices'][0]['message']['content'] or ''
            except (KeyError, IndexError, TypeError):
                raise UserError(_('Respuesta inesperada del proveedor: %s') % json.dumps(datos)[:500])
        raise UserError(_('El proveedor rechazó todos los modos de salida.'))
