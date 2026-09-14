"""Carga adaptadores desde config/ramos/<ramo>/adaptadores.yaml y asigna archivos a cada uno."""
from __future__ import annotations

import importlib
import re
from collections import defaultdict
from pathlib import Path

import yaml


def _importar(ruta: str):
    mod, cls = ruta.rsplit(".", 1)
    return getattr(importlib.import_module(mod), cls)


def cargar_registro(ramo: str, base: str | Path = "config") -> dict:
    return yaml.safe_load((Path(base) / "ramos" / ramo / "adaptadores.yaml").read_text(encoding="utf-8"))


def asignar_archivos(registro: dict, archivos: list[str | Path]) -> tuple[dict[str, list[Path]], list[Path]]:
    """Devuelve ({id_aseguradora: [archivos]}, [archivos_no_reconocidos])."""
    grupos: dict[str, list[Path]] = defaultdict(list)
    sueltos: list[Path] = []
    for a in map(Path, archivos):
        n = a.name.lower()
        hit = next((ad["id"] for ad in registro["adaptadores"] if any(re.search(p, n) for p in ad["patrones"])), None)
        (grupos[hit] if hit else sueltos).append(a)
    return dict(grupos), sueltos


def instanciar(registro: dict, id_aseguradora: str, **kw):
    if id_aseguradora == "generico":
        return _importar(registro["generico"]["clase"])(**kw)
    ad = next(a for a in registro["adaptadores"] if a["id"] == id_aseguradora)
    return _importar(ad["clase"])(**kw)
