# -*- coding: utf-8 -*-
"""셀프호스트 허브가 웹 Vinc 번들을 `/app/*` 로 서빙한다.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S3. `selfhost.enabled()` 일 때만 답하고, 꺼져
있으면 `handle` 이 False 를 돌려 기존 404 가 그대로 난다.

- 번들은 `VINC_WEB_DIR`(기본 `/srv/vinc/web`, Dockerfile.hub 가 `VITE_BASE=/app/
  VITE_SELF_HOSTED=1` 로 빌드해 넣는다)에 있다. 없으면 `/app/` 이 그렇다고 503 으로 말한다.
- `/` 는 `/app/` 으로 보낸다. 파일이 없는 확장자 없는 경로(`/app/approvals/<run>` 같은
  경로형 딥링크)는 `index.html` 을 준다. 앱은 해시로 라우팅한다.
- 경로는 번들 디렉터리 밖으로 못 나간다(실경로 비교).
- 헤더는 호스티드 웹(vincs.io 의 `staticwebapp.config.json`)과 같은 정책이다: CSP, 해시 붙은
  `assets/*` 는 1년 immutable, `index.html` 은 no-store, 서비스 워커와 매니페스트는 no-cache.
  View 의 배치 워커만 WebAssembly 를 쓰므로 그 파일에만 `'wasm-unsafe-eval'` 을 더한다.
"""
from __future__ import annotations

import os
import urllib.parse

from . import selfhost

DEFAULT_DIR = "/srv/vinc/web"
PREFIX = "/app/"

_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'{wasm}; frame-ancestors 'none'")

_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".wasm": "application/wasm", ".map": "application/json", ".txt": "text/plain; charset=utf-8",
}


def web_dir() -> str:
    return os.path.realpath(os.environ.get("VINC_WEB_DIR") or DEFAULT_DIR)


def resolve(path: str, root: str | None = None) -> tuple[str | None, bool]:
    """요청 경로(`/app/...`) → (파일 실경로 또는 None, index.html 로 떨어졌는가).

    번들 밖을 가리키면(`..`·심볼릭 링크·인코딩한 구분자) None 이다."""
    root = root or web_dir()
    rel = urllib.parse.unquote(path[len(PREFIX):]) if path.startswith(PREFIX) else ""
    if "\x00" in rel or "\\" in rel:
        return None, False
    target = os.path.realpath(os.path.join(root, rel)) if rel else os.path.join(root, "index.html")
    if target != root and not target.startswith(root + os.sep):
        return None, False
    if os.path.isfile(target):
        return target, False
    last = rel.rstrip("/").rsplit("/", 1)[-1]
    if "." in last:
        return None, False                       # 없는 자산은 404 다(index.html 로 덮지 않는다)
    index = os.path.join(root, "index.html")
    return (index, True) if os.path.isfile(index) else (None, False)


def _headers(file: str, root: str) -> dict:
    rel = os.path.relpath(file, root).replace(os.sep, "/")
    name = rel.rsplit("/", 1)[-1]
    if rel == "index.html":
        cache = "no-store"
    elif rel.startswith("assets/"):
        cache = "public, max-age=31536000, immutable"
    else:
        cache = "no-cache"
    wasm = " 'wasm-unsafe-eval'" if rel.startswith("assets/view-layout.worker-") else ""
    ext = os.path.splitext(name)[1].lower()
    return {"Content-Type": _TYPES.get(ext, "application/octet-stream"), "Cache-Control": cache,
            "Content-Security-Policy": _CSP.format(wasm=wasm), "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


def _send(handler, code: int, body: bytes, headers: dict, *, head: bool = False) -> None:
    handler.send_response(code)
    for k, v in headers.items():
        handler.send_header(k, v)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if not head:
        handler.wfile.write(body)


def handle(handler, method: str) -> bool:
    """`/`·`/app`·`/app/*` 이면 답하고 True. 셀프호스트가 아니거나 다른 경로면 False."""
    if not selfhost.enabled() or method not in ("GET", "HEAD"):
        return False
    path = urllib.parse.urlparse(handler.path).path
    head = method == "HEAD"
    if path in ("/", "/app"):
        _send(handler, 308, b"", {"Location": PREFIX, "Cache-Control": "no-store"}, head=head)
        return True
    if not path.startswith(PREFIX):
        return False
    root = web_dir()
    if not os.path.isfile(os.path.join(root, "index.html")):
        _send(handler, 503, b"the web app is not in this image (VINC_WEB_DIR has no index.html)",
              {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}, head=head)
        return True
    file, _fallback = resolve(path, root)
    if file is None:
        _send(handler, 404, b"not found", {"Content-Type": "text/plain; charset=utf-8",
                                           "Cache-Control": "no-store"}, head=head)
        return True
    with open(file, "rb") as fh:
        body = fh.read()
    _send(handler, 200, body, _headers(file, root), head=head)
    return True
