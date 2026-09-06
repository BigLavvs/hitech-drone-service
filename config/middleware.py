import secrets
from urllib.parse import urlparse

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        response["Content-Security-Policy"] = _build_csp_header(request.csp_nonce)
        return response


def _build_csp_header(nonce: str) -> str:
    cdn = "https://cdn.jsdelivr.net"
    r2_sources = _configured_r2_sources()
    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'self'"],
        "form-action": ["'self'"],
        "img-src": ["'self'", cdn, "data:", "blob:", *r2_sources],
        "style-src": ["'self'", cdn],
        # Swagger UI and Redoc add style elements at runtime. The documentation
        # templates propagate this nonce to those generated elements.
        "style-src-elem": ["'self'", cdn, f"'nonce-{nonce}'"],
        "style-src-attr": ["'none'"],
        "font-src": ["'self'", "data:"],
        "script-src": ["'self'", cdn, f"'nonce-{nonce}'"],
        "script-src-elem": ["'self'", cdn, f"'nonce-{nonce}'"],
        "connect-src": ["'self'", "blob:", *r2_sources],
        "media-src": ["'self'", "blob:", *r2_sources],
        "worker-src": ["'self'", "blob:"],
    }
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


def _configured_r2_sources() -> list[str]:
    sources = []
    for setting_name in ("R2_ENDPOINT_URL", "R2_PUBLIC_URL"):
        value = getattr(settings, setting_name, "")
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            source = f"{parsed.scheme}://{parsed.netloc}"
            if source not in sources:
                sources.append(source)
    return sources
