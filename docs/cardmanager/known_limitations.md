# Limitaciones conocidas de CardManager

- Credenciales, correo real, despliegue y QA manual no pueden inferirse del repositorio.
- Gestión de llaves mediante KMS/Key Vault no está implementada.
- La cadena hash no tiene anclaje externo inmutable ni SIEM demostrado.
- SQLite sirve desarrollo y serializa escrituras críticas; producción exige PostgreSQL.
- Las tareas asíncronas usan ejecución local/hilos, no una cola durable externa.
- La programación de `evaluate_security_policies` depende de infraestructura externa.
- Backups cifrados y restauración ensayada no están implementados por la app.
- No existe evidencia de pentest, prueba de carga ni certificación PCI.
- El significado y retención del campo “Código” requieren validación funcional; no se denomina CVV en código.
- Inventario/confianza de dispositivos se basa en señales de navegador/IP y no equivale a attestation de hardware.
