from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any


PERSON_NATURAL = "Persona natural"
PERSON_LEGAL = "Persona jurídica"
EXPECTED_ID_TYPES = ("CC", "CE", "RC", "TI", "PP", "PEP", "EX", "NUIP", "PPT", "NIT")

PERSON_GROUPS = {
    PERSON_NATURAL: "natural",
    PERSON_LEGAL: "legal",
}
GROUP_LABELS = {
    "natural": PERSON_NATURAL,
    "legal": PERSON_LEGAL,
    "empty": "Vacío",
    "other": "Otros valores no esperados",
}

NATURAL_COVERAGE_FIELDS = (
    "N_mero_de_ID",
    "First_Name",
    "Last_Name",
    "Full_Name",
    "Empresa",
    "Estado",
)
LEGAL_COVERAGE_FIELDS = (
    "N_mero_de_ID",
    "Raz_n_social",
    "Nombre_comercial",
    "Full_Name",
    "Last_Name",
    "Estado",
)

_DIGITS = re.compile(r"^\d+$")
_DIGITS_HYPHEN = re.compile(r"^\d+(?:-\d+)+$")
_DIGITS_POINTS = re.compile(r"^\d+(?:\.\d+)+$")
_DIGITS_SPACES = re.compile(r"^\d+(?:\s+\d+)+$")
_NIT_WITH_DV = re.compile(r"^(\d+)-(\d)$")
_ANALYSIS_SEPARATORS = re.compile(r"[\s.\-]+")


def is_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def classify_document_pattern(value: object) -> str:
    if not is_present(value):
        return "empty"
    text = str(value).strip()
    if _DIGITS.fullmatch(text):
        return "digits_only"
    if _DIGITS_HYPHEN.fullmatch(text):
        return "digits_with_hyphen"
    if _DIGITS_POINTS.fullmatch(text):
        return "digits_with_points"
    if _DIGITS_SPACES.fullmatch(text):
        return "digits_with_spaces"
    if text.isalnum() and any(char.isalpha() for char in text) and any(
        char.isdigit() for char in text
    ):
        return "alphanumeric"
    return "other"


def classify_lookup_structure(value: object) -> str:
    if value is None or value == "" or value == {}:
        return "empty"
    if not isinstance(value, dict):
        return "unknown"
    has_id = is_present(value.get("id"))
    has_name = is_present(value.get("name"))
    if has_id and has_name:
        return "dict_id_and_name"
    if has_id:
        return "dict_id_only"
    if has_name:
        return "dict_name_only"
    return "dict_other"


