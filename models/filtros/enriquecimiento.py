# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError

LOTE = 32


class FiltroEnriquecimiento(models.AbstractModel):
    """Noticia[N] -> Embedding[N]. Un vector por noticia sobre título + copete + texto."""
    _name = 'iag.filtro.enriquecimiento'
    _inherit = 'iag.filtro'
    _description = 'Filtro de Enriquecimiento semántico'

    def ejecutar(self, entrada, sintonizador, informe=None):
        Embedding = self.env['iag.embedding']
        if not entrada:
            return Embedding
        cfg = self._config()
        proveedor = self._proveedor()
        textos = [self._texto_noticia(n, cfg['max_chars_embedding']) for n in entrada]
        vectores = []
        for i in range(0, len(textos), LOTE):
            vectores.extend(proveedor.embeber(textos[i:i + LOTE]))
        if len(vectores) != len(entrada):
            raise UserError(_('Enriquecimiento: el proveedor devolvió %d vectores para %d noticias.')
                            % (len(vectores), len(entrada)))
        return Embedding.create([{'noticia_id': n.id, 'vector': v} for n, v in zip(entrada, vectores)])

    @staticmethod
    def _texto_noticia(noticia, max_chars):
        partes = [noticia.titulo or '', noticia.copete or '', noticia.texto or '']
        return '\n\n'.join(p for p in partes if p)[:max_chars]
