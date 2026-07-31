"""Backends internos. Los consumidores deben usar ``get_zoho()``."""

from .rest import RESTBackend
from .sdk import SDKBackend

__all__ = ["RESTBackend", "SDKBackend"]
