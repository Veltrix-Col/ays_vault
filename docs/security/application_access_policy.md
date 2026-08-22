# Política de acceso por aplicación

## 1. Objetivo

El Banco de Herramientas separa la autenticación fuerte propia de CardManager
del acceso heredado que recibirán SOAT, Cotización – Colectivos y el Portal
desde la intranet autenticada de A&S.

Esta intervención no define un token de intranet. No existe todavía una
especificación aprobada de emisor, audiencia, algoritmo, llaves o prevención de
replay. La aplicación falla cerradamente en el modo productivo hasta conectar
un validador aprobado.

## 2. Matriz de acceso

| Aplicación | Local | Producción | Login propio | MFA propio |
| --- | --- | --- | --- | --- |
| Vault/CardManager | Login CardManager | Login CardManager | Sí | Sí |
| SOAT | Acceso directo | Acceso delegado validado | No | No |
| Cotización – Colectivos | Acceso directo | Acceso delegado validado | No | No |
| Portal | Acceso directo | Acceso delegado validado | No | No |

## 3. Clasificación cerrada

La clasificación usa la resolución de URLs de Django:

- namespace `soat`;
- namespace `cotizacion_colectivos`;
- nombre exacto `public_home` para el Portal.

No se usan coincidencias parciales de paths, parámetros, `Referer`, `Origin`,
IP del navegador, hostname ni headers elegidos por el cliente.

Vault no pertenece a esta clasificación. Conserva login, MFA, sesión segura,
reauthenticación, horarios, roles y auditoría existentes.

## 4. Configuración

### Desarrollo local

```env
DEBUG=true
TOOLS_ACCESS_MODE=local_public
TOOLS_DELEGATED_ACCESS_VALIDATOR=
```

`local_public` se rechaza cuando `DEBUG=false`, salvo dentro del proceso
identificado por Django como ejecución de pruebas automatizadas. Esta excepción
no existe en un servidor web de producción.

### Producción futura

```env
DEBUG=false
TOOLS_ACCESS_MODE=trusted_intranet
TOOLS_DELEGATED_ACCESS_VALIDATOR=paquete.aprobado.validador
```

Los únicos valores permitidos son `local_public` y `trusted_intranet`. Un modo
desconocido es inválido. En `trusted_intranet`, la ausencia del callable o un
resultado no reconocido producen HTTP 403.

## 5. Punto de integración

El callable configurado recibe exclusivamente argumentos nombrados:

```python
validator(request=request, application="soat")
```

Debe devolver `DelegatedAccessResult`. Esta interfaz no interpreta tokens ni
elige algoritmos. Esa responsabilidad pertenecerá al adaptador aprobado de la
intranet.

El contrato futuro debe validar, como mínimo:

- firma y algoritmo permitido;
- emisor y audiencia;
- expiración y tolerancia temporal;
- identificador único y prevención de replay;
- identidad delegada;
- rotación de llaves;
- aplicación autorizada.

El token completo, su firma y los headers nunca deben registrarse.

## 6. Controles insuficientes

No conceden acceso por sí solos:

- `Referer` u `Origin`;
- IP o hostname;
- query string como `?trusted=true`;
- cookie no firmada;
- header arbitrario;
- redirección aportada por el cliente.

No existe redirección productiva en esta fase. Un rechazo devuelve 403, por lo
que tampoco existe superficie de open redirect ni dependencia del login de
CardManager.

## 7. Controles conservados

SOAT y Colectivos conservan CSRF, límites, validaciones, respuestas no-cache y
sus protecciones funcionales. Colectivos mantiene documentos enmascarados,
tokens firmados temporales, anti-IDOR, perfil Sandbox fijo y solo lectura.

El acceso a Portal, SOAT o Colectivos no autentica un usuario Django, no crea
una sesión segura Vault y no marca MFA como completado.

## 8. Logs

Se registran únicamente resultado, aplicación, categoría y correlación
aleatoria. No se incluyen tokens, firmas, headers, documentos, nombres,
correos ni secretos. Estos eventos no usan la cadena de auditoría MFA de Vault.

## 9. QA manual

### Local

1. Definir `DEBUG=true` y `TOOLS_ACCESS_MODE=local_public`.
2. Abrir `/`, `/soat/` y `/cotizacion-colectivos/` sin sesión.
3. Confirmar que un POST de Colectivos sin CSRF devuelve 403.
4. Confirmar que `/vault/` redirige al login y continúa exigiendo MFA.
5. Confirmar que visitar herramientas no crea `_auth_user_id` ni
   `otp_device_id`.

### Producción simulada

1. Definir `TOOLS_ACCESS_MODE=trusted_intranet`.
2. Sin validador configurado, comprobar HTTP 403 en Portal, SOAT y Colectivos.
3. Conectar únicamente un validador de pruebas aprobado y comprobar aceptación
   y categorías de rechazo.
4. Confirmar que headers o query strings inventados no conceden acceso.
5. Confirmar que Vault sigue redirigiendo a su propio login.

## 10. Pendiente

Antes del despliegue deben aprobarse el formato del token delegado, emisor,
audiencia, gestión de llaves, mecanismo anti-replay, URL de intranet —si se
decide redirigir— y política operativa de rotación/revocación.
