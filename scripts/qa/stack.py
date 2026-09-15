#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA dev stack — sidecar on a fixed port with a seeded embedded store.

    python -X utf8 scripts/qa/stack.py

Writes ``<VINC_QA_DATA>/stack.json`` with port, owner, and bearer token.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DATA = Path(os.environ.get("VINC_QA_DATA", "/tmp/vinc-qa-store"))
PORT = int(os.environ.get("VINC_QA_PORT", "8787"))


def main() -> int:
    os.environ.setdefault("MEMORY_STORE_BACKEND", "embedded")
    os.environ.setdefault("MEMORY_DATA_DIR", str(DATA / "store"))
    os.environ.setdefault("MEMORY_EMBEDDING_MODEL", "local-en")

    from store.config import load_config
    from store.backend import open_store
    from api.server import VincApp, make_server
    import seed_tooluse as S

    cfg = load_config()
    store = open_store(cfg)
    if store is None:
        print("FAIL: cannot open embedded store", file=sys.stderr)
        return 2
    store.provision(dim=cfg.embedding_dim)
    marker = DATA / ".seeded"
    if not marker.exists():
        S.seed(store)
        S.seed_doc(store)
        DATA.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")

    app = VincApp(cfg, store=store)
    httpd = make_server(app, host="127.0.0.1", port=PORT)
    meta = {
        "port": PORT,
        "owner": S.OWNER,
        "token": getattr(app.auth, "token", None),
        "data_dir": str(DATA),
    }
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "stack.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
