# Odoo 16 + dependencias del addon institucional wsfnoticias.
# El addon nuevo (wsfnoticias_iag) solo usa `requests` (ya viene con Odoo) y numpy opcional.
FROM odoo:16.0

USER root
COPY requirements-wsfnoticias.txt /tmp/requirements-wsfnoticias.txt
RUN pip3 install --no-cache-dir \
        -r /tmp/requirements-wsfnoticias.txt numpy \
    && rm /tmp/requirements-wsfnoticias.txt
# Corpora de NLTK que newspaper3k usa para keywords/summary (evita descargas en runtime)
RUN python3 -c "import nltk; nltk.download('punkt', quiet=True)" \
    && mkdir -p /var/lib/odoo/nltk_data && cp -r /root/nltk_data/* /var/lib/odoo/nltk_data/ \
    && chown -R odoo:odoo /var/lib/odoo/nltk_data
ENV NLTK_DATA=/var/lib/odoo/nltk_data
USER odoo