class ContactsProfileAccumulator:
    """Conserva solo agregados y HMAC efímeros, nunca registros completos."""

    def __init__(self) -> None:
        self._hash_key = secrets.token_bytes(32)
        self.processed = 0
        self.person_counts: Counter[str] = Counter()
        self.id_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.coverage: dict[str, dict[str, Counter[str]]] = {
            "natural": {field: Counter() for field in NATURAL_COVERAGE_FIELDS},
            "legal": {field: Counter() for field in LEGAL_COVERAGE_FIELDS},
        }
        self.document_patterns: dict[str, Counter[str]] = defaultdict(Counter)
        self.document_lengths: dict[str, Counter[int]] = defaultdict(Counter)
        self.nit_patterns: Counter[str] = Counter()
        self.nit_before_lengths: Counter[int] = Counter()
        self.nit_after_lengths: Counter[int] = Counter()
        self.lookup_structures: Counter[str] = Counter()
        self.consistency: Counter[str] = Counter()
        self._duplicates: dict[str, dict[tuple[str, str], Counter[bytes]]] = {
            "exact": defaultdict(Counter),
            "analysis_normalized": defaultdict(Counter),
        }

    @staticmethod
    def _person_group(value: object) -> str:
        if not is_present(value):
            return "empty"
        return PERSON_GROUPS.get(str(value).strip(), "other")

    @staticmethod
    def _id_group(value: object) -> str:
        if not is_present(value):
            return "empty"
        text = str(value).strip()
        return text if text in EXPECTED_ID_TYPES else "other"

    def _digest(self, value: str) -> bytes:
        return hmac.new(self._hash_key, value.encode("utf-8"), hashlib.sha256).digest()

    def consume(self, record: dict[str, Any]) -> None:
        self.processed += 1
        person = self._person_group(record.get("Tipo_de_persona"))
        id_type = self._id_group(record.get("Tipo_ID"))
        self.person_counts[person] += 1
        self.id_counts[person][id_type] += 1

        if person in self.coverage:
            for field, counts in self.coverage[person].items():
                counts["populated" if is_present(record.get(field)) else "empty"] += 1

        document = record.get("N_mero_de_ID")
        pattern = classify_document_pattern(document)
        self.document_patterns[person][pattern] += 1
        if is_present(document):
            stripped = str(document).strip()
            self.document_lengths[person][len(stripped)] += 1
            segment = (person, id_type)
            self._duplicates["exact"][segment][self._digest(stripped)] += 1
            normalized = _ANALYSIS_SEPARATORS.sub("", stripped)
            if normalized:
                self._duplicates["analysis_normalized"][segment][self._digest(normalized)] += 1

            if id_type == "NIT":
                if _DIGITS.fullmatch(stripped):
                    self.nit_patterns["digits_only"] += 1
                else:
                    match = _NIT_WITH_DV.fullmatch(stripped)
                    if match:
                        self.nit_patterns["hyphen_with_possible_check_digit"] += 1
                        self.nit_before_lengths[len(match.group(1))] += 1
                        self.nit_after_lengths[len(match.group(2))] += 1
                    elif "-" in stripped:
                        self.nit_patterns["other_hyphen"] += 1
                    else:
                        self.nit_patterns["other_format"] += 1
        elif id_type == "NIT":
            self.nit_patterns["empty"] += 1

        self.lookup_structures[classify_lookup_structure(record.get("Empresa"))] += 1

        if person == "legal" and id_type != "NIT":
            self.consistency["legal_with_non_nit_id_type"] += 1
        if person == "natural" and id_type == "NIT":
            self.consistency["natural_with_nit"] += 1
        if person == "legal" and not (
            is_present(record.get("Raz_n_social"))
            or is_present(record.get("Nombre_comercial"))
        ):
            self.consistency["legal_without_company_name"] += 1
        if person == "natural" and not is_present(record.get("Full_Name")):
            self.consistency["natural_without_full_name"] += 1
        if person == "empty":
            self.consistency["missing_person_type"] += 1
        elif person == "other":
            self.consistency["unexpected_person_type"] += 1
        if id_type == "empty":
            self.consistency["missing_id_type"] += 1
        elif id_type == "other":
            self.consistency["unexpected_id_type"] += 1
        if not is_present(document):
            self.consistency["missing_document"] += 1

    @staticmethod
    def _counter(counter: Counter) -> dict[str, int]:
        return {str(key): counter[key] for key in sorted(counter, key=str)}

    def _duplicate_summary(self, mode: str) -> list[dict[str, object]]:
        result = []
        for (person, id_type), values in sorted(self._duplicates[mode].items()):
            repeated = [count for count in values.values() if count > 1]
            if repeated:
                result.append(
                    {
                        "person_group": person,
                        "id_type": id_type,
                        "repeated_documents": len(repeated),
                        "affected_records": sum(repeated),
                    }
                )
        return result

    def result(self, *, complete: bool, pages: int, stop_reason: str = "") -> dict[str, object]:
        coverage: dict[str, dict[str, dict[str, int]]] = {}
        for person, fields in self.coverage.items():
            coverage[person] = {}
            for field, counts in fields.items():
                coverage[person][field] = {
                    "populated": counts["populated"],
                    "empty": counts["empty"],
                }
        return {
            "processed": self.processed,
            "complete": complete,
            "pages": pages,
            "stop_reason": stop_reason,
            "person_counts": self._counter(self.person_counts),
            "id_counts": {
                person: self._counter(counts) for person, counts in sorted(self.id_counts.items())
            },
            "coverage": coverage,
            "document_patterns": {
                person: self._counter(counts)
                for person, counts in sorted(self.document_patterns.items())
            },
            "document_lengths": {
                person: self._counter(counts)
                for person, counts in sorted(self.document_lengths.items())
            },
            "nit_patterns": self._counter(self.nit_patterns),
            "nit_before_hyphen_lengths": self._counter(self.nit_before_lengths),
            "nit_after_hyphen_lengths": self._counter(self.nit_after_lengths),
            "duplicates_exact": self._duplicate_summary("exact"),
            "duplicates_analysis_normalized": self._duplicate_summary("analysis_normalized"),
            "consistency": self._counter(self.consistency),
            "lookup_structures": self._counter(self.lookup_structures),
        }


