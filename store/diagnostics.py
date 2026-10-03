"""Opt-in transport diagnostics shared by the CLI and its bridges."""
from __future__ import annotations

import contextvars
import re
import sys
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

_enabled = contextvars.ContextVar("vinc_debug_transport", default=False)
_SAFE_VALUES = frozenset({"limit", "depth", "max_depth", "detail", "lang"})


def configure(enabled: bool) -> None:
    _enabled.set(enabled)


def enabled() -> bool:
    return _enabled.get()


def trace(method: str, route: str, status, started: float, *, request_id="", transport="http") -> None:
    if not _enabled.get():
        return
    parsed = urlsplit(route)
    # 호스트·헤더·본문은 내보내지 않는다. 검색어·토큰 같은 질의 값은 가린다.
    path = quote(parsed.path, safe="/%:@-._~")
    query = urlencode([(key, value if key in _SAFE_VALUES and re.fullmatch(r"[\w.-]{1,40}", value)
                        else "<redacted>") for key, value in parse_qsl(parsed.query)])
    if query:
        path += "?" + query
    ident = str(request_id)
    ident = ident if re.fullmatch(r"req_[A-Za-z0-9_-]{1,80}", ident) else "-"
    elapsed = (time.perf_counter() - started) * 1000
    print(f"vinc: debug {transport} {method} {path} -> {status} {ident} ({elapsed:.1f} ms)",
          file=sys.stderr)
