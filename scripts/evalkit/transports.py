# -*- coding: utf-8 -*-
"""평가가 제품을 부르는 경로 셋 — 인프로세스(기준) · `vinc` CLI · REST v1.

**왜 셋인가.** 인프로세스는 `api.query` 를 직접 부르는 옛 하네스와 같은 것이라 기준이다.
CLI 와 v1 은 에이전트가 실제로 부르는 표면이다(0.9.6, 게이트 파이프라인 하나). 같은
스토어·같은 런타임에서 세 경로가 **문항별로 같은 답**을 내야 그 뒤의 측정이 표면이
아니라 검색을 잰다. 다르면 그 차이가 곧 발견이다.

모든 전송은 같은 모양을 돌려준다: 도구 결과 dict(`vinc_brief` · `vinc_search` 의 응답).
실패는 **빈 결과로 삼키지 않고 던진다**(:class:`TransportError`) — `status: none` 은 실패가
아니라 답이므로 그대로 돌려준다.
근거: [[vinc.plan.rag-eval-v2-0-9-6]]
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


class TransportError(RuntimeError):
    pass


class InProcess:
    """기준 경로 — 옛 하네스와 같다. 질의 임베딩은 이 프로세스의 인코더가 한다."""
    name = "inproc"

    def __init__(self, data: str, owner: str, encoder_spec: str):
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "scripts"))
        from api import query as q                    # noqa: PLC0415
        from encoder import Encoder                   # noqa: PLC0415
        from store.backend import open_store          # noqa: PLC0415
        from store.config import load_config          # noqa: PLC0415
        self.q, self.owner = q, owner
        store = open_store(load_config({"data_dir": data}))
        if store is None:
            raise TransportError(f"스토어를 못 열었다: {data}")
        self.store = store
        name, path = encoder_spec.split("=", 1)
        self.enc = Encoder(name, path)
        self.enc.encode(["warmup"], kind="query")
        self._qv: dict[str, list] = {}

    def _vec(self, text: str):
        if text not in self._qv:
            self._qv[text] = self.enc.encode([text], kind="query")[0]
        return self._qv[text]

    def brief(self, query: str) -> dict:
        return self.q.brief(query, owner=self.owner, store=self.store,
                            query_embedding=self._vec(query))

    def search(self, query: str, limit: int) -> dict:
        return self.q.search(query, top_k=limit, owner=self.owner, store=self.store,
                             query_embedding=self._vec(query))

    def close(self) -> None:
        self.store.close()


class Cli:
    """`vinc call <tool>` 를 자식 프로세스로. `MEMORY_DATA_DIR` 로 스토어 파일을 지정하면
    CLI 는 그 옆의 `session.json` 이 가리키는 사이드카에 **붙는다**(앱 세션은 안 건드린다).
    붙지 못하면 스토어를 직접 열려다 잠금으로 실패한다 — 그것도 그대로 실패로 낸다."""
    name = "cli"

    def __init__(self, data: str, owner: str, env: dict | None = None,
                 python: str | None = None):
        self.data, self.owner = data, owner
        self.python = python or sys.executable
        self.env = {**os.environ, **(env or {}), "MEMORY_DATA_DIR": data,
                    "PYTHONIOENCODING": "utf-8"}
        for k in ("PYTHONHOME", "PYTHONPATH"):
            self.env.pop(k, None)

    def _call(self, tool: str, args: dict) -> dict:
        cmd = [self.python, "-X", "utf8", "-m", "api.cli", "--format", "json",
               "call", tool, "--args", json.dumps({**args, "owner": self.owner},
                                                  ensure_ascii=False)]
        p = subprocess.run(cmd, cwd=ROOT, env=self.env, capture_output=True,
                           text=True, encoding="utf-8", timeout=180)
        # 0 = 답, 1 = 도구가 실패나 `status: none` 을 답했다(결과 JSON 이 stdout 에 있다),
        # 그 밖(2 사용법 · 4 인증 · 5 상류)은 전송 실패다.
        if p.returncode not in (0, 1):
            raise TransportError(f"vinc call {tool} exit {p.returncode}: {p.stderr.strip()[:300]}")
        try:
            out = json.loads(p.stdout)
        except ValueError as exc:
            raise TransportError(f"vinc call {tool}: stdout 이 JSON 이 아니다: "
                                 f"{p.stdout[:200]!r} / {p.stderr[:200]!r}") from exc
        if p.returncode == 1 and out.get("status") != "none":
            raise TransportError(f"vinc call {tool} 실패: {json.dumps(out, ensure_ascii=False)[:300]}")
        return out

    def brief(self, query: str) -> dict:
        return self._call("vinc_brief", {"query": query})

    def search(self, query: str, limit: int) -> dict:
        return self._call("vinc_search", {"query": query, "limit": limit})

    def close(self) -> None:
        pass


class V1:
    """REST v1 — 로컬 루프백 서버든 호스티드 허브든 같은 클라이언트(`api.remote_client`)."""
    name = "v1"

    def __init__(self, base: str, key: str, space: str = "personal"):
        sys.path.insert(0, str(ROOT))
        from api import remote_client                 # noqa: PLC0415
        self.rc, self.base, self.key, self.space = remote_client, base, key, space

    def _call(self, tool: str, args: dict) -> dict:
        try:
            return self.rc.call(tool, args, space=self.space, key=self.key, base_url=self.base)
        except self.rc.RemoteFailure as exc:
            # 자유 질의의 `status: none` 은 v1 에서도 200 이다. 404 가 되는 것은 못 읽은 노드
            # (`unreadable`)뿐이고 그것도 **답**이다 — 인프로세스 결과와 같은 모양으로 되돌린다.
            code = (exc.result.get("error") or {}).get("code")
            if tool == "vinc_brief" and code == "not_found":
                return {"status": "none", "unreadable": True, "candidates": [],
                        "v1_error": exc.result.get("error")}
            raise TransportError(f"v1 {tool}: {json.dumps(exc.result, ensure_ascii=False)[:300]}") from exc

    def brief(self, query: str) -> dict:
        return self._call("vinc_brief", {"query": query})

    def search(self, query: str, limit: int) -> dict:
        return self._call("vinc_search", {"query": query, "limit": limit})

    def close(self) -> None:
        pass
