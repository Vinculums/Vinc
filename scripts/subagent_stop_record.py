# -*- coding: utf-8 -*-
"""subagent_stop_record.py — 로스터 에이전트(Maker·Checker)가 멈출 때 한 줄을 남기는 SubagentStop 훅.

하는 일: 하네스가 stdin 으로 주는 SubagentStop 입력에서 에이전트 종류·id·세션·cwd 를 읽고, 그 에이전트의
보고문에서 풀 리퀘스트 URL 을 찾아 JSONL 로그에 한 줄을 덧붙인다. 보고문은 두 자리에서 찾는다:
`last_assistant_message`(마지막 응답 글), 그리고 에이전트 트랜스크립트 안의 마지막 SubagentHandback
호출의 `message`(핸드백으로 보고하는 에이전트는 보고가 여기에만 있다). 트랜스크립트에서 모델 호출 수
(서로 다른 requestId 수)도 센다. 실행 기록(run record)에 붙일 사실을 로컬에 남기는 것이 전부다.

하지 않는 일: 에이전트를 붙잡지 않는다(종료 2·decision 출력이 없다), 그래프에 쓰지 않는다(vinc_run_submit
은 세션의 몫이다), 네트워크를 쓰지 않는다, URL 의 풀 리퀘스트가 실제로 있는지 확인하지 않는다.
트랜스크립트의 줄 꼴은 하네스가 문서로 약속한 계약이 아니라서, 꼴이 바뀌면 그 부분만 비어서 기록된다.

자기 오류로 에이전트를 가두지 않는다: 어떤 실패든 stderr 에 한 줄을 쓰고 0 으로 끝나며 stdout 은 비운다.

    python3 -I subagent_stop_record.py --log ~/.claude/agent-stops.jsonl < payload.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

#: GitHub 풀 리퀘스트 URL. 보고문에 여러 개면 나온 순서대로, 중복 없이 남긴다.
PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")
#: 이보다 큰 트랜스크립트는 읽지 않는다. 훅 시간 상한(설정 조각의 timeout) 안에 끝나야 한다.
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
DEFAULT_LOG = Path.home() / ".claude" / "agent-stops.jsonl"


def scan_transcript(path: str) -> tuple[str, int | None]:
    """(마지막 SubagentHandback 의 message, 모델 호출 수). 읽을 수 없으면 ("", None)."""
    fp = Path(path).expanduser()
    if not path or not fp.is_file() or fp.stat().st_size > MAX_TRANSCRIPT_BYTES:
        return "", None
    handback, requests = "", set()
    with fp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            if row.get("requestId"):
                requests.add(row["requestId"])
            content = (row.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") == "SubagentHandback"):
                    handback = str((block.get("input") or {}).get("message") or "")
    return handback, (len(requests) if requests else None)


def pull_requests(*texts: str) -> list[str]:
    seen: list[str] = []
    for text in texts:
        for url in PR_URL.findall(text or ""):
            if url not in seen:
                seen.append(url)
    return seen


def record(payload: dict) -> dict:
    """SubagentStop 입력 → 로그 한 줄(dict). 순수 함수에 가깝다: 읽는 것은 트랜스크립트 파일뿐이다."""
    last = str(payload.get("last_assistant_message") or "")
    handback, calls = scan_transcript(str(payload.get("agent_transcript_path") or ""))
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": payload.get("hook_event_name") or "SubagentStop",
        "agent_type": payload.get("agent_type") or "",
        "agent_id": payload.get("agent_id") or "",
        "session_id": payload.get("session_id") or "",
        "cwd": payload.get("cwd") or "",
        "transcript": payload.get("agent_transcript_path") or "",
        "report": "handback" if handback else ("last_message" if last else "none"),
        "pull_requests": pull_requests(handback, last),
        "model_calls": calls,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        log = Path(args[args.index("--log") + 1]).expanduser() if "--log" in args else DEFAULT_LOG
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("입력이 JSON 객체가 아니다")
        line = json.dumps(record(payload), ensure_ascii=False)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except Exception as e:  # noqa: BLE001 — 기록 실패가 에이전트를 붙잡으면 안 된다
        print(f"[subagent-stop] 기록하지 못했다: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
