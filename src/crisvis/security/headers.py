"""Cabeceras de seguridad y CSP de producción.

La página la sirve el núcleo (``crisvis.static``) con una CSP por respuesta:
``script-src`` solo admite archivos propios y, si hiciera falta un script en
línea, el nonce de esa respuesta. Ni 'unsafe-inline' ni 'unsafe-eval' en
scripts. Excepciones documentadas en docs/HARDENING_REPORT.md:

* 'wasm-unsafe-eval': compilar WebAssembly (palabra de activación Porcupine).
  No permite eval de JavaScript.
* style-src 'unsafe-inline': los paneles insertan HTML saneado con atributos
  style. Afecta a estilos, no a scripts.
* connect-src https://storage.googleapis.com: pesos del modelo de gestos de
  MediaPipe (datos, no código; el runtime se sirve desde /mediapipe).
* Kokoro (``seguridad.csp_kokoro``, desactivado): sus dependencias usan eval y
  descargan modelos de Hugging Face. Solo con esa opción se abre.

En desarrollo, Vite sirve la página con su propia política (vite.config.ts).
"""

from __future__ import annotations

_HF = (
    "https://huggingface.co https://cdn-lfs.huggingface.co https://cdn-lfs-us-1.hf.co "
    "https://cas-bridge.xethub.hf.co https://cdn.jsdelivr.net"
)


def build_csp(nonce: str | None, *, port: int, kokoro: bool = False) -> str:
    script = ["'self'", "'wasm-unsafe-eval'"]
    if nonce:
        script.append(f"'nonce-{nonce}'")
    connect = [
        "'self'", "data:", "blob:", f"ws://127.0.0.1:{port}", f"ws://localhost:{port}",
        "https://storage.googleapis.com",
    ]
    if kokoro:
        script.append("'unsafe-eval'")
        connect.append(_HF)
    directives = {
        "default-src": "'self'",
        "base-uri": "'self'",
        "object-src": "'none'",
        "frame-ancestors": "'none'",
        "form-action": "'self'",
        "script-src": " ".join(script),
        "script-src-attr": "'none'",
        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src": "'self' data: https://fonts.gstatic.com",
        "img-src": "'self' data: blob:",
        "media-src": "'self' data: blob:",
        "connect-src": " ".join(connect),
        "worker-src": "'self' blob:",
        "frame-src": (
            "'self' https://www.youtube-nocookie.com https://www.youtube.com "
            "https://player.vimeo.com"
        ),
        "manifest-src": "'self'",
    }
    return "; ".join(f"{k} {v}" for k, v in directives.items())


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": (
        "camera=(self), microphone=(self), geolocation=(), payment=(), usb=(), serial=(), "
        "bluetooth=(), clipboard-read=(), display-capture=()"
    ),
}
