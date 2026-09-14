"""Cliente de IA independiente del proveedor, compartido entre features (gestor de
cotizaciones, asistente de consulta Zoho, etc.). Ver `client.py` para el detalle de
proveedores soportados."""

from .client import LLM

__all__ = ["LLM"]
