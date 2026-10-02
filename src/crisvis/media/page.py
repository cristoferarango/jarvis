"""Mostrar una página web en la pantalla.

Los editores impiden que sus artículos se incrusten (X-Frame-Options, CORS,
bloqueo de hotlinks), y todas esas reglas se aplican contra el navegador. Así
que el núcleo descarga el documento en el servidor y lo sirve desde su propio
origen, saneado y con una CSP estricta:

  reader — solo el texto, reestilizado con la estética de la interfaz.
  live   — la página real sin ningún script.

Sin parser DOM a propósito: estas operaciones son toscas y, cuando fallan,
fallan quitando de más, nunca dejando un <script> dentro.
"""

from __future__ import annotations

import html as htmllib
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from crisvis.media.net import ProxyError, fetch_text, peek

MAX_PAGE_BYTES = 8 * 1024 * 1024
PAGE_TIMEOUT = 15.0
PEEK_TIMEOUT = 8.0

# El único script que se permite dentro de una página proxificada: desplaza la
# página cuando la blade lo pide (las manos no tienen rueda del ratón). Entra por
# nonce y el iframe va en sandbox SIN allow-same-origin, así que corre en un
# origen opaco que no puede tocar la aplicación.
SCROLL_SHIM = """
addEventListener('message', function (e) {
  var d = e.data
  if (!d || d.jarvis !== 'scroll') return
  if (d.to === 'top') { window.scrollTo({ top: 0, behavior: 'smooth' }); return }
  if (d.to === 'bottom') { window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); return }
  window.scrollBy({ top: d.dy || 0, behavior: d.smooth ? 'smooth' : 'auto' })
})
try { parent.postMessage({ jarvis: 'ready' }, '*') } catch (e) {}
"""

_KILL = [
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "form",
    "iframe",
    "object",
    "embed",
    "applet",
    "link",
    "meta",
]
_I = re.IGNORECASE
_IS = re.IGNORECASE | re.DOTALL


def strip_dangerous(html: str) -> str:
    out = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    for tag in _KILL:
        out = re.sub(rf"<{tag}\b.*?</{tag}\s*>", "", out, flags=_IS)
        out = re.sub(rf"<{tag}\b[^>]*/?>", "", out, flags=_I)
    out = re.sub(r"\son[a-z]+\s*=\s*([\"']).*?\1", "", out, flags=_IS)
    out = re.sub(r"\son[a-z]+\s*=\s*[^\s>]+", "", out, flags=_I)
    out = re.sub(
        r"(href|src|action|poster)\s*=\s*([\"'])\s*(javascript|data:text/html|vbscript):.*?\2",
        r'\1="#"',
        out,
        flags=_IS,
    )
    return out


