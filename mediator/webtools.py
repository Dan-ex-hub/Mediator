"""Web access tools for agents (Phase 20).

Two read-only primitives the agent tool-loop can call:
  - fetch_url(url)   : download a page and return its text (HTML stripped).
  - web_search(query): a keyless DuckDuckGo HTML search returning titles/links/snippets.

SECURITY: both are guarded against SSRF — requests to private, loopback, link-local, or
otherwise non-public addresses are refused, and redirects are followed manually with the
destination re-validated each hop. Fetched content is UNTRUSTED data; callers must never
execute it. Sizes and time are bounded.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_UA = "Mediator/1.0 (+local agentic review tool)"
_MAX_REDIRECTS = 4


class WebError(RuntimeError):
    """Raised for any web-tool failure (blocked host, network error, etc.)."""


def _host_is_public(host: str | None) -> bool:
    """True only if every resolved address for ``host`` is a public IP."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = info[4][0].split("%")[0]  # strip scope id on IPv6
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(text: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()


def _strip_tags(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s)).strip()


def fetch_url(url: str, max_chars: int = 6000, timeout: float = 15.0) -> str:
    """Fetch ``url`` and return its text content (HTML stripped). SSRF-guarded."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebError("Only http(s) URLs are allowed.")
    if not _host_is_public(parsed.hostname):
        raise WebError(f"Refusing to fetch a non-public or unresolvable host: {parsed.hostname}")

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          headers={"User-Agent": _UA}) as client:
            current = url
            resp = None
            for _ in range(_MAX_REDIRECTS):
                resp = client.get(current)
                if resp.is_redirect:
                    loc = resp.headers.get("location", "")
                    nxt = resp.url.join(loc)
                    if nxt.scheme not in ("http", "https") or not _host_is_public(nxt.host):
                        raise WebError("Refusing to follow an unsafe redirect.")
                    current = str(nxt)
                    continue
                break
            else:
                raise WebError("Too many redirects.")
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            raw = resp.text
    except httpx.HTTPError as exc:
        raise WebError(f"Fetch failed: {exc}") from exc

    looks_html = "html" in ctype.lower() or "<html" in raw[:600].lower()
    body = _html_to_text(raw) if looks_html else raw.strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…[truncated]…"
    return body or "(empty response)"


def _decode_ddg_href(href: str) -> str:
    """DuckDuckGo HTML wraps links as //duckduckgo.com/l/?uddg=<encoded>."""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


def web_search(query: str, max_results: int = 6, timeout: float = 15.0) -> str:
    """Keyless web search via DuckDuckGo's HTML endpoint."""
    query = (query or "").strip()
    if not query:
        return "(empty query)"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get("https://html.duckduckgo.com/html/", params={"q": query})
            resp.raise_for_status()
            page = resp.text
    except httpx.HTTPError as exc:
        raise WebError(f"Search failed: {exc}") from exc

    links = re.findall(r'class="result__a"[^>]*href="(.*?)"[^>]*>(.*?)</a>', page, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)
    if not links:
        return "(no results)"
    out: list[str] = []
    for i, (href, title) in enumerate(links[:max_results]):
        url = _decode_ddg_href(href)
        snip = _strip_tags(snippets[i]) if i < len(snippets) else ""
        out.append(f"{i + 1}. {_strip_tags(title)}\n   {url}" + (f"\n   {snip}" if snip else ""))
    return "\n\n".join(out)
