"""
CLI del gestor de cotizaciones.

  python -m gestor_cotizaciones.cli extraer   --ramo movilidad --nit 8909268031 --actual sura gestor_cotizaciones/samples/*
  python -m gestor_cotizaciones.cli consolidar --ramo movilidad --salida out/consolidado.xlsx
  python -m gestor_cotizaciones.cli run       --ramo movilidad --nit 8909268031 --actual sura --salida out/consolidado.xlsx gestor_cotizaciones/samples/*

`extraer` deja un JSON canónico por aseguradora en out/canonico/. El analista puede editarlo
(corregir un valor, añadir uno) y volver a `consolidar` sin repetir la extracción.
Variables: GESTOR_LLM_PROVIDER=bedrock|anthropic|vertex|azure_foundry|dry-run, GESTOR_LLM_MODEL, AWS_REGION.
"""
from __future__ import annotations

import argparse
import glob
import importlib
import json
import sys
from pathlib import Path

from gestor_cotizaciones.core.adaptador import cargar_catalogo, cargar_json, guardar_json
from gestor_cotizaciones.core.analisis import analizar
from gestor_cotizaciones.core.llm import LLM
from gestor_cotizaciones.core.registry import asignar_archivos, cargar_registro, instanciar

# `config/ramos/<ramo>/` vive dentro de este paquete, no en el directorio de
# trabajo: al correr `cargar_catalogo`/`cargar_registro` con su `base="config"`
# por defecto se resolvería relativo al cwd, que en este repo ya tiene su
# propio `config/` (settings de Django). Se fija explícito para que el CLI
# funcione sin importar desde dónde se invoque.
CONFIG_DIR = Path(__file__).resolve().parent / "config"


def _archivos(patrones: list[str]) -> list[str]:
    out = []
    for p in patrones:
        out += glob.glob(p) if any(ch in p for ch in "*?[") else [p]
    return sorted(out)


def cmd_extraer(a) -> list:
    cat, reg = cargar_catalogo(a.ramo, base=CONFIG_DIR), cargar_registro(a.ramo, base=CONFIG_DIR)
    llm = None if a.sin_llm else LLM(a.provider, a.model)
    grupos, sueltos = asignar_archivos(reg, _archivos(a.archivos))
    if sueltos:
        if a.otra:
            grupos[a.otra] = sueltos
        else:
            print(f"[aviso] archivos sin adaptador (usa --otra <nombre> para procesarlos con el adaptador genérico): {[s.name for s in sueltos]}")
    cots = []
    for aid, files in grupos.items():
        print(f"→ {aid}: {[Path(f).name for f in files]}")
        if aid in {x["id"] for x in reg["adaptadores"]}:
            ad = instanciar(reg, aid, catalogo=cat, llm=llm, es_actual=(aid == a.actual), opciones={"nit": a.nit or ""})
        else:
            ad = instanciar(reg, "generico", catalogo=cat, llm=llm, es_actual=False, opciones={"nit": a.nit or "", "id": aid, "nombre": aid.upper(), "ramo": a.ramo})
        cot = ad.extraer(files)
        guardar_json(cot, Path(a.canonico) / f"{cot.aseguradora}.json")
        print(f"   {len(cot.vehiculos)} vehículos · {len(cot.items)} items · {len(cot.tasas)} tasas · {len(cot.clausulas)} cláusulas")
        cots.append(cot)
    return cots


def cmd_consolidar(a, cots=None):
    cat = cargar_catalogo(a.ramo, base=CONFIG_DIR)
    if cots is None:
        cots = [cargar_json(p) for p in sorted(Path(a.canonico).glob("*.json")) if p.name != "analisis.json"]
    if not cots:
        sys.exit("No hay cotizaciones canónicas; corre `extraer` primero.")
    # compañía actual primero, luego el resto en el orden dado
    cots.sort(key=lambda c: (not c.es_actual, c.aseguradora))
    llm = None if a.sin_llm else LLM(a.provider, a.model)
    actual = next((c for c in cots if c.es_actual), None)
    placas = [v.placa for v in actual.vehiculos] if actual else sorted({v.placa for c in cots for v in c.vehiculos})
    an = analizar(cots, cat, set(placas), llm)
    Path(a.canonico, "analisis.json").write_text(json.dumps(an, ensure_ascii=False, indent=2), encoding="utf-8")
    render = importlib.import_module(f"gestor_cotizaciones.ramos.{a.ramo}.render")
    cls = next(v for k, v in vars(render).items() if k.startswith("Render"))
    out = cls(cat, cots, an, placas).generar(a.salida)
    print(f"✔ consolidado generado: {out}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="gestor", description="Gestor de cotizaciones de seguros")
    sub = p.add_subparsers(dest="cmd", required=True)

    def comunes(sp):
        sp.add_argument("--ramo", default="movilidad")
        sp.add_argument("--canonico", default="out/canonico", help="carpeta de JSON canónicos")
        sp.add_argument("--provider", default=None, help="bedrock | anthropic | vertex | dry-run")
        sp.add_argument("--model", default=None)
        sp.add_argument("--sin-llm", action="store_true", help="solo parsers deterministas (sin coberturas narrativas ni scoring)")

    for name in ("extraer", "run"):
        sp = sub.add_parser(name); comunes(sp)
        sp.add_argument("archivos", nargs="+")
        sp.add_argument("--nit", help="NIT del tomador para filtrar pólizas/flota")
        sp.add_argument("--actual", help="id de la aseguradora vigente (p.ej. sura)")
        sp.add_argument("--otra", help="nombre para archivos sin adaptador (usa el genérico)")
        if name == "run":
            sp.add_argument("--salida", default="out/consolidado.xlsx")
    sp = sub.add_parser("consolidar"); comunes(sp)
    sp.add_argument("--salida", default="out/consolidado.xlsx")

    a = p.parse_args(argv)
    if a.cmd == "extraer":
        cmd_extraer(a)
    elif a.cmd == "consolidar":
        cmd_consolidar(a)
    else:
        cmd_consolidar(a, cmd_extraer(a))


if __name__ == "__main__":
    main()
