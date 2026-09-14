"""
Normalizadores compartidos. Convierten texto de aseguradoras a valores canónicos.
Se usan tanto en los parsers deterministas como para post-procesar lo que devuelve el LLM.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from .schema import Valor

_SI = {"si", "sí", "si ampara", "aplica", "cubierto", "incluido", "ilimitado", "x", "true"}
_NO = {"no", "no aplica", "no se otorga", "no cubre", "no amparado", "n/a", "na", "-", "—", "false", "none"}


def limpiar(s: Any) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def compacta(r) -> list:
    """Celdas no vacías de una fila, en orden (elimina el desplazamiento de columnas vacías)."""
    return [c for c in r if c is not None and limpiar(c) != ""]


def a_bool(s: Any) -> Optional[bool]:
    t = limpiar(s).lower().rstrip(".")
    if not t:
        return None
    if t in _SI or t.startswith("si ") or t.startswith("sí "):
        return True
    if t in _NO or t.startswith("no se otorga") or t.startswith("no aplica"):
        return False
    return None


def a_numero(s: Any) -> Optional[float]:
    """'$4.100.000.000' -> 4100000000 ; '1,5' -> 1.5 ; 0.0172 -> 0.0172"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = limpiar(s).replace("$", "").replace(" ", "")
    if not t:
        return None
    # formato colombiano: puntos de miles, coma decimal
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", t):
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def a_fecha(s: Any) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    t = limpiar(s)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
             "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
    m = re.search(r"(\d{1,2})\s+de\s+([a-záé]+)\s+de\s+(\d{4})", t.lower())
    if m and m.group(2) in meses:
        return date(int(m.group(3)), meses[m.group(2)], int(m.group(1)))
    return None


def a_tasa(s: Any) -> Optional[float]:
    """Devuelve fracción. '1.72%' -> 0.0172 ; 1.72 -> 0.0172 ; 0.0172 -> 0.0172"""
    n = a_numero(str(s).replace("%", "")) if not isinstance(s, (int, float)) else float(s)
    if n is None:
        return None
    if isinstance(s, str) and "%" in s:
        return n / 100
    return n / 100 if n > 1 else n


def deducible(s: Any) -> Valor:
    """
    Interpreta expresiones de deducible:
      '0% - MINIMO 1.0 SMMLV' -> pct=0, min=1 SMMLV
      '10% min 2 smlmv'       -> pct=10, min=2
      '0.8 SMMLV'             -> fijo 0.8 SMMLV
      0.1                     -> 10 %
      'No cubre'              -> aplica=False
    Guarda el texto original; el valor normalizado es un dict {pct, min_smmlv, fijo_smmlv}.
    """
    texto = limpiar(s)
    v = Valor(texto=texto, unidad="deducible")
    if a_bool(texto) is False:
        v.aplica = False
        v.valor = None
        return v
    if isinstance(s, (int, float)):
        v.valor = {"pct": float(s) * 100 if s <= 1 else float(s), "min_smmlv": 0, "fijo_smmlv": None}
        v.aplica = True
        return v
    t = texto.lower().replace("smlmv", "smmlv").replace("mínimo", "min").replace("minimo", "min")
    pct = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    mn = re.search(r"(?:min|-)\s*(\d+(?:[.,]\d+)?)\s*smmlv", t)
    fijo = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*smmlv(?:\s*\(100%\))?", t)
    if fijo:
        v.valor = {"pct": None, "min_smmlv": None, "fijo_smmlv": a_numero(fijo.group(1))}
        v.aplica = True
    elif pct or mn:
        v.valor = {"pct": a_numero(pct.group(1)) if pct else 0.0,
                   "min_smmlv": a_numero(mn.group(1)) if mn else 0.0,
                   "fijo_smmlv": None}
        v.aplica = True
    elif t in ("0", "0.0", "$0"):
        v.valor = {"pct": 0.0, "min_smmlv": 0.0, "fijo_smmlv": None}
        v.aplica = True
    else:
        v.valor = None
        v.confianza = 0.5
        v.nota = "deducible no interpretado"
    return v


def cobertura_generica(s: Any) -> Valor:
    """Para celdas de cobertura: intenta bool, luego número (límite COP), si no deja texto."""
    texto = limpiar(s)
    v = Valor(texto=texto)
    b = a_bool(texto)
    if b is not None and len(texto) < 20:
        v.aplica, v.valor, v.unidad = b, b, "bool"
        return v
    n = a_numero(texto)
    if n is not None and not re.search(r"[a-zA-Z]", texto):
        v.aplica, v.valor, v.unidad = True, n, "COP"
        return v
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(smmlv|smlmv|smdlv|smldv)", texto.lower())
    if m:
        v.aplica, v.valor = True, a_numero(m.group(1))
        v.unidad = "SMMLV" if "mm" in m.group(2) or "lm" in m.group(2) else "SMDLV"
        return v
    v.aplica = None if not texto else True
    v.valor, v.unidad = texto, "texto"
    return v


def texto_para_celda(v: Valor) -> Any:
    """Cómo se escribe un Valor en el consolidado: preferimos el texto original legible."""
    if v is None:
        return None
    if v.aplica is False and (v.valor is None or v.unidad == "bool"):
        return "No"
    if v.unidad == "bool":
        return "Si" if v.valor else "No"
    if v.unidad == "COP" and isinstance(v.valor, (int, float)):
        return v.valor
    return v.texto or v.valor
