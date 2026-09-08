#!/usr/bin/env python3
"""R3(a) — 라이브 허브에서 **벡터 팔이 실제로 꺼져 있는 비율**을 잰다.

왜 생겼나: 문서를 쓸 때마다 HNSW 색인이 통째로 드롭된다(신규 벡터 삽입이 세그폴트를
내기 때문 — `store/embedded_backend.py` 의 드롭 주석). 그 창 동안 검색은 FTS 로만
답한다. 그래프의 `record:risk-vector-index-drop-global` 은 그것을 "vector search may
be off almost permanently" 라고 적는데 **그건 추정이다.** PR 259 가 `vector_rebuilding`
구조화 표식을 냈으므로 이제 셀 수 있는데, 세는 것이 아무 데도 없다: 사이드카는 요청
로그를 일부러 억제하고(`api/server.py`) MCP 앞문은 상태 코드만 적는다.

무엇을 재는가. 60초마다 앞문에 고정 질의 둘(영어·한국어)을 번갈아 던지고 `arms` ·
`vector_note` · `vector_rebuilding` 을 그대로 적는다. ⚠ **이 프로브 자신의 검색이
재빌드를 예약한다**(`api/query.py` 의 `_schedule_vector_index`). 그래서 이 수치는
"색인이 없는 시간의 비율"이 아니라 **"사용자 질의가 FTS 로만 답했을 1분 슬롯의
비율"** 이다. 후자가 사용자가 겪는 양이고, 판정에 쓸 것도 그쪽이다.

판정(계획 §6): 하루 비율 ≥ 0.20 이거나 연속 10분 초과 구간이 있으면 R7 을 연다.
< 0.05 면 R7 을 닫고 위 record 를 실측값으로 다시 쓴다.

키는 env 에서만 읽는다 — 인자로도 로그로도 안 흘린다.

    VINC_SK=... python scripts/rebuild_window_probe.py --hours 24 --out work/perf/r3a.jsonl
    python scripts/rebuild_window_probe.py --summarize work/perf/r3a.jsonl

정본: [[vinc.plan.retrieval-research-2026-09-08 §6]]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("VINC_MCP_BASE", "https://mcp.vincs.io/mcp")

#: 고정 질의 둘. 코퍼스에 확실히 있는 말이어야 재빌드 창이 아닐 때 벡터 팔이 실제로
#: 답한다 — 결과가 0건이면 팔이 돌았는지 안 돌았는지 구분이 흐려진다.
QUERIES = [("en", "embedding worker throughput"), ("ko", "임베딩 워커 처리량")]


class Session:
    """streamable-http JSON-RPC 한 벌. 세션이 끊기면 다시 연다."""

    def __init__(self, token: str):
        self.token = token
        self.sid: str | None = None

    def _rpc(self, method: str, params: dict | None = None, *, notify: bool = False):
        body: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = 1
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": f"Bearer {self.token}",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.sid = sid
            raw = r.read().decode("utf-8", "replace")
        if notify:
            return None
        # SSE 로 답할 수 있다 — data: 줄만 걷어낸다.
        if raw.lstrip().startswith(("event:", "data:")):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break
        return json.loads(raw)

    def open(self) -> None:
        self.sid = None
        init = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "rebuild-window-probe",
                                                       "version": "1"}})
        if init.get("error"):
            raise RuntimeError(json.dumps(init["error"], ensure_ascii=False)[:200])
        self._rpc("notifications/initialized", {}, notify=True)

    def search(self, text: str) -> dict:
        out = self._rpc("tools/call", {"name": "vinc_search",
                                       "arguments": {"query": text, "limit": 3}})
        if out.get("error"):
            raise RuntimeError(json.dumps(out["error"], ensure_ascii=False)[:200])
        res = out.get("result") or {}
        for c in res.get("content") or []:
            if c.get("type") == "text":
                return json.loads(c["text"])
        raise RuntimeError("no text content in tool result")


def sample(sess: Session, lang: str, text: str) -> dict:
    """한 표본. 실패도 표본이다 — 지우면 분모가 거짓말한다."""
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lang": lang}
    t0 = time.perf_counter()
    try:
        d = sess.search(text)
        row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        row["arms"] = d.get("arms")
        row["vector_rebuilding"] = bool(d.get("vector_rebuilding"))
        row["vector_note"] = (d.get("vector_note") or "")[:120] or None
        row["embed_migration"] = d.get("embed_migration")
        row["rows"] = len(d.get("rows") or [])
        row["error"] = d.get("error")
    except Exception as exc:                                        # noqa: BLE001
        row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        row["transport_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return row


def summarize(path: str) -> dict:
    rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    ok = [r for r in rows if "transport_error" not in r and not r.get("error")]
    # 분모는 **의미 팔이 요청된** 표본이다. 인코더가 아예 없으면(`arms` 가 fts 인데
    # note 도 없음) 재빌드와 무관하므로 여기서 빠진다.
    asked = [r for r in ok if r.get("vector_rebuilding") or r.get("arms") == "fts+vector"
             or r.get("vector_note")]
    reb = [r for r in asked if r.get("vector_rebuilding")]
    # 최장 연속 구간(표본 단위 → 분)
    longest = run = 0
    for r in ok:
        run = run + 1 if r.get("vector_rebuilding") else 0
        longest = max(longest, run)
    out = {"samples": len(rows), "usable": len(ok),
           "transport_errors": sum(1 for r in rows if "transport_error" in r),
           "semantic_requested": len(asked), "rebuilding": len(reb),
           "ratio": round(len(reb) / len(asked), 4) if asked else None,
           "longest_consecutive_samples": longest,
           "arms_census": {}}
    for r in ok:
        k = f"{r.get('arms')}|{'rebuilding' if r.get('vector_rebuilding') else ''}"
        out["arms_census"][k] = out["arms_census"].get(k, 0) + 1
    # 판정은 계획 §6 의 문턱을 그대로 적는다 — 읽는 사람이 다시 안 찾게.
    ratio = out["ratio"]
    out["verdict"] = ("no usable sample" if ratio is None else
                      "opens R7 (>= 0.20)" if ratio >= 0.20 else
                      "closes R7 (< 0.05)" if ratio < 0.05 else
                      "inconclusive — keep the counter and re-read")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--every", type=float, default=60.0, help="표본 간격(초)")
    ap.add_argument("--out", default="work/perf/rebuild-window.jsonl")
    ap.add_argument("--summarize", help="이미 있는 JSONL 을 요약만 하고 끝낸다")
    a = ap.parse_args()

    if a.summarize:
        print(json.dumps(summarize(a.summarize), ensure_ascii=False, indent=2))
        return 0

    token = os.environ.get("VINC_SK", "").strip()
    if not token:
        print("VINC_SK 가 비어 있다", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    sess = Session(token)
    sess.open()
    deadline = time.time() + a.hours * 3600
    i = 0
    with open(a.out, "a", encoding="utf-8") as f:
        while time.time() < deadline:
            lang, text = QUERIES[i % len(QUERIES)]
            row = sample(sess, lang, text)
            if "transport_error" in row:
                try:                      # 세션이 끊긴 것일 수 있다 — 한 번 다시 연다
                    sess.open()
                except Exception:         # noqa: BLE001
                    pass
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            i += 1
            time.sleep(max(1.0, a.every))
    print(json.dumps(summarize(a.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
