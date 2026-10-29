"""``python -X utf8 -m rag_eval.agent_tasks {run,compare,check,env}`` — 종료코드는 아래.

run: 0 끝까지 돌았다 · 1 거부(핀·런타임·분할·덮어쓰기)
compare: 0 같은 지문에 채점 차이 0 · 1 지문이 다르다(비교하지 않는다) · 3 채점 차이가 있다
check: 0 과제 파일이 유효하고 겹침이 없다 · 1 겹침이 있다(파일 오류는 예외로 멈춘다)
env: 실제 모델 키가 있는지만 말한다(값은 보지 않는다)
"""
from __future__ import annotations

import argparse
import json
import sys

from rag_eval.agent_tasks import arms, hosts, role_context, runner, tasks


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rag_eval.agent_tasks", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="한 회차를 돌린다")
    pin = r.add_mutually_exclusive_group(required=True)
    pin.add_argument("--store", help="eval_store.lock.json 의 스토어 키")
    pin.add_argument("--store-meta", help="락 항목과 같은 꼴의 핀 정보 JSON(락에 아직 없는 핀)")
    r.add_argument("--onnx", required=True, help="질의 인코더 ONNX(핀의 encoder_sha256 과 대조)")
    r.add_argument("--tasks", required=True, help="과제 파일(JSON)")
    r.add_argument("--split", required=True, choices=("smoke", "search", "test"))
    r.add_argument("--heldout", action="store_true", help="test 세트를 돌린다는 명시")
    r.add_argument("--disjoint-from", help="이 세트와 겹치는 과제가 있으면 거부한다")
    r.add_argument("--skip-handoff", action="store_true", help="handoff 과제를 빼고 그 수를 헤더에 남긴다")
    r.add_argument("--arms", default=",".join(arms.ARMS),
                   help="쉼표로 잇는다. A0 A1 A2 A3, A2 의 다른 문 A2-rest A2-cli, 역할 맥락 R")
    r.add_argument("--hosts", default=",".join(hosts.HOSTS))
    r.add_argument("--model", default="fake",
                   help="fake | azure:<배포> | openai:<모델> | claude-code:<모델 id>(구독, 키 없음)")
    r.add_argument("--policy", default="p0",
                   help="비교군 R 의 맥락 정책: p0(오늘의 prefetch) 또는 POLICY 를 내보내는 파이썬 파일")
    r.add_argument("--role-context", default=role_context.DEFAULT_ROLE, help="비교군 R 이 쓰는 역할")
    r.add_argument("--context-limit", type=int, default=role_context.DEFAULT_LIMIT,
                   help="비교군 R 의 맥락 글자 한도")
    r.add_argument("--judge-model", help="서술형 채점(judge·기권) 모델. 기본은 --model 과 같다")
    r.add_argument("--price", help="입력,출력 USD/백만 토큰(성공당 비용용; 없으면 비용을 모른다고 적는다)")
    r.add_argument("--runs", type=int, default=3, help="과제당 실행 수(계획: 3)")
    r.add_argument("--cap", type=int, default=hosts.TOOL_CALLS_PER_RUN, help="런당 도구 호출 상한")
    r.add_argument("--max-steps", type=int, default=8, help="런당 모델 호출 상한")
    r.add_argument("--a1-k", type=int, default=5)
    r.add_argument("--a1-chars", type=int, default=2000)
    r.add_argument("--candidate", default="baseline", help="후보 id(디렉터리 이름)")
    r.add_argument("--round", help="회차 이름(실행 id 앞머리, 기본: 결과 디렉터리 이름)")
    r.add_argument("--role", default=runner.DEFAULT_ROLE, help="과제에 role 이 없을 때 실행 기록의 역할")
    r.add_argument("--record", choices=("file", "dry-run", "hosted"), default="file")
    r.add_argument("--write-hosted", action="store_true", help="--record hosted 의 확인 스위치")
    r.add_argument("--store-note", help="이 핀을 고른 이유(핀 정보에 choice 가 없을 때)")
    r.add_argument("--out", required=True)
    r.add_argument("--quiet", action="store_true")
    c = sub.add_parser("compare", help="두 회차의 지문과 채점을 대조한다")
    c.add_argument("a")
    c.add_argument("b")
    k = sub.add_parser("check", help="과제 파일을 검사한다")
    k.add_argument("tasks")
    k.add_argument("--disjoint-from")
    sub.add_parser("env", help="실제 모델 키가 있는지만 말한다")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "compare":
        return runner.compare(a.a, a.b)
    if a.cmd == "check":
        return runner.check(a.tasks, a.disjoint_from)
    if a.cmd == "env":
        print(json.dumps(runner.env_report()))
        return 0
    try:
        return runner.run(a)
    except (runner.Refused, tasks.TaskFileError) as exc:
        print(f"거부: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
