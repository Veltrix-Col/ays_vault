import logging
import threading

from django.db import close_old_connections, connection, connections

logger = logging.getLogger("vault.tasks")


def _use_background_thread():
    return connection.vendor != "sqlite"


def run_async(func, *args, **kwargs):
    """Ejecuta fuera de la transacción llamadora sin crear escritores concurrentes en SQLite."""
    if not _use_background_thread():
        _run_inline_safely(func, args, kwargs)
        return None
    thread = threading.Thread(target=_run_safely, args=(func, args, kwargs), daemon=True)
    thread.start()
    return thread


def _run_inline_safely(func, args, kwargs):
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception("Tarea en segundo plano falló: %s", getattr(func, "__name__", str(func)))


def _run_safely(func, args, kwargs):
    close_old_connections()
    try:
        _run_inline_safely(func, args, kwargs)
    finally:
        connections.close_all()
