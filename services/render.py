# -*- coding: utf-8 -*-
"""Ensamblado del prompt: la única definición de cómo se ordenan objetivo, instrucciones, aclaraciones,
material empírico y formato de respuesta. La usa Composición de contexto para el prompt real y el panel
de templates para la previsualización, de modo que ambos no puedan divergir."""
import json

from .proveedor import MARCADOR_ESQUEMA

INSTRUCCION_FORMATO = 'Respondé únicamente con un objeto JSON válido que cumpla el siguiente esquema, sin texto adicional:'
MARCADOR_MATERIAL = '[Aquí Composición de contexto inserta las noticias recuperadas del corpus del Estudio.]'


def encabezado(objetivo, instrucciones, texto_libre=None):
    partes = ['# Objetivo del análisis', objetivo or '', '', '# Instrucciones', instrucciones or '', '']
    if texto_libre:
        partes += ['# Aclaraciones', texto_libre, '']
    return partes


def material(noticias, max_chars):
    partes = ['# Material empírico']
    for i, n in enumerate(noticias, 1):
        fecha = n.fecha_hora or n.fecha_registro
        fecha = fecha.strftime('%Y-%m-%d') if fecha else 's/f'
        partes.append('[%d] (%s, %s) %s' % (i, n.medio.name or 'medio desconocido', fecha, n.titulo or ''))
        if n.copete:
            partes.append(n.copete)
        partes.append((n.texto or '')[:max_chars])
        partes.append('')
    return partes


def formato(esquema):
    cuerpo = json.dumps(esquema, ensure_ascii=False, indent=2) if esquema else '(el template no declara esquema de respuesta)'
    return [MARCADOR_ESQUEMA, INSTRUCCION_FORMATO, cuerpo]


def prompt(template, instrucciones, texto_libre, noticias, max_chars):
    """El prompt completo que recibe el modelo (Prompt.texto)."""
    return '\n'.join(encabezado(template.objetivo, instrucciones, texto_libre)
                     + material(noticias, max_chars)
                     + formato(template.esquema_respuesta))


def previsualizacion(template, instrucciones):
    """Lo que el template aporta al prompt, en el orden real, con el hueco del material empírico marcado."""
    return '\n'.join(encabezado(template.objetivo, instrucciones)
                     + ['# Material empírico', MARCADOR_MATERIAL, '']
                     + formato(template.esquema_respuesta))
