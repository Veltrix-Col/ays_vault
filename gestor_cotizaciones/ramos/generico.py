"""
Adaptador genérico: para una aseguradora/formato sin adaptador propio.
Convierte todos los archivos en bloques y delega la extracción completa al LLM
(planes, coberturas, deducibles, cláusulas, generales). Los vehículos NO se extraen aquí:
para eso el analista escribe un adaptador determinista o entrega la tabla en CSV.
"""
from __future__ import annotations

from gestor_cotizaciones.core.adaptador import Adaptador, extraer_con_llm
from gestor_cotizaciones.core.ingest import ingerir
from gestor_cotizaciones.core.schema import Plan


class AdaptadorGenerico(Adaptador):
    ramo = "movilidad"
    id = "generico"
    nombre = "OTRA ASEGURADORA"
    patrones = []

    def extraer(self, archivos):
        cot = self.nueva(archivos)
        cot.ramo = self.opciones.get("ramo", self.ramo)
        cot.aseguradora = self.opciones.get("id", self.id)
        cot.aseguradora_nombre = self.opciones.get("nombre", self.nombre)
        # planes por defecto = un plan por segmento del catálogo; el LLM elige el más parecido
        for s in self.cat["segmentos"]:
            cot.planes.append(Plan(id=f"{cot.aseguradora}.{s['id']}", nombre=s["nombre"], segmento=s["id"]))
        if self.llm is None:
            cot.notas.append("Adaptador genérico requiere LLM.")
            return cot
        bl = []
        for a in archivos:
            bl += ingerir(a)
        extraer_con_llm(cot, bl, self.cat, self.llm)
        return cot
