import logging
import threading

from django.db import close_old_connections

logger = logging.getLogger("vault.tasks")


def run_async(func, *args, **kwargs):
    """Ejecuta func en un hilo de fondo. Punto único de reemplazo por Celery (func.delay(*args, **kwargs)) cuando haya un broker disponible."""
    thread = threading.Thread(target=_run_safely, args=(func, args, kwargs), daemon=True)
    thread.start()


def _run_safely(func, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception("Tarea en segundo plano falló: %s", getattr(func, "__name__", str(func)))
    finally:
        close_old_connections()
