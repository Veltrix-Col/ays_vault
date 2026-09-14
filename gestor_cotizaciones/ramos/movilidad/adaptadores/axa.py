"""
AXA COLPATRIA — cotización de flota en un solo xlsx.

Determinista:
  - 'Condiciones Técnicas' : encabezado (tomador, vigencia, forma de pago) y bloques
                             "AMPAROS | VALOR ASEGURADO | DEDUCIBLES" por segmento.
  - 'Liquidación'          : vehículo a vehículo.
  - 'Tarifación y Cobro'   : tasas por clase agrupada y rango de modelo.
LLM:
  - 'Beneficios', 'Clausulas', 'Red Siniestros y Asistencia' : asistencias, beneficios, cláusulas, condicionados.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from gestor_cotizaciones.core.adaptador import Adaptador, extraer_con_llm
from gestor_cotizaciones.core.ingest import bloques_xlsx
from gestor_cotizaciones.core.normalize import a_fecha, a_numero, a_tasa, cobertura_generica, compacta, deducible, limpiar
from gestor_cotizaciones.core.schema import Fuente, ItemCobertura, Plan, Tasa, Valor, Vehiculo

# Mapa: patrón (en minúsculas, sobre la columna AMPAROS) -> id del catálogo
_AMPAROS = [
    (r"^responsabilidad civil", "rc_bienes"),
    (r"^protecci.n patrimonial", "rc_amparo_patrimonial"),
    (r"^p.rdida total por da", "ptd"),
    (r"^p.rdida parcial por da", "ppd"),
    (r"^p.rdida total por hurto", "pth"),
    (r"^p.rdida parcial por hurto", "pph"),
    (r"^temblor", "terremoto"),
    (r"^asistencia jur.dica en proceso penal", "rc_asist_juridica"),
    (r"^muerte accidental", "ap_conductor"),
    (r"^veh.culo sustituto", "veh_reemplazo"),
    (r"^gastos de transporte", "gt_danos"),
    (r"^cobertura de asistencia", "asistencia"),
]

# Segmentos de la hoja -> (plan_id, nombre, segmento canónico). El bloque "BLINDADOS" (col E) es plan aparte.
_BLOQUES = {
    "LIVIANOS PARTICULARES": ("axa.livianos", "Automóviles, Camperos y Pickup", "livianos"),
    "LIVIANOS UTILITARIOS": ("axa.utilitarios", "Livianos utilitarios", "livianos"),
    "PESADOS": ("axa.pesados", "Furgones", "pesados"),
    "MOTOCICLETAS Y MOTOCARROS": ("axa.motos", "Motos y Motocarros", "motos"),
    "TAXIS": ("axa.taxis", "Taxis", "livianos"),
}


class AdaptadorAxa(Adaptador):
    ramo = "movilidad"
    id = "axa"
    nombre = "AXA COLPATRIA"
    patrones = [r"axa"]

    def extraer(self, archivos):
        cot = self.nueva(archivos)
        xlsx = next(a for a in archivos if str(a).lower().endswith(".xlsx"))
        wb = load_workbook(xlsx, read_only=True, data_only=True)
        self._encabezado(cot, wb["Condiciones Técnicas"], xlsx)
        self._coberturas(cot, wb["Condiciones Técnicas"], xlsx)
        self._vehiculos(cot, wb["Liquidación"], xlsx)
        self._tasas(cot, wb["Tarifación y Cobro"], xlsx)
        cot.tipo_tarifa = "Tasa Única"
        if self.llm:
            bl = bloques_xlsx(xlsx, hojas=["Beneficios", "Clausulas", "Red Siniestros y Asistencia", "Condiciones Técnicas"])
            bl = [b for b in bl if not (b.seccion == "Condiciones Técnicas" and int(b.ref.split()[1]) > 130)]
            extraer_con_llm(cot, bl, self.cat, self.llm, instrucciones_extra=(
                "Los límites de RC, PTD/PPD/PTH/PPH y sus deducibles YA fueron extraídos; concéntrate en asistencias "
                "(grúa, conductor elegido, taller móvil, etc.), beneficios, vehículo sustituto, prima mínima, "
                "condicionados (URLs) y cláusulas. Para asistencias usa el plan 'PLUS' para livianos, "
                "'Pesados' para pesados y 'Moto Esencial' para motos."))
        return cot

    # ------------------------------------------------------------------
    def _encabezado(self, cot, ws, xlsx):
        rows = [compacta(r) for r in ws.iter_rows(min_row=1, max_row=20, values_only=True)]
        for i, r in enumerate(rows):
            t = [limpiar(c) for c in r]
            if not t:
                continue
            if t[0].startswith("FECHA DE COTIZACIÓN"):
                cot.fecha_cotizacion = a_fecha(r[1])
            elif t[0] == "VIGENCIA" and i + 1 < len(rows):
                fechas = [a_fecha(c) for c in rows[i + 1] if a_fecha(c)]
                if len(fechas) >= 2:
                    cot.vigencia_desde, cot.vigencia_hasta = fechas[0], fechas[1]
            elif t[0] == "TOMADOR":
                cot.tomador = t[1] if len(t) > 1 else None
                cot.nit = re.sub(r"\D", "", t[2]) if len(t) > 2 else None
            elif t[0].startswith("FORMA DE PAGO"):
                cot.modalidad_pago = t[1] if len(t) > 1 else None

    def _coberturas(self, cot, ws, xlsx):
        plan_actual = None
        for i, row in enumerate(ws.iter_rows(min_row=29, max_row=140, values_only=True), start=29):
            b = limpiar(row[1]) if len(row) > 1 else ""
            if not b:
                continue
            if b.upper() in _BLOQUES:
                pid, nombre, seg = _BLOQUES[b.upper()]
                plan_actual = pid
                cot.planes.append(Plan(id=pid, nombre=nombre, segmento=seg, descripcion=f"Bloque '{b}' de Condiciones Técnicas"))
                if b.upper() in ("LIVIANOS PARTICULARES",):
                    cot.planes.append(Plan(id="axa.blindados", nombre="Blindados", segmento="blindados"))
                if b.upper() == "PESADOS":
                    cot.planes.append(Plan(id="axa.remolques", nombre="Remolques, tanques o Trailers", segmento="pesados"))
                continue
            if plan_actual is None:
                continue
            cid = next((cid for pat, cid in _AMPAROS if re.search(pat, b.lower())), None)
            if cid is None:
                continue
            valor_col = limpiar(row[2]) if len(row) > 2 else ""
            ded_cols = [limpiar(row[3]) if len(row) > 3 else "", limpiar(row[4]) if len(row) > 4 else ""]
            fuente = lambda col: Fuente(archivo=Path(xlsx).name, hoja=ws.title, celda=f"{col}{i}")

            # columna secundaria (E) = blindados para livianos, remolques para pesados, opción 2 motos
            planes_cols = [(plan_actual, "D")]
            if plan_actual == "axa.livianos":
                planes_cols.append(("axa.blindados", "E"))
            elif plan_actual == "axa.pesados":
                planes_cols.append(("axa.remolques", "E"))

            for k, (pid, col) in enumerate(planes_cols):
                ded = ded_cols[k]
                if cid in ("rc_bienes", "ptd", "ppd", "pth", "pph", "terremoto"):
                    # cobertura
                    if ded.upper().startswith("NO APLICA"):
                        v = Valor(texto=ded, aplica=False, valor=False, unidad="bool")
                    elif cid == "rc_bienes":
                        v = self._limite_rc(valor_col)
                    else:
                        v = Valor(texto=valor_col, aplica=True, valor=True, unidad="bool")
                    v.fuente = fuente("C")
                    cot.items.append(ItemCobertura(cobertura_id=cid, plan_id=pid, tipo="cobertura", valor=v))
                    # deducible
                    if ded:
                        dv = deducible(ded) if not ded.lower().startswith("según") else Valor(texto=ded, aplica=True, valor=ded, unidad="texto")
                        dv.fuente = fuente(col)
                        cot.items.append(ItemCobertura(cobertura_id=cid, plan_id=pid, tipo="deducible", valor=dv))
                else:
                    detalle = ded_cols[0]
                    texto = valor_col if not detalle else f"{detalle}"
                    if ded.upper().startswith("NO APLICA") or valor_col.upper().startswith("NO SE OTORGA"):
                        v = Valor(texto=ded or valor_col, aplica=False, valor=False, unidad="bool")
                    else:
                        v = cobertura_generica(texto)
                        if cid == "ap_conductor":
                            m = re.search(r"\$?\s*(\d+)\s*millones", b.lower())
                            if m:
                                v = Valor(texto=b, aplica=True, valor=float(m.group(1)) * 1_000_000, unidad="COP")
                    v.fuente = fuente("C")
                    cot.items.append(ItemCobertura(cobertura_id=cid, plan_id=pid, tipo="cobertura", valor=v))
                    if cid == "rc_asist_juridica":  # AXA lo lista penal y civil por separado
                        pass
            # RC: replicar límite a personas (LUC = límite único combinado)
            if cid == "rc_bienes":
                for pid, _ in planes_cols:
                    it = cot.item("rc_bienes", pid)
                    if it and it.valor.aplica:
                        for sub in ("rc_una_persona", "rc_dos_personas"):
                            cot.items.append(ItemCobertura(cobertura_id=sub, plan_id=pid, valor=Valor(
                                texto="Incluido en límite único combinado", aplica=True, valor="LUC", unidad="texto", fuente=fuente("C"))))

    @staticmethod
    def _limite_rc(texto: str) -> Valor:
        t = texto.lower().replace("\n", " ")
        m = re.search(r"([\d.,]+)\s*millones", t)
        if m:
            return Valor(texto=texto, aplica=True, valor=a_numero(m.group(1)) * 1_000_000, unidad="COP")
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*millones", t)
        if m:
            return Valor(texto=texto, aplica=True, valor=texto, unidad="texto")
        return cobertura_generica(texto)

    def _vehiculos(self, cot, ws, xlsx):
        rows = list(ws.iter_rows(values_only=True))
        hdr_i = next(i for i, r in enumerate(rows) if r and any(limpiar(c).upper() == "ITEM" for c in r))
        hdr = [limpiar(c).upper() for c in rows[hdr_i]]
        col = {n: hdr.index(n) for n in hdr if n}
        for r in rows[hdr_i + 1:]:
            if not r or r[col["ITEM"]] is None or not r[col["PLACA"]]:
                continue
            clase, _ = self.clase_canonica(r[col["CLASE"]])
            cot.vehiculos.append(Vehiculo(
                placa=limpiar(r[col["PLACA"]]).upper(),
                codigo_fasecolda=str(r[col["COD FASECOLDA"]]).zfill(8) if r[col["COD FASECOLDA"]] else None,
                marca_referencia=limpiar(r[col["MARCA"]]), modelo=int(r[col["MODELO"]]) if r[col["MODELO"]] else None,
                clase=clase, ciudad=limpiar(r[col["ZONA DE CIRCULACIÓN"]]), plan=limpiar(r[col["TIPO DE ASISTENCIA"]]),
                valor_asegurado=a_numero(r[col["VR. COMERCIAL (GUIA FASECOLDA)"]]),
                valor_accesorios=a_numero(r[col["VR. ACCESORIOS"]]),
                valor_total=a_numero(r[col["VR. ASEGURADO"]]),
                tasa=a_tasa(r[col["TASA FINAL"]]),
                prima_neta=a_numero(r[col["PRIMA REQUERIDA + PM"]]),
                prima_con_iva=a_numero(r[col["TOTAL A PAGAR"]]),
                extra={"servicio": limpiar(r[col["SERVICIO"]]), "prima_minima": a_numero(r[col["PRIMA MÍNIMA"]])},
            ))
        # Guía Fasecolda
        for r in rows[:hdr_i]:
            t = " ".join(limpiar(c) for c in r if c)
            m = re.search(r"FASECOLDA\s+(\d+)", t.upper())
            if m:
                cot.guia_fasecolda = m.group(1)

    def _tasas(self, cot, ws, xlsx):
        clase = None
        for i, r in enumerate(ws.iter_rows(min_row=10, max_row=60, values_only=True), start=10):
            a, b, c = (limpiar(r[1]) if len(r) > 1 else "", limpiar(r[2]) if len(r) > 2 else "", r[3] if len(r) > 3 else None)
            if a and not a.startswith("TIPO DE VEHICULO"):
                clase = a
            if clase and b and c is not None and a_tasa(c) is not None:
                cot.tasas.append(Tasa(segmento=clase, rango=b, tasa=a_tasa(c), tipo_rango="modelo",
                                      fuente=Fuente(archivo=Path(xlsx).name, hoja=ws.title, celda=f"D{i}")))
