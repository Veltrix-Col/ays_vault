"""
Contrato de los adaptadores + extracción genérica por LLM.

Un adaptador = (ramo, aseguradora, formato). Recibe la lista de archivos de esa aseguradora
y devuelve una `Cotizacion`. Lo que sea tabla se parsea con código (`_determinista`);
lo narrativo se delega a `extraer_con_llm`, que mapea texto → catálogo canónico.

Regla de oro: el LLM nunca escribe cifras que ya obtuvo el parser determinista.
`fusionar_items` respeta lo determinista y solo completa vacíos.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from .ingest import Bloque, a_texto
from .llm import LLM
from .normalize import a_bool, deducible as norm_deducible
from .schema import Clausula, Cotizacion, Fuente, ItemCobertura, Origen, Plan, Valor


def cargar_catalogo(ramo: str, base: str | Path = "config") -> dict[str, Any]:
    return yaml.safe_load((Path(base) / "ramos" / ramo / "catalogo.yaml").read_text(encoding="utf-8"))


def ids_coberturas(cat: dict) -> list[dict]:
    out = []
    for g in cat["coberturas"]:
        for it in g["items"]:
            out.append({**it, "grupo": g["grupo"]})
    return out


class Adaptador(ABC):
    ramo: str = ""
    id: str = ""            # "axa"
    nombre: str = ""        # "AXA COLPATRIA"
    # patrones (regex, sobre nombre de archivo en minúsculas) para auto-detección de archivos
    patrones: list[str] = []

    def __init__(self, catalogo: dict, llm: LLM | None = None, es_actual: bool = False,
                 opciones: dict | None = None):
        self.cat = catalogo
        self.llm = llm
        self.es_actual = es_actual
        self.opciones = opciones or {}     # p.ej. {"nit": "8909268031"} para filtrar pólizas/tomador

    @classmethod
    def acepta(cls, archivo: str | Path) -> bool:
        n = Path(archivo).name.lower()
        return any(re.search(p, n) for p in cls.patrones)

    @abstractmethod
    def extraer(self, archivos: list[str | Path]) -> Cotizacion: ...

    # ---------- utilidades comunes ----------
    def nueva(self, archivos) -> Cotizacion:
        return Cotizacion(ramo=self.ramo, aseguradora=self.id, aseguradora_nombre=self.nombre,
                          es_actual=self.es_actual, archivos=[str(a) for a in archivos])

    def clase_canonica(self, texto: str | None) -> tuple[str | None, str | None]:
        """Devuelve (clase_nombre_canonico, segmento_id) a partir del nombre de clase de la aseguradora."""
        if not texto:
            return None, None
        t = str(texto).strip().lower()
        for c in self.cat["clases"]:
            for s in [c["nombre"]] + c.get("sinonimos", []):
                if t == s.lower() or t.startswith(s.lower()):
                    return c["nombre"], c["segmento"]
        return texto, None


# ----------------------------------------------------------------------------------------
#  Extracción narrativa con LLM
# ----------------------------------------------------------------------------------------
_SYSTEM = """Eres un analista senior de seguros en Colombia. Recibes el texto de una cotización o slip
de una aseguradora (ramo {ramo}) y un CATÁLOGO canónico de coberturas con ids.
Tu tarea es mapear lo que dice el documento al catálogo, POR PLAN, sin inventar nada.

Reglas:
- Solo registra lo que el documento dice explícitamente. Si una cobertura del catálogo no se menciona
  para un plan, NO la registres (el consolidado la mostrará como "No" / vacío para revisión).
- `texto` debe ser una cita corta y fiel del documento (límite, condición, número de eventos), en español.
- `aplica`: true si se otorga/cubre; false si dice "no se otorga", "no aplica", "no cubre"; null si es ambiguo.
- `fuente_ref` es la etiqueta [archivo | sección | ref] del bloque de donde sale el dato.
- `confianza` 0-1: 1 cuando el mapeo al id es inequívoco; baja cuando el nombre de la aseguradora difiere
  del catálogo o hay que interpretar.
- Los deducibles van con tipo "deducible" y `texto` con la expresión original (p.ej. "10% mín 1 SMMLV").
- Clasifica cada cláusula/condición relevante en: condicion_particular, condicion_general, exclusion, garantia, requisito.
- Extrae también datos generales si aparecen: modalidad de pago, tipo de tarifa (única/individual),
  guía Fasecolda, condicionados vigentes (códigos/URLs), notas aclaratorias (prima mínima, abonos parciales, etc.).
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "cobertura_id": {"type": "string"},
            "plan_id": {"type": "string"},
            "tipo": {"type": "string", "enum": ["cobertura", "deducible"]},
            "texto": {"type": "string"},
            "aplica": {"type": ["boolean", "null"]},
            "confianza": {"type": "number"},
            "fuente_ref": {"type": "string"},
        }, "required": ["cobertura_id", "plan_id", "tipo", "texto", "confianza", "fuente_ref"]}},
        "clausulas": {"type": "array", "items": {"type": "object", "properties": {
            "titulo": {"type": "string"}, "texto": {"type": "string"},
            "categoria": {"type": "string", "enum": ["condicion_particular", "condicion_general", "exclusion", "garantia", "requisito"]},
            "fuente_ref": {"type": "string"}, "confianza": {"type": "number"},
        }, "required": ["titulo", "texto", "categoria", "fuente_ref", "confianza"]}},
        "generales": {"type": "object", "properties": {
            "modalidad_pago": {"type": ["string", "null"]},
            "tipo_tarifa": {"type": ["string", "null"]},
            "guia_fasecolda": {"type": ["string", "null"]},
            "condicionados": {"type": "array", "items": {"type": "string"}},
            "notas": {"type": "array", "items": {"type": "string"}},
        }},
    },
    "required": ["items", "clausulas", "generales"],
}


