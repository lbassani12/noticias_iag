# -*- coding: utf-8 -*-
{
    'name': 'Noticias IAG',
    'version': '16.0.1.0.0',
    'summary': 'Pipeline de Tubos y Filtros para el análisis de noticias con IA generativa',
    'description': """
Implementación del framework de la tesina *Una infraestructura técnica y procedimental
para el análisis de noticias de portales digitales a través de inteligencia artificial
generativa* (L. Bassani, UNR). Se monta sobre el addon institucional ``wsfnoticias``
(CAETI/CIM), que aporta el filtro de Extracción, y agrega los seis filtros restantes,
un modelo por cada tipo de dato del pipeline, el panel de templates y el Estudio.
""",
    'author': 'Lucio Bassani',
    'license': 'AGPL-3',
    'category': 'Tools',
    'depends': ['base', 'wsfnoticias'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/ir.model.access.csv',
        'views/template.xml',
        'views/tipos.xml',
        'views/veredicto.xml',
        'views/estudio.xml',
        'views/tablero.xml',
        'views/settings.xml',
        'views/menus.xml',
        'data/templates.xml',
    ],
    'installable': True,
    'application': False,
}
