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
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000

# collectstatic se ejecuta en el arranque (no en build) porque necesita
# las variables de entorno (SECRET_KEY, FIELD_ENCRYPTION_KEY, etc.) que
# Dokploy inyecta en runtime, no en build time.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
