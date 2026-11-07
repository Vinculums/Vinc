# -*- coding: utf-8 -*-
"""허브 writer 인계 리허설 — 임시 앱 정의, 쓰기·읽기 프로브, 전환 뒤 대조.

`.github/workflows/hub-handoff-rehearsal.yml` 이 부른다. 표준 라이브러리만 쓴다(러너의
파이썬에서 그대로 돈다). 운영 앱·운영 공유는 만지지 않는다: 앱은 리허설 이름으로 새로
만들고, 스토어는 빈 것으로 시작해 리허설 공유에만 스냅샷을 쓴다.

    python scripts/handoff_rehearsal.py app-yaml --name N --env-id ID --image IMG --storage S --out F
    python scripts/handoff_rehearsal.py probe   --url URL --prefix P --stop-file F --out log.jsonl
    python scripts/handoff_rehearsal.py verify  --url URL --log log.jsonl --out verdict.json

토큰은 인자로 받지 않는다(프로세스 목록에 남는다). `REHEARSAL_MCP_TOKEN` 을 읽는다.
설계: [[vinc.plan.hub-writer-handoff-2026-10-17]]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

TOKEN_ENV = "REHEARSAL_MCP_TOKEN"
#: 한 쓰기를 실패로 칠 때까지 다시 보내는 상한(초). 붙잡기 상한(90초)과 인계 대기
#: 상한(120초)을 합친 것보다 길어야, 인계 중의 503 을 실패로 세지 않는다.
WRITE_DEADLINE_S = 300.0
REQUEST_TIMEOUT_S = 100.0


# ── 앱 정의 ────────────────────────────────────────────────────────────────

def app_spec(*, name: str, env_id: str, image: str, storage: str, acr_password: str,
             mcp_token: str, sidecar_token: str, grace_s: int = 60) -> dict:
    """운영 허브와 같은 기동 경로(로컬 작업본 + /data 스냅샷 + 단일 리비전)를 가진 임시 앱.

    운영과 다른 것은 바깥 의존을 끊은 것뿐이다: 인코더·레이아웃·계정·OAuth 가 없다.
    임베딩은 운영처럼 **켠다** — 엔트리포인트가 운영과 같은 절(워커·미러 대기)을 타야
    종료 경로도 같아진다. 원격 인코더 주소는 아무도 듣지 않는 루프백이고, 적용은 멈춘다.
    첫 리비전만 빈 스토어로 시작할 수 있다(`VINC_STORE_ALLOW_EMPTY`) — 두 번째부터는
    워크플로가 이 값을 지워서, 스냅샷이 없으면 기동이 막히게 한다."""
    env = [
        {"name": "VINC_DATA_DIR", "value": "/data/db"},
        {"name": "VINC_STORE_LOCAL", "value": "on"},
        {"name": "VINC_STORE_SNAPSHOT_EVERY", "value": "60"},
        {"name": "VINC_PK_NAMESPACED", "value": "on"},
        {"name": "VINC_WAL_REPLAY_TOLERANT", "value": "1"},
        {"name": "VINC_EMBED", "value": "on"},
        {"name": "VINC_EMBED_REMOTE", "value": "http://127.0.0.1:9"},
        {"name": "VINC_EMBED_APPLY_PAUSED", "value": "on"},
        {"name": "VINC_QUOTA", "value": "off"},
        {"name": "VINC_STORE_ALLOW_EMPTY", "value": "1"},
        {"name": "VINC_REHEARSAL_GEN", "value": "1"},
        {"name": "VINC_MCP_TOKEN", "secretRef": "mcp-token"},
        {"name": "VINC_SIDECAR_TOKEN", "secretRef": "sidecar-token"},
    ]
    return {
        "location": "koreacentral",
        "name": name,
        "properties": {
            "environmentId": env_id,
            "workloadProfileName": "Consumption",
            "configuration": {
                "activeRevisionsMode": "Single",
                "ingress": {"external": True, "targetPort": 8790, "transport": "auto",
                            "allowInsecure": False},
                "registries": [{"server": "vincacr.azurecr.io", "username": "vincacr",
                                "passwordSecretRef": "acr-password"}],
                "secrets": [{"name": "acr-password", "value": acr_password},
                            {"name": "mcp-token", "value": mcp_token},
                            {"name": "sidecar-token", "value": sidecar_token}],
            },
            "template": {
                "containers": [{
                    "name": "vinc-hub",
                    "image": image,
                    "resources": {"cpu": 1.0, "memory": "2Gi"},
                    "env": env,
                    "volumeMounts": [{"volumeName": "data", "mountPath": "/data"}],
                }],
                "scale": {"minReplicas": 1, "maxReplicas": 1},
                "terminationGracePeriodSeconds": grace_s,
                "volumes": [{"name": "data", "storageType": "AzureFile",
                             "storageName": storage}],
            },
        },
    }


# ── MCP 호출 ───────────────────────────────────────────────────────────────

def _call(url: str, token: str, tool: str, arguments: dict, *, rid: int) -> tuple[int, dict | None, str]:
    """(HTTP 상태, 도구 결과 JSON 또는 None, 짧은 사유). 연결 실패는 상태 0."""
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"http {exc.code} retry-after={exc.headers.get('Retry-After')}"
    except Exception as exc:                                   # noqa: BLE001 — 연결 끊김도 센다
        return 0, None, f"{type(exc).__name__}: {exc}"[:200]
    try:
        result = json.loads(raw)["result"]
        payload = json.loads(result["content"][0]["text"])
    except Exception as exc:                                   # noqa: BLE001
        return status, None, f"unparsed: {type(exc).__name__}"
    if result.get("isError"):
        return status, payload if isinstance(payload, dict) else None, "tool error"
    return status, payload, "ok"


def _write_once(url: str, token: str, node_id: str, title: str, op: str, rid: int):
    status, payload, why = _call(url, token, "vinc_ingest", {
        "nodes": [{"id": node_id, "type": "record", "title": title}],
        "operation_id": op}, rid=rid)
    ok = status == 200 and isinstance(payload, dict) and payload.get("executed") is not False \
        and not payload.get("partial")
    return ok, status, why


def probe(url: str, token: str, prefix: str, stop_file: str, out: str, *,
          interval_s: float = 1.0, clock=time.monotonic, sleep=time.sleep) -> int:
    """멈춤 파일이 생길 때까지 1초마다 레코드 하나를 쓴다. 쓰기마다 한 줄을 남긴다.

    같은 쓰기는 같은 `operation_id` 로 다시 보낸다(중복 적용되지 않는다). 상한 안에
    끝내 못 쓴 것만 실패다 — 붙잡혀서 늦은 것, 503 뒤 재시도로 들어간 것은 성공이고
    그 지연과 첫 시도 상태를 함께 적는다."""
    n = 0
    with open(out, "a", encoding="utf-8") as log:
        while not os.path.exists(stop_file):
            n += 1
            node_id = f"record:{prefix}-{n:05d}"
            op = str(uuid.uuid4())
            started = clock()
            first, attempts, ok, status, why = None, 0, False, 0, ""
            while True:
                attempts += 1
                ok, status, why = _write_once(url, token, node_id, f"handoff probe {n}", op,
                                              rid=n * 100 + attempts)
                if first is None:
                    first = status
                if ok or clock() - started >= WRITE_DEADLINE_S:
                    break
                sleep(min(5.0, max(0.5, interval_s)))
            row = {"n": n, "id": node_id, "ok": ok, "first_status": first, "status": status,
                   "attempts": attempts, "latency_s": round(clock() - started, 2),
                   "why": "" if ok else why, "t": time.time()}
            log.write(json.dumps(row) + "\n")
            log.flush()
            sleep(interval_s)
    return 0


def summarize(rows: list[dict], found: dict[str, bool]) -> dict:
    """프로브 기록과 전환 뒤 읽기 결과로 판정을 만든다. 통과 조건은 둘이다:
    끝내 실패한 쓰기 0, 성공으로 답한 쓰기 가운데 사라진 것 0."""
    acked = [r for r in rows if r.get("ok")]
    failed = [r for r in rows if not r.get("ok")]
    missing = [r["id"] for r in acked if not found.get(r["id"], False)]
    retried = [r for r in acked if r.get("attempts", 1) > 1 or r.get("first_status") != 200]
    lat = sorted(r.get("latency_s", 0.0) for r in acked)
    return {
        "writes": len(rows),
        "acked": len(acked),
        "failed": len(failed),
        "missing_after_switch": len(missing),
        "missing_ids": missing[:50],
        "retried": len(retried),
        "first_attempt_statuses": sorted({r.get("first_status") for r in rows}, key=str),
        "max_latency_s": lat[-1] if lat else 0.0,
        "p50_latency_s": lat[len(lat) // 2] if lat else 0.0,
        "pass": bool(rows) and not failed and not missing,
    }


def verify(url: str, token: str, log_path: str, out: str) -> int:
    rows = [json.loads(line) for line in open(log_path, encoding="utf-8") if line.strip()]
    found: dict[str, bool] = {}
    for i, r in enumerate(rows):
        if not r.get("ok"):
            continue
        status, payload, _ = _call(url, token, "vinc_node", {"node_id": r["id"]}, rid=900000 + i)
        found[r["id"]] = status == 200 and isinstance(payload, dict) and bool(payload.get("found"))
    verdict = summarize(rows, found)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=1)
    print(json.dumps(verdict))
    return 0 if verdict["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="허브 writer 인계 리허설")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("app-yaml")
    a.add_argument("--name", required=True)
    a.add_argument("--env-id", required=True)
    a.add_argument("--image", required=True)
    a.add_argument("--storage", required=True)
    a.add_argument("--out", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--url", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--stop-file", required=True)
    p.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--url", required=True)
    v.add_argument("--log", required=True)
    v.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "app-yaml":
        spec = app_spec(name=args.name, env_id=args.env_id, image=args.image,
                        storage=args.storage, acr_password=os.environ["ACR_PASSWORD"],
                        mcp_token=os.environ[TOKEN_ENV],
                        sidecar_token=os.environ["REHEARSAL_SIDECAR_TOKEN"])
        # JSON 은 YAML 의 부분집합이라 `az containerapp create --yaml` 이 그대로 읽는다.
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        return 0
    token = os.environ[TOKEN_ENV]
    if args.cmd == "probe":
        return probe(args.url, token, args.prefix, args.stop_file, args.out)
    return verify(args.url, token, args.log, args.out)


if __name__ == "__main__":
    sys.exit(main())
