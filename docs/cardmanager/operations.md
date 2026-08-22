# Operación de CardManager

## Rutina

- Revisar `/healthz/`, alertas abiertas, último evento y última verificación.
- Confirmar evaluaciones periódicas y entregas de correo.
- Mantener festivos, horarios, excepciones y destinatarios.
- Revocar sesiones/dispositivos ante incidente; restablecer MFA solo tras validar identidad.
- Acotar reportes y tratar sus archivos como información sensible.

## Comandos

- `python manage.py verify_audit_chain`: verifica y registra integridad.
- `python manage.py evaluate_security_policies`: evalúa alertas/políticas; revisar opciones `--help` antes de automatizar.
- `python manage.py load_colombia_holidays`: carga festivos locales.
- `python manage.py seed_demo`: datos de demostración; no usar en producción sin revisión.

## Incidentes

1. Preservar logs y no revelar valores protegidos.
2. Revocar sesiones/dispositivos o bloquear usuario según alcance.
3. Verificar cadena y registrar evento/transición.
4. Rotar credenciales/llaves solo con procedimiento que preserve descifrado.
5. Validar restauración en ambiente aislado.

No existe en código un playbook externo, scheduler ni SIEM; son responsabilidades operativas pendientes.
