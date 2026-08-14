"""Contrato local para una futura publicación de tareas de Colectivos.

Esta iteración no contiene un adaptador Zoho ni métodos de escritura. El
publicador deshabilitado hace explícita la frontera para evitar que una captura
local active accidentalmente una integración futura.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class TaskPublishingDisabled(RuntimeError):
    """La publicación remota no está habilitada en la fase actual."""


@dataclass(frozen=True)
class ColectivosTaskPayload:
    request_kind: str
    source_kind: str
    policy_context: str
    branch_code: str
    local_reference: str
    has_attachments: bool = False


class ColectivosTaskPublisher(Protocol):
    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]: ...


class DisabledColectivosTaskPublisher:
    """Única implementación disponible hasta una fase autorizada de escritura."""

    enabled = False

    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]:
        del payload
        raise TaskPublishingDisabled("La publicación de tareas Zoho está deshabilitada.")


def get_task_publisher() -> ColectivosTaskPublisher:
    return DisabledColectivosTaskPublisher()
