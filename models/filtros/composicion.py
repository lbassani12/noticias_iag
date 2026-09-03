# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError

from ...services import render
from ...services.vectores import coseno


class FiltroComposicion(models.AbstractModel):
    """Embedding[N] -> Prompt[M]. Recuperación semántica + ensamblado del prompt.

    La consulta (objetivo del template + variables + texto libre) se embebe con el mismo
    proveedor; las noticias se ordenan por similitud coseno y se filtran por umbral y top-k.
    Estrategia ``agregado``: un prompt con las k noticias (M = 1). Estrategia ``por_noticia``:
    un prompt por noticia recuperada (M = k, con k = N por defecto).
    """
    _name = 'iag.filtro.composicion'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Composición de contexto'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Prompt = self.env['iag.prompt']
        template = sintonizador.get('template')
        if not template:
            raise UserError(_('Composición de contexto requiere un template.'))
        if not entrada:
            return Prompt
        cfg = self._config()
        proveedor = self._proveedor()
        instrucciones = template.renderizar_instrucciones(sintonizador.get('valores_variables') or {})

        consulta = self._texto_consulta(template, sintonizador)
        vector_consulta = proveedor.embeber([consulta])[0]
        puntajes = [(e, coseno(vector_consulta, e.vector or [])) for e in entrada]
        umbral = sintonizador.get('umbral_similitud') or 0.0
        top_k = sintonizador.get('top_k') or 0
        # La similitud coseno vive en [-1, 1]: un umbral <= 0 significa "sin umbral".
        seleccion = sorted([p for p in puntajes if umbral <= 0 or p[1] >= umbral], key=lambda p: -p[1])
        if top_k:
            seleccion = seleccion[:top_k]
        if informe is not None:
            informe['similitudes'] = [[e.noticia_id.id, round(s, 4)] for e, s in seleccion]
            informe['descartadas_por_umbral'] = len(puntajes) - len(seleccion)
        if not seleccion:
            raise UserError(_('Ninguna noticia supera el umbral de similitud %.2f.') % umbral)

        noticias = [e.noticia_id for e, _s in seleccion]
        if sintonizador.get('estrategia') == 'agregado':
            grupos = [noticias]
        else:
            grupos = [[n] for n in noticias]

        vals = []
        for grupo in grupos:
            vals.append({
                'texto': self._renderizar(template, instrucciones, sintonizador.get('texto_libre'),
                                          grupo, cfg['max_chars_noticia']),
                'temperatura': sintonizador.get('temperatura') or 0.0,
                'longitud_salida': sintonizador.get('longitud_salida') or 0,
                'template_id': template.id,
                'noticia_ids': [(6, 0, [n.id for n in grupo])],
            })
        return Prompt.create(vals)

    @staticmethod
    def _texto_consulta(template, sintonizador):
        partes = [template.objetivo or '']
        valores = sintonizador.get('valores_variables') or {}
        partes.extend(str(v) for v in valores.values() if v)
        if sintonizador.get('texto_libre'):
            partes.append(sintonizador['texto_libre'])
        return '\n'.join(p for p in partes if p)

    @staticmethod
    def _renderizar(template, instrucciones, texto_libre, noticias, max_chars):
        return render.prompt(template, instrucciones, texto_libre, noticias, max_chars)
