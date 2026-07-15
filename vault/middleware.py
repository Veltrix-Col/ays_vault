from .security import audit


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if request.path.startswith("/vault/"):
            response.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, private")
            response.setdefault("Pragma", "no-cache")
        return response


class AuditAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and request.path.startswith("/vault/") and response.status_code in {401, 403}:
            audit(request, "DENIED", result="DENIED", risk_level="HIGH", metadata={"status_code": response.status_code})
        return response
