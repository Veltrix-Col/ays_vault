# Mejoras pendientes de CardManager

Priorización recomendada, sin cambios implementados en esta intervención:

1. Validar con A&S el dato “Código”, su necesidad, clasificación y retención.
2. Migrar llaves a KMS/Key Vault con versionado y rotación ensayada.
3. Integrar logs/alertas con SIEM y anclar la cadena fuera de la base primaria.
4. Formalizar backups cifrados, RPO/RTO y pruebas de restauración.
5. Validar PostgreSQL, concurrencia, carga y memoria de reportes en producción.
6. Configurar scheduler durable para políticas y monitorear sus fallos.
7. Ejecutar pentest, revisión de privacidad y evaluación PCI aplicable.
8. Confirmar Microsoft Graph o SMTP con credenciales y dominio reales.
9. Definir runbooks de altas/bajas, incidente, rotación y recuperación MFA.
10. Revisar accesibilidad y QA visual con usuarios reales.
