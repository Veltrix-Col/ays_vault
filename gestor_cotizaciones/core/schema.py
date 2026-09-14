"""
Esquema canónico de una cotización.

Todo adaptador (ramo × aseguradora × formato) debe producir un objeto `Cotizacion`.
Todo render (plantilla de consolidado) consume únicamente `Cotizacion`.
Así, agregar una aseguradora nunca toca el render, y agregar un ramo nunca toca los adaptadores.

Cada valor extraído lleva trazabilidad (`fuente`) y `confianza`, para que el analista
pueda validar sobre el consolidado y saber de dónde salió cada dato.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Origen(str, Enum):
    DETERMINISTA = "determinista"   # parser de código: cifras, tablas, cruces
    LLM = "llm"                     # extraído por modelo contra el catálogo
    ANALISTA = "analista"           # corregido/ingresado manualmente


class Fuente(BaseModel):
    archivo: str
    hoja: Optional[str] = None       # hoja xlsx o sección docx
    celda: Optional[str] = None      # p.ej. "B33" o "párrafo 12"
    texto_original: Optional[str] = None


class Valor(BaseModel):
    """Un dato extraído, con su valor original, normalizado y trazabilidad."""
    valor: Any = None                # valor normalizado (número, bool, str corta)
    texto: Optional[str] = None      # texto tal como aparece en el insumo
    unidad: Optional[str] = None     # COP | SMMLV | SMDLV | % | dias | eventos | bool | texto
    aplica: Optional[bool] = None    # True = "Si"/cubierto, False = "No"/no se otorga, None = no determinado
    origen: Origen = Origen.DETERMINISTA
    confianza: float = 1.0           # 0..1
    fuente: Optional[Fuente] = None
    nota: Optional[str] = None


class Plan(BaseModel):
    """Una columna del consolidado: combinación de plan comercial y segmento de vehículo."""
    id: str                          # p.ej. "sura.global_franquicia.livianos"
    nombre: str                      # "Global con Franquicia"
    segmento: str                    # "Automóviles, Camperos y Pickup" | "Pesados" | "Motos" | "Blindados"
    descripcion: Optional[str] = None


class ItemCobertura(BaseModel):
    cobertura_id: str                # id del catálogo canónico (config/ramos/<ramo>/catalogo.yaml)
    plan_id: str
    tipo: str = "cobertura"          # cobertura | deducible | asistencia | beneficio
    valor: Valor


class Tasa(BaseModel):
    segmento: str                    # clase agrupada tal como la nombra la aseguradora
    rango: str                       # rango de modelo, valor asegurado o antigüedad
    tasa: float                      # fracción (0.0172 = 1.72 %)
    tipo_rango: str = "modelo"       # modelo | valor_asegurado | antiguedad
    fuente: Optional[Fuente] = None


class Vehiculo(BaseModel):
    placa: str
    codigo_fasecolda: Optional[str] = None
    marca_referencia: Optional[str] = None
    modelo: Optional[int] = None
    clase: Optional[str] = None
    ciudad: Optional[str] = None
    chasis: Optional[str] = None
    motor: Optional[str] = None
    plan: Optional[str] = None
    valor_asegurado: Optional[float] = None
    valor_accesorios: Optional[float] = None
    valor_total: Optional[float] = None
    tasa: Optional[float] = None
    prima_neta: Optional[float] = None
    prima_con_iva: Optional[float] = None
    prima_anterior_con_iva: Optional[float] = None   # solo la compañía actual
    numero_siniestros: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Clausula(BaseModel):
    titulo: str
    texto: str
    categoria: str = "condicion_particular"   # condicion_particular | condicion_general | exclusion | garantia | requisito
    fuente: Optional[Fuente] = None
    origen: Origen = Origen.DETERMINISTA
    confianza: float = 1.0


class Cotizacion(BaseModel):
    ramo: str
    aseguradora: str                 # id corto: "sura" | "bolivar" | "axa"
    aseguradora_nombre: str          # "AXA COLPATRIA"
    es_actual: bool = False
    tomador: Optional[str] = None
    nit: Optional[str] = None
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    fecha_cotizacion: Optional[date] = None
    modalidad_pago: Optional[str] = None
    tipo_tarifa: Optional[str] = None       # "Tasa Única" | "Tasa Individual"
    guia_fasecolda: Optional[str] = None
    descuento_flota: Optional[float] = None
    condicionados: list[str] = Field(default_factory=list)
    notas: list[str] = Field(default_factory=list)

    planes: list[Plan] = Field(default_factory=list)
    items: list[ItemCobertura] = Field(default_factory=list)
    tasas: list[Tasa] = Field(default_factory=list)
    vehiculos: list[Vehiculo] = Field(default_factory=list)
    clausulas: list[Clausula] = Field(default_factory=list)

    archivos: list[str] = Field(default_factory=list)

    # ---- helpers ----
    def item(self, cobertura_id: str, plan_id: str, tipo: str = "cobertura") -> Optional[ItemCobertura]:
        for it in self.items:
            if it.cobertura_id == cobertura_id and it.plan_id == plan_id and it.tipo == tipo:
                return it
        return None

    def por_placa(self) -> dict[str, Vehiculo]:
        return {v.placa.upper().strip(): v for v in self.vehiculos}

    def prima_total_con_iva(self) -> float:
        return float(sum(v.prima_con_iva or 0 for v in self.vehiculos))
