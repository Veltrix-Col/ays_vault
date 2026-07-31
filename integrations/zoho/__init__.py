"""Fachada desacoplada y de solo lectura para Zoho CRM API V8."""

from .factory import get_zoho
from .facade import ZohoFacade
from .services import ZohoServices

__all__ = ["ZohoFacade", "ZohoServices", "get_zoho"]
