<?php
/**
 * Plugin Name: Intranet SSO Provider
 * Description: Emite aserciones de identidad firmadas (JWT RS256) para que el banco de
 *              herramientas (bapps.dp.segurosays.com) confíe en la sesión ya iniciada en esta intranet.
 *              No comparte cookies ni sesiones de WordPress con el otro contenedor; solo
 *              emite un token corto y firmado que el otro lado verifica de forma independiente.
 *
 * Instalación: copiar este archivo tal cual a wp-content/mu-plugins/intranet-sso-provider.php
 * (los mu-plugins se cargan automáticamente, sin activarlos desde el admin).
 *
 * Variables de entorno requeridas en el contenedor de WordPress (Dokploy -> Environment):
 *   INTRANET_SSO_PRIVATE_KEY   Clave privada RSA en PEM (PKCS8), nunca debe salir de este contenedor.
 *   INTRANET_SSO_ALLOWED_REDIRECT_ORIGINS   Lista separada por comas de orígenes permitidos,
 *                                            ej: https://bapps.dp.segurosays.com
 *   INTRANET_SSO_AUDIENCE      Debe coincidir con INTRANET_SSO_AUDIENCE del banco de herramientas.
 *                               Por defecto: bapps.dp.segurosays.com
 *   INTRANET_SSO_ISSUER        Debe coincidir con INTRANET_SSO_ISSUER del banco de herramientas.
 *                               Por defecto: segurosays.com
 *   INTRANET_SSO_TOKEN_TTL     Segundos de vida del token (por defecto 60). Debe ser <=
 *                               INTRANET_SSO_ASSERTION_MAX_AGE configurado en Django.
 *
 * La clave pública correspondiente (derivada de INTRANET_SSO_PRIVATE_KEY) es la que se
 * entrega al banco de herramientas como INTRANET_SSO_PUBLIC_KEY. Genera el par una sola vez:
 *
 *   openssl genrsa -out intranet-sso-private.pem 2048
 *   openssl rsa -in intranet-sso-private.pem -pubout -out intranet-sso-public.pem
 *
 * La clave privada solo se pega (como variable de entorno, con saltos de línea reales o
 * escapados con \n) en el contenedor de WordPress. La pública se pega en el contenedor del
 * banco de herramientas. Nunca subas ninguna de las dos al repositorio.
 */

if (!defined('ABSPATH')) {
    exit;
}

const INTRANET_SSO_NAMESPACE = 'intranet-sso/v1';

add_action('rest_api_init', function () {
    register_rest_route(INTRANET_SSO_NAMESPACE, '/authorize', [
        'methods' => 'GET',
        'callback' => 'intranet_sso_authorize',
        'permission_callback' => '__return_true',
    ]);
});

function intranet_sso_authorize(WP_REST_Request $request)
{
    $redirect_uri = (string) $request->get_param('redirect_uri');
    $allowed_origin = intranet_sso_validate_redirect_uri($redirect_uri);
    if ($allowed_origin === null) {
        return new WP_Error(
            'intranet_sso_invalid_redirect',
            'redirect_uri no está en la lista de orígenes permitidos.',
            ['status' => 400]
        );
    }

    if (!is_user_logged_in()) {
        $authorize_url = add_query_arg('redirect_uri', rawurlencode($redirect_uri), rest_url('intranet-sso/v1/authorize'));
        wp_safe_redirect(wp_login_url($authorize_url));
        exit;
    }

    $token = intranet_sso_issue_token(wp_get_current_user());
    if (is_wp_error($token)) {
        return $token;
    }

    wp_safe_redirect(intranet_sso_merge_query($redirect_uri, ['sso_token' => $token]), 302, 'Intranet-SSO');
    exit;
}

/**
 * Solo acepta orígenes exactos (esquema + host) de la lista blanca. Devuelve el
 * origen permitido si es válido, o null si redirect_uri no puede usarse.
 */
function intranet_sso_validate_redirect_uri(string $redirect_uri): ?string
{
    if ($redirect_uri === '') {
        return null;
    }
    $parts = wp_parse_url($redirect_uri);
    if (!$parts || empty($parts['scheme']) || empty($parts['host'])) {
        return null;
    }
    if ($parts['scheme'] !== 'https' || !empty($parts['user']) || !empty($parts['pass'])) {
        return null;
    }
    $origin = $parts['scheme'] . '://' . $parts['host'] . (isset($parts['port']) ? ':' . $parts['port'] : '');

    $allowed = array_filter(array_map('trim', explode(',', (string) getenv('INTRANET_SSO_ALLOWED_REDIRECT_ORIGINS'))));
    foreach ($allowed as $candidate) {
        if (hash_equals(untrailingslashit($candidate), untrailingslashit($origin))) {
            return $origin;
        }
    }
    return null;
}

function intranet_sso_issue_token(WP_User $user)
{
    $private_key_pem = intranet_sso_private_key_pem();
    if ($private_key_pem === '') {
        return new WP_Error('intranet_sso_misconfigured', 'INTRANET_SSO_PRIVATE_KEY no está configurada.', ['status' => 500]);
    }
    $private_key = openssl_pkey_get_private($private_key_pem);
    if ($private_key === false) {
        return new WP_Error('intranet_sso_misconfigured', 'INTRANET_SSO_PRIVATE_KEY no es una clave RSA válida.', ['status' => 500]);
    }

    $ttl = (int) (getenv('INTRANET_SSO_TOKEN_TTL') ?: 60);
    $now = time();
    $claims = [
        'sub' => $user->user_email,
        'aud' => getenv('INTRANET_SSO_AUDIENCE') ?: 'bapps.dp.segurosays.com',
        'iss' => getenv('INTRANET_SSO_ISSUER') ?: 'segurosays.com',
        'iat' => $now,
        'exp' => $now + max(1, $ttl),
        'jti' => wp_generate_password(32, false, false),
    ];

    $header = intranet_sso_base64url_encode(wp_json_encode(['alg' => 'RS256', 'typ' => 'JWT']));
    $payload = intranet_sso_base64url_encode(wp_json_encode($claims));
    $signing_input = $header . '.' . $payload;

    $signature = '';
    $signed = openssl_sign($signing_input, $signature, $private_key, OPENSSL_ALGO_SHA256);
    if (!$signed) {
        return new WP_Error('intranet_sso_sign_failed', 'No fue posible firmar el token SSO.', ['status' => 500]);
    }

    return $signing_input . '.' . intranet_sso_base64url_encode($signature);
}

function intranet_sso_private_key_pem(): string
{
    $raw = (string) getenv('INTRANET_SSO_PRIVATE_KEY');
    // Permite que la variable de entorno traiga los saltos de línea escapados como "\n".
    return str_contains($raw, '\\n') ? str_replace('\\n', "\n", $raw) : $raw;
}

function intranet_sso_base64url_encode(string $data): string
{
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function intranet_sso_merge_query(string $url, array $extra_params): string
{
    $parts = wp_parse_url($url);
    $query = [];
    if (!empty($parts['query'])) {
        parse_str($parts['query'], $query);
    }
    $query = array_merge($query, $extra_params);

    $rebuilt = $parts['scheme'] . '://' . $parts['host'];
    if (isset($parts['port'])) {
        $rebuilt .= ':' . $parts['port'];
    }
    $rebuilt .= $parts['path'] ?? '';
    $rebuilt .= '?' . http_build_query($query);
    return $rebuilt;
}
