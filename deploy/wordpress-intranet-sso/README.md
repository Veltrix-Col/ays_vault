# SSO intranet → banco de herramientas

Este directorio no se despliega junto con el banco de herramientas: es el
entregable para el contenedor de WordPress (`seguros.com`). Documenta cómo
conectar ambos lados.

## Cómo funciona

1. Un empleado visita una herramienta del banco (`bh.seguros.com/soat/`, por
   ejemplo) sin sesión delegada todavía.
2. El middleware del banco de herramientas lo redirige a
   `https://seguros.com/wp-json/intranet-sso/v1/authorize?redirect_uri=...`.
3. Ese endpoint (este plugin) revisa si hay sesión de WordPress activa:
   - Si la hay, firma un JWT RS256 de vida muy corta (60s por defecto) con el
     email del usuario y redirige de vuelta con `?sso_token=...`.
   - Si no la hay, redirige al login normal de WordPress, que al terminar
     vuelve a este mismo endpoint.
4. El banco de herramientas verifica la firma del token con su clave pública,
   lo consume una sola vez (anti-replay) y emite **su propia** cookie de
   sesión (firmada por Django, nunca por WordPress) válida ~45 minutos.
5. Cuando esa cookie expira, el flujo se repite de forma invisible (el
   usuario no ve ningún formulario mientras su sesión en WordPress siga
   activa).

En ningún momento se comparte la cookie de sesión de WordPress ni se
requiere que ambos contenedores estén en la misma red Docker: todo viaja por
redirecciones HTTPS del navegador del usuario.

## 1. Generar el par de llaves (una sola vez)

```bash
openssl genrsa -out intranet-sso-private.pem 2048
openssl rsa -in intranet-sso-private.pem -pubout -out intranet-sso-public.pem
```

- `intranet-sso-private.pem` se queda **solo** en el contenedor de WordPress
  (variable de entorno `INTRANET_SSO_PRIVATE_KEY`). Nunca se sube al repo ni
  se comparte con el banco de herramientas.
- `intranet-sso-public.pem` se pega en el contenedor del banco de
  herramientas (`INTRANET_SSO_PUBLIC_KEY`). Es segura de exponer: con ella
  solo se puede verificar, no firmar.

## 2. Variables de entorno en el contenedor de WordPress (Dokploy)

| Variable | Valor de ejemplo |
| --- | --- |
| `INTRANET_SSO_PRIVATE_KEY` | contenido de `intranet-sso-private.pem` |
| `INTRANET_SSO_ALLOWED_REDIRECT_ORIGINS` | `https://bh.seguros.com` |
| `INTRANET_SSO_AUDIENCE` | `bh.seguros.com` |
| `INTRANET_SSO_ISSUER` | `seguros.com` |
| `INTRANET_SSO_TOKEN_TTL` | `60` |

Si Dokploy no admite saltos de línea reales en el valor, pega la clave con
`\n` literales (el plugin los normaliza automáticamente).

## 3. Copiar el plugin al contenedor

`wp-content` es un volumen con nombre, no una carpeta del host, así que se
copia directo al contenedor en ejecución:

```bash
docker cp intranet-sso-provider.php <contenedor_wordpress>:/var/www/html/wp-content/mu-plugins/intranet-sso-provider.php
```

Si `mu-plugins` no existe todavía dentro del volumen, créala primero:

```bash
docker exec <contenedor_wordpress> mkdir -p /var/www/html/wp-content/mu-plugins
```

Los mu-plugins se cargan automáticamente en cada request; no hace falta
activarlos desde `wp-admin`, y un administrador de WordPress no puede
desactivarlos por error.

## 4. Variables de entorno en el banco de herramientas (Dokploy)

| Variable | Valor de ejemplo |
| --- | --- |
| `TOOLS_ACCESS_MODE` | `trusted_intranet` |
| `TOOLS_DELEGATED_ACCESS_VALIDATOR` | `intranet_sso.delegated_access.validate_intranet_session` |
| `INTRANET_SSO_PUBLIC_KEY` | contenido de `intranet-sso-public.pem` |
| `INTRANET_SSO_AUTHORIZE_URL` | `https://seguros.com/wp-json/intranet-sso/v1/authorize` |
| `INTRANET_SSO_AUDIENCE` | `bh.seguros.com` |
| `INTRANET_SSO_ISSUER` | `seguros.com` |

`TOOLS_ACCESS_MODE=local_public` sigue disponible para desarrollo/pruebas
(solo permitido con `DEBUG=true`); en producción el proyecto ya rechaza el
arranque si se deja en `local_public`, y con `trusted_intranet` rechaza el
arranque si faltan `INTRANET_SSO_PUBLIC_KEY` o `INTRANET_SSO_AUTHORIZE_URL`.

## 5. Verificación

1. Con el navegador sin sesión en ninguno de los dos sitios, visita
   `https://bh.seguros.com/soat/`: debe redirigir al login de WordPress.
2. Inicia sesión en WordPress: debe volver automáticamente a
   `https://bh.seguros.com/soat/` ya autenticado.
3. Visita `https://bh.seguros.com/` (portal) y otras herramientas: no debe
   pedir login de nuevo mientras la cookie del banco siga vigente.
4. `https://bh.seguros.com/vault/`: debe seguir pidiendo su propio login +
   verificación en dos pasos, sin importar la sesión de la intranet.

## Notas de seguridad

- El token JWT vive máximo 60 segundos y se consume una sola vez: aunque
  quedara en un log de acceso o en el historial del navegador, no sirve para
  nada pasado ese margen ni una segunda vez.
- `redirect_uri` solo acepta orígenes de la lista blanca
  (`INTRANET_SSO_ALLOWED_REDIRECT_ORIGINS`); nunca reenvía el token a un
  dominio no autorizado aunque el enlace venga manipulado.
- Este endpoint, como cualquier flujo de "silent SSO" (incluye Google,
  Microsoft, etc.), puede ser invocado sin interacción explícita del usuario
  mientras esté logueado en WordPress (p. ej. cargando la URL de
  `authorize` desde otra pestaña). El único efecto posible es que se
  establezca la sesión delegada del banco de herramientas para ese mismo
  usuario — nunca se puede elegir ni suplantar la identidad de otra
  persona, y `vault` (MFA) no se ve afectado por este flujo en absoluto.
