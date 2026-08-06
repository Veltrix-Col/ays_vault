# Pruebas de SOAT

`soat/tests.py` cubre:

- acceso sin autenticación;
- nombre de archivo libre con estructura válida;
- rechazo de estructura ausente y Excel inválido;
- descarga válida de cinco hojas sin auditoría Vault;
- neutralización de fórmulas;
- aislamiento de procesos concurrentes;
- nueve criterios de gestión.

## Ejecución

```powershell
python manage.py check
python manage.py test soat
```

## QA manual recomendado

Usar datos ficticios: archivo mínimo, múltiples candidatos, placa solo SOAT, solo Movilidad, ambos, fechas inválidas, estados vacíos, criterios 1–9, archivo cercano a límites, archivo macro/ZIP malicioso y dos cargas concurrentes. Verificar hojas, filtros, paneles, formatos, 1:1 y limpieza temporal.

## Estado

No hay test end-to-end de navegador ni dataset dorado versionado en el repositorio. La ejecución de esta intervención se consigna en la entrega final; el QA con datos de A&S requiere autorización y redacción.