def extraer_con_llm(cot: Cotizacion, bloques: list[Bloque], cat: dict, llm: LLM,
                    instrucciones_extra: str = "") -> None:
    """Completa `cot` (items, clausulas, generales) a partir de texto narrativo. Muta `cot`."""
    catalogo_txt = "\n".join(
        f"- {c['id']}: {c['nombre']}" + (f"  (sinónimos: {', '.join(c['sinonimos'])})" if c.get("sinonimos") else "")
        for c in ids_coberturas(cat))
    deduc_txt = "\n".join(f"- {d['id']}: {d['nombre']}" for d in cat["deducibles"])
    planes_txt = "\n".join(f"- {p.id}: {p.nombre} / segmento {p.segmento}" + (f" — {p.descripcion}" if p.descripcion else "")
                           for p in cot.planes)
    user = f"""ASEGURADORA: {cot.aseguradora_nombre}

PLANES (usa exactamente estos plan_id; si el documento describe un plan que no está aquí, usa el más parecido y baja la confianza):
{planes_txt}

CATÁLOGO DE COBERTURAS (cobertura_id: nombre):
{catalogo_txt}

CATÁLOGO DE DEDUCIBLES (mismos ids, tipo "deducible"):
{deduc_txt}

{instrucciones_extra}

DOCUMENTO:
{a_texto(bloques)}
"""
    data = llm.extraer(nombre=f"{cot.ramo}_{cot.aseguradora}_coberturas",
                       system=_SYSTEM.format(ramo=cot.ramo), user=user, schema=_SCHEMA)
    if not data:
        cot.notas.append("Extracción LLM pendiente (dry-run o sin respuesta): coberturas narrativas no cargadas.")
        return

    validos = {c["id"] for c in ids_coberturas(cat)} | {d["id"] for d in cat["deducibles"]}
    planes = {p.id for p in cot.planes}
    nuevos: list[ItemCobertura] = []
    for it in data.get("items", []):
        if it["cobertura_id"] not in validos or it["plan_id"] not in planes:
            continue
        fuente = Fuente(archivo=it.get("fuente_ref", ""), texto_original=it.get("texto"))
        if it["tipo"] == "deducible":
            v = norm_deducible(it["texto"])
        else:
            v = Valor(texto=it["texto"], aplica=it.get("aplica", a_bool(it["texto"])),
                      valor=it["texto"], unidad="texto")
        v.origen, v.confianza, v.fuente = Origen.LLM, float(it.get("confianza", 0.7)), fuente
        nuevos.append(ItemCobertura(cobertura_id=it["cobertura_id"], plan_id=it["plan_id"], tipo=it["tipo"], valor=v))
    fusionar_items(cot, nuevos)

    for c in data.get("clausulas", []):
        cot.clausulas.append(Clausula(titulo=c["titulo"], texto=c["texto"], categoria=c["categoria"],
                                      fuente=Fuente(archivo=c.get("fuente_ref", "")),
                                      origen=Origen.LLM, confianza=float(c.get("confianza", 0.7))))
    g = data.get("generales", {}) or {}
    cot.modalidad_pago = cot.modalidad_pago or g.get("modalidad_pago")
    cot.tipo_tarifa = cot.tipo_tarifa or g.get("tipo_tarifa")
    cot.guia_fasecolda = cot.guia_fasecolda or g.get("guia_fasecolda")
    cot.condicionados += [c for c in g.get("condicionados", []) if c not in cot.condicionados]
    cot.notas += [n for n in g.get("notas", []) if n not in cot.notas]


def fusionar_items(cot: Cotizacion, nuevos: list[ItemCobertura]) -> None:
    """Agrega items sin pisar los deterministas; entre dos LLM gana el de mayor confianza."""
    for n in nuevos:
        ex = cot.item(n.cobertura_id, n.plan_id, n.tipo)
        if ex is None:
            cot.items.append(n)
        elif ex.valor.origen == Origen.LLM and n.valor.confianza > ex.valor.confianza:
            ex.valor = n.valor


def guardar_json(cot: Cotizacion, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(cot.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")


def cargar_json(path: str | Path) -> Cotizacion:
    return Cotizacion.model_validate_json(Path(path).read_text(encoding="utf-8"))
