# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# Dependencias de sistema:
# - libpango/libcairo/gdk-pixbuf/shared-mime-info -> requeridas por WeasyPrint
# - libpq5 -> cliente de PostgreSQL para psycopg[binary]
# - fonts-liberation -> fuentes decentes para los PDF generados
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# gunicorn no está en requirements.txt del repo original: se agrega aquí
# para no depender de que el repo lo incluya. Si ya lo agregas al
# requirements.txt, esta línea es inofensiva (pip lo detecta instalado).
#
# ays-zoho-sdk se instala desde un repo de GitHub privado (git+https). El
# token de lectura se pasa como build secret (--mount=type=secret), nunca
# como ARG/ENV: así no queda grabado en ninguna capa de la imagen ni en
# `docker history`. Ver docker-compose.yml (build.secrets) para como se
# conecta con la variable de entorno GH_TOKEN de Dokploy. Si el token no se
# provee (`required=false`), el build sigue intentando -- solo falla aqui si
# el repo en efecto sigue siendo privado y sin credencial.
RUN --mount=type=secret,id=gh_token,required=false \
    printf '#!/bin/sh\ncat /run/secrets/gh_token 2>/dev/null\n' > /usr/local/bin/gh-askpass.sh && \
    chmod +x /usr/local/bin/gh-askpass.sh && \
    git config --global credential.https://github.com.username x-access-token && \
    GIT_ASKPASS=/usr/local/bin/gh-askpass.sh pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

RUN mkdir -p /app/staticfiles /app/media /app/private_assets/colectivos

EXPOSE 8000

# collectstatic se ejecuta en el arranque (no en build) porque necesita
# las variables de entorno (SECRET_KEY, FIELD_ENCRYPTION_KEY, etc.) que
# Dokploy inyecta en runtime, no en build time.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Corre como usuario sin privilegios: limita el radio de acción si algún día
# se explota una vulnerabilidad de RCE en la app o en una dependencia.
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