def text_of(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _esc(value: str) -> str:
    return htmllib.escape(value or "", quote=True)


def meta_content(html: str, names: list[str]) -> str:
    for name in names:
        tag = re.search(
            rf"<meta[^>]+(?:property|name)\s*=\s*[\"']{re.escape(name)}[\"'][^>]*>", html, _I
        )
        if not tag:
            continue
        value = re.search(r"content\s*=\s*[\"'](.*?)[\"']", tag.group(0), _IS)
        if value:
            return text_of(value.group(1))
    return ""


def title_of(html: str) -> str:
    found = re.search(r"<title[^>]*>(.*?)</title>", html, _IS)
    return meta_content(html, ["og:title", "twitter:title"]) or (
        text_of(found.group(1)) if found else ""
    )


def _strip_furniture(html: str) -> str:
    out = html
    for tag in ("nav", "aside", "header", "footer"):
        out = re.sub(rf"<{tag}\b.*?</{tag}\s*>", "", out, flags=_IS)
    return out


def _prose_length(fragment: str) -> int:
    return sum(len(text_of(m.group(1))) for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", fragment, _IS))


def article_body(html: str) -> str:
    """El contenedor con más PROSA, no el más grande: el más grande es siempre la página entera."""
    clean = _strip_furniture(html)
    candidates: list[tuple[str, int]] = []

    def add(fragment: str) -> None:
        prose = _prose_length(fragment)
        if prose > 200:
            candidates.append((fragment, prose))

    for pattern in (
        r"<article\b[^>]*>(.*?)</article>",
        r"<main\b[^>]*>(.*?)</main>",
        r"<(?:div|section)\b[^>]*>(.*?)</(?:div|section)>",
    ):
        for m in re.finditer(pattern, clean, _IS):
            add(m.group(1))

    if candidates:
        best = max(c[1] for c in candidates)
        tight = sorted((c for c in candidates if c[1] >= best * 0.98), key=lambda c: len(c[0]))
        return tight[0][0]
    paras = [m.group(0) for m in re.finditer(r"<p\b[^>]*>.*?</p>", clean, _IS)]
    return "\n".join(paras) if paras else clean


_BLOCK = re.compile(r"<(h1|h2|h3|p|li|blockquote)\b[^>]*>(.*?)</\1\s*>", _IS)


def readable_text(body: str) -> str:
    parts: list[str] = []
    seen_prose = False
    for m in _BLOCK.finditer(body):
        tag, text = m.group(1).lower(), text_of(m.group(2))
        if not text or (tag == "li" and not seen_prose):
            continue
        if tag == "p" and len(text.split()) > 8:
            seen_prose = True
        parts.append(text)
    return " ".join(parts)


def _absolute(href: str, base: str) -> str | None:
    try:
        return urljoin(base, href)
    except ValueError:
        return None


def _img_route(url: str, origin: str) -> str:
    return f"{origin}/img?url={quote(url, safe='')}"


def to_reader(html: str, page_url: str, origin: str) -> str:
    clean = strip_dangerous(html)
    body = article_body(clean)
    title = title_of(html)
    lead = meta_content(html, ["og:image", "twitter:image"])

    blocks: list[str] = []
    seen_prose = False
    pattern = re.compile(r"<(h1|h2|h3|p|li|blockquote)\b[^>]*>(.*?)</\1\s*>|<img\b([^>]*)>", _IS)
    for m in pattern.finditer(body):
        if m.group(3) is not None:
            src = re.search(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", m.group(3), _I)
            absolute = _absolute(src.group(1), page_url) if src else None
            if absolute and re.match(r"^https?:", absolute, _I):
                blocks.append(
                    f'<img class="rd-img" loading="lazy" src="{_img_route(absolute, origin)}" alt="">'
                )
            continue
        tag, text = m.group(1).lower(), text_of(m.group(2))
        if len(text) < 2:
            continue
        if tag == "li":
            if seen_prose:
                blocks.append(f'<li class="rd-li">{_esc(text)}</li>')
        elif tag == "blockquote":
            blocks.append(f'<blockquote class="rd-q">{_esc(text)}</blockquote>')
        elif tag == "p":
            if len(text.split()) > 8:
                seen_prose = True
            blocks.append(f'<p class="rd-p">{_esc(text)}</p>')
        else:
            blocks.append(f'<{tag} class="rd-h">{_esc(text)}</{tag}>')

    lead_img = (
        f'<img class="rd-lead" src="{_img_route(lead, origin)}" alt="">'
        if lead and re.match(r"^https?:", lead, _I)
        else ""
    )
    host = (urlparse(page_url).hostname or "").removeprefix("www.")
    content = (
        "\n".join(blocks) or '<p class="rd-p">No se pudo extraer texto legible de esta página.</p>'
    )
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title or host)}</title>
<style>
  :root {{ color-scheme: dark; }}
  html,body {{ margin:0; background:transparent; }}
  body {{ font: 400 15px/1.62 ui-sans-serif, "Segoe UI", sans-serif; color:#cfe9ee;
         padding:18px 22px 40px; -webkit-font-smoothing:antialiased; }}
  .rd-src {{ font:500 10px/1 ui-monospace,monospace; letter-spacing:.16em; text-transform:uppercase;
            color:#5fd8e0; opacity:.8; }}
  .rd-title {{ font-size:25px; line-height:1.2; font-weight:600; color:#eafcff; margin:10px 0 18px; }}
  .rd-h {{ font-size:16px; color:#eafcff; margin:22px 0 8px; font-weight:600; }}
  .rd-p {{ margin:0 0 14px; }}
  .rd-li {{ margin:0 0 7px 18px; }}
  .rd-q {{ margin:16px 0; padding-left:14px; border-left:2px solid #19c4c4; color:#9fdbe2; font-style:italic; }}
  img {{ max-width:100%; height:auto; display:block; border-radius:4px; margin:14px 0; }}
  ::-webkit-scrollbar {{ width:9px; }}
  ::-webkit-scrollbar-thumb {{ background:#19c4c455; border-radius:9px; }}
</style></head><body>
<div class="rd-src">{_esc(host)}</div>
<h1 class="rd-title">{_esc(title)}</h1>
{lead_img}
{content}
</body></html>"""


def to_live(html: str, page_url: str) -> str:
    out = strip_dangerous(html)
    out = re.sub(r"<base\b[^>]*>", "", out, flags=_I)
    injected = (
        f'<base href="{_esc(page_url)}">'
        "<style>html{background:#06101a;color-scheme:dark}"
        "::-webkit-scrollbar{width:9px}::-webkit-scrollbar-thumb{background:#19c4c455;border-radius:9px}</style>"
    )
    if re.search(r"<head\b[^>]*>", out, _I):
        return re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + injected, out, count=1, flags=_I)
    return f'<!doctype html><html><head><meta charset="utf-8">{injected}</head><body>{out}</body></html>'


@dataclass
class RenderedPage:
    body: str
    title: str
    headers: dict[str, str]


async def render_page(url: str, mode: str, origin: str) -> RenderedPage:
    page = await fetch_text(url, max_bytes=MAX_PAGE_BYTES, timeout=PAGE_TIMEOUT)
    if not re.match(r"^text/html|^application/xhtml", page.type):
        raise ProxyError(415, f"no es una página web ({page.type or 'sin tipo'})")
    live = mode == "live"
    nonce = secrets.token_urlsafe(16)
    shim = f'<script nonce="{nonce}">{SCROLL_SHIM}</script>'
    rendered = to_live(page.text, page.url) if live else to_reader(page.text, page.url, origin)
    body = (
        rendered.replace("</body>", f"{shim}</body>", 1)
        if "</body>" in rendered
        else rendered + shim
    )
    csp = (
        "default-src 'none'; img-src https: http: data: blob:; "
        "style-src 'unsafe-inline' https: http: data:; font-src https: http: data:; "
        f"media-src https: http: data:; script-src 'nonce-{nonce}'; form-action 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'"
        if live
        else f"default-src 'none'; img-src {origin} data:; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{nonce}'; form-action 'none'; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'"
    )
    return RenderedPage(
        body=body,
        title=title_of(page.text),
        headers={
            "content-type": "text/html; charset=utf-8",
            "content-security-policy": csp,
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "cache-control": "no-store",
        },
    )


async def probe_url(url: str) -> dict[str, Any]:
    """Qué hay en esta URL y cuál sería la forma sensata de mostrarlo."""
    try:
        head = await peek(url, timeout=PEEK_TIMEOUT)
    except ProxyError as exc:
        return {"ok": False, "url": url, "reason": exc.message, "suggestion": "unavailable"}

    t = head.type
    kind = (
        "image"
        if t.startswith("image/")
        else "video"
        if t.startswith("video/")
        else "audio"
        if t.startswith("audio/")
        else "pdf"
        if t.startswith("application/pdf")
        else "page"
        if re.match(r"^text/html|^application/xhtml", t)
        else "other"
    )
    base: dict[str, Any] = {
        "ok": head.status == 200,
        "url": head.url,
        "status": head.status,
        "contentType": t,
        "bytes": head.bytes,
        "kind": kind,
    }
    if kind == "image":
        return {**base, "suggestion": "blade_image" if base["ok"] else "unavailable"}
    if kind in ("video", "audio"):
        return {**base, "suggestion": "blade_video" if base["ok"] else "unavailable"}
    if kind != "page" or not base["ok"]:
        return {**base, "suggestion": "speak_only" if base["ok"] else "unavailable"}
    try:
        full = await fetch_text(head.url, max_bytes=MAX_PAGE_BYTES, timeout=PAGE_TIMEOUT)
        readable = readable_text(article_body(strip_dangerous(full.text)))
        lead = meta_content(full.text, ["og:image", "twitter:image"])
        return {
            **base,
            "title": title_of(full.text),
            "readableChars": len(readable),
            "leadImage": lead or None,
            "excerpt": readable[:300],
            "suggestion": "blade_reader" if len(readable) > 900 else "blade_live",
        }
    except ProxyError as exc:
        return {**base, "suggestion": "blade_live", "reason": exc.message}