def _rows(mapping: dict[str, int], labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    if not mapping:
        return "| Sin valores | 0 |"
    return "\n".join(f"| {labels.get(key, key)} | {value} |" for key, value in mapping.items())


def _coverage_rows(fields: dict[str, dict[str, int]]) -> str:
    rows = []
    total = sum(next(iter(fields.values())).values()) if fields else 0
    for field, counts in fields.items():
        populated = counts["populated"]
        percentage = (populated * 100 / total) if total else 0
        rows.append(f"| `{field}` | {total} | {populated} | {counts['empty']} | {percentage:.1f}% |")
    return "\n".join(rows)


def _duplicate_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return "| Ninguno | Ninguno | 0 | 0 |"
    return "\n".join(
        f"| {GROUP_LABELS.get(str(item['person_group']), item['person_group'])} | {item['id_type']} | "
        f"{item['repeated_documents']} | {item['affected_records']} |"
        for item in items
    )


def render_contacts_profile_markdown(result: dict[str, object]) -> str:
    person_counts = result["person_counts"]
    id_counts = result["id_counts"]
    natural_total = int(person_counts.get("natural", 0))
    legal_total = int(person_counts.get("legal", 0))
    normalized_duplicates = result["duplicates_analysis_normalized"]
    has_duplicates = bool(normalized_duplicates)
    legal_coverage = result["coverage"]["legal"]
    reason_coverage = legal_coverage["Raz_n_social"]["populated"]
    commercial_coverage = legal_coverage["Nombre_comercial"]["populated"]
    primary_company = "Raz_n_social" if reason_coverage >= commercial_coverage else "Nombre_comercial"
    fallback_company = "Nombre_comercial" if primary_company == "Raz_n_social" else "Raz_n_social"
    observed_separate_check_digit = bool(
        result["nit_patterns"].get("hyphen_with_possible_check_digit", 0)
    )
    nit_recommendation = (
        "separar base y posible dígito de verificación para la entrada, conservando "
        "también el valor exacto registrado"
        if observed_separate_check_digit
        else "consultar el valor exacto registrado; la muestra no demuestra un dígito "
        "de verificación separado y no autoriza a quitar el último dígito"
    )
    status = "Completo" if result["complete"] else "Parcial"
    generated = datetime.now(UTC).isoformat(timespec="seconds")

    id_sections = []
    for person, counts in id_counts.items():
        id_sections.append(
            f"### {GROUP_LABELS.get(person, person)}\n\n| Tipo ID | Cantidad |\n|---|---:|\n{_rows(counts)}"
        )

    pattern_sections = []
    for person, counts in result["document_patterns"].items():
        pattern_sections.append(
            f"### {GROUP_LABELS.get(person, person)}\n\n| Patrón | Cantidad |\n|---|---:|\n{_rows(counts)}\n\n"
            f"Longitudes: `{result['document_lengths'].get(person, {})}`."
        )

    return f"""# Perfil agregado de Contacts

## 1. Alcance

Diagnóstico de calidad de datos limitado al módulo `Contacts`, con campos fijos y operaciones de solo lectura. El informe no contiene documentos, nombres, identificadores, hashes ni respuestas originales.

## 2. Perfil y entorno

- Perfil: `sandbox`.
- Entorno confirmado: `sandbox`.
- Generado: {generated}.
- Páginas procesadas: {result['pages']}.

## 3. Total procesado

- Registros: **{result['processed']}**.
- Resultado: **{status}**.
- Motivo de detención: `{result['stop_reason'] or 'fin_de_paginacion'}`.

## 4. Distribución por Tipo_de_persona

| Segmento | Cantidad |
|---|---:|
{_rows(person_counts, GROUP_LABELS)}

## 5. Distribución por Tipo_ID

{chr(10).join(id_sections)}

## 6. Cobertura de campos

### Persona natural

| Campo | Total | Poblado | Vacío | Cobertura |
|---|---:|---:|---:|---:|
{_coverage_rows(result['coverage']['natural'])}

### Persona jurídica

| Campo | Total | Poblado | Vacío | Cobertura |
|---|---:|---:|---:|---:|
{_coverage_rows(result['coverage']['legal'])}

## 7. Patrones de documento

{chr(10).join(pattern_sections)}

### NIT

| Patrón | Cantidad |
|---|---:|
{_rows(result['nit_patterns'])}

- Longitud antes del guion: `{result['nit_before_hyphen_lengths']}`.
- Longitud después del guion: `{result['nit_after_hyphen_lengths']}`.

## 8. Duplicados agregados

### Comparación exacta tras strip exterior

| Segmento | Tipo ID | Documentos repetidos | Registros afectados |
|---|---|---:|---:|
{_duplicate_rows(result['duplicates_exact'])}

### Comparación analítica sin espacios, puntos ni guiones

| Segmento | Tipo ID | Documentos repetidos | Registros afectados |
|---|---|---:|---:|
{_duplicate_rows(normalized_duplicates)}

Los HMAC fueron efímeros, permanecieron en memoria durante la ejecución y no se guardaron ni imprimieron.

## 9. Inconsistencias agregadas

| Regla | Cantidad |
|---|---:|
{_rows(result['consistency'])}

## 10. Estructura del lookup Empresa

| Estructura | Cantidad |
|---|---:|
{_rows(result['lookup_structures'])}

No se siguió el lookup y no se mostraron sus valores.

## 11. Recomendación para buscador de empresas

- `Contacts` como módulo: {'evidencia suficiente por presencia de personas jurídicas' if legal_total else 'sin evidencia suficiente en los registros procesados'}.
- Filtro obligatorio: `Tipo_de_persona = Persona jurídica` y `Tipo_ID = NIT`.
- Documento exacto: `N_mero_de_ID`, conservando una lista de selección cuando existan coincidencias múltiples.
- Nombre principal por cobertura observada: `{primary_company}`.
- Fallback: `{fallback_company}`.

## 12. Recomendación para buscador de individuos

- `Contacts` como módulo: {'evidencia suficiente por presencia de personas naturales' if natural_total else 'sin evidencia suficiente en los registros procesados'}.
- Filtro obligatorio: `Tipo_de_persona = Persona natural`; usar también `Tipo_ID` cuando el usuario conozca el tipo documental.
- Documento exacto: `N_mero_de_ID`.
- Nombre principal: `Full_Name`; para presentación estructurada pueden usarse `First_Name` y `Last_Name`.

## 13. Normalización documental recomendada

Aplicar inicialmente `strip` exterior y comparación exacta. La eliminación de espacios, puntos y guiones se utilizó solo para diagnosticar equivalencias y debe adoptarse en búsqueda únicamente después de validar su impacto por tipo documental. Para NIT, {nit_recommendation}, sin mezclar tipos de identificación.

## 14. Decisión técnica

1. Contacts para empresas: **{'sí' if legal_total else 'no confirmado'}**.
2. Contacts para individuos: **{'sí' if natural_total else 'no confirmado'}**.
3. `N_mero_de_ID` para búsqueda exacta: **sí, combinado con segmento y tipo documental**.
4. Exigir `Tipo_ID`: **sí, para reducir falsos positivos y separar NIT de documentos personales**.
5. Campo principal de empresa: **`{primary_company}`**.
6. Fallback de empresa: **`{fallback_company}`**.
7. Campo principal del individuo: **`Full_Name`**.
8. Normalización sin falsos positivos demostrados: **solo strip exterior**; normalización adicional queda condicionada por tipo.
9. NIT con dígito de verificación: **{nit_recommendation}**.
10. Duplicados y selección: **{'sí; existen equivalencias repetidas en el diagnóstico' if has_duplicates else 'no se detectaron en los registros procesados, pero la interfaz debe tolerarlos'}**.
11. Inconsistencias: deben manejarse como resultados incompletos, nunca corregirse automáticamente ni ocultarse.
12. Evidencia para ambos buscadores: **{'suficiente para un diseño defensivo' if natural_total and legal_total and result['complete'] else 'parcial; conservar validaciones y estados de datos incompletos'}**.

## 15. Limitaciones

- El resultado describe únicamente el estado observado en Sandbox al momento de ejecución.
- No valida relaciones con pólizas, asegurados o riesgos.
- Las equivalencias normalizadas son diagnósticas y no autorizan una transformación destructiva.
- Un resultado parcial no representa la totalidad del módulo.

## 16. Pendientes de relaciones

Validar por metadata y un muestreo independiente, expresamente autorizado, los lookups entre `Contacts`, `Polizas`, `Riesgos1` y demás módulos. Este comando no consultó esos módulos ni siguió relaciones.
"""
