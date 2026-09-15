#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA environment gate — sidecar health and optional app URL reachability."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("VINC_QA_PORT", "8787"))
APP_URL = os.environ.get("VINC_QA_APP_URL", "http://127.0.0.1:5173")


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read(500).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    checks: list[tuple[bool, str]] = []
    code, body = _get(f"http://127.0.0.1:{PORT}/health")
    ok = code == 200 and "ok" in body.lower() or code == 200
    checks.append((ok, f"sidecar /health -> {code}"))

    code2, _ = _get(APP_URL)
    checks.append((code2 == 200, f"app {APP_URL} -> {code2}"))

    out = {"checks": [{"ok": o, "detail": d} for o, d in checks],
           "pass": all(o for o, _ in checks)}
    print(json.dumps(out, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
