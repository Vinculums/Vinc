# -*- coding: utf-8 -*-
"""E4 채점용 도구 셸 — 평가 픽스처 위에서 MCP 도구 하나를 부르고 결과를 낸다.

    python -m scripts.tooluse_shell vinc_neighbors '{"node": "concept:view"}'

호출은 **진짜 MCP 경로**를 지난다(도구 스키마 → 핸들러 → 브리지 → HTTP → 사이드카).
픽스처는 고정 디렉터리에 멱등하게 심으므로 호출 간에 답이 변하지 않는다.

호출 이력을 `<data>/calls.jsonl` 에 남긴다 — 채점의 process 항목(요구 도구를 실제로
불렀는가 · 금지 도구를 불렀는가)은 답변 텍스트가 아니라 이 기록으로 매긴다. 모델이
"neighbors 를 썼습니다"라고 쓰는 것과 실제로 부른 것은 다른 사실이다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

#: 픽스처 스토어 위치. 임시 디렉터리로 두면 호출마다 새로 심어야 하고, 무엇보다
#: 채점 중 답이 바뀔 수 있다. 고정해 둔다(멱등 MERGE 라 반복 심기가 안전하다).
DATA = Path(os.environ.get("VINC_EVAL_DATA")
            or (Path(os.environ.get("TEMP", "/tmp")) / "vinc-eval-tooluse"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.tooluse_shell <tool> '<json args>'", file=sys.stderr)
        return 2
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = str(DATA / "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"

    from store.config import load_config
    from store.backend import open_store
    from api.server import VincApp, make_server
    from vinc_mcp import tools
    from vinc_mcp.bridge import Bridge
    import seed_tooluse as S

    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    if not (DATA / ".seeded").exists():          # 첫 호출에만 심는다(멱등이지만 느리다)
        S.seed(store)
        S.seed_doc(store)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / ".seeded").write_text("ok", encoding="utf-8")

    app = VincApp(cfg, store=store)
    httpd = make_server(app, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    bridge = Bridge(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                    token=getattr(app.auth, "token", None), owner=S.OWNER)
    try:
        out = tools.call(tool, {**args, "owner": S.OWNER}, bridge)
        err = None
    except Exception as e:                                          # noqa: BLE001
        out, err = None, f"{type(e).__name__}: {e}"
    finally:
        with (DATA / "calls.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 3), "tool": tool,
                                 "args": args, "error": err}, ensure_ascii=False) + "\n")
        try:
            httpd.shutdown()
        finally:
            app.close()

    if err:
        print(json.dumps({"error": err}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
