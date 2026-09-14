"""
Capa de análisis: a partir de las cotizaciones canónicas ya extraídas, el LLM PROPONE
puntajes 0-10 por categoría y por plan, ventajas/desventajas, scoring por tipo de vehículo
y una recomendación. Todo queda en celdas editables del consolidado (los pesos y las fórmulas
SUMPRODUCT viven en Excel, así el analista puede recalibrar sin volver a correr nada).

Si no hay LLM (dry-run), devuelve estructuras vacías y el render deja las celdas en amarillo.
"""
from __future__ import annotations

from typing import Any

from .llm import LLM
from .normalize import texto_para_celda
from .schema import Cotizacion

_SYSTEM = """Eres un analista senior de seguros de flota en Colombia. Comparas cotizaciones de varias aseguradoras
ya normalizadas a un esquema común y propones una calificación objetiva, explicando cada juicio con la evidencia
de coberturas, deducibles, asistencias y precio. No inventes coberturas: solo usa lo que aparece en los datos.
Puntajes 0-10 (10 = mejor para el cliente). Sé consistente: a igual cobertura, igual puntaje."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "scoring_coberturas": {"type": "array", "items": {"type": "object", "properties": {
            "plan_id": {"type": "string"}, "categoria_id": {"type": "string"},
            "puntaje": {"type": "number"}, "justificacion": {"type": "string"}},
            "required": ["plan_id", "categoria_id", "puntaje", "justificacion"]}},
        "scoring_tipo_vehiculo": {"type": "array", "items": {"type": "object", "properties": {
            "tipo": {"type": "string"}, "aseguradora": {"type": "string"}, "puntaje": {"type": "number"},
            "observacion": {"type": "string"}}, "required": ["tipo", "aseguradora", "puntaje"]}},
        "matriz_decision": {"type": "array", "items": {"type": "object", "properties": {
            "aseguradora": {"type": "string"}, "criterio_id": {"type": "string"}, "puntaje": {"type": "number"}},
            "required": ["aseguradora", "criterio_id", "puntaje"]}},
        "ventajas_desventajas": {"type": "array", "items": {"type": "object", "properties": {
            "aseguradora": {"type": "string"},
            "ventajas": {"type": "array", "items": {"type": "string"}},
            "desventajas": {"type": "array", "items": {"type": "string"}}},
            "required": ["aseguradora", "ventajas", "desventajas"]}},
        "recomendaciones": {"type": "array", "items": {"type": "object", "properties": {
            "perfil": {"type": "string"}, "recomendacion": {"type": "string"}, "justificacion": {"type": "string"}},
            "required": ["perfil", "recomendacion", "justificacion"]}},
        "conclusion": {"type": "string"},
    },
    "required": ["scoring_coberturas", "scoring_tipo_vehiculo", "matriz_decision", "ventajas_desventajas",
                 "recomendaciones", "conclusion"],
}


def _resumen_cotizacion(c: Cotizacion, placas: set[str]) -> str:
    lineas = [f"### {c.aseguradora_nombre} ({'compañía actual' if c.es_actual else 'oferente'})",
              f"tipo tarifa: {c.tipo_tarifa}; modalidad: {c.modalidad_pago}; notas: {'; '.join(c.notas)}",
              f"prima anual con IVA (flota comparable, {len(placas)} vehículos): "
              f"{sum(v.prima_con_iva or 0 for v in c.vehiculos if v.placa in placas):,.0f}"]
    if c.es_actual:
        lineas.append(f"prima anual anterior (2025): {sum(v.prima_anterior_con_iva or 0 for v in c.vehiculos if v.placa in placas):,.0f}")
    lineas.append("planes: " + "; ".join(f"{p.id}={p.nombre} [{p.segmento}]" for p in c.planes))
    for it in c.items:
        lineas.append(f"- {it.tipo}|{it.cobertura_id}|{it.plan_id}: {texto_para_celda(it.valor)}")
    for cl in c.clausulas[:40]:
        lineas.append(f"- clausula|{cl.categoria}|{cl.titulo}: {cl.texto[:300]}")
    return "\n".join(lineas)


def analizar(cots: list[Cotizacion], cat: dict, placas: set[str], llm: LLM | None) -> dict[str, Any]:
    vacio = {k: [] for k in _SCHEMA["properties"]} | {"conclusion": ""}
    if llm is None:
        return vacio
    categorias = "\n".join(f"- {c['id']}: {c['nombre']} (peso {c['peso']}) ← coberturas {c['coberturas']}" for c in cat["scoring"]["categorias"])
    criterios = "\n".join(f"- {c['id']}: {c['nombre']} (peso {c['peso']})" for c in cat["scoring"]["matriz_decision"])
    tipos = "Automóviles/Camperos/Pickups; Vehículos Blindados; Vehículos Pesados; Motocicletas > 250cc; Motocicletas < 250cc"
    user = f"""CATEGORÍAS DE SCORING (puntúa cada plan_id en cada categoria_id):
{categorias}

CRITERIOS DE LA MATRIZ DE DECISIÓN (puntúa cada aseguradora):
{criterios}

TIPOS DE VEHÍCULO para scoring por tipo (puntúa cada aseguradora en cada tipo): {tipos}

Para 'ventajas_desventajas' usa 3-6 frases cortas por aseguradora. Para 'recomendaciones' usa perfiles como
"Prioriza CALIDAD de coberturas", "Prioriza PRECIO", "Busca EQUILIBRIO", "Tiene VEHÍCULOS BLINDADOS", "Tiene VEHÍCULOS PESADOS".
Usa el id corto de aseguradora ({', '.join(c.aseguradora for c in cots)}).

DATOS:
""" + "\n\n".join(_resumen_cotizacion(c, placas) for c in cots)
    data = llm.extraer(nombre=f"{cots[0].ramo}_analisis", system=_SYSTEM, user=user, schema=_SCHEMA, max_tokens=12000)
    return data or vacio
